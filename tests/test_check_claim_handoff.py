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

import ast
import importlib.util
import pathlib
import shutil
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


#: Re-review 4's V3: a claim that does not assign `status` first.
_COLUMNS_SWAPPED_STORE = """
    def reopen(conn, wid):
        conn.execute("UPDATE work_items SET updated_at = ?, status = 'active' WHERE id = ?")
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


def _bad(root: Path) -> list[tuple[str, str, str, bool, bool]]:
    """The violations: neither bracketed nor delegated to a checked caller."""
    return [r for r in _load(root).audit() if not (r[3] or r[4])]


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
    # Re-review 4's V3: the same claim with the columns swapped. `_CLAIM_SQL`
    # wanted `status` to be the *first* assigned column, and every writer in
    # `store/` happens to put it first -- so this was a one-word spelling change
    # away in any future `store/` diff, and invisible.
    "claim_with_status_not_the_first_column": """
        async def f(db, wid):
            await db.write(lambda c: store.reopen(c, wid))
            return "escaped"
    """,
    # Re-review 4's V4: the claim reached by a direct import rather than off
    # `store`. The `base == "store"` rule is there to exclude the HTTP client's
    # `approve_gate`, and it also made an alias invisible.
    "claim_called_through_a_direct_import": """
        from kraft.store.work_items import claim_for_run

        async def f(db, wid):
            await db.write(lambda c: claim_for_run(c, wid))
            return "escaped"
    """,
}


@pytest.mark.parametrize("shape", sorted(_UNBRACKETED_SHAPES))
def test_every_unbracketed_claim_is_caught(tmp_path, shape):
    store = _DEFAULT_STORE
    if shape == "claim_written_by_an_f_string":
        store += _FSTRING_STORE
    if shape == "claim_with_status_not_the_first_column":
        store += _COLUMNS_SWAPPED_STORE
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
    doors, both bracketed -- and the helper's own line is still a *row*, marked
    delegated rather than deleted. Round 4 deleted it, which is how a colliding
    name came to delete a real door's claim as well."""
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
    rows = {r[2]: (r[3], r[4]) for r in mod.audit()}
    assert rows == {
        "agent_door": (True, False),
        "human_door": (True, False),
        "apply_it": (False, True),  # unbracketed, but delegated -- and still shown
    }


def test_a_door_named_after_the_store_function_it_wraps_is_not_promoted(tmp_path):
    """The reachable promotion hole, and round 4's high finding.

    Kraft names its route handlers after the store function they wrap:
    `resume_work_item`, `retry_work_item`, `approve_gate` and `reject_gate` are
    each both a `store/` function and a door. Round 4 promoted the *owner* of an
    unbracketed claim on its bare name and then deleted its row, so taking the
    bracket off `resume_work_item` promoted the door, deleted its claim, found no
    other unbracketed call of that name and **exited 0 with nothing flagged** --
    on one of the five sites the original defect occurred at.

    Two guards, both pinned here. A `store/` name is never promoted, and no row is
    ever deleted.
    """
    root = _tree(
        tmp_path,
        """
        async def resume_work_item(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            await db.write(lambda c: store.resume_work_item(c, wid, None))
            return "escaped"

        async def other(db, wid):
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                return await resume_work_item(db, wid)
        """,
        store=_DEFAULT_STORE
        + """
    def resume_work_item(conn, wid, steer):
        conn.execute("INSERT INTO events (work_item_id) VALUES (?)")
""",
    )
    mod = _load(root)
    assert mod.main([]) == 1, "the door's own claim must not be promoted away"
    assert [r[2] for r in _bad(root)] == ["resume_work_item"]


def test_an_uncalled_function_is_not_promoted_into_invisibility(tmp_path):
    """The same guard from the other side: a handler nobody calls from inside a
    bracket is nobody's delegate, so its claim is its own."""
    root = _tree(
        tmp_path,
        """
        async def route(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            return "escaped"
        """,
    )
    assert [r[2] for r in _bad(root)] == ["route"]


def test_a_delegating_helper_flags_only_its_unbracketed_door(tmp_path):
    """Re-review 4's V5. One helper, two doors, one of them bracketed: the helper
    delegates (so its own row stands aside) and the *unbracketed door* is the
    violation. Getting this wrong in either direction is a whole class -- flag the
    helper and the clean tree never goes green, flag neither and a real door
    hides."""
    root = _tree(
        tmp_path,
        """
        async def apply_it(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            return 3

        async def good_door(db, wid):
            async with stops.claimed_or_stopped(db, wid, None, reason="r"):
                return await apply_it(db, wid)

        async def bad_door(db, wid):
            return await apply_it(db, wid)
        """,
    )
    assert [r[2] for r in _bad(root)] == ["bad_door"]


