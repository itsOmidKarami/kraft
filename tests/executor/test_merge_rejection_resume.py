"""`on_merge_rejected` across a stop: a daemon restart mid-repair or
mid-rewind, and every walk that comes back to a `merge` node whose drop
nothing answered yet -- a resume, the rate-limit poller, a person's retry.
None of them asks the forge to merge a head the train rejected, or a repair's
commit, before a gate saw it."""

import pytest
from support.item_walk import (
    MERGE_POLICY,
    NO_SETUP,
    approve,
    commits_a_fix,
    merge_chain,
    merge_drops,
    records_merge_sessions,
    walk_once,
    walk_through_review,
)

from kraft import executor, node_runs, store
from kraft import policy as _policy
from kraft.executor import gates


class _Crash(BaseException):
    """The daemon dying mid-walk: nothing after it runs, nothing catches it."""


def _crash_after(monkeypatch, module, name: str, *, before: bool = False, when=None) -> None:
    """The daemon dies once, right after (or `before`) `module.name` runs --
    on the first call `when(*args)` accepts, if given."""
    real = getattr(module, name)

    async def crashing(*args, **kwargs):
        if when is not None and not when(*args):
            return await real(*args, **kwargs)
        monkeypatch.setattr(module, name, real)
        if not before:
            await real(*args, **kwargs)
        raise _Crash

    monkeypatch.setattr(module, name, crashing)


async def _retry_where_it_stands(it, steer=None, at=None):
    """A person's `kraft item retry ID`: its default target, the node the
    item stands on (`lifecycle._retry_target`), once the item is claimed --
    or `kraft item retry ID --from AT`."""
    from kraft.executor import walk
    from kraft.templates.forks import ChainPath

    row = it.row()
    target = ChainPath.parse(walk.chain_of(row), at or row["current_node_id"])
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status = 'active' WHERE id = ?", (it.id,))
    )
    return await executor.retry(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        target=target,
        by_person=True,
        steer=steer,
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


@pytest.mark.parametrize(
    ("crash", "cause", "commits", "held"),
    [
        (("dispatch", "run_recovery", False), "code", True, True),
        (("dispatch", "run_recovery", False), "code", False, True),
        (("walk", "_reopen_gates", True), "code", True, True),
        (("walk", "_clear_span_counters", True), "code", True, True),
        (("walk", "_restart_span", True), "conflict", False, False),
    ],
    ids=[
        "during-the-repair-after-it-committed",
        "during-the-repair-before-any-commit",
        "restarting-before-the-gates-reopened",
        "restarting-after-the-gates-reopened",
        "before-a-rebased-conflict-s-rewind",
    ],
)
async def test_a_daemon_restart_mid_rejection_never_merges_the_repair_unseen(
    item_on, script, monkeypatch, crash, cause, commits, held
):
    """The daemon dies while the repair works, or partway through the rewind's
    writes, the item still standing on `merge`. A resume must not stop it
    there: a retry's default target would push the repair's commit and merge
    it with no gate seeing it. Once a repair started it holds the item at
    `feedback` with `review` reopened instead, whatever the repair left where,
    so the retry walks back through the gate. With no repair started -- a
    conflict Kraft's own rebase cleared -- the item stops at `merge`, and the
    retry there rewinds through the gate rather than re-queue the rebased
    head. Neither stop is one automatic escalation answers."""
    from kraft.executor import dispatch, walk

    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script, cause)
    records_merge_sessions(it, script)
    if commits:
        script.effects["repair"] = commits_a_fix(it)
    module, name, before = crash
    _crash_after(monkeypatch, {"dispatch": dispatch, "walk": walk}[module], name, before=before)

    with pytest.raises(_Crash):
        await walk_once(it)
    assert it.row()["current_node_id"] == "merge"
    status = await executor.resume_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        adopted={},
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )

    assert status == "needs_human"
    assert it.row()["current_node_id"] == ("feedback" if held else "merge")
    assert [e["payload"] for e in it.events("gate_reopened")] == (
        [{"gate": "review", "reason": "merge_rejected"}] if held else []
    )
    stops = it.events("work_item_needs_human")
    assert stops[-1]["payload"]["reason"].startswith(
        "resume: the merge-rejection repair in node merge did not finish"
    )
    assert not gates.stuck_stop(stops)
    assert await _retry_where_it_stands(it) == "awaiting_gate"
    assert script.calls.count("merge") == 1, "merged code no gate saw"
    assert it.events("gate_reopened")[-1]["payload"]["gate"] == "review"


