"""`kraft.worker.kit`: decoding, claiming and lowering a Kit descriptor.
The conformance set is `tests/fixtures/kit/<SPEC_TAG's milestone>/`."""

import json
from pathlib import Path

import pytest
import yaml

from kraft.worker import kit

FIXTURES = Path(__file__).parents[1] / "fixtures" / "kit" / "m7"
EXPECTED = yaml.safe_load((FIXTURES / "expected.yaml").read_text())
REF = "registry.example.com/acme/kraft-worker@sha256:" + "a" * 64

NETWORK = {"type": kit.NETWORK, "config": {"runtime": {"allow": ["api.example.com"]}}}
CREDENTIAL = {
    "type": kit.CREDENTIAL,
    "config": {
        "service": "example",
        "phase": "runtime",
        "apiKey": {
            "name": "EXAMPLE_KEY",
            "proxyManaged": True,
            "inject": [{"domain": "api.example.com", "header": "x-api-key"}],
        },
    },
}


def _kit(*capabilities: dict, network: bool = True, **top) -> str:
    """A workload's compact JSON descriptor: `NETWORK` first unless told not."""
    caps = [NETWORK, *capabilities] if network else list(capabilities)
    return json.dumps({"schemaVersion": "3", "kind": "workload", "capabilities": caps, **top})


def _credential(**config) -> dict:
    return {**CREDENTIAL, "config": {**CREDENTIAL["config"], **config}}


def _api_key(**key) -> dict:
    return _credential(apiKey={**CREDENTIAL["config"]["apiKey"], **key})


def test_the_conformance_set_is_all_expected():
    """No fixture sits in the directory without an expected outcome."""
    assert {p.stem for p in (FIXTURES / "upstream").glob("*.yaml")} == set(EXPECTED["upstream"])
    assert {p.stem for p in (FIXTURES / "kraft").iterdir()} == set(EXPECTED["kraft"])


@pytest.mark.parametrize("fixture", sorted(EXPECTED["upstream"]))
def test_the_upstream_capability_lists(fixture):
    descriptor = kit.decode((FIXTURES / "upstream" / f"{fixture}.yaml").read_text())
    expected = EXPECTED["upstream"][fixture]
    if "refused" in expected:
        with pytest.raises(kit.KitRefused, match=f"^requires {expected['refused']}"):
            kit.capability_claims(descriptor.capabilities)
        return
    found = kit.capability_claims(descriptor.capabilities)
    assert json.loads(found.model_dump_json(by_alias=True, exclude_defaults=True)) == expected


