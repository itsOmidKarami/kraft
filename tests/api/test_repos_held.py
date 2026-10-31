"""A repo's commands run for every work item on it, so turning on commands an
agent chose is a person's call (#1198). A sibling of test_repos.py."""

from __future__ import annotations

import pytest
from support.harness import make_repo, make_repo_with_submodule

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
        ("worker", {"setup_command": "./configure"}, 403),
        ("on:worker", {"setup_command": "curl x | sh"}, 403),
        ("on:assistant", {"test_scopes": [{"paths": ["**"], "command": "x"}]}, 403),
        ("on:assistant", {"name": "renamed"}, 200),
        ("on:person", {"setup_command": "./configure"}, 200),
        ("worker", {"forge_account": "corp"}, 403),
        ("on:assistant", {"forge_account": None}, 403),
    ],
    ids=[
        "assistant-enables",
        "worker-enables",
        "assistant-disables",
        "person-enables",
        "worker-sets-commands-on-a-held-repo",
        "worker-sets-commands-on-an-enabled-repo",
        "assistant-sets-scopes-on-an-enabled-repo",
        "assistant-renames-an-enabled-repo",
        "person-sets-commands-on-an-enabled-repo",
        "worker-names-a-forge-account",
        "assistant-clears-a-forge-account",
    ],
)
def test_turning_a_repo_on_is_a_persons_call(client, tmp_path, caller, patch, status):
    """And so is setting a connected repo's commands, enabled (`on:`) or not:
    a disabled repo's open items, filed by hand, would run them too."""
    repo = make_repo(tmp_path)
    on = caller.startswith("on:")
    held = client.post(
        "/api/repos",
        json={"path": str(repo), "test_command": "pytest"},
        headers={} if on else _CALLERS["worker"],
    ).json()

    r = client.patch(
        f"/api/repos?path={held['path']}", json=patch, headers=_CALLERS[caller.removeprefix("on:")]
    )

    assert r.status_code == status, r.text
    stored = client.get("/api/repos").json()["repos"][0]
    assert stored["enabled"] is (on or (status == 200 and patch.get("enabled", False)))
    if status == 403:
        assert "a person's" in r.json()["detail"]
        assert all(stored.get(k) == held.get(k) for k in patch if k != "enabled")


@pytest.mark.parametrize(
    ("door", "caller", "enabled", "status"),
    [
        ("create", "assistant", False, 403),
        ("create", "worker", False, 403),
        ("dry-run", "assistant", False, 403),
        ("trigger", "assistant", False, 403),
        ("duplicate", "assistant", False, 403),
        ("create", "person", False, 201),
        ("create", "assistant", True, 201),
        ("no-tests", "assistant", False, 403),
    ],
    ids=[
        "assistant-files",
        "worker-files",
        "assistant-previews",
        "assistant-triggers",
        "assistant-duplicates",
        "person-files",
        "assistant-files-on-an-enabled-repo",
        "assistant-files-on-a-repo-with-no-test-command",
    ],
)
def test_an_agent_files_no_work_on_a_disabled_repo(client, tmp_path, door, caller, enabled, status):
    """A disabled repo's commands have not been turned on by a person, so an
    agent cannot file work that would run them; a person may still file by
    hand. Without this the hold above stops nothing: `enabled` gates only
    auto-intake."""
    repo = make_repo(tmp_path)
    tests = {} if door == "no-tests" else {"test_command": "true"}
    path = client.post("/api/repos", json={"path": str(repo), **tests, "enabled": enabled}).json()[
        "path"
    ]
    item = {"title": "t", "repo": path, "autostart": False}
    headers = _CALLERS[caller]
    if door == "duplicate":
        wid = client.post("/api/work-items", json=item).json()["id"]
        r = client.post(f"/api/work-items/{wid}/duplicate", headers=headers)
    elif door == "trigger":
        r = client.post("/api/triggers", json={"title": "t", "repo": path}, headers=headers)
    else:
        url = "/api/work-items" + ("?dry_run=true" if door == "dry-run" else "")
        r = client.post(url, json=item, headers=headers)

    assert r.status_code == status, r.text
    if status == 403:
        needs = "sets its test command and " if door == "no-tests" else ""
        assert f"is disabled: a person {needs}enables it in Settings › Repos" in r.json()["detail"]


