"""A task's tool policy, written into a CLI's own permission config at launch
(Kraft-4in7z.4 opencode, Kraft-4in7z.2 amp).

For a CLI Kraft has no per-call hook into, the launch renders `deny_tools`
and `allowed_tools` into rules the CLI enforces itself. Nothing reaches
Kraft's permission gate, so nothing is logged as a `permission_decision`,
and grants are not rendered: a CLI glob such as `git push *` also matches
`git push x; rm -rf y`, which `kraft.grants` would never allow.

Policy names map to the CLI's through the harness's `tool_names` (CLI tool
-> Kraft names), read the same way the cursor hook reads them: a CLI tool is
denied if any of its Kraft names is denied, and allowed under an allowlist
only if all of them are listed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

ToolNames = Mapping[str, tuple[str, ...]]


def unmapped(
    tool_names: ToolNames, allowed: tuple[str, ...] | None, deny: tuple[str, ...]
) -> list[str]:
    """Policy tool names no CLI tool maps to: rules can't be written for them,
    so a launch naming one is refused rather than run with it unenforced."""
    known = {k for names in tool_names.values() for k in names}
    return [t for t in dict.fromkeys((*deny, *(allowed or ()))) if t not in known]


def _denied(names: tuple[str, ...], allowed: tuple[str, ...] | None, deny: tuple[str, ...]) -> bool:
    return any(k in deny for k in names) or (
        allowed is not None and not all(k in allowed for k in names)
    )


def _opencode(tool_names, allowed, deny, directory, session_id):
    """`OPENCODE_CONFIG_CONTENT` with a `permission` block, which only a
    `--standalone` run reads: without it `opencode run` talks to the
    background service, which holds its own config (probe, 2.0.15). The last
    matching rule wins, so the allowlist's `*` comes first."""
    # `external_directory` and `doom_loop` are guards, not tools: put back to
    # `ask` (which `--auto` approves) so an allowlist doesn't stop a worker
    # writing its result file outside the worktree.
    perm: dict = (
        {"*": "deny", "external_directory": "ask", "doom_loop": "ask"}
        if allowed is not None
        else {}
    )
    for cli, names in tool_names.items():
        if _denied(names, allowed, deny):
            # A plain `deny` drops the tool from the request, and OpenCode's
            # free tier refuses any request without its shell tool ("can only
            # be used from within OpenCode", 403, measured on 2.0.15). `?*`
            # keeps the tool listed and still denies every call to it.
            perm[cli] = {"?*": "deny"} if cli == "bash" else "deny"
        elif allowed is not None:
            perm[cli] = "allow"
    return {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": perm})}, ("--standalone",)


def _amp(tool_names, allowed, deny, directory, session_id):
    """A settings file of its own, `--settings-file`, which replaces the
    user's ~/.config/amp/settings.json for the run (login lives elsewhere).
    User rules come before Amp's built-ins and the first match wins. Never
    `delegate`: edit_file hangs under it (probe, 0.0.1790142911)."""
    rules = []
    for cli, names in tool_names.items():
        if _denied(names, allowed, deny):
            rules.append({"tool": cli, "action": "reject"})
        elif allowed is not None:
            # An `allow` outranks Amp's built-in asks for this tool.
            rules.append({"tool": cli, "action": "allow"})
    if allowed is not None:
        rules.append({"tool": "*", "action": "reject"})
    directory.mkdir(parents=True, exist_ok=True)
    # ponytail: one file per session, never removed -- a worker can outlive
    # the Kraft that launched it, and Amp may re-read it. Sweep if they pile up.
    path = directory / f"{session_id}.json"
    path.write_text(json.dumps({"amp.permissions": rules}, indent=2) + "\n")
    return {}, ("--settings-file", str(path))


Renderer = Callable[..., tuple[dict[str, str], tuple[str, ...]]]

#: `permission_rules:` in a harness file names one of these.
RENDERERS: dict[str, Renderer] = {"opencode": _opencode, "amp": _amp}


def render(
    renderer: str,
    tool_names: ToolNames,
    allowed: tuple[str, ...] | None,
    deny: tuple[str, ...],
    *,
    directory: Path,
    session_id: str,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """The env and argv carrying this launch's rules, or `({}, ())` when the
    policy has nothing to enforce. `directory` is Kraft's own, for a renderer
    that writes a file."""
    if allowed is None and not deny:
        return {}, ()
    return RENDERERS[renderer](tool_names, allowed, deny, directory, session_id)
