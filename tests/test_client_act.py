"""Driving a work item from a session: gates, pause, resume."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo
from test_client_read import run_with_app

from kraft import client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    monkeypatch.delenv("KRAFT_WORK_ITEM_ID", raising=False)
    import kraft.api as api

    monkeypatch.setattr(
        client,
        "http",
        lambda: httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://kraft"
        ),
    )
    return api


def test_resume_starts_a_paused_item(wired, tmp_path):
    """A created-paused item is the phase 2 output; resume is how it begins."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("drive me", repo=str(repo))
        resumed = await client.resume(work_item_id=created["id"])
        return created, resumed

    created, resumed = run_with_app(wired, scenario)
    assert resumed["id"] == created["id"]


def test_pause_refuses_an_item_that_is_not_running(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("already paused", repo=str(repo))
        return await client.pause(work_item_id=created["id"])

    with pytest.raises(ValueError, match="409"):
        run_with_app(wired, scenario)


def test_approving_a_gate_that_is_not_pending_is_refused(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("no gate yet", repo=str(repo))
        return await client.approve_gate("spec_approval", work_item_id=created["id"])

    with pytest.raises(ValueError, match="409"):
        run_with_app(wired, scenario)


def test_an_unknown_gate_name_is_refused(wired, tmp_path):
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("bad gate", repo=str(repo))
        return await client.approve_gate("not_a_gate", work_item_id=created["id"])

    with pytest.raises(ValueError, match="404"):
        run_with_app(wired, scenario)


def test_rejecting_without_a_note_is_refused_before_the_request(wired, tmp_path):
    """A rejection with no reason strands whoever picks the work up next."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("no note", repo=str(repo))
        return await client.reject_gate("   ", "spec_approval", work_item_id=created["id"])

    with pytest.raises(ValueError, match="note"):
        run_with_app(wired, scenario)


def test_approve_gate_defaults_to_the_pending_gate(wired, tmp_path):
    """Naming the gate is the caller repeating what the item already knows."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("default gate", repo=str(repo))
        # nothing is pending on a never-started item, so this reports that
        return await client.approve_gate(work_item_id=created["id"])

    with pytest.raises(ValueError, match="no gate is pending"):
        run_with_app(wired, scenario)


@pytest.mark.parametrize(
    "call",
    [
        lambda wid: client.approve_gate("spec_approval", work_item_id=wid),
        lambda wid: client.reject_gate("no good", "spec_approval", work_item_id=wid),
        lambda wid: client.pause(work_item_id=wid),
        lambda wid: client.resume(work_item_id=wid),
    ],
    ids=["approve", "reject", "pause", "resume"],
)
def test_every_act_function_refuses_a_worker_acting_on_itself(wired, tmp_path, monkeypatch, call):
    """The guard has to be wired into all four, not just the one that was
    written first — a single unguarded act function is the whole hole."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("mine", repo=str(repo))
        monkeypatch.setenv("KRAFT_WORK_ITEM_ID", created["id"])
        return await call(created["id"])

    with pytest.raises(PermissionError, match="its own work item"):
        run_with_app(wired, scenario)


def test_retry_on_an_item_that_is_not_stopped_is_a_readable_409(wired, tmp_path):
    """A 409 proves the route resolved: a wrong URL would be a 404. `/retry` is
    the only door back onto a `needs_human` stop — resume wants `paused`, pause
    wants `running`, approve/reject want a pending gate (Kraft-5lpl)."""
    repo = make_repo(tmp_path)

    async def scenario():
        created = await client.create_work_item("not stopped", repo=str(repo))
        return await client.retry(work_item_id=created["id"])

    with pytest.raises(ValueError, match="409"):
        run_with_app(wired, scenario)
