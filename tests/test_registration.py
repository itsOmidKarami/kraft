"""Which name Claude Code knows Kraft's permission tool by. `HOME` is a
throwaway directory already registering `kraft` at user scope (conftest's
`_isolated_kraft_home`); each case starts from what it needs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from support.harness import make_repo

from kraft import registration
from kraft.registration import DIRECT, PLUGIN


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _commit(repo: Path, name: str, data: dict) -> None:
    _write(repo / name, data)
    subprocess.run(["git", "add", name], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", name], cwd=repo, check=True)


@pytest.fixture
def home() -> Path:
    home = Path.home()
    (home / ".claude.json").unlink()
    return home


KRAFT_SERVER = {"mcpServers": {"kraft": {"command": "kraft", "args": ["admin", "mcp"]}}}


def test_user_scope_registration_is_the_direct_name():
    assert registration.permission_tool(None) == (DIRECT, str(Path.home() / ".claude.json"))


@pytest.mark.parametrize("key", ["kraft@kraft", "kraft@itsomidkarami-kraft"])
def test_the_enabled_plugin_is_the_namespaced_name_from_any_marketplace(home, key):
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {key: True}})
    assert registration.permission_tool(None)[0] == PLUGIN


@pytest.mark.parametrize(
    "plugins",
    [{"kraft@kraft": False}, {"kraft-lite@kraft": True}],
    ids=["disabled", "kraft-lite"],
)
def test_no_kraft_plugin_enabled_is_nothing(home, plugins):
    _write(home / ".claude" / "settings.json", {"enabledPlugins": plugins})
    assert registration.permission_tool(None) is None


def test_a_direct_registration_wins_over_the_plugin():
    """Claude Code drops the plugin's server as a duplicate of the direct one
    (measured, claude 2.1.281), so only the direct name exists."""
    _write(Path.home() / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    assert registration.permission_tool(None)[0] == DIRECT


def test_a_committed_mcp_json_is_direct_and_beats_the_plugin(home, tmp_path):
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    repo = make_repo(tmp_path)
    _commit(repo, ".mcp.json", KRAFT_SERVER)
    assert registration.permission_tool(repo) == (DIRECT, str(repo / ".mcp.json"))


def test_an_uncommitted_mcp_json_never_reaches_a_worktree(home, tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / ".mcp.json", KRAFT_SERVER)
    assert registration.permission_tool(repo) is None


def test_a_plugin_enabled_in_committed_project_settings(home, tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, ".claude/settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    assert registration.permission_tool(repo)[0] == PLUGIN
