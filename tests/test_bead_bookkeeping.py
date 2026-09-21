"""Kraft opens beads it never closes (2026-09-09 design): one test per bead.

Kraft-p8q1: close every bead a work item's description names, not just the
tracking bead. Kraft-dr3n: an item filed while bd was down still gets a bead
by completion. Kraft-t5g: no test may reach the operator's real `~/.beads`.
Kraft-ikze: a recorded decision, not code -- no test here.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from support.harness import make_repo, v1_named_chain

from kraft import db, executor
from kraft.adapters import beads
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE = f"{sys.executable} {_FAKE_AGENT}"


def _quick_task(tmp_path):
    """The shipped gateless `quick-task`, its agent task on the fake agent."""
    return v1_named_chain(tmp_path / "templates", agent_command=_FAKE)


def test_completion_closes_every_sub_bead_the_item_states(bd, tmp_path):
    """Kraft-p8q1: the sub-beads a work item states via `implements_beads` close
    with it, not just the tracking bead. Passed explicitly: the description is
    no longer parsed for ids."""
    tracker = bd.init(make_repo(tmp_path))

    async def scenario():
        sub_a = await beads.intake("sub task a", cwd=str(tracker))
        sub_b = await beads.intake("sub task b", cwd=str(tracker))
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="implements two sub-beads",
                implements_beads=[sub_a, sub_b],
                repo=str(tracker),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert json.loads(row["implements_beads"]) == [sub_a, sub_b]

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
            )
            assert result == "completed"
            assert bd.status(sub_a, cwd=tracker) == "closed"
            assert bd.status(sub_b, cwd=tracker) == "closed"
            tracking_row = database.read(
                lambda c: c.execute(
                    "SELECT bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert bd.status(tracking_row["bead_id"], cwd=tracker) == "closed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_item_filed_while_bd_was_down_still_gets_a_bead_by_completion(bd, tmp_path, monkeypatch):
    """Kraft-dr3n: bd being unavailable at intake time is a degrade, not a
    permanent hole -- an item that completes still ends up with a closed
    bead, filed late rather than never."""
    repo = make_repo(tmp_path)  # no .beads at intake time
    monkeypatch.delenv("KRAFT_BD_CWD", raising=False)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="filed while bd was down",
                repo=str(repo),
                chain=_quick_task(tmp_path),
            )
            assert (
                database.read(
                    lambda c: c.execute(
                        "SELECT bead_id FROM work_items WHERE id = ?", (wid,)
                    ).fetchone()
                )["bead_id"]
                is None
            )

            # bd shows up before completion -- give the repo a workspace now.
            bd.init(repo)

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
            )
            assert result == "completed"
            row = database.read(
                lambda c: c.execute(
                    "SELECT bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["bead_id"], "no bead was filed at completion"
            assert bd.status(row["bead_id"], cwd=repo) == "closed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_no_app_fixture_still_avoids_the_operators_real_home(tmp_path, monkeypatch):
    """Kraft-t5g: bd's fallback when it finds no `.beads/` walking up from cwd
    is a hardcoded `~/.beads`. A test with no `app` fixture and no explicit
    `bd_cwd` must still land in a fake HOME, not the operator's real one."""
    import os
    import pwd

    monkeypatch.delenv("KRAFT_BD_CWD", raising=False)
    # The autouse fixture already swapped HOME to somewhere under this test's
    # own tmp_path -- proof it ran, not a re-check of its own logic.
    assert Path(os.environ["HOME"]).is_relative_to(tmp_path)

    # `pwd` reads the OS-level home directly, ignoring $HOME -- the one way to
    # name the operator's real home while HOME itself is monkeypatched.
    real_beads = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".beads"
    before = real_beads.exists() and set(real_beads.iterdir())

    repo = make_repo(tmp_path)  # no .beads: intake walks up, would hit ~/.beads

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await executor.intake(
                database,
                rd,
                title="never touches the real home",
                repo=str(repo),
                chain=_quick_task(tmp_path),
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    after = real_beads.exists() and set(real_beads.iterdir())
    assert before == after, "intake touched the operator's real ~/.beads"
