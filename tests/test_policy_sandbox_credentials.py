"""`sandbox.credentials` (spec §6, rulings E1/E2): validated at load, frozen
and hashable, and under Ruling 105's equality lock whole."""

import pytest
import yaml
from pydantic import ValidationError

from kraft import policy

_NETWORK = {"runtime": {"allow": ["api.anthropic.com", "registry.example.com:443"]}}
_OWN = {
    "env": "REGISTRY_TOKEN",
    "service": "registry",
    "inject": [
        {"domain": "registry.example.com", "header": "authorization", "format": "Bearer %s"}
    ],
}
_SANDBOX = {
    "kind": "docker",
    "image": "kraft/worker:1",
    "network": _NETWORK,
    "credentials": [{"env": "ANTHROPIC_API_KEY"}, _OWN],
}


def _with(**changes) -> dict:
    return {**_SANDBOX, **changes}


def test_a_credential_by_name_or_in_full_loads_and_round_trips():
    """By name (the harness says how) or in full (the repository's own), and
    back out as written: frozen into snapshots, saved to repos.yaml, hashed
    by `meet`."""
    sandbox = policy.SandboxPolicy.model_validate(_SANDBOX)

    assert sandbox.model_dump() == _SANDBOX
    assert policy.SandboxPolicy.model_validate(yaml.safe_load(yaml.safe_dump(_SANDBOX))) == sandbox
    assert len({sandbox, policy.SandboxPolicy.model_validate(_SANDBOX)}) == 1
    without = policy.SandboxPolicy.model_validate(_with(credentials=[]))
    assert without.model_dump() == {k: v for k, v in _SANDBOX.items() if k != "credentials"}


def _inject(domain="registry.example.com", header="authorization", **extra) -> list[dict]:
    return [{**_OWN, "inject": [{"domain": domain, "header": header, **extra}]}]


@pytest.mark.parametrize(
    ("changes", "expect"),
    [
        ({"network": None}, "'credentials' needs 'network'"),
        ({"network": {}}, "'credentials' needs 'network'"),
        (
            {"network": {"runtime": {"allow": ["*.example.com"]}}},
            "does not allow by name in either phase",
        ),
        (
            {"network": {"runtime": {**_NETWORK["runtime"], "deny": ["registry.example.com"]}}},
            "does not allow by name in either phase",
        ),
        ({"credentials": _inject(domain="*.example.com")}, "is not an exact host"),
        ({"credentials": _inject(domain="registry.example.com:443")}, "is not an exact host"),
        ({"credentials": _inject(header="x api key")}, "is not an HTTP header name"),
        ({"credentials": _inject(format="Bearer")}, "must hold '%s' once"),
        ({"credentials": [{"env": "NOT-A-NAME"}]}, "is not an environment variable name"),
        ({"credentials": [{"env": "A"}, {"env": "A"}]}, "'A' is listed twice"),
        (
            {"credentials": [*_inject(), {**_inject()[0], "env": "OTHER"}]},
            "two credentials set header 'authorization'",
        ),
        ({"credentials": [{"env": "A", "header": "x"}]}, "Extra inputs are not permitted"),
    ],
    ids=[
        "no-network",
        "open-network",
        "only-a-wildcard",
        "denied",
        "wildcard-domain",
        "domain-with-port",
        "bad-header",
        "format-without-value",
        "bad-env",
        "env-twice",
        "one-header-twice",
        "unknown-key",
    ],
)
def test_a_credential_is_checked_at_load(changes, expect):
    """E1: a repository's own credential goes to a host its policy names, in
    a phase that allows it; every credential needs the proxy (`network:`)."""
    data = {k: v for k, v in _with(**changes).items() if v is not None}
    with pytest.raises(ValidationError, match=expect):
        policy.SandboxPolicy.model_validate(data)


@pytest.mark.parametrize(
    "credentials",
    [[{"env": "ANTHROPIC_API_KEY"}], [*_SANDBOX["credentials"], {"env": "X"}], []],
    ids=["dropped-one", "added-one", "dropped-all"],
)
def test_a_narrower_layer_cannot_change_inherited_credentials(credentials):
    changed = _with(credentials=credentials)
    instance = policy.InstancePolicy.from_input(policy.InstancePolicyInput.model_validate({}))
    managed = instance.apply_template_override({"sandbox": _SANDBOX})
    with pytest.raises(policy.PolicyError, match="sandbox"):
        managed.apply_template_override({"sandbox": changed})
