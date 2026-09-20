"""Enforce the claim-then-hand-off invariant across `src/kraft/`.

    No path may write a *runnable* status and then take a branch that returns
    without either handing the item to a walk, or leaving it in a status some
    selector picks up again.

A path that does leaves the item reading `active` -- which *means* "a walk is
behind this" -- with nothing behind it. It looks running, is not, and nothing
will select it again: `ci_wait.tick` filters `status = 'waiting'`,
`rate_limit_retry.tick` filters `status = 'rate_limited'`, and no selector
filters `active`.

This is a check, not a report: it exits 1 on any unprotected exit, so it can
gate CI. Run `uv run python dev/check_claim_handoff.py [-v]`.

## What it derives, and why each rule is the shape it is

Three rounds of one defect on one branch taught the same lesson twice, once
about the code and once about this file. Both are written into the rules below,
because a checker that can be fooled is worse than no checker: it licenses the
belief it was built to test.

**CLAIMS is derived from `store/`, not listed here.** A hand-maintained
vocabulary is the same "hand a fixer a list" failure the invariant is about, one
level up -- and it had already drifted: the first version of this file omitted
`store.approve_gate`, which is an unconditional `UPDATE work_items SET status =
'active'`, so the whole gate-approval family was invisible. `_claims()` reads
the SQL and the `to_status` default out of the AST instead. Its output is
printed with `-v` so the input set is auditable rather than trusted.

**Dominance, never lexical order.** A stop only counts if it *must* have run
before the exit: `_dominators` walks the ancestor chain and keeps, for each
ancestor block, only the siblings preceding that ancestor. `_must_run` narrows
again -- a preceding `if` contributes only its test, never its branch bodies.
This is the load-bearing rule. Round 1's poller bug read as already handled
under a lexical scan, because its `mark_needs_human` sits in a *different
branch* of an earlier `if`. The `with` arm recurses into the body (a `with`
block does run) but not into `except` handlers, for the same reason.

**"After a claim" is dominance too, not a line-number comparison.** A claim on
line 10 does not reach an exit on line 20 if the two are in sibling branches,
and a claim inside a loop body does reach a `break` above it in source order.

**Bracket credit is scoped to the bracket.** `stops.claimed_or_stopped` covers
an exit only when that exit is a descendant of the `async with` that opens it --
not when the function merely mentions it somewhere. Function-wide credit passes
a claim made *outside* the bracket, which is the same false-positive shape as a
lexical stop.

**Exits include `break`, `continue` and falling off the end.** A function whose
last statement is not a `return`/`raise` can complete without one, and that is
an exit like any other.
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

#: Functions that leave a work item in a status some selector picks up again, or
#: a terminal one. Read off `store/`'s own `SET status = '<name>'` literals --
#: anything that is not `active` and not a parameter is a stop.
_STOP_SQL = re.compile(r"UPDATE\s+work_items\s+SET\s+status\s*=\s*'(?!active')", re.I)

#: Registering or awaiting a walk. Name-based, because the hand-off is a call
#: into another module rather than a SQL write with a recognisable shape.
HANDOFF = frozenset({"spawn", "run", "run_once", "_spawn_conflict_resolution"})

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
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _store_functions(pattern: re.Pattern[str]) -> set[str]:
    """Every `store/` function whose own SQL matches `pattern`.

    Nested defs are attributed to themselves, which is right: `store/` has none
    that matter, and a nested writer would be its own claim.
    """
    if not STORE.is_dir():
        raise SystemExit(f"{STORE} is not a directory; is this a Kraft checkout?")
    out: set[str] = set()
    for path in sorted(STORE.glob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text())):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and any(
                pattern.search(s) for s in _strings(fn)
            ):
                out.add(fn.name)
    return out


def claims() -> set[str]:
    """Every `store/` function that leaves an existing work item runnable.

    `UPDATE`, never `INSERT`: a claim is a *transition* of a row that already
    exists, and the hand-off for it belongs to the same function. `create_work_item`
    also takes `status="active"`, but its hand-off is its caller's -- `intake`
    returns an id and the route spawns the walk -- so a region spanning two
    functions is not the shape this invariant is about. `SET status = ?`
    (`claim_for_run`) counts: over-including a call that passes a non-active
    status is the conservative direction.
    """
    return _store_functions(_CLAIM_SQL)


def stops() -> set[str]:
    return _store_functions(_STOP_SQL)


def _must_run(stmt: ast.stmt) -> set[str]:
    """Calls guaranteed to have run if `stmt` completed.

    A preceding `if` contributes only its test. Crediting its branch bodies is
    the over-count that let round 1's poller bug read as handled: their
    `mark_needs_human` sits in a *different* branch of an earlier `if`, and a
    lexical "appears above" scan calls that a stop.
    """
    out: set[str] = set()
    if isinstance(stmt, ast.If | ast.While):
        out |= _called(stmt.test)
    elif isinstance(stmt, ast.For | ast.AsyncFor):
        out |= _called(stmt.iter)
    elif isinstance(stmt, ast.Try):
        # The body runs (until an exception); the handlers need not.
        for s in stmt.body + stmt.finalbody:
            out |= _must_run(s)
    elif isinstance(stmt, ast.With | ast.AsyncWith):
        # The block does run, so recurse -- but into the body only, the same way
        # `Try` does. `_called(stmt)` here would walk `except` handlers nested
        # inside it and re-introduce exactly the lexical over-credit this
        # function exists to remove.
        for item in stmt.items:
            out |= _called(item.context_expr)
        for s in stmt.body:
            out |= _must_run(s)
    elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        pass  # a definition is not a call
    else:
        out |= _called(stmt)
    return out


def _dominators(fn: ast.AST, node: ast.AST, parents: dict) -> list[ast.stmt]:
    """Statements that must have executed before `node`: for each ancestor
    block, the siblings preceding that ancestor in it."""
    out: list[ast.stmt] = []
    cur = node
    while cur is not fn and cur in parents:
        parent = parents[cur]
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and cur in block:
                out.extend(block[: block.index(cur)])
        cur = parent
    return out


def _owner(fn: ast.AST, node: ast.AST, parents: dict) -> ast.AST:
    """The innermost function that lexically owns `node`."""
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef):
            return cur
    return fn


def _can_fall_through(stmt: ast.stmt) -> bool:
    """Whether control can reach the statement *after* `stmt`.

    Without this, a function whose body ends in the bracket's own `async with`
    was reported as falling off the end -- and the credit for that phantom exit
    was computed over the whole compound statement, which is the lexical
    over-credit this file exists to refuse.
    """
    if isinstance(stmt, ast.Return | ast.Raise | ast.Break | ast.Continue):
        return False
    if isinstance(stmt, ast.If):
        return not stmt.orelse or _body_falls_through(stmt.body) or _body_falls_through(stmt.orelse)
    if isinstance(stmt, ast.With | ast.AsyncWith):
        return _body_falls_through(stmt.body)
    if isinstance(stmt, ast.Try):
        return _body_falls_through(stmt.body) or any(
            _body_falls_through(h.body) for h in stmt.handlers
        )
    return True


def _body_falls_through(body: list[ast.stmt]) -> bool:
    return not body or _can_fall_through(body[-1])


def _exits(fn: ast.FunctionDef | ast.AsyncFunctionDef, parents: dict) -> list[ast.stmt]:
    """Every way control leaves `fn`: `return`, `raise`, `break`, `continue`,
    and falling off the end when the last statement is neither a return nor a
    raise."""
    out = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Return | ast.Raise | ast.Break | ast.Continue)
        and _owner(fn, n, parents) is fn
    ]
    if _body_falls_through(fn.body):
        out.append(fn.body[-1])  # falls off the end: an exit like any other
    return out


def _bracketed(node: ast.AST, parents: dict, dom: set[str]) -> bool:
    """Whether the bracket covers `node`: either it encloses the exit, or it has
    already completed before it.

    Scoped to the block and to dominance, never to the function: function-wide
    credit passes a claim made *outside* the bracket, which is the same false
    positive as a lexical stop. The "already completed" half is what covers a
    deliberate read *after* the bracket -- `resume_after_escalation` reports the
    status the bracket may just have written, so that read has to be outside it.

    The walk does not stop at the owning function, deliberately: a nested `def`
    written inside a bracket (`ci_wait`'s `db.write` transaction body) hands its
    value back to a caller that *is* bracketed, so it is covered too. That is
    also why the bracket there starts before the claim rather than after it --
    placing it after would leave the nested def outside, and the checker would
    report a row a human has to re-adjudicate on every run.
    """
    if BRACKET in dom:
        return True
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.With | ast.AsyncWith) and any(
            BRACKET in _called(item.context_expr) for item in cur.items
        ):
            return True
    return False


def audit() -> list[tuple[str, str, str, str, str, str]]:
    claim_names, stop_names = claims(), stops()
    rows = []
    for path in sorted(ROOT.rglob("*.py")):
        if "_bundled" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for ex in _exits(fn, parents):
                dom: set[str] = set()
                for stmt in _dominators(fn, ex, parents):
                    dom |= _must_run(stmt)
                # "After a claim" by dominance, not by line number: a claim in a
                # sibling branch does not reach this exit, and a claim in a loop
                # body does reach a `break` written above it.
                if not dom & claim_names:
                    continue
                dom |= _called(ex)
                rows.append(
                    (
                        f"{path.relative_to(ROOT.parent.parent)}:{ex.lineno}",
                        fn.name,
                        type(ex).__name__.lower(),
                        "STOP" if dom & stop_names else "",
                        "HANDOFF" if dom & HANDOFF else "",
                        "BRACKET" if _bracketed(ex, parents, dom) else "",
                    )
                )
    return rows


def main(argv: list[str]) -> int:
    rows = audit()
    bad = [r for r in rows if not (r[3] or r[4] or r[5])]
    if "-v" in argv:
        print(f"claims derived from store/: {sorted(claims())}")
        print(f"stops  derived from store/: {sorted(stops())}")
        print("\nevery exit a claim reaches:")
        for r in sorted(rows):
            print(f"  {r[0]:<46} {r[1]:<28} {r[2]:<9}{r[3]:<6}{r[4]:<9}{r[5]}")
        print()
    files = len({r[0].rsplit(":", 1)[0] for r in rows})
    print(f"{len(rows)} exits a claim reaches, across {files} files")
    if not bad:
        print("ok: every one of them stops, hands off, or sits inside the bracket")
        return 0
    print(f"{len(bad)} with no dominating stop, hand-off or bracket:\n")
    for r in bad:
        print(f"  {r[0]:<46} {r[1]:<28} {r[2]}")
    print(
        "\nEach of these can leave a work item reading `active` with nothing behind it. "
        "Wrap the region in `stops.claimed_or_stopped` rather than adding a stop per exit."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
