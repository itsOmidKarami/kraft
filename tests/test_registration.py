"""Which name Claude Code knows Kraft's permission tool by. `HOME` is a
throwaway directory already registering `kraft` at user scope (conftest's
`_isolated_kraft_home`); each case starts from what it needs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import commit_all, git, make_repo, write

from kraft import registration
from kraft.registration import DIRECT, PLUGIN


def _json(path: Path, data: dict) -> Path:
    return write(path.parent, path.name, json.dumps(data))


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
    _json(
        home / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {key: [{"scope": "user"}] for key in keys}},
    )


KRAFT_SERVER = {"mcpServers": {"kraft": {"command": "kraft", "args": ["admin", "mcp"]}}}


def test_a_managed_mcp_json_naming_kraft_is_the_direct_name(home, tmp_path, monkeypatch):
    managed = tmp_path / "managed-mcp.json"
    monkeypatch.setattr(registration, "MANAGED_MCP", managed)
    _json(managed, KRAFT_SERVER)
    assert registration.permission_tool(None) == (
        DIRECT,
        str(managed),
        [(str(managed), KRAFT_SERVER["mcpServers"]["kraft"])],
    )


def test_a_managed_mcp_json_without_kraft_shuts_out_every_other_registration(tmp_path, monkeypatch):
    """It has exclusive control while it exists (claude 2.1.281): the user
    scope registration conftest wrote does not load."""
    managed = tmp_path / "managed-mcp.json"
    monkeypatch.setattr(registration, "MANAGED_MCP", managed)
    _json(managed, {"mcpServers": {"other": {"command": "x"}}})
    assert registration.permission_tool(None) is None


def test_a_local_scope_registration_in_the_repo_reaches_its_worktrees(home, tmp_path):
    """`claude mcp add --scope local` keys `projects` by the checkout, and a
    `claude -p` in a git worktree of it loads that entry (measured, 2.1.281)."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(worktree))
    _json(home / ".claude.json", {"projects": {str(repo.resolve()): KRAFT_SERVER}})
    assert registration.permission_tool(worktree)[0] == DIRECT
    assert registration.permission_tool(None) is None


