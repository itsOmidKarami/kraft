"""Kraft opens beads it never closes (2026-09-09 design): one test per bead.

Kraft-p8q1: close every bead a work item's description names, not just the
tracking bead. Kraft-dr3n: an item filed while bd was down still gets a bead
by completion. Kraft-t5g: no test may reach the operator's real `~/.beads`.
Kraft-ikze: a recorded decision, not code -- no test here. Kraft-iaou3: a bead
closes only when the item's change carries it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import isolated_bd, make_repo, v1_named_chain, v1_resolved

from kraft import db, events, executor, store
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


async def test_item_filed_while_bd_was_down_still_gets_a_bead_by_completion(
    bd, tmp_path, monkeypatch, database, run_dirs, repo
):
    """Kraft-dr3n: bd being unavailable at intake time is a degrade, not a
    permanent hole -- an item that completes still ends up with a closed
    bead, filed late rather than never."""
    monkeypatch.delenv("KRAFT_BD_CWD", raising=False)

    wid = await executor.intake(
        database,
        run_dirs,
        title="filed while bd was down",
        repo=str(repo),
        chain=_quick_task(tmp_path),
    )
    assert (
        database.read(
            lambda c: c.execute("SELECT bead_id FROM work_items WHERE id = ?", (wid,)).fetchone()
        )["bead_id"]
        is None
    )

    # bd shows up before completion -- give the repo a workspace now.
    bd.init(repo)

    result = await executor.run(
        database,
        run_dirs,
        work_item_id=wid,
    )
    assert result == "completed"
    row = database.read(
        lambda c: c.execute("SELECT bead_id FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    assert row["bead_id"], "no bead was filed at completion"
    assert bd.status(row["bead_id"], cwd=repo) == "closed"


async def test_no_app_fixture_still_avoids_the_operators_real_home(
    tmp_path, monkeypatch, database, run_dirs, repo
):
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

    await executor.intake(
        database,
        run_dirs,
        title="never touches the real home",
        repo=str(repo),
        chain=_quick_task(tmp_path),
    )

    after = real_beads.exists() and set(real_beads.iterdir())
    assert before == after, "intake touched the operator's real ~/.beads"


# -- Kraft-iaou3: a bead closes only when the item's change carries it --------

_IMPLEMENT = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: do it}]
"""


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(worktree, rel, text):
    (worktree / rel).parent.mkdir(parents=True, exist_ok=True)
    (worktree / rel).write_text(text)
    _git(worktree, "add", "-f", rel)
    _git(worktree, "commit", "-qm", f"change {rel}")


async def _nothing(database, wid, worktree):
    pass


async def _only_its_attachment(database, wid, worktree):
    _commit(worktree, ".engineering/spec.md", "# spec\n")
    attached = json.dumps([{"kind": "spec", "path": ".engineering/spec.md"}])
    await database.write(
        lambda c: c.execute("UPDATE work_items SET attachments = ? WHERE id = ?", (attached, wid))
    )


async def _a_code_change(database, wid, worktree):
    _commit(worktree, "calc.py", "def add(a, b):\n    return a + b\n")


async def _someone_elses_change_rebased_in(database, wid, worktree):
    # A rebase mid-walk moves `base_ref` onto the new base; the row the walk
    # read at its start still names the old one.
    _commit(worktree, "calc.py", "def add(a, b):\n    return a + b\n")
    await database.write(lambda c: store.set_base_ref(c, wid, _git(worktree, "rev-parse", "HEAD")))


async def _a_merged_submodule(database, wid, worktree):
    def add(c):
        row_id = store.add_repo(
            c, work_item_id=wid, repo_path=str(worktree / "sub"), role="submodule", merge_rank=1
        )
        store.update_repo_state(c, row_id, merge_state="merged")

    await database.write(add)


@pytest.mark.parametrize(
    ("change", "by_hand", "closes"),
    [
        (_nothing, False, False),
        (_only_its_attachment, False, False),
        (_someone_elses_change_rebased_in, False, False),
        (_a_code_change, False, True),
        (_a_merged_submodule, False, True),
        # Ruling 167: a person completing by hand and asking for the close has
        # said where the work landed; Kraft has nothing of its own to check.
        (_nothing, True, True),
    ],
    ids=[
        "nothing-committed",
        "only-its-attachment",
        "someone-elses-change-rebased-in",
        "a-code-change",
        "a-merged-submodule",
        "by-hand-when-asked",
    ],
)
async def test_a_bead_closes_only_when_the_items_change_carries_it(
    bd, database, run_dirs, repo, tmp_path, change, by_hand, closes
):
    """Kraft-iaou3: a completed chain is not evidence on its own -- a bead
    closed with no code behind it is how the board came to say two P1s were
    done. Kraft closes the item's beads only when its branch changed something
    besides its own attachments, or a member's merge request merged; otherwise
    they stay open and the item says why."""
    tracker = isolated_bd(tmp_path)
    sub = await beads.intake("the stated sub-bead", cwd=str(tracker))
    wid = await executor.intake(
        database,
        run_dirs,
        title="t",
        repo=str(repo),
        chain=v1_resolved(_IMPLEMENT),
        bd_cwd=str(tracker),
        implements_beads=[sub],
    )
    worktree = run_dirs.worktrees / wid
    _git(repo, "worktree", "add", "-q", "-b", f"kraft/{wid}", str(worktree))
    base = _git(worktree, "rev-parse", "HEAD")
    await database.write(lambda c: store.set_base_ref(c, wid, base))
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )

    await change(database, wid, worktree)
    await executor.close_beads(database, row, str(tracker), run_dirs, by_hand=by_hand)

    want = "closed" if closes else "open"
    assert [bd.status(b, cwd=tracker) for b in (row["bead_id"], sub)] == [want, want]
    left = [
        e["payload"]
        for e in database.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == "beads_left_open"
    ]
    assert [p["beads"] for p in left] == ([] if closes else [[row["bead_id"], sub]])
    assert all(p["reason"] for p in left)
