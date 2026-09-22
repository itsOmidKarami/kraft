"""`POST /work-items/{id}/retry` addresses its target by canonical path. The
route validates and hands the target to `executor.retry`; what each scope
reruns is tests/executor/test_retry_scopes.py."""

from __future__ import annotations

import pytest
from support.api import _force_node


@pytest.fixture
def retried(client, monkeypatch):
    """What the route handed `executor.retry`, instead of walking it."""
    from kraft import executor

    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)
        return "completed"

    monkeypatch.setattr(executor, "retry", fake)
    return seen


def _stopped_at(client, repo, node_id):
    """Stopped at `node_id`, forced there: nothing walks before the test."""
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    _force_node(wid, node_id, "needs_human")
    return wid


@pytest.mark.parametrize(
    ("body", "path", "scope"),
    [
        ({}, "verification", "node"),
        ({"path": "verification.review"}, "verification.review", "step"),
        ({"path": "verification.review.code_review"}, "verification.review.code_review", "task"),
        ({"path": "spec"}, "spec", "node"),
        ({"restart": True}, None, None),
    ],
    ids=["stopped-node", "step", "task", "completed-node", "restart"],
)
def test_a_retry_hands_its_target_to_the_fork(client, repo, retried, body, path, scope):
    wid = _stopped_at(client, repo, "verification")

    r = client.post(f"/api/work-items/{wid}/retry", json=body)

    assert r.status_code == 200, r.text
    assert r.json()["path"] == path
    _wait_for(lambda: "target" in retried)
    target = retried["target"]
    assert (target and target.path, target and target.scope.value) == (path, scope)


@pytest.mark.parametrize(
    ("body", "status", "says"),
    [
        ({"path": "verification.nope"}, 422, "path: 'verification.nope': node"),
        ({"path": "merge"}, 409, "path: 'merge' is after the node the item stands on"),
        ({"path": "spec", "restart": True}, 422, "path: a restart reruns the whole chain"),
        (
            {"path": "verification.review.code_review", "task_config": {"kind": "subprocess"}},
            422,
            "task_config.kind: a retry cannot change",
        ),
        (
            {"path": "verification.review.code_review", "task_config": {"id": "other"}},
            422,
            "task_config.id: a retry cannot change",
        ),
        (
            {"path": "verification.review", "task_config": {"model": "x"}},
            422,
            "task_config: task configuration applies to a task",
        ),
        (
            {"path": "verification", "policy": {"max_attempts": "lots"}},
            422,
            "policy.max_attempts: ",
        ),
        ({"restart": True, "task_config": {"model": "x"}}, 422, "task_config: a work-item"),
    ],
    ids=[
        "unknown-path",
        "forward",
        "path-and-restart",
        "override-changes-the-kind",
        "override-renames-the-task",
        "override-task-config-on-a-step",
        "override-policy-of-the-wrong-type",
        "override-on-restart",
    ],
)
def test_a_retry_the_route_cannot_honour_is_refused_naming_the_field(
    client, repo, retried, body, status, says
):
    """The item is left exactly as it was: still stopped, nothing forked."""
    wid = _stopped_at(client, repo, "verification")

    r = client.post(f"/api/work-items/{wid}/retry", json=body)

    assert r.status_code == status, r.text
    assert says in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
    assert retried == {}


def test_an_empty_override_is_no_override(client, repo, retried):
    wid = _stopped_at(client, repo, "verification")

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": "verification.review.code_review", "task_config": {}, "policy": {}},
    )

    assert r.status_code == 200, r.text
    _wait_for(lambda: "target" in retried)
    assert retried["override"] is None


@pytest.fixture
def walked(monkeypatch):
    """`executor.retry` runs for real -- it records the fork -- and only the
    walk after it is stood in for."""
    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)
        return "completed"

    monkeypatch.setattr("kraft.executor.walk.run", fake)
    return seen


