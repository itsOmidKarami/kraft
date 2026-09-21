"""Retry by canonical path: each scope reruns exactly its own work and
everything after it, on a new run fork (docs/templates-v1-design.md "Operator
controls and run forks")."""

from __future__ import annotations

import pytest

from kraft import executor, store
from kraft.executor import dispatch
from kraft.templates.forks import ChainPath

#: `a` has two steps, the first of two concurrent tasks; a gate; then `b`.
CHAIN = """
- id: a
  kind: exec
  steps:
    - id: first
      tasks:
        - {id: x, kind: subprocess, command: "true"}
        - {id: y, kind: subprocess, command: "true"}
    - id: second
      tasks: [{id: z, kind: subprocess, command: "true"}]
- {id: review, kind: gate}
- {id: b, kind: exec, tasks: [{id: w, kind: subprocess, command: "true"}]}
"""

LAUNCH = executor.LaunchContext(repo_entry={"setup_command": ""}, steering_dir=None)
REST = ["a.second.z", "b.main.w"]


@pytest.fixture
def dispatched(monkeypatch):
    ran: list[str] = []

    async def fake(db, run_dirs, task, node, row, worktree, **kwargs):
        ran.append(task.path)
        return "done"

    monkeypatch.setattr(dispatch, "dispatch_node", fake)
    return ran


async def _stopped_in_first_step(item_on):
    """`x` failed and `y` completed beside it; the review gate was approved on
    an earlier pass."""
    it = await item_on(CHAIN, "a", worktree=True)
    await it.session("s-x", "a.first.x", "failed")
    await it.session("s-y", "a.first.y", "done")
    await it.database.write(lambda c: store.approve_gate(c, it.id, "review"))
    return it


async def _retry(it, path):
    chain = store.materialized_chain_of(it.row())
    return await executor.retry(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        target=ChainPath.parse(chain, path) if path else None,
        registry=None,
        launch=LAUNCH,
    )


async def test_task_retry_reruns_only_task_then_later_work(item_on, dispatched):
    """`task-retry-reruns-that-task-and-later-work`: `y`, its completed
    sibling in the concurrent step, is kept rather than rerun."""
    it = await _stopped_in_first_step(item_on)

    await _retry(it, "a.first.x")

    assert dispatched[:2] == ["a.first.x", "a.second.z"]


async def test_step_retry_reruns_all_step_tasks_then_later_work(item_on, dispatched):
    it = await _stopped_in_first_step(item_on)

    await _retry(it, "a.first")

    assert dispatched[:3] == ["a.first.x", "a.first.y", "a.second.z"]


async def test_a_later_step_retry_keeps_the_steps_before_it(item_on, dispatched):
    it = await _stopped_in_first_step(item_on)

    await _retry(it, "a.second")

    assert dispatched[:1] == ["a.second.z"]


async def test_node_retry_reruns_that_node_and_later_work(item_on, dispatched):
    """`node-retry-reruns-that-node-and-later-work`, and
    `retry-reopens-invalidated-gates`: the approved review gate is in the
    rerun's span, so the rerun stops there again."""
    it = await _stopped_in_first_step(item_on)

    assert await _retry(it, "a") == "awaiting_gate"
    assert dispatched == ["a.first.x", "a.first.y", *REST[:1]]
    assert it.row()["current_node_id"] == "review"


async def test_a_retry_after_a_gate_keeps_its_decision(item_on, dispatched):
    """Gate decisions before the retry target stay in effect: a retry of `b`
    never walks back over the approved review."""
    it = await _stopped_in_first_step(item_on)

    assert await _retry(it, "b") == "completed"
    assert dispatched == ["b.main.w"]
    assert it.events("gate_reopened") == []


async def test_retry_can_target_completed_work(item_on, dispatched):
    """`retry-can-target-completed-work`: `a` completed and the item moved on to
    stop at `b`. Retrying `a` reruns it, and it completes again in the fork."""
    it = await item_on(CHAIN, "b", worktree=True)
    await it.database.write(lambda c: store.complete_node(c, it.id, "a"))

    assert await _retry(it, "a") == "awaiting_gate"
    assert dispatched == ["a.first.x", "a.first.y", "a.second.z"]
    assert [e["payload"]["node_id"] for e in it.events("node_completed")].count("a") == 2


async def test_work_item_restart_reruns_the_complete_chain(item_on, dispatched):
    it = await item_on(CHAIN, "b", worktree=True)
    await it.database.write(lambda c: store.approve_gate(c, it.id, "review"))

    assert await _retry(it, None) == "awaiting_gate"
    assert dispatched == ["a.first.x", "a.first.y", "a.second.z"]
    assert it.events("run_forked")[-1]["payload"]["scope"] == "work_item"


async def test_every_retry_is_its_own_fork(item_on, dispatched):
    """`retry-creates-an-immutable-run-fork`: one fork per retry, each the
    child of the last, and the prior run's sessions are still there."""
    it = await _stopped_in_first_step(item_on)

    await _retry(it, "b")
    await _retry(it, "b")

    forks = it.database.read(lambda c: store.run_forks(c, it.id))
    assert [f.parent for f in forks] == [None, forks[0].id]
    assert {"s-x", "s-y"} <= {s["id"] for s in it.sessions()}
