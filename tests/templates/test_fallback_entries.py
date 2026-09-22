"""An agent task's `fallback:` list as chain content (Kraft-0a3h8): the entry's
shape, the candidates it resolves to, and the policy it may never escape."""

from __future__ import annotations

import re

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


def test_a_gate_review_task_refuses_a_fallback_list():
    """`gate_review` launches its reviewer once and never walks a list."""
    reviewer = {"id": "r", "kind": "agent", "harness": "claude", "prompt": "review"}
    gate = {"id": "g", "kind": "gate", "auto_review": reviewer}
    Chain.model_validate({"id": "c", "nodes": [gate]})
    reviewer["fallback"] = [{"model": "sonnet"}]
    with pytest.raises(ValidationError, match="a gate review never falls back"):
        Chain.model_validate({"id": "c", "nodes": [gate]})


# -- profile routes (agent profiles, Kraft-ps1ao) ------------------------------

PROFILES = {
    "deep": {
        "effort": "high",
        "model": {"claude": "opus", "codex": "gpt-5.6-sol"},
        "fallback": [{"harness": "codex"}, {"profile": "strong"}],
    },
    "strong": {
        "effort": "high",
        "model": {"claude": "sonnet", "codex": "gpt-5.6-terra"},
        "fallback": [{"model": "haiku"}],
    },
}


def _table(profiles=PROFILES):
    from kraft import harness as _harness
    from kraft.templates.environment import HarnessProfileTable

    return HarnessProfileTable.from_mapping(
        {"harnesses": {"claude": {"provider": "claude"}, "codex": {"provider": "codex"}},
         "profiles": profiles},
        "harnesses.yaml",
        harnesses=_harness.load(None).valid,
    )  # fmt: skip


def _route(t: AgentTask) -> tuple:
    return (t.harness, t.profile, t.model, t.effort)


@pytest.mark.parametrize(
    ("entry", "on_profile", "on_model"),
    [
        ({"harness": "codex"}, ("codex", "deep", None, None), ("codex", None, "opus", "high")),
        ({"profile": "strong"}, ("claude", "strong", None, None), ("claude", "strong", None, None)),
        ({"model": "sonnet"}, ("claude", None, "sonnet", None), ("claude", None, "sonnet", "high")),
    ],
    ids=["harness", "profile", "model"],
)
def test_each_entry_shape_against_both_primary_routes(entry, on_profile, on_model):
    """The spec's table: an entry's route replaces a profile route whole, and
    merges per field into a field route."""
    by_profile = _task(profile="deep", fallback=[entry])
    by_model = _task(model="opus", effort="high", fallback=[entry])
    assert _route(fallback.candidates(by_profile)[1]) == on_profile
    assert _route(fallback.candidates(by_model)[1]) == on_model


def test_a_profile_task_takes_its_profiles_list_and_lists_do_not_chain():
    """deep's list names `strong`, whose own list (`haiku`) is not followed."""
    cands = fallback.candidates(_task(profile="deep"), _table())
    assert [_route(c) for c in cands] == [
        ("claude", "deep", None, None),
        ("codex", "deep", None, None),
        ("claude", "strong", None, None),
    ]


@pytest.mark.parametrize(
    ("own", "expected"),
    [([{"model": "sonnet"}], [("claude", None, "sonnet", None)]), ([], [])],
    ids=["replaces", "disables"],
)
def test_a_tasks_own_list_replaces_its_profiles(own, expected):
    cands = fallback.candidates(_task(profile="deep", fallback=own), _table())
    assert [_route(c) for c in cands[1:]] == expected


def test_an_entry_with_a_profile_and_a_model_is_refused():
    with pytest.raises(ValidationError, match="one or the other"):
        _task(fallback=[{"profile": "deep", "model": "opus"}])


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ({"profile": "gone"}, "profiles.deep.fallback[0]: profile 'gone' is not defined"),
        ({"profile": "strong", "effort": "low"}, "one or the other"),
        ({"retries": 2}, "profiles.deep"),
        ({}, "would relaunch the same thing"),
    ],
    ids=["unknown-profile", "both-routes", "unknown-key", "empty"],
)
def test_a_bad_profile_list_is_refused_when_harnesses_yaml_loads(entry, why):
    from kraft.templates.environment import TemplateEnvironmentError

    profiles = {**PROFILES, "deep": {**PROFILES["deep"], "fallback": [entry]}}
    with pytest.raises(TemplateEnvironmentError, match=re.escape(why)):
        _table(profiles)
