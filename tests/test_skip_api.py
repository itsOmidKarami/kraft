"""POST /work-items/{wid}/skip — advance past a node or gate without running it."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
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
    """`autostart: false` is `create_work_item`'s own "not started" case
    (kraft.api.routes.work_items):
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
            json={
                "title": "KRAFT_FAIL once",
                "repo": str(repo),
                "chain_template": "quick-task",
                # Kraft-lpdd: this test is about skip, not the unrelated
                # auto-escalate trigger racing it onto the same needs_human
                # stop `_wait_for_status` below is waiting on.
                "node_overrides": {"implementation": {"auto_escalate_stuck": False}},
            },
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


def test_two_concurrent_skips_produce_one_advance_and_one_409(tmp_path, monkeypatch):
    """Kraft-qx1q / one-walk-per-item: two callers racing `/skip` on the same
    item must produce exactly one advance and one 409."""
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
        app = client.app

        async def scenario():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://kraft") as ac:
                return await asyncio.gather(
                    ac.post(f"/api/work-items/{wid}/skip", json={}),
                    ac.post(f"/api/work-items/{wid}/skip", json={}),
                )

        a, b = client.portal.call(scenario)
        assert sorted([a.status_code, b.status_code]) == [200, 409]
        evts = _poll_events(client, wid, "node_skipped")
        assert sum(e["type"] == "node_skipped" for e in evts) == 1


def test_skip_refuses_and_writes_nothing_when_a_walk_is_still_live_at_a_stop(tmp_path, monkeypatch):
    """`needs_human` (a pending gate under auto_escalate review, or the brief
    window while the walk that just called request_gate/mark_needs_human is
    still unwinding) can still have a live walk task behind it. `/skip` must
    refuse before it claims and writes `skip_node` -- not claim to `active`,
    record the node skipped, and only then discover the live walk (leaving
    the item `active` with the node skipped and no walk behind it)."""
    from kraft.api import deps

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

        async def _never_returning():
            await asyncio.Event().wait()

        async def inject():
            deps.spawn(client.app, wid, _never_returning())

        client.portal.call(inject)

        r = client.post(f"/api/work-items/{wid}/skip", json={})

        assert r.status_code == 409, r.text
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["pending_gate"] == "spec_approval"
        evts = client.get(f"/api/work-items/{wid}/events").json()
        assert not any(e["type"] == "node_skipped" for e in evts)

        async def cleanup():
            task = client.app.state.tasks.pop(wid)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        client.portal.call(cleanup)


def test_skip_cancels_the_live_walk_before_it_writes(tmp_path, monkeypatch):
    """The old walk has to be dead *before* `/skip` claims the item and
    records the node skipped. Cancelling afterwards left every await in
    between as a chance for the event loop to resume that walk, which then
    dispatches the next node and orphans its agent in the worktree."""
    from kraft import store
    from kraft.api import deps

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "5")
    repo = make_repo(tmp_path)
    live_at_write = []
    real_skip_node = store.skip_node

    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "KRAFT_SLOW go", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            item = client.get(f"/api/work-items/{wid}").json()
            if item["status"] == "active" and any(
                s["status"] == "running" for s in item.get("worker_sessions", [])
            ):
                break
            time.sleep(0.1)
        assert item["status"] == "active", "the slow node never started running"

        def spy(conn, work_item_id, *a, **kw):
            live_at_write.append(deps.task_is_live(client.app, work_item_id))
            return real_skip_node(conn, work_item_id, *a, **kw)

        monkeypatch.setattr(store, "skip_node", spy)
        r = client.post(f"/api/work-items/{wid}/skip", json={})
        assert r.status_code == 200, r.text

    assert live_at_write == [False], "skip wrote skip_node with the old walk still live"