@pytest.mark.parametrize("fixture", sorted(EXPECTED["kraft"]))
def test_a_workload_kit_claims_what_it_declares(fixture):
    """Through the whole `decode`, `claims` and `lower`, from JSON (the
    published form) and YAML (Kits published before it): the image is the
    Kit, network phase for phase with both written out, `2gib` as `2g`,
    each credential its phases and bound `source` and no sentinel, and what
    was skipped or ignored recorded."""
    (path,) = (FIXTURES / "kraft").glob(f"{fixture}.*")
    expected = EXPECTED["kraft"][fixture]
    lowered = kit.lower(REF, kit.decode(path.read_text()), expected["bindings"])
    assert lowered.kit == REF
    assert {
        "policy": lowered.policy.model_dump(mode="json"),
        "skipped": list(lowered.skipped),
        "ignored": list(lowered.ignored),
    } == {k: expected[k] for k in ("policy", "skipped", "ignored")}


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        (_kit(extra=1), r"^extra: Extra inputs"),
        (
            _kit({"type": kit.NETWORK, "config": {"runtime": {"allowed": []}}}, network=False),
            r"^capabilities\.0\.config\.runtime\.allowed: Extra inputs",
        ),
        (_kit({"type": "Example/thing@1", "optional": True}), r"^capabilities\.1\.type: String"),
        (_kit({"type": kit.RESOURCES}, {"type": kit.RESOURCES, "config": {"cpu": 1}}), "also at"),
        ('{"schemaVersion": "3", "kind": "workload", "kind": "mixin"}', "JSON descriptor repeats"),
        ("schemaVersion: '3'\nkind: workload\nkind: mixin\n", "YAML descriptor repeats"),
        (
            _kit(CREDENTIAL, _credential(phase=["install", "runtime"])),
            "credential 'example' in phase runtime is also at",
        ),
        (
            _kit({"type": kit.CREDENTIAL, "config": {"service": "x", "apiKey": {"name": "X"}}}),
            "phase",
        ),
        (
            _kit(_credential(phase="install")),
            r"inject\.0\.domain: 'api\.example\.com' is not in the network policy's install",
        ),
        (_kit().replace('"3"', '"2"', 1), r"^schemaVersion: Input should be '3'"),
        (_kit(description="x" * kit.MAX_DESCRIPTOR), "over 512 KiB"),
        ("schemaVersion: '3'\nkind: &k workload\ndisplayName: *k\n", "uses an alias"),
        ("[" * 100_000, "nested too deeply"),
        (_kit({"type": kit.CREDENTIAL, "config": {"service": "x", "phase": "runtime"}}), "neither"),
        (_kit({"type": "com.docker.sandbox/sbx@1", "config": {}}), "sbx@1 takes no config"),
        (_kit(*[{"type": "example.com/thing@1", "optional": True}] * 2), "repeats capabilities.1"),
        (
            _kit({"type": "com.docker.sandbox/network-policy@2", "optional": True}),
            "network-policy@1 and network-policy@2",
        ),
    ],
    ids=[
        "unknown-top-level-key",
        "unknown-key-in-a-claimed-config",
        "bad-type",
        "singleton-twice",
        "duplicate-json-key",
        "duplicate-yaml-key",
        "overlapping-service-phase",
        "credential-without-phase",
        "inject-domain-not-allowed",
        "schema-version-2",
        "oversized",
        "yaml-alias",
        "nested-too-deep",
        "credential-with-neither-apikey-nor-oauth",
        "config-on-a-configless-type",
        "exact-duplicate",
        "network-policy-1-and-2",
    ],
)
def test_a_descriptor_decodes_strictly(text, refusal):
    with pytest.raises(kit.KitRefused, match=refusal):
        kit.decode(text)


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        (_kit({"type": "com.docker.sandbox/lifecycle@1", "config": {}}), "lifecycle@1;"),
        (_kit({"type": "com.docker.sandbox/sbx@1"}), "sbx@1;"),
        (
            _kit(
                {"type": "com.docker.sandbox/network-policy@2", "config": {}},
                network=False,
            ),
            "network-policy@2;",
        ),
        (
            _kit(
                {
                    "type": kit.CREDENTIAL,
                    "config": {
                        "service": "example",
                        "phase": "runtime",
                        "oauth": {"passthrough": False},
                    },
                }
            ),
            "example: oauth only",
        ),
        (_kit(_api_key(name="")), "example: inject-only"),
        (
            _kit(
                _api_key(inject=[{"domain": "api.example.com", "scheme": "basic", "username": "u"}])
            ),
            "example: injects by scheme",
        ),
        (_kit(_api_key(proxyManaged=False)), "example: not proxyManaged"),
        (_kit({"type": kit.RESOURCES, "config": {"gpu": "1"}}), "resources@1 gpu;"),
        (_kit(kind="mixin"), "kind: mixin"),
        (_kit(requires=["node >= 20"]), r"requires \['node >= 20'\]"),
        (_kit({"group": {"name": "Feature", "capabilities": []}}), "capability group 'Feature'"),
        (
            _kit(
                {"type": kit.NETWORK, "config": {"runtime": {"allow": ["${{ kit.args.host }}"]}}},
                network=False,
            ),
            r"capabilities\.0\.config\.runtime\.allow\.0 holds a `\$\{\{` reference",
        ),
        (
            _kit(
                {
                    "type": "example.com/thing@1",
                    "optional": True,
                    "config": {"p": "${{kit.env.HOME}}"},
                }
            ),
            r"capabilities\.1\.config\.p holds",
        ),
        (
            _kit(
                {"type": "example.com/thing@1", "optional": True, "config": {"${{kit.args.k}}": 1}}
            ),
            r"capabilities\.1\.config\.\$\{\{kit\.args\.k\}\} holds",
        ),
        (_kit(args={"level": {"default": "1", "env": "LEVEL"}}), "arg 'level' exports LEVEL"),
        (
            _kit({"type": kit.RESOURCES, "config": {"cpu": 1}}, network=False),
            "declares no com.docker",
        ),
    ],
    ids=[
        "lifecycle",
        "sbx",
        "network-policy-2",
        "oauth-only",
        "inject-only",
        "basic-scheme",
        "not-proxy-managed",
        "gpu",
        "mixin",
        "requires",
        "group",
        "arg-reference",
        "env-reference",
        "reference-in-a-key",
        "args-with-env-export",
        "no-network-policy",
    ],
)
def test_what_kraft_does_not_enforce_is_refused_by_name(text, refusal):
    with pytest.raises(kit.KitRefused, match=refusal):
        kit.claims(kit.decode(text))


