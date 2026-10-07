"""`sandbox: {kind: kit}`: a Kit pinned by digest, the single source of what
the sandbox reaches and holds, under Ruling 105's equality lock whole
(spec §9.3)."""

import pytest
import yaml
from pydantic import ValidationError

from kraft import policy
from kraft.templates.environment import WorkItemTarget
from kraft.templates.library import TemplateLibrary

DIGEST = "sha256:" + "a" * 64
KIT = {"kind": "kit", "runtime": "docker", "kit": f"registry.example.com/acme/kit@{DIGEST}"}


@pytest.mark.parametrize(
    ("ref", "loads"),
    [
        ("registry.example.com/acme/kit:1", False),
        ("registry.example.com/acme/kit", False),
        ("registry.example.com/acme/kit@sha256:" + "a" * 63, False),
        (f"localhost:5000/acme/kit:1@{DIGEST}", True),
    ],
    ids=["tag-only", "no-digest", "short-digest", "tag-and-digest"],
)
def test_a_kit_is_pinned_by_digest(ref, loads):
    """The digest is what fixes the descriptor (and what P8 diffs): a tag
    alone could name a different Kit tomorrow."""
    if loads:
        assert policy.SandboxPolicy.model_validate({**KIT, "kit": ref}).kit == ref
        return
    with pytest.raises(ValidationError, match="pinned by digest"):
        policy.SandboxPolicy.model_validate({**KIT, "kit": ref})


@pytest.mark.parametrize(
    "field",
    [
        {"image": "kraft/worker:1"},
        {"network": {"runtime": {"allow": ["x.io"]}}},
        {"resources": {"cpu": 2.0}},
        {"credentials": [{"env": "K"}]},
        {"unrestricted_network": True},
    ],
    ids=["image", "network", "resources", "credentials", "unrestricted-network"],
)
def test_a_kit_sandbox_takes_no_local_fields(field):
    """Open question 4: the enforced surface is exactly the Kit's."""
    with pytest.raises(ValidationError, match=f"kind: kit takes no '{next(iter(field))}'"):
        policy.SandboxPolicy.model_validate({**KIT, **field})


@pytest.mark.parametrize("field", ["runtime", "kit"])
def test_a_docker_sandbox_takes_no_kit_fields(field):
    with pytest.raises(ValidationError, match=f"kind: docker takes no '{field}'"):
        policy.SandboxPolicy.model_validate(
            {"kind": "docker", "image": "kraft/worker:1", field: KIT[field]}
        )


def test_a_kit_sandbox_names_its_runtime():
    with pytest.raises(ValidationError, match="needs 'runtime'"):
        policy.SandboxPolicy.model_validate({"kind": "kit", "kit": KIT["kit"]})


@pytest.mark.parametrize(
    "changed",
    [
        {**KIT, "kit": "registry.example.com/acme/kit@sha256:" + "b" * 64},
        {**KIT, "kit": f"registry.example.com/acme/kit:1@{DIGEST}"},
        {"kind": "docker", "image": KIT["kit"]},
    ],
    ids=["digest", "tag", "to-docker"],
)
def test_a_narrower_layer_cannot_change_the_kit(changed):
    """Ruling 105: the kind, the runtime and the reference are locked whole.
    `runtime` has one legal value, and leaving it out is refused at load."""
    instance = policy.InstancePolicy.from_input(policy.InstancePolicyInput.model_validate({}))
    kit = instance.apply_template_override({"sandbox": KIT})
    with pytest.raises(policy.PolicyError, match="sandbox"):
        kit.apply_template_override({"sandbox": changed})
    assert kit.apply_template_override({"sandbox": KIT}).sandbox == kit.sandbox


@pytest.mark.parametrize("mode", ["python", "json"])
def test_a_kit_sandbox_round_trips_as_written(mode):
    """`GET /repos` and the Settings save give back the three keys written,
    with no `image`, `network` or `resources` a reload would then refuse."""
    dumped = policy.SandboxPolicy.model_validate(KIT).model_dump(mode=mode)
    assert dumped == KIT
    assert policy.SandboxPolicy.model_validate(dumped) == policy.SandboxPolicy.model_validate(KIT)


@pytest.mark.parametrize("layer", ["chain", "library"])
def test_a_kit_in_a_chain_or_library_layer_is_the_same_value(tmp_path, layer):
    """The same model at every layer, so legal wherever a sandbox is, and
    frozen into the snapshot `item_sandbox` lowers (spec §9.3)."""
    task = {"kind": "agent", "harness": "codex", "prompt": "do it"}
    chain = {"id": "default", "nodes": [{"id": "n", "kind": "exec", "tasks": [{"id": "t"}]}]}
    if layer == "chain":
        chain["policy"] = {"sandbox": KIT}
        chain["nodes"][0]["tasks"][0].update(task)
    else:
        chain["nodes"][0]["tasks"][0]["extends"] = "base"
        task["policy"] = {"sandbox": KIT}
    (tmp_path / "chains").mkdir()
    (tmp_path / "library.yaml").write_text(yaml.safe_dump({"tasks": {"base": task}}))
    (tmp_path / "chains" / "default.yaml").write_text(yaml.safe_dump(chain))
    materialized = (
        TemplateLibrary.from_yaml_dir(tmp_path)
        .resolve_chain("default")
        .materialize(
            target=WorkItemTarget.for_repository("target"),
            effective_policy=policy.InstancePolicy.from_input(policy.InstancePolicyInput()),
        )
    )
    assert materialized.item_sandbox() == policy.SandboxPolicy.model_validate(KIT)
