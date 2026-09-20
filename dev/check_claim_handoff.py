"""Enumerate the "claimed, then returned without handing off" class.

Derived, not asserted:

  CLAIMS  store functions that leave a work item in a *runnable* status --
          found by reading src/kraft/store/ for `SET status = 'active'` and
          `to_status: str = "active"`.
  STOPS   store functions that leave it in a status some selector picks up
          again, or a terminal one.
  HANDOFF registering or awaiting a walk.

For every function that calls a CLAIM, every `Return`/`Raise` after it is
listed with whether a STOP or a HANDOFF *dominates* that exit -- i.e. appears
among the statements that must have run before it: the siblings preceding each
of its ancestor blocks. Lexical "appears somewhere earlier in the function"
over-credits a stop that sits in a different branch, which is exactly how the
two pollers' bug survived a review.

Run: uv run python dev/check_claim_handoff.py [-v]
"""

import ast
import pathlib
import sys

CLAIMS = {"claim_for_run", "mark_reentered", "resume_work_item", "retry_after_cap", "skip_node"}
STOPS = {
    "mark_needs_human",
    "pause_work_item",
    "mark_waiting",
    "mark_rate_limited",
    "mark_completed",
    "abandon_work_item",
    "pause_for_broken_base",
}
HANDOFF = {"spawn", "run", "_spawn_conflict_resolution"}
#: The bracket introduced in fix round 2. A function using it needs no per-exit
#: stop, because the bracket performs one on any exit that left the item claimed.
BRACKET = {"claimed_or_stopped"}

ROOT = pathlib.Path("src/kraft")


def called(node):
    """Every function name called anywhere under `node`."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return out


def must_run(stmt):
    """Calls that are *guaranteed* to have run if `stmt` completed.

    A preceding `if` contributes only its test -- crediting its branch bodies is
    the over-count that let the two pollers' bug read as already handled: their
    `mark_needs_human` sits in a *different* branch of an earlier `if`, and a
    lexical "appears above" scan calls that a stop.
    """
    out = set()
    if isinstance(stmt, ast.If | ast.While):
        out |= called(stmt.test)
    elif isinstance(stmt, ast.For | ast.AsyncFor):
        out |= called(stmt.iter)
    elif isinstance(stmt, ast.Try):
        for s in stmt.body + stmt.finalbody:
            out |= must_run(s)
    elif isinstance(stmt, ast.With | ast.AsyncWith):
        out |= called(stmt)
    elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
        pass  # a nested def is not a call
    else:
        out |= called(stmt)
    return out


def dominators(fn, exit_node, parents):
    """Statements that must have executed before `exit_node`: for each ancestor
    block, the siblings that precede the ancestor in it."""
    out, cur = [], exit_node
    while cur is not fn:
        parent = parents[cur]
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and cur in block:
                out.extend(block[: block.index(cur)])
        cur = parent
    return out


rows = []
for path in sorted(ROOT.rglob("*.py")):
    if "_bundled" in path.parts:
        continue
    tree = ast.parse(path.read_text())
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        fn_calls = called(fn)
        if not fn_calls & CLAIMS:
            continue
        claim_line = min(
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
            in CLAIMS
        )
        bracketed = bool(fn_calls & BRACKET)
        for ex in ast.walk(fn):
            if not isinstance(ex, ast.Return | ast.Raise) or ex.lineno <= claim_line:
                continue
            # A nested `def` is its own scope: the `return result` of a
            # `db.write(...)` transaction body is a value, not a control exit
            # from the claiming function.
            owner, cur = fn, ex
            while cur in parents:
                cur = parents[cur]
                if isinstance(cur, ast.FunctionDef | ast.AsyncFunctionDef):
                    owner = cur
                    break
            if owner is not fn:
                continue
            dom = set()
            for stmt in dominators(fn, ex, parents):
                dom |= must_run(stmt)
            dom |= called(ex)
            rows.append(
                (
                    f"{path}:{ex.lineno}",
                    fn.name,
                    "return" if isinstance(ex, ast.Return) else "raise",
                    "STOP" if dom & STOPS else "",
                    "HANDOFF" if dom & HANDOFF else "",
                    "BRACKET" if bracketed else "",
                )
            )

bad = [r for r in rows if not (r[3] or r[4] or r[5])]
print(
    f"{len(rows)} exits after a claim, across {len({r[0].rsplit(':', 1)[0] for r in rows})} files"
)
print(f"{len(bad)} with no dominating stop, hand-off or bracket:\n")
for r in bad:
    print(f"  {r[0]:<46} {r[1]:<28} {r[2]}")
if "-v" in sys.argv:
    print("\nevery exit:")
    for r in sorted(rows):
        print(f"  {r[0]:<46} {r[1]:<28} {r[2]:<7}{r[3]:<6}{r[4]:<9}{r[5]}")
