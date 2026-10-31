"""`sandbox: {kind: os}`: the host's own agent CLI under the operating
system's process sandbox, reaching Kraft only through its session's channel."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from kraft import policy

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
        (["~/.ssh"], "protected"),
        ([], None),
        # A forge token file under an expose is refused at launch, never here: one token
        # file must not stop repos.yaml loading for every repo.
        (["/srv/shared"], None),
    ],
    ids=["relative", "protected", "empty", "holds-a-forge-token-file-loads"],
)
def test_expose_paths(expose, error, monkeypatch):
    from kraft.adapters.forge import credentials

    monkeypatch.setattr(credentials, "_SECRET_FILES", (Path("/srv/shared/tok"),))
    data = {**OS, "expose": expose}
    if error:
        with pytest.raises(ValidationError, match=error):
            policy.SandboxPolicy.model_validate(data)
    else:
        assert policy.SandboxPolicy.model_validate(data).model_dump().get("expose") == (
            expose or None
        )
