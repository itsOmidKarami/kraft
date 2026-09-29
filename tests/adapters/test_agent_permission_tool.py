"""The permission tool a host Claude launch names is the one registered.
`HOME` registers `kraft` at user scope (conftest's `_isolated_kraft_home`);
these take it away."""

from __future__ import annotations

from pathlib import Path

import pytest

from kraft.adapters import agent


def test_a_plugin_only_install_launches_with_the_plugins_tool_name(run):
    """The Kraft plugin's server namespaces its tools (claude 2.1.281), so the
    direct name would fail the first permission ask: the 0-token stop."""
    home = Path.home()
    (home / ".claude.json").unlink()
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text('{"enabledPlugins": {"kraft@kraft": true}}')
    cmd = run()["cmd"]
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == (
        "mcp__plugin_kraft_kraft__permission_request"
    )


def test_a_claude_launch_with_no_kraft_server_is_refused_naming_the_fix(run):
    (Path.home() / ".claude.json").unlink()
    with pytest.raises(agent.LaunchRefused, match="install the Kraft plugin"):
        run()
