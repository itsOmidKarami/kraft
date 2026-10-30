"""`POST /work-items/{id}/retry` addresses its target by canonical path. The
route validates and hands the target to `executor.retry`; what each scope
reruns is tests/executor/test_retry_scopes.py."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from support.api import (
    _budget_stopped_item,
    _force_node,
    _poll_events,
    _post_default,
    _set_status,
)

from kraft import store
from kraft.executor import walk
from kraft.policy import Cap
from kraft.templates.forks import ChainPath


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
    ("kind", "capped", "fixed", "path"),
    [
        ("cap", None, False, "verification.review"),
        ("budget", None, False, "verification.review"),
        ("cap", {"cycles": 3, "attempts": 3}, False, "verification"),
        ("cap", None, True, "verification"),
        ("failed", None, False, "verification"),
    ],
    ids=["time-cap", "budget", "loop-cap", "after-a-fix-cycle", "failure"],
)
def test_a_plain_retry_after_a_cap_reruns_the_step_it_ran_out_in(
    client, repo, retried, kind, capped, fixed, path
):
    """Stopped in `verification`'s second step: running out of time or money
    says nothing about the first, which is not rerun. A loop cap, a failure,
    and a cap once a fix may have broken the first step rerun the node."""
    wid = _stopped_at(client, repo, "verification")
    db = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE work_items SET current_step = 1 WHERE id = ?", (wid,))
        store.mark_needs_human(conn, wid, "verification", "ran out", capped, kind=kind)
        if fixed:
            row = conn.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            node = ChainPath.parse(store.materialized_chain_of(row), "verification").node
            store.bump_counter(conn, wid, walk._loop_key(node), Cap(attempts=3, wall_clock_s=600))

    r = client.post(f"/api/work-items/{wid}/retry", json={})

    assert r.status_code == 200, r.text
    _wait_for(lambda: "target" in retried)
    assert retried["target"].path == path


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
    return {"x-kraft-session-id": f"{wid}/{sid}"}


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


_OLD_ESC = "8cbfe6e27c1044b3e445c0f0d726357d"


@pytest.mark.parametrize(
    ("running", "caller", "own"),
    [
        ("e5c4l2", "{wid}/e5c4l2", True),
        (_OLD_ESC, _OLD_ESC, True),
        ("e5c4l2", "w-other/e5c4l2", False),
        ("e5c4l2", "e5c4l2", None),
    ],
    ids=["qualified", "bare", "same-id-other-item", "bare-per-item-id"],
)
def test_an_escalation_retrying_its_own_item_is_told_apart_by_item_and_id(
    client, repo, walked, running, caller, own
):
    """`<wid>/<sid>` is this item's escalation turn only when `wid` is this
    item: another item's session with the same id is a stranger, whose retry
    stops the turn and runs. A bare id is its own only as a pre-upgrade
    one's 32 characters; a bare per-item id says nothing of its item, so it
    is refused (`own` None) rather than taken for this item's. The deferral
    records the bare id, as every event records a session."""
    wid = _stopped_at(client, repo, "verification")
    _escalation_running(wid, running)

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={},
        headers={"x-kraft-session-id": caller.format(wid=wid)},
    )

    assert r.status_code == (400 if own is None else 200), r.text
    asked = [q["session_id"] for q in _self_retry_requests(client, wid)]
    assert asked == ([running] if own else [])


def test_a_deferred_self_retry_refuses_an_override_out_of_bounds(client, repo, walked):
    wid = _stopped_at(client, repo, "verification")

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": "verification.review.code_review", "task_config": {"kind": "subprocess"}},
        headers=_escalation_running(wid),
    )

    assert r.status_code == 422, r.text
    assert _self_retry_requests(client, wid) == []


_MCP = {"x-kraft-client": "mcp"}


@pytest.mark.parametrize(
    ("caller", "task_config", "status"),
    [
        ("escalation", {"prompt": "do this instead"}, 403),
        ("mcp", {"prompt": "do this instead"}, 403),
        ("other-worker", {"prompt": "do this instead"}, 403),
        ("mcp", {"effort": "low", "prompt": "and this"}, 403),
        ("mcp", {"effort": "low", "model": "claude-opus-4-1"}, 200),
        ("person", {"prompt": "do this instead"}, 200),
    ],
    ids=[
        "escalation-prompt",
        "assistant-prompt",
        "another-items-worker-prompt",
        "assistant-effort-and-prompt",
        "assistant-model-and-effort",
        "person-prompt",
    ],
)
def test_a_retry_that_changes_more_than_a_tasks_model_is_a_persons(
    client, repo, retried, caller, task_config, status
):
    """#1189: a retry's `task_config` reaches the rerun task, so a prompt or
    command in it is a person's to set. An agent's retry may still change the
    task's model and effort; a refusal forks nothing."""
    wid = _stopped_at(client, repo, "verification")
    headers = {
        "escalation": lambda: _escalation_running(wid),
        "mcp": lambda: _MCP,
        "other-worker": lambda: {"x-kraft-session-id": "w-other/abc123"},
        "person": dict,
    }[caller]()

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": "verification.review.code_review", "task_config": task_config},
        headers=headers,
    )

    assert r.status_code == status, r.text
    if status == 403:
        assert "changing a task's prompt is a person's call" in r.json()["detail"]
        assert _forks(client, wid)[0] == []
        assert _self_retry_requests(client, wid) == []
        assert retried == {}
    else:
        _wait_for(lambda: "target" in retried)


def _at_a_gate(client, repo, status: str) -> str:
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, status)
    return wid


def _stopped(client, repo, status: str) -> str:
    breach = {"scope": "work_item", "spent_usd": 5.0, "cap_usd": 5.0}
    return _budget_stopped_item(client, repo, breach)


@pytest.mark.parametrize(
    ("item", "status", "verb", "detail"),
    [
        (
            _at_a_gate,
            "paused",
            "retry",
            "work item is paused, not stopped: resume it instead, or skip what it would run",
        ),
        (
            _stopped,
            "needs_human",
            "resume",
            "work item is needs_human, not paused: it stopped, so retry it instead "
            "(kraft item retry {wid}, which takes --steer too)",
        ),
        (
            _at_a_gate,
            "needs_human",
            "resume",
            "work item is waiting at its spec_approval gate, not paused: approve or reject "
            "it (kraft item approve {wid}, or kraft item reject {wid} --note ...)",
        ),
    ],
    ids=["retry-on-paused", "resume-on-stopped", "resume-at-a-gate"],
)
def test_the_wrong_door_names_the_right_one(client, repo, item, status, verb, detail):
    """R10b-01: retry claims only a stopped item. A paused one used to hear
    only "work item is not stopped", which named no way on; Resume is it. And
    the other way round: Resume on a stopped item said only "not paused",
    where Retry is the way on, or at a gate, approve or reject (R11a)."""
    wid = item(client, repo, status)

    r = client.post(f"/api/work-items/{wid}/{verb}", json={})

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == detail.format(wid=wid)
