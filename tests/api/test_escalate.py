"""POST /work-items/{wid}/escalate (spec:
docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path

from support.api import _poll_events, _set_status, _wait_for_status


def _post(client, repo, title="fine so far", **body):
    return client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": title, "chain_template": "quick-task", **body},
    ).json()["id"]


def _needs_human_item(client, repo, title="KRAFT_FAIL once"):
    """A real item driven to `needs_human` by the fake agent's own KRAFT_FAIL
    marker -- same recipe as
    test_api_retry_open_log.py::test_retry_restarts_a_stopped_node_that_has_no_fix_loop.
    """
    # Kraft-lpdd: this file drives the manual escalate/stop-escalate routes by
    # hand -- the unrelated auto-escalate trigger would otherwise race it onto
    # the same needs_human stop.
    wid = _post(
        client, repo, title, node_overrides={"implementation": {"auto_escalate_stuck": False}}
    )
    _wait_for_status(client, wid, "needs_human", timeout=120)
    return wid


def _sql(sql, *args):
    """Direct SQL on the client's own database -- driving a real item to a
    state through the full machinery needs more than these route checks are
    worth exercising for."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        rows = conn.execute(sql, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _seed_running_escalation(client, wid, session_id="running-turn"):
    """A `pending` escalation session on the item's current node, inserted
    directly -- the dispatch machinery itself is exercised elsewhere; these
    tests only need the guard every door onto the same worktree has to check."""
    node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
    _sql(
        "INSERT INTO worker_sessions "
        "(id, work_item_id, node_id, hook_point, log_path, result_path, status, created_at) "
        "VALUES (?, ?, ?, 'escalation', 'l', 'r', 'pending', 'now')",
        session_id,
        wid,
        node_id,
    )


def _settled(client, repo):
    """A fresh item whose walk has finished, so the route's own `task_is_live`
    409 (Kraft-s7c04.20) is out of the way and only its status check decides.
    The walk has to be gone from the task registry, not just off `active`:
    that can land a beat before the task is popped."""
    import kraft.api as api
    from kraft.api import deps

    wid = _post(client, repo)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and deps.task_is_live(api.app, wid):
        time.sleep(0.05)
    assert not deps.task_is_live(api.app, wid), "work item's walk never settled"
    return wid


def _escalate(client, wid, message="help", **body):
    return client.post(f"/api/work-items/{wid}/escalate", json={"message": message, **body})


def test_escalate_refuses_an_item_that_is_not_needs_human(client, repo):
    wid = _settled(client, repo)
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "completed"
    assert _escalate(client, wid).status_code == 409


def test_escalate_succeeds_from_paused(client, repo):
    """Kraft-k5ol: `escalate_work_item` used to refuse anything but
    `needs_human` -- the paused-state Escalate button had to be removed
    rather than shipped against a 409. Widened to accept `paused` too."""
    wid = _settled(client, repo)
    _set_status(wid, "paused")
    r = _escalate(client, wid)
    assert r.status_code == 200
    assert r.json()["status"] == "escalating"


def test_escalate_still_409s_from_a_never_started_paused_item(client, repo):
    """A `paused` item with no `current_node_id` (the `autostart: false`
    default `client.create_work_item` uses -- "it lands paused: an agent
    files work, a human starts it") has no node/context to escalate about.
    Widening the status check to admit `paused` (Kraft-k5ol) must not also
    admit this -- there is nothing yet for an agent to be escalated onto."""
    wid = _post(client, repo, "not started", autostart=False)
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "paused"
    assert item["current_node_id"] is None
    assert _escalate(client, wid).status_code == 409


def test_escalate_still_409s_from_active(client, repo):
    """Pins the widening at exactly `{needs_human, paused}` -- an `active`
    item must still 409, not silently start accepting escalation from
    anywhere."""
    wid = _settled(client, repo)
    _set_status(wid, "active")
    assert _escalate(client, wid).status_code == 409


def test_escalate_requires_a_nonempty_message(client, repo):
    wid = _needs_human_item(client, repo)
    assert _escalate(client, wid, "   ").status_code == 400


def test_escalate_refuses_a_second_call_while_one_is_running(client, repo):
    wid = _needs_human_item(client, repo)
    _seed_running_escalation(client, wid)

    r = _escalate(client, wid)
    assert r.status_code == 409
    assert "running-turn" in r.json()["detail"]


def test_retry_kills_a_running_escalation_turn_and_proceeds(client, repo, monkeypatch):
    """`retry` dispatches into the same worktree an escalation agent may
    already be committing in -- a stranger's retry (no matching session
    header) now kills that turn first rather than refusing outright
    (Kraft-vyk8; see test_api_lifecycle.py's
    test_retry_kills_a_strangers_running_escalation_and_proceeds for the
    self-retry-vs-stranger distinction this pins from the other side)."""
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: None)
    wid = _needs_human_item(client, repo)
    _seed_running_escalation(client, wid)

    r = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r.status_code == 200, r.text