@pytest.mark.parametrize("shape", ["auto-connected-child", "connected-before-its-root"])
def test_an_agent_files_no_work_on_a_workspace_with_a_disabled_member(client, tmp_path, shape):
    """A member runs its own test command at verification, so a disabled one
    stops an agent's workspace item as a disabled root would; a person may
    still file it by hand. A member connected on its own first (Kraft-d7aj3)
    lives at its own path, not under the root: it is found by id."""
    root, member = make_repo_with_submodule(tmp_path, submodule_path="libs/a")
    if shape == "connected-before-its-root":
        held = {"path": str(member), "test_command": "evil"}
        assert client.post("/api/repos", json=held, headers=_CALLERS["assistant"]).json()["held"]
    client.post("/api/repos", json={"path": str(root), "test_command": "true"})
    body = client.get("/api/repos").json()
    ((ws_id, ws),) = body["workspaces"].items()
    [member_id] = ws["members"]
    item = {"title": "t", "repo": str(root), "autostart": False, "workspace": ws_id}
    agent = _CALLERS["assistant"]

    refused = client.post("/api/work-items", json={**item, "members": [member_id]}, headers=agent)
    root_only = client.post("/api/work-items", json={**item, "members": []}, headers=agent)
    by_hand = client.post("/api/work-items", json={**item, "members": [member_id]})

    assert refused.status_code == 403, refused.text
    assert "is disabled: a person" in refused.json()["detail"]
    assert (root_only.status_code, by_hand.status_code) == (201, 201), root_only.text


@pytest.mark.parametrize(
    ("caller", "started", "status"),
    [("assistant", False, 403), ("person", False, 200), ("assistant", True, 200)],
    ids=["agent-never-started", "person-never-started", "agent-mid-run"],
)
def test_an_agent_does_not_start_a_persons_item_on_a_disabled_repo(
    client, tmp_path, monkeypatch, caller, started, status
):
    """The person filing by hand is told to read the repo's commands before
    starting it; an agent's resume would start it before they had. An item
    already run has run them; the person starts theirs."""
    from support.api import _force_node

    from kraft.api import deps

    monkeypatch.setattr(deps, "spawn", lambda app, w, coro: deps.discard(coro))
    repo = make_repo(tmp_path)
    path = client.post(
        "/api/repos", json={"path": str(repo), "test_command": "evil"}, headers=_CALLERS["worker"]
    ).json()["path"]
    wid = client.post("/api/work-items", json={"title": "t", "repo": path}).json()["id"]
    if started:
        _force_node(wid, "implementation", "paused")

    r = client.post(f"/api/work-items/{wid}/resume", json={}, headers=_CALLERS[caller])

    assert r.status_code == status, r.text
    if status == 403:
        assert "is disabled: a person enables it" in r.json()["detail"]
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"


@pytest.mark.parametrize(
    ("caller", "status"),
    [("assistant", 403), ("worker", 403), ("person", 201)],
    ids=[
        "assistant-connects-naming-one",
        "worker-connects-naming-one",
        "person-connects-naming-one",
    ],
)
def test_an_agent_does_not_choose_a_repos_forge_account(client, tmp_path, caller, status):
    """The account decides whose token the forge calls use: refused, not held,
    since there is nothing for a person to review."""
    repo = make_repo(tmp_path)
    client.post("/api/forges", json={"name": "corp", "forge": "gitlab", "web_url": "https://gl.x"})
    body = {"path": str(repo), "forge": "gitlab", "forge_account": "corp", "enabled": False}

    r = client.post("/api/repos", json=body, headers=_CALLERS[caller])

    assert r.status_code == status, r.text
    saved = client.get("/api/repos").json()["repos"]
    if status == 403:
        assert "a person's to choose" in r.json()["detail"]
        assert saved == []
    else:
        assert saved[0]["forge_account"] == "corp"
