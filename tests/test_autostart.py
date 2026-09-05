"""A work item can be created without starting it (design §6 rule 1)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _poll_for(client, wid, event_type, timeout=30):
    """Wait for an event type to land. The executor runs in a background task, so
    a status read straight after resume races it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matching = [
            e for e in client.get(f"/work-items/{wid}/events").json() if e["type"] == event_type
        ]
        if matching:
            return matching
        time.sleep(0.2)
    raise AssertionError(f"{event_type} never arrived for {wid}")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    with TestClient(api.app) as c:
        yield c


def test_autostart_false_lands_paused_and_never_ran(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items", json={"title": "wait for me", "repo": str(repo), "autostart": False}
    ).json()["id"]

    item = client.get(f"/work-items/{wid}").json()
    assert item["status"] == "paused"
    assert item["current_node_id"] is None
    # never started means no session was ever launched, not "a session was killed"
    assert item["worker_sessions"] == []


def test_autostart_defaults_true_so_the_ui_is_unaffected(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post("/work-items", json={"title": "go now", "repo": str(repo)}).json()["id"]
    assert client.get(f"/work-items/{wid}").json()["status"] == "active"


def test_resuming_a_never_started_item_begins_at_node_zero(client, tmp_path):
    """api.py's `next(..., 0)` default is what makes a NULL current_node_id
    resolve to the first node. Nothing else was written for this case, so if that
    expression is ever refactored, this test is the thing that notices."""
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items", json={"title": "start me", "repo": str(repo), "autostart": False}
    ).json()["id"]
    first_node = client.get(f"/work-items/{wid}").json()["chain_definition"]["nodes"][0]["id"]

    assert client.post(f"/work-items/{wid}/resume", json={}).status_code == 200

    started = _poll_for(client, wid, "node_started")
    assert started[0]["payload"]["node_id"] == first_node, (
        "a never-started item must begin at the first node, not skip it"
    )


def test_pausing_a_never_started_item_is_refused(client, tmp_path):
    """It is already paused; /pause requires an active item."""
    repo = make_repo(tmp_path)
    wid = client.post(
        "/work-items", json={"title": "already waiting", "repo": str(repo), "autostart": False}
    ).json()["id"]
    assert client.post(f"/work-items/{wid}/pause").status_code == 409
