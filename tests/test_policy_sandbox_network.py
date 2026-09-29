"""`sandbox.network`: network-policy@1 host patterns, validated at load, frozen
and hashable, and under Ruling 105's equality lock whole."""

import pytest
from pydantic import ValidationError

from kraft import policy

_PLAIN = {"kind": "docker", "image": "kraft/worker:1"}
_POLICED = {**_PLAIN, "network": {"runtime": {"allow": ["api.github.com"]}}}


def _sandbox(host: str) -> policy.SandboxPolicy:
    return policy.SandboxPolicy.model_validate(
        {**_PLAIN, "network": {"install": {"allow": [host]}}}
    )


@pytest.mark.parametrize(
    "host",
    ["api.github.com", "api.github.com:443", "*.example.com", "*", "**", "10.0.0.5", " pypi.org "],
    ids=["exact", "host-port", "one-label-wildcard", "star", "star-star", "ip", "padded"],
)
def test_a_network_policy_host_is_accepted(host):
    assert _sandbox(host).network.install.allow == (host.strip(),)


@pytest.mark.parametrize(
    "host",
    ["10.0.0.0/8", ":443", "a.*.example.com", "**.example.com", "https://api.github.com", ""],
    ids=["cidr", "bare-port", "inner-wildcard", "star-star-prefix", "url", "empty"],
)
def test_a_malformed_host_is_refused_at_load(host):
    with pytest.raises(ValidationError, match="is not a network-policy@1 host"):
        _sandbox(host)


def test_a_sandbox_without_network_dumps_as_it_always_did():
    """Frozen into snapshots and saved back to repos.yaml: `network: {}` is no
    policy at all, so it must read (and lock) the same as no key."""
    empty = policy.SandboxPolicy.model_validate({**_PLAIN, "network": {}})
    assert empty == policy.SandboxPolicy.model_validate(_PLAIN)
    assert empty.model_dump() == _PLAIN
    assert policy.SandboxPolicy.model_validate(_POLICED).model_dump() == _POLICED


@pytest.mark.parametrize(
    "network",
    [{"runtime": {"allow": []}}, {"install": {}, "runtime": {"deny": []}}],
    ids=["empty-allow", "empty-phases"],
)
def test_an_explicit_empty_phase_is_a_policy_that_denies_everything(network):
    """Only a literal `network: {}` is no policy. A phase written out, even
    empty, is deny-everything, and must survive a dump and reload (a snapshot,
    repos.yaml) rather than collapse to open egress."""
    sandbox = policy.SandboxPolicy.model_validate({**_PLAIN, "network": network})
    assert sandbox.network is not None
    dumped = sandbox.model_dump()
    assert dumped["network"]
    assert policy.SandboxPolicy.model_validate(dumped).network is not None


@pytest.mark.parametrize(
    "network",
    [{"runtime": {"allow": ["**"]}}, None],
    ids=["loosened", "dropped"],
)
def test_a_narrower_layer_cannot_change_inherited_network(network):
    """`meet` hashes sandboxes (`dict.fromkeys`), so the lists must be tuples
    of a frozen model, not lists, or two repositories' sandboxes cannot meet."""
    changed = {**_PLAIN, **({"network": network} if network else {})}
    instance = policy.InstancePolicy.from_input(policy.InstancePolicyInput.model_validate({}))
    policed = instance.apply_template_override({"sandbox": _POLICED})
    with pytest.raises(policy.PolicyError, match="sandbox"):
        policed.apply_template_override({"sandbox": changed})
    with pytest.raises(policy.PolicyError, match="different sandboxes"):
        policy.TemplatePolicyOverride.meet(
            [
                policy.TemplatePolicyOverride(sandbox=_POLICED),
                policy.TemplatePolicyOverride(sandbox=changed),
            ]
        )