def test_a_strangers_retry_cancels_the_escalation_task_not_just_its_pid(client, repo, monkeypatch):
    """Kraft-s7c04.20: `/escalate` used to spawn under a separate
    `f"{wid}:escalate"` task-registry key, invisible to `/retry`'s own
    `deps.cancel(request.app, wid)` preemption -- which could therefore only
    ever kill the pid (`_stop_live_sessions`), never the coroutine. Sharing
    `wid` means that same preemption now actually cancels it."""
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    wid = _needs_human_item(client, repo)
    _seed_running_escalation(client, wid)

    # Stand in for the live escalation coroutine `/escalate` would have
    # spawned under this same key -- registered directly rather than
    # driven through a real dispatch, the same seam
    # test_api_lifecycle.py's task_is_live tests already use.
    import kraft.api as api

    async def _never_returning():
        await asyncio.Event().wait()

    async def inject():
        api.deps.spawn(api.app, wid, _never_returning())

    client.portal.call(inject)

    r = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r.status_code == 200, r.text
    assert terminated == [None]  # _seed_running_escalation's row has no pid

    async def task_gone():
        task = api.app.state.tasks.get(wid)
        return task is None or task.done()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not client.portal.call(task_gone):
        time.sleep(0.05)
    assert client.portal.call(task_gone), "the old escalation task was never cancelled"


def test_resume_refuses_while_an_escalation_turn_is_running(client, repo):
    """Same race as `retry`, reached through `resume` instead: a `paused`
    item can carry a leftover running escalation row (Kraft-esc: an item can
    leave `needs_human` some other way while an escalation session exists)."""
    wid = _needs_human_item(client, repo)
    _seed_running_escalation(client, wid)
    _set_status(wid, "paused")

    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 409
    assert "running-turn" in r.json()["detail"]


def test_stop_escalation_kills_the_turn_without_changing_item_status(client, repo):
    wid = _needs_human_item(client, repo)
    _seed_running_escalation(client, wid, session_id="turn-1")

    r = client.post(f"/api/work-items/{wid}/escalate/stop")
    assert r.status_code == 200
    assert r.json()["session_id"] == "turn-1"

    assert _sql("SELECT status FROM worker_sessions WHERE id = 'turn-1'") == [("paused",)]
    assert _sql("SELECT status FROM work_items WHERE id = ?", wid) == [("needs_human",)]


def test_stop_escalation_refuses_when_none_is_running(client, repo):
    wid = _needs_human_item(client, repo)
    assert client.post(f"/api/work-items/{wid}/escalate/stop").status_code == 409


def test_escalate_route_forwards_new_thread(client, repo, monkeypatch):
    """Kraft-dkb6g: `new_thread` on the request body must reach
    `escalate.dispatch` -- pinned via a spy rather than the real dispatch
    path, since this route test only cares that the flag is threaded
    through, not that a thread actually gets dispatched (that's
    test_escalate.py's job)."""
    import kraft.escalate as escalate_mod

    seen = {}

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, **kw):
        seen["new_thread"] = kw.get("new_thread")
        return "done"

    monkeypatch.setattr(escalate_mod, "dispatch", fake_dispatch)

    wid = _needs_human_item(client, repo)
    r = _escalate(client, wid, "please look", new_thread=True)
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 10
    while "new_thread" not in seen and time.monotonic() < deadline:
        time.sleep(0.1)
    assert seen.get("new_thread") is True


def test_escalate_consumes_a_self_retry_left_by_a_human_escalated_agent(client, repo, monkeypatch):
    """A human escalates via `/escalate`, the agent fixes the problem and
    calls `kraft item retry` on itself mid-turn (lifecycle.py's
    `work_item_self_retry_requested` deferral), and the turn then exits.
    Without a consumer on this manual path the item silently stayed
    `needs_human` forever even though the caller got HTTP 200 (Kraft
    code-review finding); it must now actually retry once the turn ends."""
    import kraft.escalate as escalate_mod
    from kraft import events

    def fake_dispatch(node_id):
        async def _fake(database, run_dirs, *, work_item_id, **kw):
            await database.write(
                lambda c: events.append(
                    c,
                    work_item_id,
                    "work_item_self_retry_requested",
                    {
                        "session_id": "s1",
                        "node_id": node_id,
                        "key": None,
                        "gate_key": None,
                        "steer": "fixed it",
                    },
                )
            )
            return "done"

        return _fake

    async def fake_refresh(worktree, repo, branch, **_kw):
        return None

    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    monkeypatch.setattr("kraft.executor.gates._builtins.refresh_worktree_base", fake_refresh)
    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)

    wid = _needs_human_item(client, repo)
    node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
    monkeypatch.setattr(escalate_mod, "dispatch", fake_dispatch(node_id))

    r = _escalate(client, wid, "please look")
    assert r.status_code == 200, r.text

    _poll_events(client, wid, "work_item_retried")
    assert walk_calls and walk_calls[0]["work_item_id"] == wid