def test_an_optional_capability_kraft_does_not_enforce_is_skipped_and_recorded():
    """Skipped, and the rest of the Kit still applies: an optional
    resources@1's gpu is dropped but its cpu and memory hold."""
    text = _kit(
        {"type": "example.com/thing@1", "optional": True, "config": {"a": 1}},
        _api_key(proxyManaged=False) | {"optional": True},
        {"type": kit.RESOURCES, "optional": True, "config": {"gpu": "all", "memory": "1g"}},
    )
    lowered = kit.lower(REF, kit.decode(text), {})
    assert lowered.skipped == (
        "example.com/thing@1",
        f"{kit.CREDENTIAL} example: not proxyManaged",
        f"{kit.RESOURCES} gpu",
    )
    assert lowered.policy.credentials is None
    assert lowered.policy.resources.memory == "1g"


def test_a_required_credential_with_no_binding_refuses_the_kit():
    """credential@1: a required entry with no binding fails resolution.
    The refusal names the binding to add."""
    with pytest.raises(kit.KitRefused, match="'example' has no binding: add `example: <DAEMON"):
        kit.lower(REF, kit.decode(_kit(CREDENTIAL)), {"other": "OTHER"})


def test_cpu_zero_is_no_limit():
    """resources@1: unset means no constraint, and `cpu: 0` is how a Kit
    says it; `SandboxResources` refuses a zero limit."""
    lowered = kit.lower(REF, kit.decode(_kit({"type": kit.RESOURCES, "config": {"cpu": 0}})), {})
    assert lowered.policy.resources is None


@pytest.mark.parametrize("memory", ["2gb", "1.5g"])
def test_a_memory_spelling_kraft_cannot_read_refuses_the_kit(memory):
    text = _kit({"type": kit.RESOURCES, "config": {"memory": memory}})
    with pytest.raises(
        kit.KitRefused,
        match=rf"^lowers to a sandbox Kraft refuses: resources\.memory: memory '{memory}' is not",
    ):
        kit.lower(REF, kit.decode(text), {})


def test_the_documented_worker_kit_lowers_as_the_guide_says():
    """The guide's descriptor is the fixture `test_a_workload_kit_claims_what_
    it_declares` lowers, so what the docs show is what is pinned."""
    guide = (
        Path(__file__).parents[2] / "docsite/content/3.guides/3.harnesses/3.worker-kit.md"
    ).read_text()
    shown = next(
        block.removeprefix("yaml\n")
        for block in guide.split("```")
        if block.startswith("yaml\n# syntax=docker/sandbox-kit:3")
    )
    fixture = FIXTURES / "kraft" / "egress-credential-resources.yaml"
    assert kit.decode(shown) == kit.decode(fixture.read_text())
