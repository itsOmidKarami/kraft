"""Which name Claude Code knows Kraft's permission tool by, on this machine.

Every unsandboxed Claude worker passes `--permission-prompt-tool` naming it,
and the name depends on how the `kraft` MCP server got registered. Measured
against claude 2.1.281, launched the way a worker is (`claude -p`, the
`worker.env` allowlist, cwd a project directory):

- registered directly -- user scope (`kraft admin init`, `~/.claude.json`) or
  repo scope (`.mcp.json`) -- the tool is `mcp__kraft__permission_request`;
- loaded from the Kraft plugin, it is `mcp__plugin_kraft_kraft__...`
  (`plugin:<plugin>:<server>`), whatever marketplace the plugin came from;
- both at once, Claude Code drops the plugin's copy as a duplicate of the
  direct one (same command), so the direct name is the one that exists.

Read from the files Claude Code keeps, never written: Kraft does not edit the
operator's agent config (`doctor._mcp_check`).
"""

from __future__ import annotations

import json
from pathlib import Path

from kraft.config import git_read

DIRECT = "mcp__kraft__permission_request"
PLUGIN = "mcp__plugin_kraft_kraft__permission_request"

#: What an operator is told when nothing registers the server.
FIX = "install the Kraft plugin (then `/kraft:onboard`), or run `kraft admin init`"


def _json(text: str | None) -> dict:
    try:
        loaded = json.loads(text or "")
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read(path: Path) -> dict:
    try:
        return _json(path.read_text())
    except OSError:
        return {}


def _committed(repo: Path, name: str) -> dict:
    """`name` as HEAD has it: a worktree checks out HEAD, so an uncommitted
    file in the operator's checkout never reaches a worker."""
    return _json(git_read(repo, "show", f"HEAD:{name}", expected_failure=True, strip=False))


def _names_kraft(config: dict) -> bool:
    servers = config.get("mcpServers")
    return isinstance(servers, dict) and "kraft" in servers


def _plugins(settings: dict) -> dict:
    plugins = settings.get("enabledPlugins")
    return plugins if isinstance(plugins, dict) else {}


def _kraft_on(plugins: dict) -> bool:
    return any(key.split("@")[0] == "kraft" and on is True for key, on in plugins.items())


def permission_tool(repo: Path | None) -> tuple[str, str] | None:
    """`(tool name, where it is registered)` for a worker in `repo`, or None
    when nothing registers the `kraft` server. `repo` None asks about the
    user-scope files only."""
    home = Path.home()
    user = home / ".claude.json"
    if _names_kraft(_read(user)):
        return DIRECT, str(user)
    if repo is not None and _names_kraft(_committed(repo, ".mcp.json")):
        return DIRECT, str(repo / ".mcp.json")
    # Claude Code merges `enabledPlugins` key by key (`plugin@marketplace`),
    # a project's value overriding the user's: a repo that turns
    # `kraft@kraft` off wins over a user who turned it on, while a repo that
    # only mentions another marketplace's copy leaves the user's alone.
    user_settings = home / ".claude" / "settings.json"
    user_plugins = _plugins(_read(user_settings))
    project_plugins = _plugins(_committed(repo, ".claude/settings.json")) if repo else {}
    if not _kraft_on(user_plugins | project_plugins):
        return None
    if _kraft_on(project_plugins):
        return PLUGIN, str(repo / ".claude" / "settings.json")
    return PLUGIN, str(user_settings)
