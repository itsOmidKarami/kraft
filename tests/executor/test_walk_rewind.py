"""Task 6: a gateless request_changes' rewind, honoured at the right node
(review threads anywhere §1). Split out of test_walk.py to keep it under the
line budget."""

from __future__ import annotations

from support.harness import entry_of

from kraft import executor, store
from kraft.adapters import forge as _forge

NO_SETUP = entry_of({"setup_command": ""})
#: The same, on a forge: a V1 forge task runs on `backend: auto`, which reads
#: the forge off the repo entry. Without one the task fails in-process on the
#: missing forge before the patched `resolve` is ever asked for a backend.
FORGE_REPO = entry_of({"setup_command": "", "forge": "github"})


def _agent(task_id="implement", **fields):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _forge_task(task_id, target):
    return {"id": task_id, "kind": "forge", "target": target}


def _exec(node_id, *tasks, **fields):
    return {"id": node_id, "kind": "exec", "tasks": list(tasks), **fields}


def _walk(it, *, repo_entry=NO_SETUP, **kwargs):
    """`run_once` over the item: one walk of its chain from `start_index`."""
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=repo_entry),
        **kwargs,
    )


async def test_a_pending_rewind_takes_effect_when_the_node_completes(item_on, script):
    """Review Focus 1/3: a rewind requested while a later node is running is
    honoured only once that node *completes* -- the honour point in
    `run_once` sits directly before the loop's own `i += 1`, after every stop
    above it has already returned, so nothing but a real completion reaches
    it. The rewind's note reaches the re-run, not the node's ordinary steer."""
    it = await item_on(
        [_exec("implementation", _agent("implement")), _exec("verify", _agent("check"))]
    )
    captured: dict = {}

    async def _queue_rewind(row):
        # One-shot: without this the re-run of "verify" below would queue a
        # second rewind and the chain would never reach "completed".
        script.effects.pop("check", None)
        await it.database.write(
            lambda c: store.request_rewind(
                c, it.id, review_id="r1", target="implementation", note="fix the thing"
            )
        )

    async def _capture_the_rerun_steer(row):
        # Read at dispatch time, the way the real (unfaked) dispatch would
        # bake it into the prompt: `_report_if_undelivered`'s own read, once
        # the whole walk has finished, would otherwise empty it first.
        if len(script.steers["implement"]) == 2:
            captured["note"] = script.steers["implement"][-1].take()

    script.effects["check"] = _queue_rewind
    script.effects["implement"] = _capture_the_rerun_steer

    assert await _walk(it) == "completed"

    events = it.events()
    starts = [e["payload"]["node_id"] for e in events if e["type"] == "node_started"]
    # "implementation" and "verify" each start twice -- once for real, once
    # for the rewind's rerun; `store.complete_node` dedupes a second
    # `node_completed` for the same node within one run (no `run_forks` row
    # here, unlike a real `/retry`), so only one `node_completed` of each
    # survives -- that alone does not tell us the rewind fired, `starts` does.
    assert starts == ["implementation", "verify", "implementation", "verify"]
    verify_completed_at = next(
        i
        for i, e in enumerate(events)
        if e["type"] == "node_completed" and e["payload"]["node_id"] == "verify"
    )
    impl_restarted_at = [
        i
        for i, e in enumerate(events)
        if e["type"] == "node_started" and e["payload"]["node_id"] == "implementation"
    ][1]
    assert verify_completed_at < impl_restarted_at
    # The rewind's note reached the re-run, not the node's ordinary steer.
    assert captured["note"] == "fix the thing"


async def test_a_ci_wait_resume_does_not_jump_to_a_pending_rewind(item_on, script, monkeypatch):
    """Review Focus 2: a `request_changes` at an earlier node while a CI wait
    is running must not make the CI-wait poller's resume jump to the target --
    only the waiting node's own completion (inside `walk.run_once`), or a
    human `/resume`/`/retry`, may honour it.

    The real poller's door is `waits.tick`, which re-enters through
    `executor.run` (== `walk.run`) with **no position** -- the walk resumes at
    the item's own cursor, the waiting step (`waits.py`'s own docstring). It
    reads no rewind of its own; this drives that exact call directly, not
    `/resume` (which the brief named, but which is a *different* door: it goes
    through `resuming.reconcile_current_node`, meant for a crash resume, and a
    session left `waiting` there reads as "did not resolve cleanly" --
    behavior this task does not touch)."""
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _forge.FakeForge(ci_states=["pending"]))
    # "implement" is faked (script); "ci_poll" runs for real, so it actually
    # asks the (mocked) forge rather than the script's default "done".
    script.real.add("ci_poll")
    it = await item_on(
        [
            _exec("implementation", _agent("implement")),
            _exec("mr_checks", _forge_task("ci_poll", "mr.ci")),
        ]
    )

    assert await _walk(it, repo_entry=FORGE_REPO) == "waiting"
    assert it.status() == "waiting"

    await it.database.write(
        lambda c: store.request_rewind(
            c, it.id, review_id="r1", target="implementation", note="go back"
        )
    )
    # A fresh poll: the CI pipeline has gone green since the item last checked.
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _forge.FakeForge(ci_states=["success"]))
    # `waits.tick`'s own first move: flip `waiting` back to `active` before
    # the walk it spawns has done anything (Kraft-ppk9).
    await it.database.write(lambda c: store.mark_reentered(c, it.id))

    # No position: exactly what `waits.tick` passes.
    status = await executor.run(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=FORGE_REPO),
    )

    assert status == "completed"
    events = it.events()
    mr_completed_at = next(
        i
        for i, e in enumerate(events)
        if e["type"] == "node_completed" and e["payload"]["node_id"] == "mr_checks"
    )
    impl_starts = [
        i
        for i, e in enumerate(events)
        if e["type"] == "node_started" and e["payload"]["node_id"] == "implementation"
    ]
    assert len(impl_starts) == 2, "the rewind's target never reran"
    # The waiting node's own completion comes before the rewind's honour --
    # never the other way around (a poller's resume jumping past it).
    assert mr_completed_at < impl_starts[1]
