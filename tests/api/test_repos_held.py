"""A repo's commands run for every work item on it, so turning on commands an
agent chose is a person's call (#1198). A sibling of test_repos.py."""

from __future__ import annotations

import pytest
from support.harness import make_repo

from kraft.api.routes.repos import HELD

_CALLERS = {
    "assistant": {"x-kraft-client": "mcp"},
    "worker": {"x-kraft-session-id": "w-o/abc123"},
    "person": {},
}


@pytest.mark.parametrize(
    ("caller", "body", "enabled"),
    [
        ("assistant", {"test_command": "make check"}, False),
        ("worker", {"setup_command": "./configure"}, False),
        ("assistant", {"test_command": "make check", "enabled": True}, False),
        ("assistant", {"test_command": ""}, False),
        ("assistant", {"test_scopes": [{"paths": ["web/**"], "command": "npm test"}]}, False),
        ("assistant", {"test_command": "pytest", "setup_command": ""}, False),
        ("assistant", {"setup_command": ""}, None),
        ("person", {"test_command": "make check", "setup_command": "./configure"}, True),
    ],
    ids=[
        "assistant-test-command",
        "worker-setup-command",
        "assistant-asks-to-enable",
        "assistant-declares-no-tests",
        "assistant-test-scopes",
        "assistant-test-command-and-no-setup",
        "assistant-says-nothing-to-prepare",
        "person-commands",
    ],
)
def test_a_repo_an_agent_connects_with_its_own_commands_waits_for_a_person(
    client, tmp_path, caller, body, enabled
):
    """Commands an agent chose save the repo disabled, saying why; Kraft's own
    proposal, and a person's commands, connect as before. `enabled` None:
    whatever the probe's proposal decides, never held."""
    repo = make_repo(tmp_path)

    r = client.post("/api/repos", json={"path": str(repo), **body}, headers=_CALLERS[caller])

    assert r.status_code == 201, r.text
    if enabled is None:
        assert "held" not in r.json()
        return
    assert r.json()["enabled"] is enabled
    assert client.get("/api/repos").json()["repos"][0]["enabled"] is enabled
    assert ("held" in r.json()) is (not enabled)
    if not enabled:
        assert r.json()["held"] == HELD


@pytest.mark.parametrize(
    ("caller", "patch", "status"),
    [
        ("assistant", {"enabled": True}, 403),
        ("worker", {"enabled": True, "test_command": "pytest"}, 403),
        ("assistant", {"enabled": False}, 200),
        ("person", {"enabled": True}, 200),
    ],
    ids=["assistant-enables", "worker-enables", "assistant-disables", "person-enables"],
)
def test_turning_a_repo_on_is_a_persons_call(client, tmp_path, caller, patch, status):
    repo = make_repo(tmp_path)
    held = client.post(
        "/api/repos", json={"path": str(repo), "test_command": "pytest"}, headers=_CALLERS["worker"]
    ).json()

    r = client.patch(f"/api/repos?path={held['path']}", json=patch, headers=_CALLERS[caller])

    assert r.status_code == status, r.text
    stored = client.get("/api/repos").json()["repos"][0]
    assert stored["enabled"] is (status == 200 and patch["enabled"])
    if status == 403:
        assert "enabling a repo is a person's call" in r.json()["detail"]
