"""Every claim in `src/kraft/` must sit inside `stops.claimed_or_stopped`.

A *claim* is a write that leaves a work item runnable (`UPDATE work_items SET
status = 'active'`). It means "a walk is behind this". A region that claims and
then leaves without either handing the item to a walk or moving the status again
leaves the item reading `active` with nothing behind it: it looks running, is
not, and nothing will select it again -- `waits.tick` filters
`status = 'waiting'`, `rate_limit_retry.tick` filters `status = 'rate_limited'`,
and no selector filters `active`.

This is a check, not a report: it exits 1 on any unbracketed claim, so it can
gate CI. Run `uv run python dev/check_claim_handoff.py [-v]`.

## What it proves, and what it does not

**It proves: every call this file's derivation *recognises* as a claim is either
lexically inside a `stops.claimed_or_stopped` block, or is reported.** Two
qualifiers, both load-bearing and both learned the hard way:

* *"recognises as a claim"*, not "every claim". The vocabulary is derived by one
  regex over the string literals in `store/*.py`, and it has missed a real claim
  three times (a hand-written list without `approve_gate`; an `ast.Constant`-only
  reader that could not see `reject_gate`'s f-string; a pattern that required
  `status` to be the first assigned column). So `N claim sites` is a **floor, not
  a count**, and nothing in the output says how much fell outside it. The fix for
  that is not a better regex -- it is `store/` asserting its own status writers so
  the vocabulary stops being inferred (Kraft-gyrbx).
* *"or is reported"*, not "is bracketed". A claim whose bracket belongs to its
  callers (`executor.apply_rejection`) is printed `DLG` and its callers are
  checked. Nothing is ever silently dropped -- see the promotion section below for
  why that sentence is written this way.

**It is not a proof that no work item can be stranded.** Five things pass it:

1. a bracket whose `handed_off` predicate is wrong (this is not hypothetical: a
   real instance of it was found in `resume_after_escalation` *after* this check
   went green over that site);
2. a `finally` whose own `db.write` raises, replacing the stop with a new
   exception;
3. cancellation -- an `await` in the `finally` raises `CancelledError` before the
   stop lands;
4. a claim the derivation cannot see (above);
5. a claim called other than as `store.<name>(...)` or a bare `<name>(...)`. The
   attribute base is checked so that `mcp.py`'s and `cli/item.py`'s *HTTP client*
   `approve_gate` is not a false row -- but that makes any other alias of the
   `store` module invisible. A bare name is accepted (outside `store/` itself) so
   a direct `from kraft.store.work_items import claim_for_run` is seen; an
   `import ... as` alias still is not.

Within that scope it is sound, and the earlier shape of this file was not. Four
rounds of one defect taught the lesson twice, once about the code and once about
this file:

**Ask "is this claim bracketed", never "is this exit handled".** The first
version enumerated every way control could leave a function that had claimed,
and asked of each whether a stop dominated it. That question cannot be answered
statically: the exits are not all statements. A `KeyError` from a dict index, a
defaultless `next(...)`, a `LookupError` from a legacy row -- none is a `return`
or a `raise` node, and the original defect was exactly that shape. Five of seven
synthetic violations, including that one, passed the exit walk at exit 0.

The inverse question needs no exit analysis at all, because `try/finally`
protects exception exits *by construction*: `claimed_or_stopped`'s body after
`yield` is a bare `finally` with no `return`, `break` or `raise`, so every way
control leaves the bracketed region runs the stop. So "the claim is inside a
bracket" is *sufficient*, where "every enumerable exit is covered" was not. It
also needs no allowlist.

**The vocabulary is derived from `store/`, not listed here.** A hand-maintained
set is the same "hand a fixer a list" failure the invariant is about, one level
up -- and it had already drifted twice. The first version omitted
`store.approve_gate`, so the whole gate-approval family was invisible. The
second read SQL out of the AST but only from `ast.Constant`, so
`store.reject_gate`'s f-string `SET status = {status}` was invisible and hid an
unbracketed claim site in the human reject door. `_strings` now renders a
`JoinedStr` with `?` standing in for each interpolation, which is the
conservative direction. The derived set is printed with `-v` so it is auditable
rather than trusted.

**A claim reached through a helper is still a claim -- and promotion must never
delete a row.** `executor.apply_rejection` wraps `store.reject_gate` and hands the
re-entry index back to its caller, so the bracket belongs in the caller, not in
it. Rather than allowlisting it, a function holding an unbracketed claim is
*promoted* and its own call sites are checked as well.

The first version of that promoted on the bare name of *any* owner and then
**deleted the promoted owner's row**, which reopened this file's own defect class
one level up. Kraft names its route handlers after the store function they wrap,
so taking the bracket off `resume_work_item` promoted the *door* on that name,
deleted its claim row, found no other unbracketed call of the name, and reported
**exit 0 with nothing flagged** -- on one of the five sites the original defect
occurred at. Removing a bracket was usefully reported at 2 of 9 sites; three
others cascaded into 7 to 100+ rows reaching `__main__.py` with the real line
absent.

Three rules, each closing one of those:

* **No row is ever dropped.** A delegated claim prints `DLG` and is excluded from
  the failure list, but it is in the output and countable.
* **A `store/` name is never promoted.** The door and the store function share it,
  so promoting on the name checks the wrong call sites.
* **Promotion requires a bracketed call site** -- the codebase saying the bracket
  belongs to this function's callers. That is what separates `apply_rejection`
  from `rate_limit_retry._retry_one`, whose caller is a poller and not a bracket,
  and it is what stops the cascade: a function whose callers are unbracketed is
  nobody's delegate, so its own claim stays its own.

`tests/test_check_claim_handoff.py` runs the check that decides whether any of
this is worth anything: it removes each of the nine real brackets in `src/kraft`
in turn and requires exit 1 with the violations in *that file and no other*. A
gate that passes on good code and stays quiet on bad is not a gate.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

#: Anchored to this file, not the working directory: a cwd-relative root makes
#: the check pass silently from anywhere else, which is the failure mode a CI
#: guard can least afford.
ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "kraft"
STORE = ROOT / "store"

#: An `UPDATE` that leaves a work item runnable. `SELECT ... WHERE status =
#: 'active'` (`active_count`, and `claim_for_run`'s own capacity clause) must not
#: match, so the UPDATE and the table are both part of the pattern.
#:
#: `status` is looked for *anywhere* after the `SET`, not as the first assigned
#: column: `SET updated_at = ?, status = 'active'` is the same claim spelled
#: differently, and every writer in `store/` happening to put `status` first is a
#: coincidence one word wide. The cost is that an `UPDATE work_items SET
#: <something else> ... WHERE status = 'active'` would also match -- an
#: over-inclusion, which is the direction this check errs in on purpose.
_CLAIM_SQL = re.compile(
    r"UPDATE\s+work_items\s+SET\b.*?\bstatus\s*=\s*(?:'active'|\?)", re.I | re.S
)

#: The bracket that enforces the invariant for a whole region
#: (`kraft.executor.stops.claimed_or_stopped`).
BRACKET = "claimed_or_stopped"


def _called(node: ast.AST) -> set[str]:
    """Every function name called anywhere under `node`."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return out


