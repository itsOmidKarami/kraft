"""Named grants in policy (Kraft-4in7z): operations Kraft guarantees a task,
never command patterns. They accumulate down layers like deny_tools, and a
workspace's meet intersects them."""

import pytest
from support.harness import write_harness_profiles

from kraft import policy as p
from kraft.adapters import agent
from kraft.templates.models import AgentTask


def _base() -> p.InstancePolicy:
    return p.InstancePolicy.from_input(p.InstancePolicyInput.model_validate({}))


def test_an_unknown_grant_is_refused_naming_the_known_ones():
    with pytest.raises(ValueError, match="git-push"):
        p.TaskPolicyOverride.model_validate({"grants": ["git-force-push"]})


def test_grants_accumulate_down_the_layers():
    outer = _base().apply_template_override({"grants": ["git-commit"]})
    inner = outer.apply_template_override({"grants": ["git-push", "git-commit"]})
    assert inner.grants == ("git-commit", "git-push")
    assert inner.apply_template_override({"grants": []}).grants == ("git-commit", "git-push")


def test_a_workspace_meet_intersects_grants():
    a = p.TemplatePolicyOverride.model_validate({"grants": ["git-commit", "git-push"]})
    b = p.TemplatePolicyOverride.model_validate({"grants": ["git-commit"]})
    assert p.TemplatePolicyOverride.meet([a, b]).grants == ["git-commit"]


def test_a_launch_carries_its_tasks_grants(tmp_path, monkeypatch):
    write_harness_profiles(tmp_path, {"claude": {"provider": "claude"}})
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(tmp_path))
    task = AgentTask.model_validate(
        {"id": "t", "kind": "agent", "prompt": "p", "harness": "claude"}
    )
    policy = _base().apply_template_override({"grants": ["git-rebase"]})
    assert agent.resolve_agent_task(task, None, None, policy=policy).grants == ("git-rebase",)
