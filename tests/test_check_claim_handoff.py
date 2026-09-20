"""`dev/check_claim_handoff.py` must mean what it says.

The checker exists because one defect recurred three times on one branch, and
its own first version could be fooled by three synthetic shapes -- a
hand-maintained `CLAIMS` set that had already drifted, function-wide bracket
credit, and a lexical over-credit inside `with` blocks. A derivation tool that
can be fooled is worse than none: it licenses exactly the belief it was built to
test. So each blind spot the re-review demonstrated gets a test here.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_claim_handoff.py"


def _load(root: Path | None = None):
    spec = importlib.util.spec_from_file_location("_check_claim_handoff", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if root is not None:
        mod.ROOT = root
        mod.STORE = root / "store"
    return mod


def _tree(tmp_path: Path, body: str, *, store: str = "") -> Path:
    """A throwaway `src/kraft`-shaped tree: one module plus a `store/` the
    checker derives its vocabulary from."""
    root = tmp_path / "kraft"
    (root / "store").mkdir(parents=True)
    (root / "store" / "work_items.py").write_text(
        textwrap.dedent(store)
        or textwrap.dedent("""
            def claim_for_run(conn, wid, *, to_status: str = "active"):
                conn.execute("UPDATE work_items SET status = ?, updated_at = ? WHERE id = ?")

            def mark_needs_human(conn, wid, node, reason):
                conn.execute("UPDATE work_items SET status = 'needs_human' WHERE id = ?")

            def active_count(conn):
                return conn.execute("SELECT COUNT(*) FROM work_items WHERE status = 'active'")
        """)
    )
    (root / "shape.py").write_text(textwrap.dedent(body))
    return root


def _bad(mod):
    return [r for r in mod.audit() if not (r[3] or r[4] or r[5])]


def test_claims_are_derived_from_store_not_hand_listed(tmp_path):
    """The omission that hid a live instance: `store.approve_gate` is an
    unconditional `UPDATE work_items SET status = 'active'` and the first
    version's hand-written set did not contain it, so the whole gate-approval
    family was invisible. A `SELECT ... WHERE status = 'active'` must *not*
    match, or `active_count` becomes a claim."""
    root = _tree(
        tmp_path,
        "def f():\n    pass\n",
        store="""
            def approve_gate(conn, wid, gate):
                conn.execute("UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?")

            def active_count(conn):
                return conn.execute("SELECT COUNT(*) FROM work_items WHERE status = 'active'")

            def create_work_item(conn, *, status: str = "active"):
                conn.execute("INSERT INTO work_items (id, status) VALUES (?, ?)")
        """,
    )
    mod = _load(root)
    assert mod.claims() == {"approve_gate"}, (
        "an UPDATE to 'active' is a claim; a SELECT is not, and an INSERT hands off to its caller"
    )


def test_a_claim_outside_the_bracket_is_not_credited_by_it(tmp_path):
    """Function-wide bracket credit passed a claim made *outside* the bracket --
    the same false positive as a lexical stop, one level up."""
    root = _tree(
        tmp_path,
        """
        async def f(db, wid):
            await db.write(lambda c: claim_for_run(c, wid))
            if something:
                return "escaped"
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                return await spawn()
        """,
    )
    assert [r[2] for r in _bad(_load(root))] == ["return"]


def test_a_stop_inside_a_with_block_branch_is_not_credited(tmp_path):
    """`must_run`'s `with` arm walked the whole block including `except`
    handlers, re-introducing the lexical over-credit the file disclaims. A
    handler need not run."""
    root = _tree(
        tmp_path,
        """
        async def f(db, wid):
            await db.write(lambda c: claim_for_run(c, wid))
            with open("x"):
                try:
                    pass
                except RuntimeError:
                    await db.write(lambda c: mark_needs_human(c, wid, None, "r"))
            return "escaped"
        """,
    )
    assert len(_bad(_load(root))) == 1, "a stop in an except handler is not a dominating stop"


def test_break_continue_and_falling_off_the_end_are_exits(tmp_path):
    """A function claiming inside a loop with a `continue` above it and a
    `break` after it used to yield zero rows -- the exit vocabulary was
    `return`/`raise` only, and the filter was a line-number comparison."""
    root = _tree(
        tmp_path,
        """
        async def f(db, rows):
            for row in rows:
                await db.write(lambda c: claim_for_run(c, row))
                if a:
                    continue
                if b:
                    break
        """,
    )
    kinds = sorted(r[2] for r in _bad(_load(root)))
    # Not the fall-off-the-end exit as well: a claim *inside* the loop body does
    # not dominate the end of the function, because the loop may run zero times.
    # That is dominance working, not a gap.
    assert kinds == ["break", "continue"], kinds


def test_a_function_whose_body_cannot_fall_through_reports_no_phantom_exit(tmp_path):
    """The other half: a body ending in the bracket's own `async with`, whose
    block ends in a `return`, cannot fall off the end. Reporting it did, and
    then crediting it over the whole compound statement, is the lexical
    over-credit again."""
    root = _tree(
        tmp_path,
        """
        async def f(db, wid):
            await db.write(lambda c: claim_for_run(c, wid))
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                return await spawn()
        """,
    )
    mod = _load(root)
    assert _bad(mod) == []
    assert [r[2] for r in mod.audit()] == ["return"], "no phantom fall-through exit"


def test_the_checker_refuses_to_pass_from_the_wrong_directory(tmp_path):
    """A cwd-relative root printed `0 exits across 0 files` and exited 0 from
    anywhere else -- the one failure mode a CI guard cannot afford. Two halves:
    the default root is anchored to the script rather than to `os.getcwd()`, and
    a root that is not a checkout raises instead of passing."""
    default = _load()
    assert default.ROOT.is_absolute() and default.ROOT.is_dir()
    assert default.ROOT == _SCRIPT.parent.parent / "src" / "kraft"

    mod = _load(tmp_path / "not-a-checkout")
    with pytest.raises(SystemExit, match="is not a directory"):
        mod.claims()


def test_it_exits_non_zero_on_an_unprotected_exit(tmp_path, capsys):
    """`bad` was printed and never raised, so it could not gate anything."""
    root = _tree(
        tmp_path,
        """
        async def f(db, wid):
            await db.write(lambda c: claim_for_run(c, wid))
            return "escaped"
        """,
    )
    mod = _load(root)
    assert mod.main([]) == 1
    assert "no dominating stop" in capsys.readouterr().out


def test_the_real_tree_is_clean(capsys):
    """And the property the whole thing exists for, over `src/kraft` itself."""
    assert _load().main([]) == 0
    assert "ok: every one of them" in capsys.readouterr().out
