"""`sandbox.resources`: validated, frozen and hashable, and under Ruling 105's
equality lock whole -- a narrower layer can neither loosen nor tighten nor
drop a limit it inherits."""

import pytest
from pydantic import ValidationError

from kraft import policy

_LIMITED = {"kind": "docker", "image": "kraft/worker:1", "resources": {"memory": "4g", "pids": 512}}


def _sandbox(**resources):
    return policy.SandboxPolicy.model_validate({**_LIMITED, "resources": resources})


def test_resources_are_validated_and_normalised():
    sandbox = _sandbox(cpu=1.5, memory="512M", pids=64)
    assert sandbox.resources == policy.SandboxResources(cpu=1.5, memory="512m", pids=64)
    # Hashed by `scope_sandboxes` and `meet`.
    assert hash(sandbox) == hash(_sandbox(cpu=1.5, memory="512m", pids=64))


@pytest.mark.parametrize(
    ("resources", "refused"),
    [
        ({"memory": "lots"}, "is not a size"),
        ({"memory": "1m"}, "under 6m"),
        ({"memory": 512}, "valid string"),
        ({"cpu": 0}, "greater than 0"),
        ({"cpu": True}, "valid number"),
        ({"pids": 0}, "greater than or equal to 1"),
        ({"disk": "1g"}, "Extra inputs"),
    ],
    ids=[
        "memory-word",
        "memory-too-small",
        "memory-bare-int",
        "cpu-zero",
        "cpu-bool",
        "pids-0",
        "unknown-limit",
    ],
)
def test_a_malformed_limit_is_refused_at_load(resources, refused):
    with pytest.raises(ValidationError, match=refused):
        _sandbox(**resources)


def test_a_sandbox_without_resources_dumps_as_it_always_did():
    """Frozen into snapshots and saved back to repos.yaml: an unset field must
    not appear as `resources: null` in either."""
    plain = {"kind": "docker", "image": "kraft/worker:1"}
    assert policy.SandboxPolicy.model_validate(plain).model_dump() == plain
    assert _sandbox(memory="4g").model_dump()["resources"] == {"memory": "4g"}


@pytest.fixture
def instance_policy():
    return policy.InstancePolicy.from_input(policy.InstancePolicyInput.model_validate({}))


@pytest.mark.parametrize(
    "resources",
    [{"memory": "8g", "pids": 512}, {"memory": "1g", "pids": 512}, {"pids": 512}, None],
    ids=["loosened", "tightened", "dropped-one", "dropped-all"],
)
def test_a_narrower_layer_cannot_change_inherited_resources(instance_policy, resources):
    limited = instance_policy.apply_template_override({"sandbox": _LIMITED})
    assert limited.apply_template_override({"sandbox": _LIMITED}).sandbox == limited.sandbox
    changed = {k: v for k, v in _LIMITED.items() if k != "resources"}
    if resources is not None:
        changed["resources"] = resources
    with pytest.raises(policy.PolicyError, match="sandbox") as refused:
        limited.apply_template_override({"sandbox": changed})
    assert refused.value.field == "sandbox"


def test_repository_layers_with_different_resources_have_no_meet():
    with pytest.raises(policy.PolicyError, match="sandbox"):
        policy.TemplatePolicyOverride.meet(
            [
                policy.TemplatePolicyOverride(sandbox=_LIMITED),
                policy.TemplatePolicyOverride(sandbox={**_LIMITED, "resources": {"cpu": 1}}),
            ]
        )
