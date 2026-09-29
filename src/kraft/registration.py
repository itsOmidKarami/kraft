"""Which name Claude Code knows Kraft's permission tool by, on this machine.

Every unsandboxed Claude worker passes `--permission-prompt-tool` naming it,
and the name depends on how the `kraft` MCP server got registered. Measured
against claude 2.1.281, launched the way a worker is (`claude -p`, the
`worker.env` allowlist, cwd a project directory):

- registered directly -- user scope (`kraft admin init`, `~/.claude.json`),
  local scope (`~/.claude.json` `projects.<repo>`, which a git worktree of
  that repo shares), repo scope (`.mcp.json`) or managed (`managed-mcp.json`)
  -- the tool is `mcp__kraft__permission_request`;
- loaded from the Kraft plugin, it is `mcp__plugin_kraft_kraft__...`
  (`plugin:<plugin>:<server>`), whatever marketplace the plugin came from;
- both at once, Claude Code drops the plugin's copy as a duplicate of the
  direct one (same command), so the direct name is the one that exists;
- a `managed-mcp.json` takes exclusive control while it exists: only its
  servers load, so without `kraft` in it nothing registers the tool;
- a plugin enabled but not installed loads nothing, and is not installed by
  the launch: `~/.claude/plugins/installed_plugins.json` must list it too.

Read from the files Claude Code keeps, never written: Kraft does not edit the
operator's agent config (`doctor._mcp_check`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from kraft.config import git_read

DIRECT = "mcp__kraft__permission_request"
PLUGIN = "mcp__plugin_kraft_kraft__permission_request"

#: Claude Code's managed MCP config, per platform (as claude 2.1.281 reads it).
MANAGED_MCP = (
    Path(
        {
            "darwin": "/Library/Application Support/ClaudeCode",
            "win32": r"C:\Program Files\ClaudeCode",
        }.get(sys.platform, "/etc/claude-code")
    )
    / "managed-mcp.json"
)

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


def _local_scope(user: dict, repo: Path) -> dict:
    """`projects.<repo>` in `~/.claude.json`. Claude Code keys it by the main
    checkout's real path, and resolves a git worktree to that same key, so a
    worker in an item's worktree gets the repo's entry (measured, 2.1.281)."""
    common = git_read(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    projects = user.get("projects")
    if common is None or not isinstance(projects, dict):
        return {}
    entry = projects.get(str(Path(common).parent.resolve()))
    return entry if isinstance(entry, dict) else {}


def _plugins(settings: dict) -> dict:
    plugins = settings.get("enabledPlugins")
    return plugins if isinstance(plugins, dict) else {}


def _kraft_on(plugins: dict, installed: dict) -> bool:
    """A `kraft@<marketplace>` key switched on and installed. Both formats of
    `installed_plugins.json` (v1 and v2) key `plugins` by `plugin@marketplace`."""
    return any(
        key.split("@")[0] == "kraft" and on is True and bool(installed.get(key))
        for key, on in plugins.items()
    )


def permission_tool(repo: Path | None) -> tuple[str, str] | None:
    """`(tool name, where it is registered)` for a worker in `repo`, or None
    when nothing registers the `kraft` server. `repo` None asks about the
    machine- and user-scope files only."""
    if MANAGED_MCP.exists():
        # Exclusive while it exists, even unreadable: nothing else loads.
        return (DIRECT, str(MANAGED_MCP)) if _names_kraft(_read(MANAGED_MCP)) else None
    home = Path.home()
    user = home / ".claude.json"
    user_config = _read(user)
    if _names_kraft(user_config):
        return DIRECT, str(user)
    if repo is not None and _names_kraft(_local_scope(user_config, repo)):
        return DIRECT, f"{user} (local scope)"
    if repo is not None and _names_kraft(_committed(repo, ".mcp.json")):
        return DIRECT, str(repo / ".mcp.json")
    # Claude Code merges `enabledPlugins` key by key (`plugin@marketplace`),
    # a project's value overriding the user's: a repo that turns
    # `kraft@kraft` off wins over a user who turned it on, while a repo that
    # only mentions another marketplace's copy leaves the user's alone.
    user_settings = home / ".claude" / "settings.json"
    user_plugins = _plugins(_read(user_settings))
    project_plugins = _plugins(_committed(repo, ".claude/settings.json")) if repo else {}
    installed = _read(home / ".claude" / "plugins" / "installed_plugins.json").get("plugins")
    installed = installed if isinstance(installed, dict) else {}
    if not _kraft_on(user_plugins | project_plugins, installed):
        return None
    if _kraft_on(project_plugins, installed):
        return PLUGIN, str(repo / ".claude" / "settings.json")
    return PLUGIN, str(user_settings)