def test_a_plugin_enabled_but_not_installed_is_nothing(home):
    """Claude Code loads nothing for it, and does not install it at launch."""
    _json(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    assert registration.permission_tool(None) is None


def test_user_scope_registration_is_the_direct_name():
    assert registration.permission_tool(None)[:2] == (DIRECT, str(Path.home() / ".claude.json"))


@pytest.mark.parametrize("key", ["kraft@kraft", "kraft@itsomidkarami-kraft"])
def test_the_enabled_plugin_is_the_namespaced_name_from_any_marketplace(home, installed, key):
    _json(home / ".claude" / "settings.json", {"enabledPlugins": {key: True}})
    assert registration.permission_tool(None)[0] == PLUGIN


@pytest.mark.parametrize(
    "plugins",
    [{"kraft@kraft": False}, {"kraft-lite@kraft": True}],
    ids=["disabled", "kraft-lite"],
)
def test_no_kraft_plugin_enabled_is_nothing(home, installed, plugins):
    _json(home / ".claude" / "settings.json", {"enabledPlugins": plugins})
    assert registration.permission_tool(None) is None


def test_a_direct_registration_wins_over_the_plugin():
    """Claude Code drops the plugin's server as a duplicate of the direct one
    (measured, claude 2.1.281), so only the direct name exists."""
    _json(Path.home() / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    assert registration.permission_tool(None)[0] == DIRECT


def test_a_committed_mcp_json_is_direct_and_beats_the_plugin(home, installed, tmp_path):
    _json(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    repo = make_repo(tmp_path)
    _json(repo / ".mcp.json", KRAFT_SERVER)
    commit_all(repo)
    assert registration.permission_tool(repo)[:2] == (DIRECT, str(repo / ".mcp.json"))


def test_an_uncommitted_mcp_json_never_reaches_a_worktree(home, tmp_path):
    repo = make_repo(tmp_path)
    _json(repo / ".mcp.json", KRAFT_SERVER)
    assert registration.permission_tool(repo) is None


def test_a_plugin_enabled_in_committed_project_settings(home, installed, tmp_path):
    repo = make_repo(tmp_path)
    _json(repo / ".claude/settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    commit_all(repo)
    assert registration.permission_tool(repo)[0] == PLUGIN


def test_a_repo_that_turns_the_plugin_off_overrides_the_user(home, installed, tmp_path):
    """Project settings win over user settings in Claude Code: the worker in
    this repo has no plugin, so no tool, whatever the user enabled."""
    _json(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    repo = make_repo(tmp_path)
    _json(repo / ".claude/settings.json", {"enabledPlugins": {"kraft@kraft": False}})
    commit_all(repo)
    assert registration.permission_tool(repo) is None
    assert registration.permission_tool(None)[0] == PLUGIN


def test_a_repo_that_disables_another_marketplaces_copy_leaves_the_users_on(
    home, installed, tmp_path
):
    """The merge is per `plugin@marketplace` key: a stale key for another
    marketplace's copy does not switch off the one the user enabled."""
    _json(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    repo = make_repo(tmp_path)
    _json(repo / ".claude/settings.json", {"enabledPlugins": {"kraft@old-mkt": False}})
    commit_all(repo)
    assert registration.permission_tool(repo)[:2] == (
        PLUGIN,
        str(home / ".claude" / "settings.json"),
    )


def test_every_direct_entry_claude_could_start_the_server_from_is_a_candidate(home, tmp_path):
    """Claude starts the local entry before the project's and the user's
    (`claude mcp get`): which one it takes is its call, so each is one to
    check. The name and where it is registered stay the user scope's."""
    repo = make_repo(tmp_path)
    _json(repo / ".mcp.json", {"mcpServers": {"kraft": {"command": "/project/kraft"}}})
    commit_all(repo)
    local = {"mcpServers": {"kraft": {"command": "/local/kraft"}}}
    _json(home / ".claude.json", {**KRAFT_SERVER, "projects": {str(repo.resolve()): local}})
    name, where, entries = registration.permission_tool(repo)
    assert (name, where) == (DIRECT, str(home / ".claude.json"))
    assert sorted(entry["command"] for _, entry in entries) == [
        "/local/kraft",
        "/project/kraft",
        "kraft",
    ]


SSE = {"type": "sse", "url": "http://127.0.0.1:8765/mcp"}


@pytest.mark.parametrize(
    ("decided_by", "installs", "command"),
    [
        pytest.param(
            "user", {"user": {"command": "/venv/bin/kraft"}}, "/venv/bin/kraft", id="user-install"
        ),
        pytest.param("user", {"user": None}, "kraft", id="install-without-mcp-json"),
        pytest.param("user", {"user": SSE}, None, id="install-sse"),
        # Another project's copy may run something else, and never loads here.
        pytest.param(
            "user", {"other": {"command": "/elsewhere/kraft"}}, None, id="only-another-repos"
        ),
        pytest.param(
            "project", {"mine": {"command": "/mine/kraft"}}, "/mine/kraft", id="this-repos"
        ),
        pytest.param(
            "project", {"other": {"command": "/x/kraft"}}, None, id="project-only-another-repos"
        ),
    ],
)
def test_the_plugin_entry_is_the_install_that_applies_here(
    home, tmp_path, decided_by, installs, command
):
    """The installed plugin's own `.mcp.json`, of an install that applies to
    the worker's repo: user scope, or a project install for that repo. One
    with no `.mcp.json` runs the bare `kraft` the plugin ships; none that
    applies leaves nothing to check."""
    repo = make_repo(tmp_path)
    if decided_by == "user":
        _json(home / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
    else:
        _json(repo / ".claude" / "settings.json", {"enabledPlugins": {"kraft@kraft": True}})
        commit_all(repo)
    # Listed first, so a pick that ignores `projectPath` takes it.
    records = [
        {"scope": "project", "installPath": str(tmp_path / "decoy"), "projectPath": str(tmp_path)}
    ]
    _json(tmp_path / "decoy" / ".mcp.json", {"mcpServers": {"kraft": {"command": "/decoy/kraft"}}})
    for which, entry in installs.items():
        install = tmp_path / which
        install.mkdir()
        if entry is not None:
            _json(install / ".mcp.json", {"mcpServers": {"kraft": entry}})
        scope = {"user": "user", "other": "project", "mine": "project"}[which]
        owner = {"mine": str(repo)}.get(which, str(tmp_path / "another-repo"))
        records.append({"scope": scope, "installPath": str(install), "projectPath": owner})
    _json(
        home / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"kraft@kraft": records}},
    )
    entries = registration.permission_tool(repo)[2]
    assert [entry.get("command") for _, entry in entries] == [command]


@pytest.mark.parametrize(
    ("entries", "path", "verdict"),
    [
        pytest.param([{"command": "kraft"}], "bin", True, id="bare-on-path"),
        pytest.param([{"command": "kraft"}], "empty", False, id="bare-missing"),
        pytest.param([{"command": sys.executable}], "empty", True, id="absolute-present"),
        pytest.param([{"command": "/nowhere/kraft"}], "bin", False, id="absolute-missing"),
        pytest.param([{"type": "http", "url": "http://x/mcp"}], "bin", None, id="http"),
        pytest.param(
            [{"command": "${KRAFT_BIN}/kraft"}], "bin", None, id="a-variable-claude-expands"
        ),
        # Found from Claude's own working folder, which Kraft does not know.
        pytest.param([{"command": "bin/kraft"}], "empty", None, id="relative-with-a-slash"),
        # Claude fails these too: refused, and never a crash.
        pytest.param([{"command": "~/nowhere/kraft"}], "bin", False, id="tilde"),
        pytest.param([{"command": "kraft admin mcp"}], "bin", False, id="args-in-the-command"),
        # The entry's own `env.PATH` is the one Claude looks the command up on.
        pytest.param([{"command": "kraft", "env": {"PATH": "bin"}}], "empty", True, id="own-path"),
        pytest.param(
            [{"command": "kraft", "env": {"PATH": "empty"}}], "bin", False, id="own-path-without-it"
        ),
        pytest.param(
            [{"command": "kraft", "env": {"PATH": "${PATH}"}}], "bin", None, id="own-path-variable"
        ),
        pytest.param(
            [{"command": "kraft"}, {"command": sys.executable}], "empty", True, id="one-of-two-runs"
        ),
        pytest.param(
            [{"command": "kraft"}, {"type": "sse", "url": "u"}], "empty", None, id="one-unjudged"
        ),
        pytest.param([], "bin", None, id="none"),
    ],
)
def test_claude_is_judged_unable_to_start_the_server_only_when_kraft_is_sure(
    tmp_path, entries, path, verdict
):
    """False refuses a launch, so only when every entry is checkable and none
    runs; True when one runs; None, nothing to check, whenever Kraft cannot tell."""
    stub = write(tmp_path / "bin", "kraft", "#!/bin/sh\n")
    stub.chmod(0o755)
    (tmp_path / "empty").mkdir()

    def placed(entry: dict) -> dict:
        env = entry.get("env")
        if env and "$" not in env["PATH"]:
            return {**entry, "env": {"PATH": str(tmp_path / env["PATH"])}}
        return entry

    found = [("where", placed(entry)) for entry in entries]
    assert registration.starts(found, str(tmp_path / path)) is verdict


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


def test_a_copy_is_handed_no_kraft_a_commit_dropped_since_it_was_found(home, tmp_path):
    """`permission_tool` and `copy_servers` read HEAD apart: a commit between
    them that drops `kraft` leaves it out, rather than failing the launch."""
    repo = make_repo(tmp_path)
    _json(repo / ".mcp.json", {"mcpServers": {"other": {"command": "x"}}})
    commit_all(repo)
    assert registration.copy_servers(repo, str(repo / ".mcp.json")) == {}
