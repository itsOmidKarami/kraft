"""Kraft opens beads it never closes (2026-09-09 design): one test per bead.

Kraft-p8q1: close every bead a work item's description names, not just the
tracking bead. Kraft-dr3n: an item filed while bd was down still gets a bead
by completion. Kraft-t5g: no test may reach the operator's real `~/.beads`.
Kraft-ikze: a recorded decision, not code -- no test here.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from support.harness import fake_registry, make_repo

from kraft import db, executor
from kraft.paths import RunDirs
from kraft.templates import Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def _bd_status(repo, bead_id) -> str:
    out = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)[0]["status"]


def _bd_create(repo, title) -> str:
    out = subprocess.run(
        ["bd", "create", "--json", "--title", title, "--type", "task"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out[out.index("{") :])["id"]


def test_completion_closes_every_sub_bead_the_description_names(tmp_path):
    """Kraft-p8q1: today only the tracking bead ever closes. The sub-beads a
    work item's own description lists as `- Kraft-xxxx — ...` bullets must
    close too, not just drift open on the board forever."""
    tracker = make_repo(tmp_path)
    # `Kraft-` prefix so the ids the extractor regex matches (`Kraft-[a-z0-9]+`)
    # are the same ids this repo's own workspace actually has beads for.
    subprocess.run(
        ["bd", "init", "--prefix", "Kraft"], cwd=tracker, check=True, capture_output=True
    )
    sub_a = _bd_create(tracker, "sub task a")
    sub_b = _bd_create(tracker, "sub task b")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="implements two sub-beads",
                description=f"- {sub_a} — part one\n- {sub_b} — part two",
                repo=str(tracker),
                template=_quick_task(),
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
                registry=fake_registry(sys.executable, _FAKE_AGENT),
            )
            assert result == "completed"
            assert _bd_status(tracker, sub_a) == "closed"
            assert _bd_status(tracker, sub_b) == "closed"
            tracking_row = database.read(
                lambda c: c.execute(
                    "SELECT bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert _bd_status(tracker, tracking_row["bead_id"]) == "closed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_item_filed_while_bd_was_down_still_gets_a_bead_by_completion(tmp_path, monkeypatch):
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
                template=_quick_task(),
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
            subprocess.run(
                ["bd", "init", "--prefix", "LATE"], cwd=repo, check=True, capture_output=True
            )

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=fake_registry(sys.executable, _FAKE_AGENT),
            )
            assert result == "completed"
            row = database.read(
                lambda c: c.execute(
                    "SELECT bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["bead_id"], "no bead was filed at completion"
            assert _bd_status(repo, row["bead_id"]) == "closed"
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
                template=_quick_task(),
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    after = real_beads.exists() and set(real_beads.iterdir())
    assert before == after, "intake touched the operator's real ~/.beads"
