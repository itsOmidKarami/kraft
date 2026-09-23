"""`escalation_harness`: the profile an escalation turn runs on, a policy field
like any operational one (Kraft-wge0e). Split from test_policy.py for its line
budget."""

from __future__ import annotations

import pytest
from support.harness import write_harness_profiles

from kraft.policy import (
    InstancePolicy,
    InstancePolicyInput,
    PolicyError,
    PolicyInput,
    TemplatePolicyOverride,
    WorkItemPolicy,
)


def _instance(**defaults) -> InstancePolicy:
    return InstancePolicy.from_input(InstancePolicyInput.model_validate({"defaults": defaults}))


def test_escalation_harness_layers_like_any_operational_field():
    """Unset is `claude`, what it always was; `defaults:` sets it; a node's
    layer and then the item's own override replace it in turn."""
    assert _instance().escalation_harness == "claude"
    base = _instance(escalation_harness="cx")
    assert base.escalation_harness == "cx"
    node = base.apply_template_override(TemplatePolicyOverride(escalation_harness="item"))
    assert node.escalation_harness == "item"
    item = WorkItemPolicy(escalation_harness="gem").apply_to(node, "implementation")
    assert item.escalation_harness == "gem"


def test_repositories_that_name_different_escalation_harnesses_do_not_meet():
    """A workspace item escalates on one harness, so two repositories naming
    two are refused, as two sandboxes are; agreeing ones meet."""
    same = TemplatePolicyOverride.meet(
        [TemplatePolicyOverride(escalation_harness="cx"), TemplatePolicyOverride()]
    )
    assert same.escalation_harness == "cx"
    with pytest.raises(PolicyError) as refused:
        TemplatePolicyOverride.meet(
            [
                TemplatePolicyOverride(escalation_harness="cx"),
                TemplatePolicyOverride(escalation_harness="claude"),
            ]
        )
    assert refused.value.field == "escalation_harness"


@pytest.mark.parametrize("value", ["cx", "item"])
def test_policy_yaml_takes_a_known_profile_or_item(tmp_path, monkeypatch, value):
    templates = tmp_path / "templates"
    write_harness_profiles(templates, {"cx": {"provider": "codex"}})
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    path = templates / "policy.yaml"
    path.write_text(
        f"default: {{attempts: 3, wall_clock_s: 60}}\ndefaults: {{escalation_harness: {value}}}\n"
    )

    parsed = PolicyInput.from_yaml(path)

    assert parsed.instance_policy().escalation_harness == value


def test_policy_yaml_refuses_an_unknown_escalation_harness_naming_the_known(tmp_path, monkeypatch):
    """Refused when the file is read, not when the first item gets stuck."""
    templates = tmp_path / "templates"
    write_harness_profiles(templates, {"cx": {"provider": "codex"}})
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
    path = templates / "policy.yaml"
    path.write_text(
        "default: {attempts: 3, wall_clock_s: 60}\ndefaults: {escalation_harness: gemini}\n"
    )

    with pytest.raises(PolicyError) as refused:
        PolicyInput.from_yaml(path)

    assert "'gemini'" in str(refused.value)
    assert "['cx']" in str(refused.value)
