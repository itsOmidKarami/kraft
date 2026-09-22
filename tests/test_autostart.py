"""A work item can be created without starting it (design §6 rule 1)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from support.harness import make_repo

#: No default repo entry: an autostarted item stops where an unconfigured repo stops.
pytestmark = pytest.mark.api_client(default_setup=False)


_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _poll_for(client, wid, event_type, timeout=30):
    """Wait for an event type to land. The executor runs in a background task, so
    a status read straight after resume races it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matching = [
            e for e in client.get(f"/api/work-items/{wid}/events").json() if e["type"] == event_type
        ]
        if matching:
            return matching
        time.sleep(0.2)
    raise AssertionError(f"{event_type} never arrived for {wid}")


def test_autostart_false_lands_paused_and_never_ran(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post(
        "/api/work-items", json={"title": "wait for me", "repo": str(repo), "autostart": False}
    ).json()["id"]

    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "paused"
    assert item["current_node_id"] is None
    # never started means no session was ever launched, not "a session was killed"
    assert item["worker_sessions"] == []


def test_autostart_defaults_true_so_the_ui_is_unaffected(client, tmp_path):
    repo = make_repo(tmp_path)
    wid = client.post("/api/work-items", json={"title": "go now", "repo": str(repo)}).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "active"


def test_resuming_a_never_started_item_begins_at_node_zero(client, tmp_path):
    """`kraft.api.routes.lifecycle.resume_work_item`'s `next(..., 0)` default is
    what makes a NULL current_node_id resolve to the first node. Nothing else
    was written for this case, so if that expression is ever refactored, this
    test is the thing that notices."""
    repo = make_repo(tmp_path)
    wid = client.post(
        "/api/work-items", json={"title": "start me", "repo": str(repo), "autostart": False}
    ).json()["id"]
    first_node = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"][0]["id"]

    assert client.post(f"/api/work-items/{wid}/resume", json={}).status_code == 200

    started = _poll_for(client, wid, "node_started")
    assert started[0]["payload"]["node_id"] == first_node, (
        "a never-started item must begin at the first node, not skip it"
    )


def test_pausing_a_never_started_item_is_refused(client, tmp_path):
    """It is already paused; /pause requires an active item."""
    repo = make_repo(tmp_path)
    wid = client.post(
        "/api/work-items", json={"title": "already waiting", "repo": str(repo), "autostart": False}
    ).json()["id"]
    assert client.post(f"/api/work-items/{wid}/pause").status_code == 409


@pytest.mark.parametrize(
    "caller",
    [{"X-Kraft-Session-Id": "s1"}, {"X-Kraft-Client": "mcp"}],
    ids=["worker", "mcp-assistant"],
)
def test_an_agent_files_work_paused_and_cannot_autostart_it(client, tmp_path, caller):
    """Kraft-s7c04.31: `autostart` is a human's choice. A Kraft session (its
    `X-Kraft-Session-Id`) or an MCP client asking for it is refused before
    anything is filed; the same caller filing paused is still let through."""
    repo = make_repo(tmp_path)
    body = {"title": "t", "repo": str(repo)}
    refused = client.post("/api/work-items", json={**body, "autostart": True}, headers=caller)
    assert refused.status_code == 403, refused.text
    assert "paused" in refused.json()["detail"]
    assert client.get("/api/work-items").json()["items"] == []

    filed = client.post("/api/work-items", json={**body, "autostart": False}, headers=caller)
    assert filed.status_code == 201 and filed.json()["status"] == "paused"
