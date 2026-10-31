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
import os
import shutil
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

#: What an operator is told when the registered command is not on the PATH a
#: worker gets: Claude then cannot start the server, and the first ask fails.
PATH_FIX = (
    "put `kraft` on that PATH (install it as the install docs say, or `just install` in a "
    "checkout), register the server by its absolute path (`claude mcp add --scope user kraft "
    "-- /path/to/kraft admin mcp`), or give the service a PATH that has it (`kraft admin "
    "uninstall-service`, then `kraft admin install-service` from a shell where `kraft` runs)"
)


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


def _checkout(repo: Path) -> str | None:
    """The main checkout's real path, which Claude Code keys a project by: a
    git worktree of it resolves to the same key (measured, 2.1.281)."""
    common = git_read(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return None if common is None else str(Path(common).parent.resolve())


def _local_scope(user: dict, repo: Path) -> dict:
    """`projects.<repo>` in `~/.claude.json`, which a worker in an item's
    worktree gets too (`_checkout`)."""
    key, projects = _checkout(repo), user.get("projects")
    if key is None or not isinstance(projects, dict):
        return {}
    entry = projects.get(key)
    return entry if isinstance(entry, dict) else {}


def _plugins(settings: dict) -> dict:
    plugins = settings.get("enabledPlugins")
    return plugins if isinstance(plugins, dict) else {}


def _kraft_on(plugins: dict, installed: dict) -> str | None:
    """The `kraft@<marketplace>` key switched on and installed, if any. Both
    formats of `installed_plugins.json` (v1 and v2) key `plugins` by
    `plugin@marketplace`."""
    return next(
        (
            key
            for key, on in plugins.items()
            if key.split("@")[0] == "kraft" and on is True and bool(installed.get(key))
        ),
        None,
    )


def _kraft(config: dict) -> dict:
    """`config`'s `kraft` server entry, as Claude Code would start it."""
    entry = config["mcpServers"]["kraft"]
    return entry if isinstance(entry, dict) else {}


def _plugin_entry(installs, repo: Path | None, scope: str) -> dict:
    """The `kraft` entry of the installed plugin's own `.mcp.json`, from an
    install that applies to a worker in `repo` -- user scope, or a project's
    for that repo -- the one in `scope` first. An install without one runs
    the bare `kraft` the plugin ships; none that applies, nothing to check."""
    key = _checkout(repo) if repo is not None else None
    installs = installs if isinstance(installs, list) else [installs]
    usable = [
        i
        for i in installs
        if isinstance(i, dict)
        and (
            i.get("scope", "user") == "user"
            or (
                key is not None
                and isinstance(i.get("projectPath"), str)
                and str(Path(i["projectPath"]).resolve()) == key
            )
        )
    ]
    for install in sorted(
        usable, key=lambda i: (i.get("scope", "user") == "user") != (scope == "user")
    ):
        path = install.get("installPath")
        if isinstance(path, str):
            config = _read(Path(path) / ".mcp.json")
            return _kraft(config) if _names_kraft(config) else {"command": "kraft"}
    return {"command": "kraft"} if usable else {}


def permission_tool(repo: Path | None) -> tuple[str, str, list[tuple[str, dict]]] | None:
    """`(tool name, where it is registered, the entries Claude could start the
    server from)` for a worker in `repo`, or None when nothing registers the
    `kraft` server. Each entry is `(where, its mcpServers.kraft)`: every
    direct one that applies, since Claude takes local scope before the
    project's and the user's and the name is the same, or the plugin's own.
    `repo` None asks about the machine- and user-scope files only."""
    if MANAGED_MCP.exists():
        # Exclusive while it exists, even unreadable: nothing else loads.
        managed, where = _read(MANAGED_MCP), str(MANAGED_MCP)
        return (DIRECT, where, [(where, _kraft(managed))]) if _names_kraft(managed) else None
    home = Path.home()
    user = home / ".claude.json"
    user_config = _read(user)
    scopes = [(str(user), user_config)]
    if repo is not None:
        scopes.append((f"{user} (local scope)", _local_scope(user_config, repo)))
        scopes.append((str(repo / ".mcp.json"), _committed(repo, ".mcp.json")))
    direct = [(where, _kraft(config)) for where, config in scopes if _names_kraft(config)]
    if direct:
        return DIRECT, direct[0][0], direct
    # Claude Code merges `enabledPlugins` key by key (`plugin@marketplace`),
    # a project's value overriding the user's: a repo that turns
    # `kraft@kraft` off wins over a user who turned it on, while a repo that
    # only mentions another marketplace's copy leaves the user's alone.
    user_settings = home / ".claude" / "settings.json"
    user_plugins = _plugins(_read(user_settings))
    project_plugins = _plugins(_committed(repo, ".claude/settings.json")) if repo else {}
    installed = _read(home / ".claude" / "plugins" / "installed_plugins.json").get("plugins")
    installed = installed if isinstance(installed, dict) else {}
    if key := _kraft_on(project_plugins, installed):
        where = str(repo / ".claude" / "settings.json")
        return PLUGIN, where, [(where, _plugin_entry(installed[key], repo, "project"))]
    if key := _kraft_on(user_plugins | project_plugins, installed):
        where = str(user_settings)
        return PLUGIN, where, [(where, _plugin_entry(installed[key], repo, "user"))]
    return None


def _starts(entry: dict, path: str | None) -> bool | None:
    """Whether Claude can start `entry` on `path`; None when Kraft cannot tell."""
    command, env = entry.get("command"), entry.get("env")
    if not isinstance(command, str) or not command or "$" in command:
        return None  # reached by `url`, or a variable Claude expands
    if os.sep in command and not os.path.isabs(command) and not command.startswith("~"):
        return None  # found from Claude's own working folder, which Kraft does not know
    if isinstance(env, dict) and "PATH" in env:
        # Claude looks the command up on the entry's own PATH.
        path = env["PATH"]
        if not isinstance(path, str) or "$" in path:
            return None
    return shutil.which(command, path=path) is not None


def starts(entries: list[tuple[str, dict]], path: str | None) -> bool | None:
    """Whether Claude can start the server from one of `entries` (from
    `permission_tool`) on `path`, the PATH it runs with: False only when Kraft
    is sure none can, since False refuses a launch; None when it cannot tell."""
    verdicts = [_starts(entry, path) for _, entry in entries]
    if True in verdicts:
        return True
    return False if verdicts and None not in verdicts else None


def named(entries: list[tuple[str, dict]]) -> str:
    """The commands `entries` run, and where each is registered."""
    return " or ".join(f"{e.get('command')!r} (registered in {where})" for where, e in entries)


def copy_servers(repo: Path, where: str) -> dict:
    """The MCP servers a launch in a copy of `repo` is handed, given `where`
    `permission_tool(repo)` found the `kraft` server. Claude Code keys a
    project's servers by its folder path, so the copy loads none of `repo`'s:
    it is handed the person's local-scope servers for `repo`, and `kraft`
    from the committed `.mcp.json` when that is where it is registered. The
    `.mcp.json`'s other servers are not: Claude asks to approve those per
    folder. Nothing under a `managed-mcp.json`, which only its own servers
    pass; user and plugin servers load anywhere already."""
    if where == str(MANAGED_MCP):
        return {}
    local = _local_scope(_read(Path.home() / ".claude.json"), repo).get("mcpServers")
    servers = dict(local) if isinstance(local, dict) else {}
    committed = _committed(repo, ".mcp.json").get("mcpServers")
    # Read again: a commit since `permission_tool` may have dropped it.
    if where == str(repo / ".mcp.json") and isinstance(committed, dict) and "kraft" in committed:
        servers["kraft"] = committed["kraft"]
    return servers
