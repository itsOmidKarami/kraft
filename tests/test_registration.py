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


@pytest.fixture
def installed(home: Path) -> None:
    """The plugins these cases enable, installed the way `claude plugin
    install` records them (v2 format, claude 2.1.281)."""
    keys = ("kraft@kraft", "kraft@itsomidkarami-kraft", "kraft-lite@kraft")
    _write(
        home / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {key: [{"scope": "user"}] for key in keys}},
    )


KRAFT_SERVER = {"mcpServers": {"kraft": {"command": "kraft", "args": ["admin", "mcp"]}}}


def test_a_managed_mcp_json_naming_kraft_is_the_direct_name(home, tmp_path, monkeypatch):
    managed = tmp_path / "managed-mcp.json"
    monkeypatch.setattr(registration, "MANAGED_MCP", managed)
    _write(managed, KRAFT_SERVER)
    assert registration.permission_tool(None) == (DIRECT, str(managed))


def test_a_managed_mcp_json_without_kraft_shuts_out_every_other_registration(tmp_path, monkeypatch):
    """It has exclusive control while it exists (claude 2.1.281): the user
    scope registration conftest wrote does not load."""
    managed = tmp_path / "managed-mcp.json"
    monkeypatch.setattr(registration, "MANAGED_MCP", managed)
    _write(managed, {"mcpServers": {"other": {"command": "x"}}})
    assert registration.permission_tool(None) is None


def test_a_local_scope_registration_in_the_repo_reaches_its_worktrees(home, tmp_path):
    """`claude mcp add --scope local` keys `projects` by the checkout, and a
    `claude -p` in a git worktree of it loads that entry (measured, 2.1.281)."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(worktree)], cwd=repo, check=True)
    _write(home / ".claude.json", {"projects": {str(repo.resolve()): KRAFT_SERVER}})
    assert registration.permission_tool(worktree)[0] == DIRECT
    assert registration.permission_tool(None) is None


def test_a_plugin_enabled_but_not_installed_is_nothing(home):
    """Claude Code loads nothing for it, and does not install it at launch."""
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    assert registration.permission_tool(None) is None


def test_user_scope_registration_is_the_direct_name():
    assert registration.permission_tool(None) == (DIRECT, str(Path.home() / ".claude.json"))


@pytest.mark.parametrize("key", ["kraft@kraft", "kraft@itsomidkarami-kraft"])
def test_the_enabled_plugin_is_the_namespaced_name_from_any_marketplace(home, installed, key):
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {key: True}})
    assert registration.permission_tool(None)[0] == PLUGIN


@pytest.mark.parametrize(
    "plugins",
    [{"kraft@kraft": False}, {"kraft-lite@kraft": True}],
    ids=["disabled", "kraft-lite"],
)
def test_no_kraft_plugin_enabled_is_nothing(home, installed, plugins):
    _write(home / ".claude" / "settings.json", {"enabledPlugins": plugins})
    assert registration.permission_tool(None) is None


def test_a_direct_registration_wins_over_the_plugin():
    """Claude Code drops the plugin's server as a duplicate of the direct one
    (measured, claude 2.1.281), so only the direct name exists."""
    _write(Path.home() / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    assert registration.permission_tool(None)[0] == DIRECT


def test_a_committed_mcp_json_is_direct_and_beats_the_plugin(home, installed, tmp_path):
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    repo = make_repo(tmp_path)
    _commit(repo, ".mcp.json", KRAFT_SERVER)
    assert registration.permission_tool(repo) == (DIRECT, str(repo / ".mcp.json"))


def test_an_uncommitted_mcp_json_never_reaches_a_worktree(home, tmp_path):
    repo = make_repo(tmp_path)
    _write(repo / ".mcp.json", KRAFT_SERVER)
    assert registration.permission_tool(repo) is None


def test_a_plugin_enabled_in_committed_project_settings(home, installed, tmp_path):
    repo = make_repo(tmp_path)
    _commit(repo, ".claude/settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    assert registration.permission_tool(repo)[0] == PLUGIN


def test_a_repo_that_turns_the_plugin_off_overrides_the_user(home, installed, tmp_path):
    """Project settings win over user settings in Claude Code: the worker in
    this repo has no plugin, so no tool, whatever the user enabled."""
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    repo = make_repo(tmp_path)
    _commit(repo, ".claude/settings.json", {"enabledPlugins": {"kraft@kraft": False}})
    assert registration.permission_tool(repo) is None
    assert registration.permission_tool(None)[0] == PLUGIN


def test_a_repo_that_disables_another_marketplaces_copy_leaves_the_users_on(
    home, installed, tmp_path
):
    """The merge is per `plugin@marketplace` key: a stale key for another
    marketplace's copy does not switch off the one the user enabled."""
    _write(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    repo = make_repo(tmp_path)
    _commit(repo, ".claude/settings.json", {"enabledPlugins": {"kraft@old-mkt": False}})
    assert registration.permission_tool(repo) == (
        PLUGIN,
        str(home / ".claude" / "settings.json"),
    )


@pytest.mark.e2e("claude")
def test_a_marketplace_install_is_found_and_names_the_tool_as_expected(home, tmp_path):
    """The two Claude Code facts the resolver rests on: installing the plugin
    records it in `~/.claude/settings.json` `enabledPlugins`, and a headless
    session then offers `mcp__plugin_kraft_kraft__permission_request`. Read
    from the session's `init` line, stopped before it asks the model anything."""
    root = Path(__file__).resolve().parents[1]
    for args in (["marketplace", "add", str(root)], ["install", "kraft@kraft"]):
        subprocess.run(["claude", "plugin", *args], check=True, capture_output=True)
    assert registration.permission_tool(None)[0] == PLUGIN

    with subprocess.Popen(
        ["claude", "-p", "ok", "--output-format", "stream-json", "--verbose"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        text=True,
    ) as session:
        init = next(
            json.loads(line) for line in session.stdout if json.loads(line).get("subtype") == "init"
        )
        session.kill()
    assert PLUGIN in init["tools"]