def _strings(node: ast.AST) -> list[str]:
    """Every string literal under `node`, with f-strings rendered.

    An f-string's SQL arrives as fragments, and the only fragment
    `store.reject_gate` carries is `"UPDATE work_items SET status = "` -- which
    matches nothing. `?` stands in for each interpolation: an interpolated status
    *may* be `'active'`, and over-including is the conservative direction.
    """
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.JoinedStr):
            out.append("".join(p.value if isinstance(p, ast.Constant) else "?" for p in n.values))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
    return out


def claims() -> set[str]:
    """Every `store/` function that leaves an existing work item runnable.

    `UPDATE`, never `INSERT`: a claim is a *transition* of a row that already
    exists, and the hand-off for it belongs to the same region.
    `create_work_item` also takes `status="active"`, but its hand-off is its
    caller's -- `intake` returns an id and the route spawns the walk. `SET status
    = ?` (`claim_for_run`) counts: over-including a call that passes a
    non-active status is the conservative direction.

    Nested defs are attributed to themselves, which is right: `store/` has none
    that matter, and a nested writer would be its own claim. `rglob`, not
    `glob`: a future `store/` subpackage must not be silently unread.
    """
    if not STORE.is_dir():
        raise SystemExit(f"{STORE} is not a directory; is this a Kraft checkout?")
    out: set[str] = set()
    for path in sorted(STORE.rglob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text())):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                _CLAIM_SQL.search(s) for s in _strings(fn)
            ):
                out.add(fn.name)
    return out


def _owner(node: ast.AST, parents: dict) -> ast.AST | None:
    """The innermost function lexically containing `node`."""
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef):
            return cur
    return None


def _bracketed(node: ast.AST, parents: dict) -> bool:
    """Whether a `claimed_or_stopped` block encloses `node`.

    The walk crosses function boundaries deliberately: a nested `def` written
    inside a bracket (the wait scheduler's claim, written under its bracket) claims on behalf
    of a region that *is* bracketed, so it is covered too.
    """
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.With | ast.AsyncWith) and any(
            BRACKET in _called(item.context_expr) for item in cur.items
        ):
            return True
    return False


