"""An agent task's `fallback:` list as chain content (Kraft-0a3h8): the entry's
shape, the candidates it resolves to, and the policy it may never escape."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kraft.executor import fallback
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError
from kraft.templates.environment import WorkItemTarget
from kraft.templates.models import AgentTask, Chain, ResolvedChain


def _task(**kw) -> AgentTask:
    return AgentTask.model_validate(
        {"id": "t", "kind": "agent", "harness": "claude", "prompt": "do it", **kw}
    )


@pytest.mark.parametrize(
    "entry",
    [{}, {"harness": "codex", "retries": 2}, {"model": 5}],
    ids=["empty", "unknown-key", "not-a-string"],
)
def test_a_malformed_entry_is_refused_at_load(entry):
    with pytest.raises(ValidationError):
        _task(fallback=[entry])


def test_an_empty_entry_says_why():
    with pytest.raises(ValidationError, match="would relaunch the same thing"):
        _task(fallback=[{}])


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"harness": "codex"}, ("codex", "opus", "high")),
        ({"model": "sonnet"}, ("claude", "sonnet", "high")),
        ({"effort": "low"}, ("claude", "opus", "low")),
        ({"harness": "codex", "model": "gpt-5.6-terra"}, ("codex", "gpt-5.6-terra", "high")),
    ],
    ids=["harness", "model", "effort", "harness-and-model"],
)
def test_an_entry_keeps_what_it_omits_from_the_task(entry, expected):
    task = _task(model="opus", effort="high", fallback=[entry])

    primary, cand = fallback.candidates(task)

    assert primary is task
    assert (cand.harness, cand.model, cand.effort) == expected
    assert cand.fallback is None


@pytest.mark.parametrize("fallback_list", [None, []], ids=["unset", "empty"])
def test_no_list_is_the_task_alone(fallback_list):
    task = _task(fallback=fallback_list)
    assert fallback.candidates(task) == [task]


def _materialize(task: dict, policy: dict):
    chain = Chain.model_validate(
        {"id": "c", "policy": policy, "nodes": [{"id": "n", "kind": "exec", "tasks": [task]}]}
    )
    return ResolvedChain.from_chain(chain).materialize(
        target=WorkItemTarget.for_repository("target"),
        effective_policy=InstancePolicy.from_input(InstancePolicyInput.model_validate({})),
    )


def test_a_fallback_harness_outside_allowed_harnesses_fails_materialization():
    """`fallback-never-escapes-allowed-harnesses`."""
    task = {
        "id": "t",
        "kind": "agent",
        "harness": "claude",
        "prompt": "do it",
        "fallback": [{"model": "sonnet"}, {"harness": "codex"}],
    }

    with pytest.raises(PolicyError, match=r"fallback harness 'codex' is not in") as refused:
        _materialize(task, {"allowed_harnesses": ["claude"]})
    assert refused.value.field == "allowed_harnesses"
    # Inside the policy, the same list materializes.
    assert _materialize(task, {"allowed_harnesses": ["claude", "codex"]}) is not None
