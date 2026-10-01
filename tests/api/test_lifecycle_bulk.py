"""Retry's reported attempt number and the bulk pause/cancel/archive actions
(B2, B9). A sibling of test_lifecycle.py."""

from __future__ import annotations

from support.api import _force_node, _poll_events, _post_default, _set_status

# --- F: retry's attempt, bulk (B2, B9) ------------------------------------


def _no_spawn(app, wid, coro):
    """Stand in for `deps.spawn`: discards the coroutine instead of running
    it, so a retry's response can be checked without a real walk racing the
    test's own `worker_sessions` setup (and without needing a fake agent at
    all). `deps.discard`, not a bare `coro.close()`: `coro` here is
    `deps.guard(...)`'s own wrapper, which `discard` closes down to the
    `executor.retry(...)` coroutine inside -- same as `spawn`'s own
    `AlreadyRunning` path."""
    from kraft.api import deps

    deps.discard(coro)


def test_retry_response_reports_the_next_attempt_number(client, repo, monkeypatch):
    """B2: `attempt` is this (item, node, task)'s own session count + 1, read
    off `worker_sessions` before anything is dispatched -- a retry with no
    `path` reruns the whole node, whose one task (`implementation.main.
    implement`, a `tasks:` shorthand) is the only candidate."""
    from kraft import store
    from kraft.api import deps

    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "implementation", "needs_human")
    monkeypatch.setattr(deps, "spawn", _no_spawn)

    r1 = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r1.status_code == 200, r1.text
    assert r1.json()["attempt"] == 1

    db = client.app.state.db
    for i in range(2):
        client.portal.call(
            db.write,
            lambda c, i=i: store.create_session(
                c,
                id=f"s-{i}",
                work_item_id=wid,
                node_id="implementation",
                hook_point="implementation.main.implement",
                log_path="/l",
                result_path="/r",
            ),
        )
    _force_node(wid, "implementation", "needs_human")

    r2 = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r2.status_code == 200, r2.text
    assert r2.json()["attempt"] == 3


def test_retry_attempt_for_a_specific_path_counts_only_that_tasks_own_sessions(
    client, repo, monkeypatch
):
    """A `path` naming one task (`verification.review.code_review`) is not
    thrown off by a sibling task's (`verification.tests.test_changed_scopes`)
    own session count; a `path`-less retry of the whole node takes the
    highest next-attempt over all of the node's tasks instead."""
    from kraft import store
    from kraft.api import deps

    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "verification", "needs_human")
    monkeypatch.setattr(deps, "spawn", _no_spawn)

    db = client.app.state.db
    client.portal.call(
        db.write,
        lambda c: store.create_session(
            c,
            id="s-review",
            work_item_id=wid,
            node_id="verification",
            hook_point="verification.review.code_review",
            log_path="/l",
            result_path="/r",
        ),
    )

    r = client.post(
        f"/api/work-items/{wid}/retry", json={"path": "verification.tests.test_changed_scopes"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["attempt"] == 1

    _force_node(wid, "verification", "needs_human")
    r2 = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r2.status_code == 200, r2.text
    assert r2.json()["attempt"] == 2


def test_bulk_pause_mixed_batch_does_not_stop_at_a_failure(client, repo):
    """B9: a running item is paused, an already-ended one is refused, and an
    unknown id answers 'not found' -- one id's failure does not stop the
    loop, and the results come back in `ids` order."""
    running = _post_default(client, repo)
    _poll_events(client, running, "gate_requested")
    _set_status(running, "active")

    done = _post_default(client, repo)
    _set_status(done, "completed")

    r = client.post(
        "/api/work-items/bulk",
        json={"action": "pause", "ids": [running, done, "no-such-id"]},
    )

    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [res["id"] for res in results] == [running, done, "no-such-id"]
    assert results[0] == {"id": running, "ok": True, "status": "paused"}
    assert results[1] == {
        "id": done,
        "ok": False,
        "status": "completed",
        "error": "work item is completed, not running",
    }
    assert results[2] == {"id": "no-such-id", "ok": False, "error": "not found"}


def test_bulk_cancel_without_a_reason_is_422_before_touching_anything(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "active")

    r = client.post("/api/work-items/bulk", json={"action": "cancel", "ids": [wid]})

    assert r.status_code == 422, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "active"


def test_bulk_cancel_with_a_reason_cancels_each_item(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "active")

    r = client.post(
        "/api/work-items/bulk",
        json={"action": "cancel", "ids": [wid], "reason": "no longer needed"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["results"] == [{"id": wid, "ok": True, "status": "abandoned"}]


def test_bulk_archive_takes_only_ended_items(client, repo):
    active = _post_default(client, repo)
    _poll_events(client, active, "gate_requested")
    _set_status(active, "active")

    done = _post_default(client, repo)
    _set_status(done, "completed")

    r = client.post("/api/work-items/bulk", json={"action": "archive", "ids": [active, done]})

    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results[0]["ok"] is False
    assert results[1] == {"id": done, "ok": True, "status": "completed"}
    assert client.get(f"/api/work-items/{done}").json()["archived_at"] is not None
