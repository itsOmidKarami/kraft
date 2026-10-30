"""The permission tool a host Claude launch names is the one registered.
`HOME` registers `kraft` at user scope (conftest's `_isolated_kraft_home`);
these take it away."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.harness import commit_all, make_repo, write

from kraft import registration
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
DOCS = {"command": "docs-mcp"}
#: Committed beside `kraft`: Claude asks to approve it per folder, so it is never handed.
TEAM = {"command": "team-mcp"}


@pytest.mark.parametrize(
    ("scope", "local", "handed"),
    [
        pytest.param("committed-mcp-json", None, {"kraft": KRAFT}, id="committed-mcp-json"),
        pytest.param("local-scope", {"kraft": LOCAL}, {"kraft": LOCAL}, id="local-scope"),
        pytest.param(
            "local-scope",
            {"kraft": LOCAL, "docs": DOCS},
            {"kraft": LOCAL, "docs": DOCS},
            id="local-scope-and-another",
        ),
        pytest.param("user-scope", None, None, id="user-scope"),
        pytest.param("user-scope", {"docs": DOCS}, {"docs": DOCS}, id="user-scope-local-another"),
        # Only its own servers load, in the checkout too.
        pytest.param("managed", {"docs": DOCS}, None, id="managed"),
    ],
)
def test_a_copy_launch_finds_the_checkouts_registration_and_is_handed_it(
    run, in_place, tmp_path, scope, local, handed
):
    """The copy is empty at launch and Claude keys a project's servers by its
    folder path: the lookup is of the person's checkout, and the servers tied
    to it ride in the argv, beside the ones that load anywhere rather than
    instead of them. A registration that loads anywhere is left alone, even
    beside a committed `.mcp.json`."""
    repo = make_repo(tmp_path)
    write(repo, ".mcp.json", json.dumps({"mcpServers": {"kraft": KRAFT, "team": TEAM}}))
    commit_all(repo)
    user = Path.home() / ".claude.json"
    if scope == "committed-mcp-json":
        user.unlink()
    elif scope == "managed":
        registration.MANAGED_MCP.write_text(json.dumps({"mcpServers": {"kraft": KRAFT}}))
    if local is not None:
        config = json.loads(user.read_text()) if scope != "local-scope" else {}
        config["projects"] = {str(repo.resolve()): {"mcpServers": local}}
        user.write_text(json.dumps(config))
    row = {"id": "w1", "branch": None, "repo": str(repo)}

    copy = str(tmp_path / "copy")
    cmd = run(db=SimpleNamespace(read=lambda fn: row), cwd=copy, in_copy=True)["cmd"]

    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "mcp__kraft__permission_request"
    assert "--strict-mcp-config" not in cmd
    if handed:
        config = json.loads(cmd[cmd.index("--mcp-config") + 1])
        assert config == {"mcpServers": handed}
    else:
        assert "--mcp-config" not in cmd
