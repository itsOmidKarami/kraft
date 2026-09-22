"""Skipping a task or a step: only the selected scope stops, and the walk
counts it as done (`skip-stops-only-the-selected-scope`)."""

from __future__ import annotations

import asyncio

import pytest
from support.harness import entry_of

from kraft import executor, store
from kraft.executor import dispatch

#: One step of two concurrent tasks, then a second step, then a second node.
CHAIN = """
- id: n
  kind: exec
  steps:
    - id: pair
      tasks:
        - {id: a, kind: subprocess, command: "true"}
        - {id: b, kind: subprocess, command: "true"}
    - id: then
      tasks: [{id: c, kind: subprocess, command: "true"}]
- {id: after, kind: exec, tasks: [{id: d, kind: subprocess, command: "true"}]}
"""

LAUNCH = executor.LaunchContext(repo_entry=entry_of({"setup_command": ""}), steering_dir=None)


@pytest.fixture
def held(monkeypatch):
    """Dispatch that holds `n.pair.a` and `n.pair.b` open until released, the
    way a live session is: `release[path]` sets what it ends with. A stopped
    session ends `paused`, the status the route's stop leaves it."""
    started: list[str] = []
    release: dict[str, asyncio.Future] = {}

    async def fake(db, run_dirs, task, node, row, worktree, **kwargs):
        started.append(task.path)
        if task.path in ("n.pair.a", "n.pair.b"):
            release[task.path] = asyncio.get_running_loop().create_future()
            return await release[task.path]
        return "done"

    monkeypatch.setattr(dispatch, "dispatch_node", fake)
    return started, release


async def _both_running(started):
    for _ in range(200):
        if {"n.pair.a", "n.pair.b"} <= set(started):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the pair never started: {started}")


async def test_skip_task_does_not_skip_sibling(item_on, database, run_dirs, held):
    """`a` is skipped while both run. Only `a` stops; `b` runs on to its own
    result, and the walk counts `a` as done rather than as a pause."""
    started, release = held
    it = await item_on(CHAIN, worktree=True)
    walk = asyncio.create_task(
        executor.run_once(database, run_dirs, work_item_id=it.id, launch=LAUNCH)
    )
    await _both_running(started)

    await database.write(lambda c: store.skip_scope(c, it.id, "n.pair.a", "flaky"))
    release["n.pair.a"].set_result("paused")
    await asyncio.sleep(0.05)
    assert not walk.done(), "the walk moved on without waiting for the sibling"
    release["n.pair.b"].set_result("done")

    assert await walk == "completed"
    assert started == ["n.pair.a", "n.pair.b", "n.then.c", "after.main.d"]
    assert [e["payload"] for e in it.events("scope_skipped")] == [
        {"path": "n.pair.a", "note": "flaky"}
    ]


async def test_a_skipped_step_is_not_dispatched_when_the_walk_reaches_it(
    item_on, database, run_dirs, held
):
    started, release = held
    it = await item_on(CHAIN, worktree=True)
    await database.write(lambda c: store.skip_scope(c, it.id, "n.then", None))
    walk = asyncio.create_task(
        executor.run_once(database, run_dirs, work_item_id=it.id, launch=LAUNCH)
    )
    await _both_running(started)
    for path in ("n.pair.a", "n.pair.b"):
        release[path].set_result("done")

    assert await walk == "completed"
    assert "n.then.c" not in started


async def test_a_retry_after_a_skip_runs_the_skipped_work_again(item_on, database, run_dirs, held):
    """A skip belongs to the run it was made in: a retry's fork reruns what it
    covers, a skipped task included."""
    started, release = held
    it = await item_on(CHAIN, "after", worktree=True)
    await database.write(lambda c: store.skip_scope(c, it.id, "after.main.d", None))
    assert database.read(lambda c: store.skipped_paths(c, it.id)) == {"after.main.d"}

    await database.write(lambda c: store.fork_run(c, it.id, None, by_person=True))

    assert database.read(lambda c: store.skipped_paths(c, it.id)) == frozenset()
