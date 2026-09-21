"""A node's declared `escalation` task: it runs only once the node's recovery
and fix loop cannot advance it, is bounded, retries the node from its first
step when it succeeds, and otherwise leaves the item for a human -- where the
generic auto-escalation does not follow it."""

import shlex

import pytest

from kraft import executor, store
from kraft import policy as _policy
from kraft.executor import gates

NO_SETUP = {"setup_command": ""}
LAUNCH = executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None)


def _sub(task_id):
    return {"id": task_id, "kind": "subprocess", "command": shlex.join(["true"])}


def _node(**fields):
    """prep -> check, one node-level repair, a one-attempt fix loop, and an
    escalation task."""
    return {
        "id": "build",
        "kind": "exec",
        "steps": [
            {"id": "prep", "tasks": [_sub("prep")]},
            {"id": "check", "tasks": [_sub("check")]},
        ],
        "on_failure": {"tasks": [_sub("repair")]},
        "fix_loop": {"tasks": [_sub("fix")], "max_attempts": 1},
        "escalation": _sub("esc"),
        **fields,
    }


def _policy_(cap=3):
    return _policy.Policy(loops={}, default=_policy.Cap(9, 3600), auto_escalate_stuck_cap=cap)


def _walk(it, policy=None):
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=policy or _policy_(),
        launch=LAUNCH,
    )


def _reason(it):
    return it.events("work_item_needs_human")[-1]["payload"]["reason"]


async def test_escalation_runs_only_after_recovery_and_the_fix_loop_and_retries_the_node(
    item_on, script
):
    """`stuck-escalation-is-an-exec-node-control` and
    `successful-stuck-escalation-retries-the-node`: it waits for the repair and
    the fix loop to be spent, and a clean finish reruns the node from its first
    step with a fresh fix-loop budget."""
    script.plan = {"check": ["failed", "failed", "failed", "done"]}
    it = await item_on([_node()])

    assert await _walk(it) == "completed"
    assert script.calls == [
        "prep", "check",  # measured
        "repair", "prep", "check",  # node recovery, re-measured from the first step
        "fix", "prep", "check",  # the fix loop's one attempt, re-measured
        "esc",  # stuck: escalated
        "prep", "check",  # retried from the first step
    ]  # fmt: skip
    [started] = it.events("stuck_escalation_started")
    assert started["payload"]["task"] == "build.escalation.esc"
    assert started["payload"]["reason"].startswith("build.fix_loop exhausted after 1")
    assert not it.events("work_item_needs_human")
    assert it.database.read(lambda c: store.read_counter(c, it.id, "build.fix_loop")) is None


@pytest.mark.parametrize("ending", ["failed", "needs_context"])
async def test_a_failed_or_questioning_escalation_leaves_the_item_for_a_human(
    item_on, script, ending
):
    """`failed-or-questioning-stuck-escalation-needs-human`."""
    script.plan = {"check": ["failed"], "esc": [ending]}
    it = await item_on([_node()])

    assert await _walk(it) == "needs_human"
    assert script.calls.count("esc") == 1
    if ending == "failed":
        assert _reason(it).startswith("build.fix_loop exhausted after 1")
        assert _reason(it).endswith("(stuck escalation esc [subprocess] ended failed)")
    else:
        assert _reason(it) == "needs_context: (no question given)"


async def test_escalation_is_bounded_per_node(item_on, script):
    script.plan = {"check": ["failed"]}
    it = await item_on([_node()])

    assert await _walk(it, _policy_(cap=2)) == "needs_human"
    assert script.calls.count("esc") == 2
    assert _reason(it).endswith("(stuck escalation spent: 2 of 2)")


@pytest.mark.parametrize("stop", ["needs_context", "config_error"])
async def test_a_stop_the_controls_did_not_reach_is_not_escalated(item_on, script, stop):
    """A question is for a human, and a task that could not start is a
    configuration stop: neither is the node being stuck."""
    script.plan = {"check": [stop]}
    it = await item_on(
        [{"id": "build", "kind": "exec", "tasks": [_sub("check")], "escalation": _sub("esc")}]
    )

    async def record(_row):
        # What the real dispatch leaves behind: the session its status is read
        # back from.
        await it.session(f"s-{stop}", "build.main.check", stop, round=0)

    script.effects = {"check": record}

    assert await _walk(it) == "needs_human"
    assert "esc" not in script.calls


async def test_the_generic_auto_escalation_does_not_follow_a_declared_one(
    item_on, script, monkeypatch
):
    dispatched = []

    async def fake_dispatch(*a, **kw):
        dispatched.append(kw)
        return "done"

    monkeypatch.setattr(gates.escalate, "dispatch", fake_dispatch)
    script.plan = {"check": ["failed"], "esc": ["failed"]}
    it = await item_on([_node()])
    assert await _walk(it) == "needs_human"

    status = await gates.auto_escalate_stuck(
        "needs_human",
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=_policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=0),
        launch=LAUNCH,
    )

    assert status == "needs_human"
    assert dispatched == []
    [skipped] = it.events("work_item_auto_escalate_skipped")
    assert skipped["payload"] == {"reason": "node_escalation"}