def test_a_claim_in_a_nested_def_of_an_unbracketed_function_is_caught(tmp_path):
    """Re-review 4's V6. `ci_wait` writes its claim inside a `db.write`
    transaction body, so the bracket test crosses function boundaries on purpose
    -- which must not turn into crediting a nested def with a bracket that is not
    there."""
    root = _tree(
        tmp_path,
        """
        async def f(db, wid):
            def txn(c):
                store.claim_for_run(c, wid)

            await db.write(txn)
            return "escaped"
        """,
    )
    assert [r[2] for r in _bad(root)] == ["txn"]


def test_a_helper_called_only_from_unbracketed_sites_keeps_its_own_row(tmp_path):
    """`rate_limit_retry._retry_one`'s shape. Round 4 promoted any owner that was
    called anywhere, so removing a poller's bracket promoted `_retry_one`, then
    its caller `poller`, then `lifespan` -- a cascade of 7 to 100+ bogus rows with
    the real line absent. Promotion now needs the codebase to *say* the bracket
    belongs to the callers, by having at least one bracketed call site."""
    root = _tree(
        tmp_path,
        """
        async def _retry_one(db, wid):
            await db.write(lambda c: store.claim_for_run(c, wid))
            return False

        async def poller(db, wid):
            return await _retry_one(db, wid)
        """,
    )
    assert [r[2] for r in _bad(root)] == ["_retry_one"], "no cascade, and the real site named"


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


# --------------------------------------------------------------------------
# The check the other way round, and the one that decides whether this file is
# worth anything: the gate's job is not to pass on good code, it is to FAIL when
# somebody takes a bracket off. Round 4's version was silent at 2 of these 9 and
# named the wrong file at 2 more, while passing every test in this file.
# --------------------------------------------------------------------------


def _brackets() -> list[tuple[pathlib.Path, int, int, int]]:
    """Every `stops.claimed_or_stopped` block in `src/kraft`, as line spans.

    Enumerated from the tree rather than listed, for the reason the claim
    vocabulary is: a hand-written list of the sites to test is the same failure
    one level up, and a tenth bracket added tomorrow has to be covered without
    anyone remembering to add it here.
    """
    src = _SCRIPT.parent.parent / "src" / "kraft"
    out = []
    for path in sorted(src.rglob("*.py")):
        if "_bundled" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.With | ast.AsyncWith) and any(
                _SCRIPT_BRACKET in ast.dump(i.context_expr) for i in node.items
            ):
                out.append((path, node.lineno, node.body[0].lineno, node.body[-1].end_lineno))
    return out


_SCRIPT_BRACKET = "claimed_or_stopped"


def _without_bracket(text: str, header: int, body_start: int, body_end: int) -> str:
    """The same source with one `async with` header gone and its body dedented."""
    lines = text.split("\n")
    body = [ln[4:] if ln.startswith("    ") else ln for ln in lines[body_start - 1 : body_end]]
    return "\n".join(lines[: header - 1] + body + lines[body_end:])


def test_there_are_nine_brackets_to_check():
    """So a bracket deleted outright cannot quietly shrink the sweep below."""
    assert len(_brackets()) == 9


@pytest.mark.parametrize("index", range(9))
def test_removing_any_real_bracket_is_caught_and_names_its_file(tmp_path, index):
    """Take bracket `index` off `src/kraft` and the checker must exit 1 with its
    violations in *that file and no other*.

    Both halves matter. Round 4 exited 0 for `resume_work_item` and
    `retry_work_item`; for the two gate doors it exited 1 while naming
    `cli/item.py` and omitting the real line; and for three more it cascaded into
    7 to 100+ rows reaching `__main__.py`, with the real line absent. An alarm
    that fires pointing somewhere else is not much better than one that does not
    fire.
    """
    path, header, body_start, body_end = _brackets()[index]
    src = _SCRIPT.parent.parent / "src" / "kraft"
    root = tmp_path / "kraft"
    shutil.copytree(src, root)
    target = root / path.relative_to(src)
    target.write_text(_without_bracket(path.read_text(), header, body_start, body_end))

    mod = _load(root)
    assert mod.main([]) == 1, f"removing the bracket at {path.name}:{header} was not caught"
    bad = [r for r in mod.audit() if not (r[3] or r[4])]
    assert {r[0].rsplit(":", 1)[0].split("kraft/", 1)[1] for r in bad} == {
        str(path.relative_to(src))
    }, f"flagged the wrong file for {path.name}:{header}: {[r[0] for r in bad]}"
    assert len(bad) <= 2, f"cascade: {len(bad)} rows for {path.name}:{header}"