@pytest.mark.parametrize(
    "crash", ["repair", "cap", "restart"], ids=["the-repair-s-stop", "the-cap-stop", "the-restart"]
)
async def test_the_cursor_leaves_merge_before_the_walk_stops_or_moves_on(
    item_on, script, monkeypatch, crash
):
    """The repair committed and then stopped, or its rewind found the cap's
    clock spent: the item is held at `feedback` with `review` reopened before
    its stop is written. A rewind stands it at `feedback` before the walk
    enters it. A crash in between leaves it active at `feedback` -- where a
    resume walks back through the gate -- never stopped on `merge`, or left
    on it, with the repair's commit unseen."""
    from kraft.executor import walk

    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script, times=2)
    commit = commits_a_fix(it)
    late = []

    async def repair(_row):
        await commit(_row)
        if script.calls.count("repair") == 2:
            late.append(True)

    script.effects["repair"] = repair
    policy = None
    if crash == "repair":
        script.plan["repair"] = ["failed"]
        _crash_after(
            monkeypatch, walk, "_sentinel_stop", before=True, when=lambda *a: a[3] == "failed"
        )
    elif crash == "cap":
        real_now = walk._now
        monkeypatch.setattr(
            walk, "_now", lambda: "2999-01-01T00:00:00+00:00" if late else real_now()
        )
        policy = _policy.Policy(
            loops={"merge.on_merge_rejected": _policy.Cap(3, 3600)}, default=_policy.Cap(3, 3600)
        )
        _crash_after(monkeypatch, walk, "_stop_span_capped", before=True)
    else:
        _crash_after(
            monkeypatch,
            node_runs,
            "entered",
            before=True,
            when=lambda *a: a[3] == "feedback" and "repair" in script.calls,
        )

    with pytest.raises(_Crash):
        await walk_through_review(it, policy)
    assert it.row()["status"] == "active"
    assert it.row()["current_node_id"] == "feedback"
    assert it.events("gate_reopened")[-1]["payload"] == {
        "gate": "review",
        "reason": "merge_rejected",
    }


async def test_a_restart_back_on_merge_does_not_answer_the_old_drop_again(
    item_on, script, monkeypatch
):
    """The span already restarted, `review` was approved again, and the walk
    is back on `merge` when the daemon dies before the forge is asked: that
    drop was answered. The resume does not hold the item for it a second
    time, sending the reviewed head back through `review`."""
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    records_merge_sessions(it, script)
    script.effects["repair"] = commits_a_fix(it)
    assert await walk_once(it) == "awaiting_gate"
    await approve(it, "review")
    _crash_after(monkeypatch, node_runs, "entered", when=lambda *a: a[3] == "merge")
    with pytest.raises(_Crash):
        await walk_once(it)

    await executor.resume_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        adopted={},
        policy=_policy.Policy(loops={}, default=_policy.Cap(3, 3600)),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )
    assert it.row()["current_node_id"] == "merge"
    assert len(it.events("gate_reopened")) == 1


async def _relaunch(it):
    """The rate-limit poller's relaunch, or a resume: Kraft's own retry of
    the node it stopped on, then a walk from the cursor."""
    await it.database.write(
        lambda c: store.retry_after_cap(
            c, it.id, it.row()["current_node_id"], None, None, by_person=False
        )
    )
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status = 'active' WHERE id = ?", (it.id,))
    )
    return await walk_once(it)


_STEER = "the lockfile is what the train rejects"


@pytest.mark.parametrize(
    ("first", "again", "steered"),
    [
        ("rate_limited", _relaunch, False),
        ("paused", _relaunch, False),
        ("done", lambda it: _retry_where_it_stands(it, _STEER), True),
        ("needs_context", lambda it: _retry_where_it_stands(it, _STEER), True),
    ],
    ids=["rate-limited", "paused", "retry-after-nothing-committed", "retry-after-a-question"],
)
async def test_back_on_merge_after_an_unfinished_repair_the_repair_runs_again(
    item_on, script, first, again, steered
):
    """The repair stopped before committing anything: rate limited, paused,
    with nothing committed, or with a question. Whatever walks the item back
    onto `merge` -- the rate-limit poller, a resume, a person's retry there --
    runs the repair again, with the person's steer, and never asks the forge
    to merge the head it rejected. The repair's commit then rewinds through
    `review` as any repair's does, under the same cap."""
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    records_merge_sessions(it, script)
    script.plan["repair"] = [first, "done"]
    commit = commits_a_fix(it)

    async def repair(row):
        if script.calls.count("repair") == 2:
            await commit(row)
        elif first == "needs_context":
            await it.session("s-repair", "merge.on_merge_rejected.repair.main.repair", first)

    script.effects["repair"] = repair
    assert await walk_once(it) in ("needs_human", "paused", "rate_limited")
    assert it.row()["current_node_id"] == "merge"

    assert await again(it) == "awaiting_gate"
    assert script.calls == ["ci", "merge", "repair", "repair", "ci"]
    [restart] = it.events("merge_rejection_restart")
    assert restart["payload"]["restart"] == 1
    assert [e["payload"] for e in it.events("gate_reopened")] == [
        {"gate": "review", "reason": "merge_rejected"}
    ]
    assert (_STEER in script.steers["repair"][1].take()) is steered


@pytest.mark.parametrize(
    ("at", "calls"),
    [("review", ["merge"]), ("feedback", ["ci", "merge"])],
    ids=["from-the-gate", "from-an-earlier-node"],
)
async def test_a_retry_back_through_the_gate_answers_the_drop(item_on, script, at, calls):
    """A person who retries the item from the gate, or from before it, has
    the head seen again: back on `merge`, it is queued, not repaired again."""
    it = await item_on(merge_chain(on_merge_rejected=MERGE_POLICY))
    await approve(it, "review")
    merge_drops(it, script)
    records_merge_sessions(it, script)
    assert await walk_once(it) == "needs_human"

    assert await _retry_where_it_stands(it, at=at) == "awaiting_gate"
    await approve(it, "review")
    assert await walk_once(it) == "completed"
    assert script.calls == ["ci", "merge", "repair", *calls]