def _modules() -> list[tuple[pathlib.Path, ast.Module, dict]]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        if "_bundled" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        out.append((path, tree, {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}))
    return out


def _store_names() -> set[str]:
    """Every function `store/` defines, claim or not.

    Used to refuse a promotion, not to find claims: Kraft names its route
    handlers after the store function they wrap (`resume_work_item`,
    `retry_work_item`, `approve_gate`, `reject_gate` are each both a `store/`
    function and a door), and promoting a door on that name is how round 4's
    checker went silent on two of the five original defect sites.
    """
    if not STORE.is_dir():
        raise SystemExit(f"{STORE} is not a directory; is this a Kraft checkout?")
    return {
        fn.name
        for path in sorted(STORE.rglob("*.py"))
        for fn in ast.walk(ast.parse(path.read_text()))
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _calls(mods: list[tuple[pathlib.Path, ast.Module, dict]]) -> list[tuple]:
    """(location, callee name, attribute base or None, owning function name,
    bracketed) for every call in the tree.

    One pass over everything, because promotion asks a question about calls that
    are not claims -- "is this function ever called from inside a bracket" -- and
    computing that from a claims-only list is what made round 4's promotion guess.
    """
    out = []
    for path, tree, parents in mods:
        rel = str(path.relative_to(ROOT.parent.parent))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            f = call.func
            if isinstance(f, ast.Attribute):
                name, base = f.attr, getattr(f.value, "id", None)
            else:
                name, base = getattr(f, "id", ""), None
            fn = _owner(call, parents)
            out.append(
                (
                    f"{rel}:{call.lineno}",
                    name,
                    base,
                    fn.name if fn is not None else "<module>",
                    _bracketed(call, parents),
                )
            )
    return out


def audit() -> list[tuple[str, str, str, bool, bool]]:
    """(location, claim name, owning function, bracketed, delegated) per claim site.

    A row is a violation when it is neither bracketed nor delegated. **No row is
    ever dropped**: round 4 deleted a promoted owner's row, and a name collision
    then deleted every row in the colliding function -- so taking the bracket off
    `resume_work_item` left this check at exit 0 with nothing flagged, on one of
    the five sites the original defect occurred at.
    """
    mods = _modules()
    calls = _calls(mods)
    store_claims, store_names, promoted = claims(), _store_names(), set()

    # `store/`'s own internal calls are not claim *sites*: the bracket lives in a
    # caller outside `store/`, which is what makes a `store/` function the claim
    # primitive rather than a claim user.
    inside_store = f"{STORE.relative_to(ROOT.parent.parent)}/"

    def sites():
        return [
            c
            for c in calls
            if (
                c[1] in store_claims
                and c[2] in (None, "store")
                and not c[0].startswith(inside_store)
            )
            or c[1] in promoted
        ]

    while True:
        # "Delegating" is the codebase saying the bracket belongs to this
        # function's callers: at least one call to it is inside a bracket. That is
        # what makes `executor.apply_rejection` a helper and `_retry_one` not one
        # -- and it is why removing a real bracket can no longer promote the site
        # away, since a site whose callers are unbracketed is nobody's delegate.
        delegating = {c[1] for c in calls if c[4]}
        grow = {
            c[3]
            for c in sites()
            # A `store/` name is never promoted: the door and the store function
            # share it, so promoting on the name checks the wrong call sites.
            if not c[4] and c[3] not in promoted and c[3] in delegating and c[3] not in store_names
        }
        if not grow:
            return [(c[0], c[1], c[3], c[4], c[3] in promoted) for c in sites()]
        promoted |= grow


def main(argv: list[str]) -> int:
    rows = audit()
    bad = [r for r in rows if not (r[3] or r[4])]
    if "-v" in argv:
        print(f"claims derived from store/: {sorted(claims())}\n")
        print("every claim site:")
        for r in sorted(rows):
            tag = "OK  " if r[3] else ("DLG " if r[4] else "BAD ")
            print(f"  {tag}{r[0]:<46} {r[2]:<28} {r[1]}")
        print("\n  DLG = delegated to this function's callers, which are checked instead\n")
    files = len({r[0].rsplit(":", 1)[0] for r in rows})
    print(f"{len(rows)} claim sites, across {files} files")
    if not bad:
        print(f"ok: every one of them is inside a `stops.{BRACKET}` bracket")
        return 0
    print(f"{len(bad)} outside any bracket:\n")
    for r in bad:
        print(f"  {r[0]:<46} {r[2]:<28} {r[1]}")
    print(
        "\nEach of these can leave a work item reading `active` with nothing behind it. "
        f"Wrap the region in `stops.{BRACKET}` rather than adding a stop per exit."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
