"""A node's declared `escalation` task: it runs only once the node's recovery
and fix loop cannot advance it, is bounded, retries the node from its first
step when it succeeds, and otherwise leaves the item for a human -- where the
generic auto-escalation does not follow it."""

import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest

from kraft import events, executor, rate_limit_retry, store
from kraft import policy as _policy
from kraft.executor import dispatch, gates

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


# -- Ruling 176: one stuck set, read by both mechanisms ----------------------
#
# A node's declared escalation and the generic `auto_escalate_stuck` answer the
# same four stops and nothing else. Every other stop goes straight to a human
# under its own named cause, declared escalation or not.

_REVIEWER = Path(__file__).parents[1] / "support" / "fake_reviewer.py"
_FINDING = {
    "severity": "important",
    "message": "still broken",
    "file": "a.py",
    "line": 1,
    "source_plugin": "fake",
}


def _reviewing_node(**fields):
    """A fix-loop node whose measuring task reports the same finding every
    round (`support/fake_reviewer.py`)."""
    review = {
        "id": "check",
        "kind": "subprocess",
        "command": shlex.join([sys.executable, str(_REVIEWER)]),
    }
    return {
        "id": "build",
        "kind": "exec",
        "tasks": [review],
        "fix_loop": {"tasks": [_sub("fix")], "max_attempts": 5, **fields},
    }


async def _stall(it, script, monkeypatch, tmp_path):
    plan = tmp_path / "review-plan.json"
    plan.write_text(json.dumps([{"status": "done", "findings": [_FINDING]}]))
    monkeypatch.setenv("KRAFT_FAKE_REVIEW_PLAN", str(plan))
    script.real = {"check"}


async def _judge(it, script, monkeypatch, tmp_path):
    await _stall(it, script, monkeypatch, tmp_path)
    script.real.add("fix")  # the judge is due only once a fix session exists

    async def verdict(*a, **kw):
        return "stop_needs_human", "not converging"

    monkeypatch.setattr(dispatch, "judge_verdict", verdict)


def _session_for(stop):
    async def arrange(it, script, monkeypatch, tmp_path):
        async def record(_row):
            await it.session(f"s-{stop}", "build.main.check", stop, round=0)

        script.plan = {"check": [stop]}
        script.effects = {"check": record}

    return arrange


def _returns(stop, event=None):
    async def arrange(it, script, monkeypatch, tmp_path):
        async def record(_row):
            if event is not None:
                await it.database.write(
                    lambda c: events.append(c, it.id, event, {"reason": f"{event} happened"})
                )

        script.plan = {"check": [stop]}
        script.effects = {"check": record}

    return arrange


def _loopless(**fields):
    return {"id": "build", "kind": "exec", "tasks": [_sub("check")], **fields}


#: stop kind -> (node, arrange, a fragment of the stop's own named cause)
STUCK = {
    "task failed after recovery": (
        lambda: _loopless(on_failure={"tasks": [_sub("repair")]}),
        _returns("failed"),
        "task failed in node build",
    ),
    "fix-loop cap exhausted": (
        lambda: {**_loopless(), "fix_loop": {"tasks": [_sub("fix")], "max_attempts": 1}},
        _returns("failed"),
        "build.fix_loop exhausted",
    ),
    "stall": (_reviewing_node, _stall, "stuck: "),
    "judge stop_needs_human": (
        lambda: _reviewing_node(judge=_sub("judge")),
        _judge,
        "judge: not converging",
    ),
}
NOT_STUCK = {
    "config error": (_loopless, None, "could not start check"),
    "budget": (_loopless, _returns("budget"), "budget cap reached"),
    "infra stop": (_loopless, _returns("infra_stop", "ci_infra_exhausted"), "ci_infra_exhausted"),
    "abandoned CI run": (
        _loopless,
        _returns("infra_stop", "ci_run_abandoned"),
        "ci_run_abandoned",
    ),
    "reviewer error": (
        _loopless,
        _returns("infra_stop", "automated_review_errored"),
        "automated_review_errored",
    ),
    "wait timeout": (_loopless, _returns("capped_out"), "timed out waiting"),
    "rate limit": (_loopless, _returns("rate_limited"), "rate_limit retries exhausted"),
    "needs_context question": (_loopless, None, "needs_context: "),
}
_SESSION_STOPS = {"config error": "config_error", "needs_context question": "needs_context"}


async def _stopped(item_on, script, monkeypatch, tmp_path, stub_app, kind, *, declared):
    node, arrange, cause = {**STUCK, **NOT_STUCK}[kind]
    fields = {"escalation": _sub("esc")} if declared else {}
    it = await item_on([{**node(), **fields}])
    if arrange is None:
        arrange = _session_for(_SESSION_STOPS[kind])
    await arrange(it, script, monkeypatch, tmp_path)
    script.plan["esc"] = ["failed"]

    status = await _walk(it)
    if kind == "rate limit":
        # The poller's relaunch budget, spent: that is where a rate limit
        # reaches a human.
        assert status == "rate_limited"
        app = stub_app(
            policy=_policy.Policy(loops={}, default=_policy.Cap(9, 3600), rate_limit_retries=1),
            templates_dir=tmp_path / "templates",
            skills_dir=tmp_path / "skills",
        )
        assert await rate_limit_retry.tick(app) == [it.id]
        await asyncio.gather(*app.state.tasks.values())
        assert await rate_limit_retry.tick(app) == []
    else:
        assert status == "needs_human"
    assert it.status() == "needs_human"
    assert cause in _reason(it), _reason(it)
    return it


async def _generic(it, monkeypatch):
    dispatched = []

    async def fake_dispatch(*a, **kw):
        dispatched.append(kw)
        return "done"

    monkeypatch.setattr(gates.escalate, "dispatch", fake_dispatch)
    status = await gates.auto_escalate_stuck(
        "needs_human",
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=_policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=0),
        launch=LAUNCH,
    )
    assert status == "needs_human"
    return dispatched


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
@pytest.mark.parametrize("kind", list(STUCK))
async def test_a_stuck_stop_is_escalated_by_exactly_one_mechanism(
    item_on, script, monkeypatch, tmp_path, stub_app, kind, declared
):
    """Ruling 176: the four stuck stops. A node that declares an escalation
    hands them to it, and the generic fallback stands aside; a node that does
    not gets the generic one."""
    it = await _stopped(item_on, script, monkeypatch, tmp_path, stub_app, kind, declared=declared)

    assert script.calls.count("esc") == (1 if declared else 0)
    dispatched = await _generic(it, monkeypatch)
    assert len(dispatched) == (0 if declared else 1)


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "undeclared"])
@pytest.mark.parametrize("kind", list(NOT_STUCK))
async def test_a_stop_outside_the_stuck_set_goes_straight_to_a_human(
    item_on, script, monkeypatch, tmp_path, stub_app, kind, declared
):
    """Ruling 176 (Kraft-w0fca, Kraft-gzqia): an agent cannot fix config,
    money, infra, a slow pipeline or a rate limit, and a question is for a
    person. Neither mechanism answers these, whether or not the node declares
    an escalation, and the stop keeps its own named cause."""
    it = await _stopped(item_on, script, monkeypatch, tmp_path, stub_app, kind, declared=declared)

    assert "esc" not in script.calls
    assert await _generic(it, monkeypatch) == []
    assert not it.events("work_item_auto_escalate_skipped")
