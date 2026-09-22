"""Config-error stops: a launch whose sandbox or repository steering cannot
be resolved reports as its own session rather than crashing the walk.

Split out of `test_policy_enforcement.py` (Kraft-9ct4q's sandbox rows plus
Kraft-jzdyp code review round 1's steering rows) to keep that file under the
800-line budget; shares its fixtures, chains and helpers by import, the same
way it already imports from `test_policy_tool_names`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kraft import gate_review
from kraft.adapters import agent as agent_mod
from kraft.executor import dispatch
from kraft.executor.context import CONFIG_ERROR
from kraft.templates.models import ResolvedChain
from kraft.worker import steering as steering_mod

# -- a sandbox nobody can resolve stops the launch, attributed (Kraft-9t2dp) -------------


async def _two_sandboxes(item_on, monkeypatch, chain, **kwargs):
    """An item whose snapshot froze two sandboxes, built past the build-time
    refusal the way a pre-Ruling-189 row reaches a launch."""
    monkeypatch.setattr(ResolvedChain, "check_scopes", lambda *_a, **_kw: None)
    it = await item_on(chain, **kwargs)
    monkeypatch.undo()
    return it


def _never_launches(monkeypatch) -> list:
    launched = []

    async def launch(*_a, **kw):
        launched.append(kw)
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", launch)
    monkeypatch.setattr(agent_mod._subprocess, "run_task", launch)
    monkeypatch.setattr(agent_mod, "run_agent_task", launch)
    return launched


def _config_error_session(it, hook_point, expect="different sandboxes"):
    (session,) = [s for s in it.sessions() if s["hook_point"] == hook_point]
    assert session["status"] == CONFIG_ERROR
    assert expect in Path(session["log_path"]).read_text()


@pytest.mark.parametrize("node_index", [1, 2, 3], ids=["subprocess", "builtin", "agent"])
async def test_a_task_whose_sandbox_cannot_be_resolved_stops_as_its_own_session(
    item_on, monkeypatch, fake_agent, node_index
):
    from executor.test_policy_enforcement import _OTHER, _SANDBOX, TESTED, _sandboxed_elsewhere

    chain = _sandboxed_elsewhere(_SANDBOX)
    chain[3]["tasks"][0]["policy"] = {"sandbox": _OTHER}
    it = await _two_sandboxes(item_on, monkeypatch, chain)
    launched = _never_launches(monkeypatch)
    node = it.chain.chain.nodes[node_index]
    task = node.steps[0].tasks[0]
    launch = TESTED

    status = await dispatch.dispatch_node(
        it.database, it.run_dirs, task, node, it.row(), it.repo, launch=launch
    )

    assert status == CONFIG_ERROR
    assert launched == []
    _config_error_session(it, task.path)


async def test_a_gate_review_whose_sandbox_cannot_be_resolved_stops_as_its_own_session(
    item_on, monkeypatch, fake_agent
):
    from executor.test_policy_enforcement import (
        _OTHER,
        _SANDBOX,
        NO_SETUP,
        _requested,
        _reviewed_gate,
    )

    chain = _reviewed_gate({"sandbox": _OTHER})
    chain[0]["tasks"][0]["policy"] = {"sandbox": _SANDBOX}
    it = await _two_sandboxes(item_on, monkeypatch, chain, auto_gate=True)
    launched = _never_launches(monkeypatch)
    await _requested(it)
    gate = it.chain.chain.nodes[1]

    verdict, note = await gate_review.review(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate="spec_approval",
        node=gate,
        launch=NO_SETUP,
    )

    assert (verdict, launched) == ("undecided", [])
    assert "different sandboxes" in note
    _config_error_session(it, gate.auto_review.path)


@pytest.mark.parametrize("auto", [False, True], ids=["manual", "auto"])
async def test_an_escalation_whose_sandbox_cannot_be_resolved_stops_as_its_own_session(
    item_on, monkeypatch, fake_agent, auto
):
    from executor.test_policy_enforcement import _OTHER, _SANDBOX, _agent, _escalate, _node

    chain = [
        {"id": "first", "kind": "exec", "tasks": [_agent(policy={"sandbox": _SANDBOX})]},
        *_node(_agent(policy={"sandbox": _OTHER})),
    ]
    it = await _two_sandboxes(item_on, monkeypatch, chain, node="implementation")
    launched = _never_launches(monkeypatch)

    assert await _escalate(it, auto) == CONFIG_ERROR

    assert launched == []
    _config_error_session(it, "escalation")


def _raising_resolver(monkeypatch, message="boom"):
    """`resolve_agent_task` raising `SteeringError` -- its pre-freeze branch
    (a snapshot filed before repository steering was frozen, selecting a name
    the live library no longer defines) still raises this even after
    Kraft-jzdyp's review round removed the frozen branch's own raise, so
    `escalate.dispatch` and `gate_review.review` each need their own clean
    catch for it."""

    def _raise(*_a, **_kw):
        raise steering_mod.SteeringError(message)

    monkeypatch.setattr(agent_mod, "resolve_agent_task", _raise)


async def test_a_gate_review_whose_steering_cannot_be_resolved_ends_undecided_not_raising(
    item_on, monkeypatch, fake_agent
):
    """Kraft-jzdyp code review round 1: `review()`'s own docstring promises it
    never raises for an agent that misbehaved -- a `SteeringError` from
    `resolve_agent_task` used to reach `deps.guard`'s generic catch instead of
    this clean `("undecided", note)`, and left `gate_auto_review_started`
    dangling with no terminal event for `_gate_review_attempts` to count."""
    from executor.test_policy_enforcement import NO_SETUP, _requested, _reviewed_gate

    it = await item_on(_reviewed_gate({}), auto_gate=True)
    launched = _never_launches(monkeypatch)
    await _requested(it)
    gate = it.chain.chain.nodes[1]
    _raising_resolver(monkeypatch)

    verdict, note = await gate_review.review(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        gate="spec_approval",
        node=gate,
        launch=NO_SETUP,
    )

    assert (verdict, launched) == ("undecided", [])
    assert "boom" in note
    _config_error_session(it, gate.auto_review.path, expect="boom")


@pytest.mark.parametrize("auto", [False, True], ids=["manual", "auto"])
async def test_an_escalation_whose_steering_cannot_be_resolved_stops_as_its_own_session(
    item_on, monkeypatch, fake_agent, auto
):
    """Kraft-jzdyp code review round 1: `escalate.dispatch` did not catch
    `SteeringError` next to `HarnessUnavailable`, so it also fell through to
    the walk's generic guard instead of the turn's own clean refusal."""
    from executor.test_policy_enforcement import _agent, _escalate, _node

    it = await item_on(_node(_agent()), "implementation")
    launched = _never_launches(monkeypatch)
    _raising_resolver(monkeypatch)

    assert await _escalate(it, auto) == CONFIG_ERROR

    assert launched == []
    _config_error_session(it, "escalation", expect="boom")