def _forks(client, wid):
    """The item's forks, read through a connection of this thread's own."""
    import os
    import sqlite3
    from pathlib import Path

    from kraft import store

    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        return store.run_forks(conn, wid), conn.execute(
            "SELECT * FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
    finally:
        conn.close()


def test_an_override_within_bounds_is_applied_to_the_fork(client, repo, walked):
    """`retry-overrides-are-policy-bounded`, end to end: the override the
    validator accepts is the fork's own chain, and the item runs it."""
    from kraft import store
    from kraft.templates.forks import ChainPath

    wid = _stopped_at(client, repo, "verification")
    path = "verification.review.code_review"

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": path, "task_config": {"effort": "low"}, "policy": {"deny_tools": ["Bash"]}},
    )

    assert r.status_code == 200, r.text
    _wait_for(lambda: walked)
    [fork], row = _forks(client, wid)
    assert fork.override["task_config"] == {"effort": "low"}
    assert "Bash" in fork.override["policy"]["deny_tools"]
    task = ChainPath.parse(store.materialized_chain_of(row), path).task.task
    assert task.effort == "low" and "Bash" in task.policy.deny_tools


def test_an_override_out_of_bounds_forks_nothing(client, repo, walked):
    wid = _stopped_at(client, repo, "verification")

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": "verification.review.code_review", "task_config": {"kind": "subprocess"}},
    )

    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith("task_config.kind: ")
    assert _forks(client, wid)[0] == []
    assert walked == {}


def _wait_for(predicate, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "the retry never reached executor.retry"
        time.sleep(0.02)


@pytest.mark.parametrize("door", ["retry", "resume"])
def test_a_refresh_conflict_at_the_door_is_handed_to_the_walk(client, repo, monkeypatch, door):
    """Kraft-e7anb: `/retry` and `/resume` no longer stop for a human on the
    spot. The conflict rides into the walk, which gives it to the node's
    `on_conflict` handler or stops
    (tests/executor/test_base_change.py::test_a_conflict_at_the_door_goes_to_the_nodes_handler)."""
    import kraft.builtins as builtins_mod
    from kraft import executor

    async def conflicts(*args, **kwargs):
        raise builtins_mod.RebaseConflict("rebase failed for x")

    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)
        return "completed"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", conflicts)
    monkeypatch.setattr(executor, "retry" if door == "retry" else "run", fake)
    wid = _stopped_at(client, repo, "verification")
    if door == "resume":
        from support.api import _set_status

        _set_status(wid, "paused")

    assert client.post(f"/api/work-items/{wid}/{door}", json={}).status_code == 200
    _wait_for(lambda: "conflict" in seen)
    assert seen["conflict"] == "rebase failed for x"


def _escalation_running(wid, sid="s-esc"):
    """A live escalation session: a retry sent with its id is the escalation
    agent retrying its own item, and is deferred rather than run."""
    import os
    import sqlite3
    from pathlib import Path

    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
            "result_path, status, created_at) VALUES (?, ?, 'verification', 'escalation', "
            "'x', 'x', 'running', datetime('now'))",
            (sid, wid),
        )
        conn.commit()
    finally:
        conn.close()
    return {"x-kraft-session-id": sid}


def _self_retry_requests(client, wid):
    return [
        e["payload"]
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "work_item_self_retry_requested"
    ]


def test_a_deferred_self_retry_carries_the_validated_override(client, repo, walked):
    """Kraft-vvj32: the escalation agent's own `/retry` is deferred until its
    turn ends, and the override it asked for rides the deferral as validated
    -- the fork's copy of the chain included -- not dropped."""
    wid = _stopped_at(client, repo, "verification")
    path = "verification.review.code_review"

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": path, "task_config": {"effort": "low"}},
        headers=_escalation_running(wid),
    )

    assert r.status_code == 200, r.text
    [request] = _self_retry_requests(client, wid)
    assert request["override"]["task_config"] == {"effort": "low"}
    assert request["override"]["path"] == path
    assert '"effort":"low"' in request["override"]["chain"]


def test_a_deferred_self_retry_refuses_an_override_out_of_bounds(client, repo, walked):
    wid = _stopped_at(client, repo, "verification")

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": "verification.review.code_review", "task_config": {"kind": "subprocess"}},
        headers=_escalation_running(wid),
    )

    assert r.status_code == 422, r.text
    assert _self_retry_requests(client, wid) == []
