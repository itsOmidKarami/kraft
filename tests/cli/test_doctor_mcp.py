"""`kraft doctor`'s mcp server row, repo by repo: the rest of it is in
`test_doctor.py`, which is at its line budget."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import yaml
from support.harness import git, make_repo, write

from kraft import client, doctor

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.


def test_mcp_check_warns_naming_a_repo_whose_committed_settings_turn_the_plugin_off(app, tmp_path):
    """A user-level plugin is not enough: a connected repo's committed
    `.claude/settings.json` wins for its workers, which are then refused. A
    warning naming the harness, not a failure: a repo whose items run only
    sandboxed or on another harness never meets that refusal (Kraft-9efnk.31)."""
    home = Path.home()
    (home / ".claude.json").unlink()
    write(home, ".claude/settings.json", json.dumps({"enabledPlugins": {"kraft@kraft": True}}))
    write(
        home,
        ".claude/plugins/installed_plugins.json",
        json.dumps({"version": 2, "plugins": {"kraft@kraft": [{"scope": "user"}]}}),
    )
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    write(repo, ".claude/settings.json", json.dumps({"enabledPlugins": {"kraft@kraft": False}}))
    git(repo, "add", ".claude")
    git(repo, "commit", "-qm", "off")
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert check["ok"] is True and check["warn"] is True
    assert str(repo) in check["detail"]
    assert "on harness claude" in check["detail"]
    assert "Claude Code's registration only" in check["detail"]


def test_mcp_check_fails_when_a_connected_repo_has_nothing_registered_either(app, tmp_path):
    """Nothing at user scope and nothing in the only connected repo is no
    registration at all: the first Claude worker is refused, so doctor fails
    with repos connected exactly as it does with none."""
    (Path.home() / ".claude.json").unlink()
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert check["ok"] is False and check["warn"] is False
    assert "Claude workers are refused" in check["detail"]
    assert "install the Kraft plugin" in check["detail"] and "kraft admin init" in check["detail"]


def test_mcp_check_still_only_warns_when_one_repo_registers_it_and_another_does_not(app, tmp_path):
    """No user scope, but one repo's committed `.mcp.json` registers the
    server: a repo-by-repo difference, which stays the Kraft-9efnk.31 warning
    and names only the repo without one."""
    (Path.home() / ".claude.json").unlink()
    registered, bare = make_repo(tmp_path, "registered"), make_repo(tmp_path, "bare")
    for repo in (registered, bare):
        asyncio.run(client.ensure_repo(str(repo)))
    write(registered, ".mcp.json", json.dumps({"mcpServers": {"kraft": {}}}))
    git(registered, "add", ".mcp.json")
    git(registered, "commit", "-qm", "mcp")
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert check["ok"] is True and check["warn"] is True
    assert str(bare) in check["detail"] and str(registered) not in check["detail"]


def _templates() -> Path:
    import os

    return Path(os.environ["KRAFT_CONFIG_DIR"])


def _codex_only() -> None:
    """Every profile on codex: the suite puts them on `fake`, which is claude."""
    harnesses = _templates() / "harnesses.yaml"
    table = yaml.safe_load(harnesses.read_text())
    for profile in table["harnesses"].values():
        profile["provider"] = "codex"
        profile.pop("defaults", None)
    harnesses.write_text(yaml.safe_dump(table))


@pytest.mark.parametrize("connected", [True, False], ids=["a-repo-connected", "no-repo"])
def test_mcp_check_only_warns_when_no_chain_launches_a_harness_that_asks_through_it(
    app, tmp_path, connected
):
    """A Codex-only setup: every profile runs on a provider whose launch never
    names the permission tool, so nothing registered refuses no worker. The
    launch refuses only an unsandboxed task on a harness asking through the
    direct tool (`adapters.agent`), so doctor must be able to pass here."""
    (Path.home() / ".claude.json").unlink()
    _codex_only()
    if connected:
        asyncio.run(client.ensure_repo(str(make_repo(tmp_path))))
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert check["ok"] is True and check["warn"] is True
    assert "kraft MCP server registered" in check["detail"]


def test_mcp_check_only_warns_when_every_connected_repo_is_sandboxed(app, tmp_path):
    """A sandboxed launch never reads the host's registration (its MCP server,
    if any, is the session's own), so a machine whose every repo is sandboxed
    needs none."""
    (Path.home() / ".claude.json").unlink()
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    repos = _templates() / "repos.yaml"
    data = yaml.safe_load(repos.read_text())
    entries = data["repos"] if isinstance(data, dict) else data
    for entry in entries:
        entry["sandbox"] = {"kind": "docker", "image": "kraft-worker:py"}
    repos.write_text(yaml.safe_dump(data))
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert check["ok"] is True and check["warn"] is True


@pytest.mark.parametrize(
    ("server_up", "present", "ok", "warn"),
    [
        # Its workers get the server's PATH: the server's own answer decides.
        pytest.param(True, False, False, False, id="server-says-not-on-its-path"),
        # As with nothing registered: no launch here needs it until a chain does.
        pytest.param(True, None, True, True, id="server-says-not-on-its-path-none-needs-it"),
        # Present with the server up is `test_mcp_check_passes_on_a_user_scope_registration`.
        # This shell's PATH may not be the server's, a service's above all: only a warning.
        pytest.param(False, False, True, True, id="no-server-not-on-this-path"),
        pytest.param(False, True, True, False, id="no-server-on-this-path"),
    ],
)
def test_mcp_check_says_when_claude_cannot_start_the_registered_server(
    request, tmp_path, server_up, present, ok, warn
):
    """Registered, but by a command Claude cannot find: the server never
    starts, and every unsandboxed Claude worker is refused at launch."""
    if server_up:
        request.getfixturevalue("app")
    if present is None:
        _codex_only()
    command = sys.executable if present else str(tmp_path / "nowhere" / "kraft")
    registered = Path.home() / ".claude.json"
    registered.write_text(json.dumps({"mcpServers": {"kraft": {"command": command}}}))
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert (check["ok"], check["warn"]) == (ok, warn)
    if not present:
        assert repr(command) in check["detail"] and str(registered) in check["detail"]
        assert "absolute path" in check["detail"]
        assert ("the server's may differ" in check["detail"]) is (not server_up)
        assert ("before one does" in check["detail"]) is (present is None)
