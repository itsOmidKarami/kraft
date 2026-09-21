"""`dev/check_claim_handoff.py` must mean what it says.

The checker exists because one defect recurred three times on one branch, and
two versions of the checker itself could be fooled. Round 2 asked "is every exit
a claim reaches protected", and five of seven synthetic violations passed it at
exit 0 -- including the original defect's own shape, a `KeyError` after the
claim, because an exception exit is not a `return` node and no AST walk can
enumerate it. Round 4 asks the inverse question, "is every claim inside a
bracket", which needs no exit analysis because `try/finally` covers exception
exits by construction.

So the seven shapes that fooled the exit walk are pinned here as the cases the
inverse check must catch, plus two more for the parts of the inverse check that
are not free: the derived vocabulary, and a claim reached through a helper.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "dev" / "check_claim_handoff.py"

_DEFAULT_STORE = """
    def claim_for_run(conn, wid, *, to_status: str = "active"):
        conn.execute("UPDATE work_items SET status = ?, updated_at = ? WHERE id = ?")

    def mark_needs_human(conn, wid, node, reason):
        conn.execute("UPDATE work_items SET status = 'needs_human' WHERE id = ?")

    def active_count(conn):
        return conn.execute("SELECT COUNT(*) FROM work_items WHERE status = 'active'")
"""

#: `store.reject_gate`'s own shape: the status arrives interpolated, so the only
#: `ast.Constant` fragment is `"UPDATE work_items SET status = "`.
_FSTRING_STORE = """
    def reopen(conn, wid, *, reopen: bool = True):
        status = "'active'" if reopen else "status"
        conn.execute(f"UPDATE work_items SET status = {status}, updated_at = ? WHERE id = ?")
