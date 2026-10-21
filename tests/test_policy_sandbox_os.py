"""`sandbox: {kind: os}`: the host's own agent CLI under the operating
system's process sandbox, reaching Kraft only through its session's channel."""

import pytest
import yaml
from pydantic import ValidationError

from kraft import policy
from kraft.worker import callback

OS = {"kind": "os", "network": {"runtime": {}}}


@pytest.mark.parametrize(
    "field",
    [
        {"image": "kraft/worker:1"},
        {"runtime": "docker"},
        {"kit": "registry.example.com/acme/kit@sha256:" + "a" * 64},
        {"resources": {"cpu": 2.0}},
        {"unrestricted_network": True},
        {"credentials": [{"env": "K"}]},
    ],
    ids=["image", "runtime", "kit", "resources", "unrestricted-network", "credentials"],
)
def test_os_refuses(field):
    with pytest.raises(ValidationError, match=f"kind: os takes no '{next(iter(field))}'"):
        policy.SandboxPolicy.model_validate({**OS, **field})


@pytest.mark.parametrize(
    "data",
    [{"kind": "os"}, {"kind": "os", "network": {}}],
    ids=["absent", "network-empty"],
)
def test_os_needs_network(data):
    with pytest.raises(ValidationError, match="kind: os needs 'network'"):
        policy.SandboxPolicy.model_validate(data)


def test_os_round_trips():
    loaded = yaml.safe_load("kind: os\nnetwork: {runtime: {}}\nexpose: ['~/.cargo/bin']\n")
    dumped = policy.SandboxPolicy.model_validate(loaded).model_dump()
    assert dumped["expose"] == ["~/.cargo/bin"] and isinstance(dumped["expose"], list)
    assert yaml.safe_load(yaml.safe_dump(dumped)) == dumped


@pytest.mark.parametrize(
    ("expose", "error"),
    [
        (["x"], "'x' is not an absolute or ~/ path"),
        ([], None),
    ],
    ids=["relative", "empty"],
)
def test_expose_paths(expose, error):
    data = {**OS, "expose": expose}
    if error:
        with pytest.raises(ValidationError, match=error):
            policy.SandboxPolicy.model_validate(data)
    else:
        assert "expose" not in policy.SandboxPolicy.model_validate(data).model_dump()


def test_the_worker_api_of_an_os_worker_is_the_proxy_host():
    assert callback.address("os") == "http://kraft"
