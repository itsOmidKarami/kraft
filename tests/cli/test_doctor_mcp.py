"""`kraft doctor`'s mcp server row, repo by repo: the rest of it is in
`test_doctor.py`, which is at its line budget."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from support.harness import make_repo

from kraft import client, doctor

# `app` fixture: tests/conftest.py. It wires client.transport.http() to the ASGI app.


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_mcp_check_warns_naming_a_repo_whose_committed_settings_turn_the_plugin_off(app, tmp_path):
    """A user-level plugin is not enough: a connected repo's committed
    `.claude/settings.json` wins for its workers, which are then refused. A
    warning naming the harness, not a failure: a repo whose items run only
    sandboxed or on another harness never meets that refusal (Kraft-9efnk.31)."""
    home = Path.home()
    (home / ".claude.json").unlink()
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    _write(
        home / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"kraft@kraft": [{"scope": "user"}]}},
    )
    repo = make_repo(tmp_path)
    asyncio.run(client.ensure_repo(str(repo)))
    _write(repo / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": False}})
    subprocess.run(["git", "add", ".claude"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "off"], cwd=repo, check=True)
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
    _write(registered / ".mcp.json", {"mcpServers": {"kraft": {}}})
    subprocess.run(["git", "add", ".mcp.json"], cwd=registered, check=True)
    subprocess.run(["git", "commit", "-qm", "mcp"], cwd=registered, check=True)
    check = next(r for r in asyncio.run(doctor.run_checks()) if r["name"] == "mcp server")
    assert check["ok"] is True and check["warn"] is True
    assert str(bare) in check["detail"] and str(registered) not in check["detail"]


def _templates() -> Path:
    import os

    return Path(os.environ["KRAFT_CONFIG_DIR"])


@pytest.mark.parametrize("connected", [True, False], ids=["a-repo-connected", "no-repo"])
def test_mcp_check_only_warns_when_no_chain_launches_a_harness_that_asks_through_it(
    app, tmp_path, connected
):
    """A Codex-only setup: every profile runs on a provider whose launch never
    names the permission tool, so nothing registered refuses no worker. The
    launch refuses only an unsandboxed task on a harness asking through the
    direct tool (`adapters.agent`), so doctor must be able to pass here."""
    (Path.home() / ".claude.json").unlink()
    harnesses = _templates() / "harnesses.yaml"
    table = yaml.safe_load(harnesses.read_text())
    # Every profile on codex: the suite puts them on `fake`, which is claude.
    for profile in table["harnesses"].values():
        profile["provider"] = "codex"
        profile.pop("defaults", None)
    harnesses.write_text(yaml.safe_dump(table))
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
