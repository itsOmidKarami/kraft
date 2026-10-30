"""The permission tool a host Claude launch names is the one registered.
`HOME` registers `kraft` at user scope (conftest's `_isolated_kraft_home`);
these take it away."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.harness import commit_all, make_repo, write

from kraft.adapters import agent


def test_a_plugin_only_install_launches_with_the_plugins_tool_name(run):
    """The Kraft plugin's server namespaces its tools (claude 2.1.281), so the
    direct name would fail the first permission ask: the 0-token stop."""
    home = Path.home()
    (home / ".claude.json").unlink()
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"enabledPlugins": {"kraft@kraft": true}}')
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        '{"version": 2, "plugins": {"kraft@kraft": [{"scope": "user"}]}}'
    )
    cmd = run()["cmd"]
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == (
        "mcp__plugin_kraft_kraft__permission_request"
    )


def test_a_claude_launch_with_no_kraft_server_is_refused_naming_the_fix(run):
    (Path.home() / ".claude.json").unlink()
    with pytest.raises(agent.LaunchRefused, match="install the Kraft plugin"):
        run()


KRAFT = {"command": "kraft", "args": ["admin", "mcp"]}
LOCAL = {"command": "/opt/kraft/bin/kraft", "args": ["admin", "mcp"]}


@pytest.mark.parametrize(
    ("scope", "handed"),
    [
        pytest.param("committed-mcp-json", KRAFT, id="committed-mcp-json"),
        pytest.param("local-scope", LOCAL, id="local-scope"),
        pytest.param("user-scope", None, id="user-scope"),
    ],
)
def test_a_copy_launch_finds_the_checkouts_registration_and_is_handed_it(
    run, in_place, tmp_path, scope, handed
):
    """The copy is empty at launch and Claude approves a project's servers per
    folder path: the lookup is of the person's checkout, and a registration
    tied to it rides in the argv. One that loads anywhere is left alone, even
    beside a committed `.mcp.json`."""
    repo = make_repo(tmp_path)
    write(repo, ".mcp.json", json.dumps({"mcpServers": {"kraft": KRAFT}}))
    commit_all(repo)
    user = Path.home() / ".claude.json"
    if scope == "committed-mcp-json":
        user.unlink()
    elif scope == "local-scope":
        projects = {"projects": {str(repo.resolve()): {"mcpServers": {"kraft": LOCAL}}}}
        user.write_text(json.dumps(projects))
    row = {"id": "w1", "branch": None, "repo": str(repo)}

    copy = str(tmp_path / "copy")
    cmd = run(db=SimpleNamespace(read=lambda fn: row), cwd=copy, in_copy=True)["cmd"]

    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"
    if handed:
        config = json.loads(cmd[cmd.index("--mcp-config") + 1])
        assert config == {"mcpServers": {"kraft": handed}}
    else:
        assert "--mcp-config" not in cmd
