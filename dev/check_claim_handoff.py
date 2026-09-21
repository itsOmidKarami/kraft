"""Every claim in `src/kraft/` must sit inside `stops.claimed_or_stopped`.

A *claim* is a write that leaves a work item runnable (`UPDATE work_items SET
status = 'active'`). It means "a walk is behind this". A region that claims and
then leaves without either handing the item to a walk or moving the status again
leaves the item reading `active` with nothing behind it: it looks running, is
not, and nothing will select it again -- `ci_wait.tick` filters
`status = 'waiting'`, `rate_limit_retry.tick` filters `status = 'rate_limited'`,
and no selector filters `active`.

This is a check, not a report: it exits 1 on any unbracketed claim, so it can
gate CI. Run `uv run python dev/check_claim_handoff.py [-v]`.

## What it proves, and what it does not

**It proves: every claim call in `src/kraft/` is lexically inside a
`stops.claimed_or_stopped` block, or inside a helper whose own call sites all
are.** That is the whole claim. It is *not* a proof that no work item can be
stranded -- a bracket with the wrong `handed_off`, or a stop written into the
`finally` that itself raises, is outside what any AST walk can see.

But within that scope it is sound, and the earlier shape of this file was not.
Three rounds of one defect taught the lesson twice, once about the code and once
about this file:

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
also needs no allowlist: all ten claim sites in `src/kraft` pass.

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

**A claim reached through a helper is still a claim.** `executor.apply_rejection`
wraps `store.reject_gate` and hands the re-entry index back to its caller, so the
bracket belongs in the caller, not in it. Rather than allowlisting it, a function
holding an unbracketed claim is *promoted* into the vocabulary and its own call
sites are checked instead -- but only when it has call sites, because promoting a
route handler nobody calls would make its claim vanish, which is the false
negative this whole file exists to refuse.
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
_CLAIM_SQL = re.compile(r"UPDATE\s+work_items\s+SET\s+status\s*=\s*(?:'active'|\?)", re.I)

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
    inside a bracket (`ci_wait`'s `db.write` transaction body) claims on behalf
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


def _sites(
    mods: list[tuple[pathlib.Path, ast.Module, dict]],
    store_claims: set[str],
    promoted: set[str],
) -> list[tuple[str, str, str, bool, ast.AST | None]]:
    """(location, claim name, owning function, bracketed, owning function node).

    A `store/`-derived name counts only when called off `store` itself: `mcp.py`
    and `cli/item.py` both have an `approve_gate` that is the *HTTP client*'s,
    not the store's. A promoted name is a plain call in this tree, so it is
    matched on the callee name alone.
    """
    out = []
    for path, tree, parents in mods:
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            f = call.func
            if isinstance(f, ast.Attribute):
                name, base = f.attr, getattr(f.value, "id", None)
            else:
                name, base = getattr(f, "id", ""), None
            if not ((name in store_claims and base == "store") or name in promoted):
                continue
            fn = _owner(call, parents)
            out.append(
                (
                    f"{path.relative_to(ROOT.parent.parent)}:{call.lineno}",
                    name,
                    fn.name if fn is not None else "<module>",
                    _bracketed(call, parents),
                    fn,
                )
            )
    return out


def audit() -> list[tuple[str, str, str, bool]]:
    """Every claim site in `src/kraft`, and whether a bracket encloses it."""
    mods = _modules()
    store_claims, promoted = claims(), set()
    while True:
        sites = _sites(mods, store_claims, promoted)
        # A function that claims without a bracket claims on its caller's
        # behalf, so check its call sites instead of it -- but only if it has
        # any. Promoting an uncalled route handler would make its claim vanish.
        called = {n for _, tree, _ in mods for n in _called(tree)}
        grow = {
            fn.name
            for _, _, _, ok, fn in sites
            if not ok and fn is not None and fn.name in called and fn.name not in promoted
        }
        if not grow:
            # A promoted helper's own claim is answered by the checks on its call
            # sites, so it is not a row of its own -- reporting both says the
            # same thing twice and names the wrong line first.
            return [
                (loc, name, owner, ok) for loc, name, owner, ok, _ in sites if owner not in promoted
            ]
        promoted |= grow


def main(argv: list[str]) -> int:
    rows = audit()
    bad = [r for r in rows if not r[3]]
    if "-v" in argv:
        print(f"claims derived from store/: {sorted(claims())}\n")
        print("every claim site:")
        for r in sorted(rows):
            print(f"  {'OK  ' if r[3] else 'BAD '}{r[0]:<46} {r[2]:<28} {r[1]}")
        print()
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
