"""Which bd workspace intake files into, and what happens when there is none.

Kraft-ibwj: a daemon's cwd is an accident of how it was launched, so the bd
workspace has to come from the work item's own repo. Kraft-7gy: bd is optional,
so a repo with no `.beads` — or a machine with no `bd` — still files work.

Every test here deletes KRAFT_BD_CWD. tests/conftest.py:48 sets it for the whole
suite, which is why none of this was caught.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support.fake_beads import ON_FAKE_AND_REAL_BD
from support.harness import fake_templates_dir, isolated_bd, make_repo, v1_named_chain

from kraft import db, executor
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"
#: The fake agent lives under tests/support, not fixtures/ (which holds the
#: `claude` PATH shim `just dev` uses). Same file tests/test_executor.py:16 uses.
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE = f"{sys.executable} {_FAKE_AGENT}"


def _quick_task(tmp_path):
    """The shipped gateless `quick-task`, its agent task on the fake agent."""
    return v1_named_chain(tmp_path / "templates", agent_command=_FAKE)


def _row(database, wid, columns="*"):
    return database.read(
        lambda c: c.execute(f"SELECT {columns} FROM work_items WHERE id = ?", (wid,)).fetchone()
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The app with KRAFT_BD_CWD *deleted* — the installed-daemon default, and
    the state tests/conftest.py never lets the rest of the suite reach."""
    monkeypatch.delenv("KRAFT_BD_CWD", raising=False)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    with TestClient(api.app, client=("127.0.0.1", 54321)) as c:
        yield c


@ON_FAKE_AND_REAL_BD
def test_intake_files_the_bead_in_the_work_items_repo(tier, bd, tmp_path, monkeypatch):
    """Kraft-ibwj: with no KRAFT_BD_CWD, the bead goes to the item's repo, not
    to whatever directory the server process happens to be sitting in."""
    monkeypatch.delenv("KRAFT_BD_CWD", raising=False)
    repo = isolated_bd(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="file me where I belong",
                repo=str(repo),
                chain=_quick_task(tmp_path),
            )
            row = _row(database, wid, "bead_id, bead_cwd")
            assert row["bead_id"]
            assert row["bead_id"] in bd.ids(cwd=repo)
            assert row["bead_cwd"] == str(repo)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_kraft_bd_cwd_still_overrides_the_repo(bd, tmp_path, monkeypatch):
    """The repo is the *default*, not the winner. An operator who set
    KRAFT_BD_CWD as the workaround for Kraft-ibwj must not silently start
    filing into per-repo trackers on upgrade."""
    tracker = isolated_bd(tmp_path, name="tracker")
    repo = isolated_bd(tmp_path, name="theproject")
    monkeypatch.setenv("KRAFT_BD_CWD", str(tracker))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="the env still wins",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            row = _row(database, wid, "bead_id, bead_cwd")
            assert row["bead_id"] in bd.ids(cwd=tracker)
            assert row["bead_id"] not in bd.ids(cwd=repo)
            assert row["bead_cwd"] == str(tracker)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_repo_with_no_beads_workspace_still_files_a_work_item(client, tmp_path):
    """The Kraft-ibwj repro, inverted: this must not be a 502. bd's own words
    reach the caller, and the timeline records why there is no bead."""
    repo = make_repo(tmp_path)  # a plain git repo: no .beads
    resp = client.post(
        "/api/work-items", json={"title": "no tracker here", "repo": str(repo), "autostart": False}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "no beads database" in body["bead_warning"]

    item = client.get(f"/api/work-items/{body['id']}").json()
    assert item["bead_id"] is None

    events = client.get(f"/api/work-items/{body['id']}/events").json()
    filed = [e for e in events if e["type"] == "bead_not_filed"]
    assert len(filed) == 1
    assert "no beads database" in filed[0]["payload"]["reason"]
    assert filed[0]["payload"]["cwd"] == str(repo)


@pytest.mark.beads_adapter
def test_no_bd_on_path_still_files_a_work_item(client, tmp_path, monkeypatch):
    """Kraft-7gy: bd is a tracker a newcomer has never heard of. Not having it
    installed is not an intake failure."""
    repo = make_repo(tmp_path)
    # After the fixtures, which need the real bd to build their workspaces.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    resp = client.post(
        "/api/work-items", json={"title": "no bd at all", "repo": str(repo), "autostart": False}
    )
    assert resp.status_code == 201, resp.text
    assert "not installed" in resp.json()["bead_warning"]
    assert client.get(f"/api/work-items/{resp.json()['id']}").json()["bead_id"] is None


def test_a_bead_less_item_completes_without_calling_bd(tmp_path, monkeypatch, caplog):
    """Without the `if row["bead_id"]` guard, `bd close None` raises TypeError
    inside the except and gets logged as a bead close failure that never
    happened. Harmless, and exactly the kind of noise a degrade must not add."""
    monkeypatch.delenv("KRAFT_BD_CWD", raising=False)
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    repo = make_repo(tmp_path)  # no .beads: intake files no bead

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
            )
            assert _row(database, wid, "bead_id")["bead_id"] is None
            with caplog.at_level("WARNING"):
                result = await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=None,
                )
            assert result == "completed"
            assert "bead close failed" not in caplog.text
        finally:
            await database.close()

    asyncio.run(scenario())
