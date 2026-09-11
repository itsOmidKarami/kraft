"""POST /work-items/{wid}/skip — advance past a node or gate without running it."""

from __future__ import annotations

import time
from pathlib import Path

from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch, *, peer=("127.0.0.1", 54321)):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    from fastapi.testclient import TestClient

    import kraft.api as api

    return TestClient(api.app, client=peer)


def _poll_events(client, wid, want, timeout=30):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        seen = client.get(f"/api/work-items/{wid}/events").json()
        if any(e["type"] == want for e in seen):
            return seen
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen; got {[e['type'] for e in seen]}")


def _wait_for_status(client, wid, status, timeout=30):
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/work-items/{wid}").json()
        if body["status"] == status:
            return body
        time.sleep(0.15)
    raise AssertionError(f"status never became {status!r}; last body={body}")


def test_skip_is_refused_on_an_item_that_has_not_started(tmp_path, monkeypatch):
    """`autostart: false` is api.py's own "not started" case (api.py:693-696):
    `current_node_id` stays NULL until `/resume`. `chain_template: quick-task`'s
    fake agent otherwise finishes so fast that a plain create would already
    have a `current_node_id` by the time this second request lands."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={
                "title": "not started",
                "repo": str(repo),
                "chain_template": "quick-task",
                "autostart": False,
            },
        ).json()["id"]
        r = client.post(f"/api/work-items/{wid}/skip", json={})
        assert r.status_code == 409
        assert client.post("/api/work-items/nope/skip", json={}).status_code == 404


def test_skip_advances_past_a_stopped_task_node_without_rerunning_it(tmp_path, monkeypatch):
    """quick-task: env_setup (builtin) -> implementation (agent) -> verify
    (subprocess). KRAFT_FAIL fails the agent-kind node, `implementation`,
    leaving `verify` unrun. Skip must move straight to `verify` — proof it did
    not retry `implementation` is a second `node_started` for `implementation`
    never showing up."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "KRAFT_FAIL once", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]
        item = _wait_for_status(client, wid, "needs_human")
        assert item["current_node_id"] == "implementation"

        r = client.post(f"/api/work-items/{wid}/skip", json={"note": "known flake"})
        assert r.status_code == 200, r.text

        evts = _poll_events(client, wid, "node_started")
        skipped = [e for e in evts if e["type"] == "node_skipped"]
        assert skipped == [
            {
                **skipped[0],
                "payload": {"node_id": "implementation", "gate": None, "note": "known flake"},
            }
        ]
        started = [e["payload"]["node_id"] for e in evts if e["type"] == "node_started"]
        assert started.count("implementation") == 1  # only the original attempt
        assert "verify" in started


def test_skip_bypasses_a_pending_gate_without_approving_it(tmp_path, monkeypatch):
    """default template's first gate is spec_approval, after the `spec` node.
    Skip must reach `plan` without a `gate_approved` event — approval means
    the artifact was ingested, and skip never ingests one."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "t", "repo": str(repo), "chain_template": "default"},
        ).json()["id"]
        _poll_events(client, wid, "gate_requested")
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["pending_gate"] == "spec_approval"

        r = client.post(f"/api/work-items/{wid}/skip", json={})
        assert r.status_code == 200, r.text

        evts = _poll_events(client, wid, "node_started")
        skipped = [e for e in evts if e["type"] == "node_skipped"]
        assert skipped[0]["payload"] == {"node_id": "spec", "gate": "spec_approval", "note": None}
        assert not any(e["type"] == "gate_approved" for e in evts)
        assert any(e["type"] == "node_started" and e["payload"]["node_id"] == "plan" for e in evts)


def test_skip_while_active_kills_the_running_session_and_still_advances(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "5")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "KRAFT_SLOW go", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]

        deadline = time.monotonic() + 20
        sessions = []
        while time.monotonic() < deadline:
            item = client.get(f"/api/work-items/{wid}").json()
            sessions = item.get("worker_sessions", [])
            if item["status"] == "active" and any(s["status"] == "running" for s in sessions):
                break
            time.sleep(0.1)
        assert item["status"] == "active", "the slow node never started running"
        running_id = next(s["id"] for s in sessions if s["status"] == "running")

        r = client.post(f"/api/work-items/{wid}/skip", json={})
        assert r.status_code == 200, r.text

        killed = client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
        assert next(s for s in killed if s["id"] == running_id)["status"] == "paused"
        evts = _poll_events(client, wid, "node_skipped")
        assert any(e["type"] == "node_skipped" for e in evts)