"""


def _load(root: Path | None = None):
    spec = importlib.util.spec_from_file_location("_check_claim_handoff", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if root is not None:
        mod.ROOT = root
        mod.STORE = root / "store"
    return mod


def _tree(tmp_path: Path, body: str, *, store: str = _DEFAULT_STORE) -> Path:
    """A throwaway `src/kraft`-shaped tree: one module plus a `store/` the
    checker derives its vocabulary from."""
    root = tmp_path / "kraft"
    (root / "store").mkdir(parents=True)
    (root / "store" / "work_items.py").write_text(textwrap.dedent(store))
    (root / "shape.py").write_text(textwrap.dedent(body))
    return root


def _bad(root: Path) -> list[tuple[str, str, str, bool]]:
    return [r for r in _load(root).audit() if not r[3]]


# --------------------------------------------------------------------------
# The seven shapes that passed the exit walk at exit 0.
#
# Every one of them is one thing: a claim with no bracket around it. That is
# the entire argument for asking the inverse question -- the shapes differ only
# in how the *exit* is written, and the inverse check never looks at the exit.
# --------------------------------------------------------------------------

_UNBRACKETED_SHAPES = {
    # The original defect: a `KeyError` between the claim and the stops. Every
    # *statement* exit carries a real stop, so the exit walk passed it.
    "exception_shaped": """
        async def f(db, wid, nodes, gate):
            await db.write(lambda c: store.claim_for_run(c, wid))
            start = nodes[gate] + 1
            if start is None:
                await db.write(lambda c: store.mark_needs_human(c, wid, None, "no start"))
                return "needs_human"
            await db.write(lambda c: store.mark_needs_human(c, wid, None, "later"))
            return "waiting"
    """,
    # A claim after a bracket that has already closed. The exit walk credited
    # it, because the `with` contributed its context call to the dominator set.
    "claim_after_a_closed_bracket": """
        async def f(db, wid):
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                pass
            await db.write(lambda c: store.claim_for_run(c, wid))
            return "escaped"
    """,
    # `HANDOFF` was a hand-written name set, so any `.run(...)` counted.
    "false_handoff": """
        async def f(db, wid, thing, x):
            await db.write(lambda c: store.claim_for_run(c, wid))
            await thing.run(x)
            return "handed off, allegedly"
    """,
    # `STOP` was matched by name, so a same-named method on anything counted.
    "false_stop": """
        async def f(db, wid, report):
            await db.write(lambda c: store.claim_for_run(c, wid))
            report.mark_needs_human()
            return "stopped, allegedly"
    """,
    # Falling off the end took lexical credit over the whole trailing compound
    # statement, so a stop in one branch of a trailing `if` was a dominator.
    "fall_through_over_a_compound_statement": """
        async def f(db, wid, x):
            await db.write(lambda c: store.claim_for_run(c, wid))
            if x:
                await db.write(lambda c: store.mark_needs_human(c, wid, None, "r"))
    """,
    # These two the exit walk did catch. They must not regress.
    "break_and_continue": """
        async def f(db, rows):
            for row in rows:
                await db.write(lambda c: store.claim_for_run(c, row))
                if a:
                    continue
                if b:
                    break
    """,
    "bare_raise": """
        async def f(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            raise RuntimeError("boom")
    """,
    # Mine: the claim is two frames down, in a helper, and the helper's caller
    # has no bracket. This is `executor.apply_rejection`'s real shape with the
    # bracket taken off its door.
    "claim_through_an_unbracketed_helper": """
        async def apply_it(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            return 3

        async def door(db, wid):
            target = await apply_it(db, wid)
            return target
    """,
    # Mine: the store writes its status through an f-string, which is how
    # `store.reject_gate` stayed outside the derived vocabulary for two rounds.
    "claim_written_by_an_f_string": """
        async def f(db, wid):
            await db.write(lambda c: store.reopen(c, wid))
            return "escaped"
    """,
}


@pytest.mark.parametrize("shape", sorted(_UNBRACKETED_SHAPES))
def test_every_unbracketed_claim_is_caught(tmp_path, shape):
    store = _DEFAULT_STORE
    if shape == "claim_written_by_an_f_string":
        store += _FSTRING_STORE
    root = _tree(tmp_path, _UNBRACKETED_SHAPES[shape], store=store)
    assert _bad(root), f"{shape} claims with no bracket around it and was not reported"
    assert _load(root).main([]) == 1


def test_a_bracketed_claim_passes(tmp_path):
    """The other direction: the check must not simply flag every claim."""
    root = _tree(
        tmp_path,
        """
        async def f(db, wid):
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                await db.write(lambda c: store.claim_for_run(c, wid))
                return await spawn()
        """,
    )
    mod = _load(root)
    assert [(r[1], r[3]) for r in mod.audit()] == [("claim_for_run", True)]
    assert mod.main([]) == 0


def test_a_helper_whose_call_sites_are_all_bracketed_passes(tmp_path):
    """`executor.apply_rejection`'s real shape: the claim is in a helper that
    hands the re-entry index back, so the bracket belongs to its callers. Two
    doors, both bracketed -- and the helper's own line is not a row of its own,
    because reporting it would name the wrong line first."""
    root = _tree(
        tmp_path,
        """
        async def apply_it(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            return 3

        async def human_door(db, wid):
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                target = await apply_it(db, wid)
                return await spawn(target)

        async def agent_door(db, wid):
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                return await apply_it(db, wid)
        """,
    )
    mod = _load(root)
    assert mod.main([]) == 0
    assert sorted(r[2] for r in mod.audit()) == ["agent_door", "human_door"]


def test_an_uncalled_function_is_not_promoted_into_invisibility(tmp_path):
    """The one way promotion could hurt: a route handler nobody calls, holding
    an unbracketed claim, promoted into the vocabulary and then found to have no
    call sites -- its claim would vanish, which is the false negative this whole
    file exists to refuse."""
    root = _tree(
        tmp_path,
        """
        async def route(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            return "escaped"
        """,
    )
    assert [r[2] for r in _bad(root)] == ["route"]


def test_claims_are_derived_from_store_not_hand_listed(tmp_path):
    """The omission that hid a live instance twice. `store.approve_gate` is an
    unconditional `UPDATE work_items SET status = 'active'` and round 1's
    hand-written set did not contain it. `store.reject_gate` writes its status
    through an f-string and round 2's `ast.Constant`-only reader could not see
    it. A `SELECT ... WHERE status = 'active'` must still *not* match, or
    `active_count` becomes a claim, and an `INSERT` hands off to its caller."""
    root = _tree(
        tmp_path,
        "def f():\n    pass\n",
        store="""
            def approve_gate(conn, wid, gate):
                conn.execute("UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?")

            def reject_gate(conn, wid, *, reopen: bool):
                status = "'active'" if reopen else "status"
                conn.execute(f"UPDATE work_items SET status = {status} WHERE id = ?")

            def active_count(conn):
                return conn.execute("SELECT COUNT(*) FROM work_items WHERE status = 'active'")

            def create_work_item(conn, *, status: str = "active"):
                conn.execute("INSERT INTO work_items (id, status) VALUES (?, ?)")
        """,
    )
    assert _load(root).claims() == {"approve_gate", "reject_gate"}


def test_a_same_named_call_off_another_object_is_not_a_store_claim(tmp_path):
    """`mcp.py` and `cli/item.py` both call an `approve_gate` that is the HTTP
    client's, not the store's. A name-only match reports two rows a human has to
    re-adjudicate on every run."""
    root = _tree(
        tmp_path,
        """
        async def f(client, wid, gate):
            await client.approve_gate(wid, gate)
            return "not a claim"
        """,
        store="""
            def approve_gate(conn, wid, gate):
                conn.execute("UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?")
        """,
    )
    assert _load(root).audit() == []


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


def test_the_real_tree_is_clean(capsys):
    """And the property the whole thing exists for, over `src/kraft` itself."""
    assert _load().main([]) == 0
    assert "every one of them is inside" in capsys.readouterr().out
