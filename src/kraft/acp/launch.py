"""Assembling and refusing an ACP launch (spec §4, §7, part 1's scope)."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from kraft.acp.spec import LaunchSpec

TOOL_LIMITS_REFUSAL = (
    "tool limits over ACP are not supported yet (this launch sets {what}); remove the tool "
    "policy at the layer that sets it, or use a kind: cli harness"
)


def client_prefix() -> list[str]:
    """Kraft's client as a command. `-I`, as `hook_install.hook_argv` has it: the worker
    writes the worktree the client starts in, and plain `-m` would put it first on
    `sys.path`."""
    return [sys.executable, "-I", "-m", "kraft", "admin", "acp-client"]


def launch_refusal(h, *, allowed_tools, deny_tools, permission_mode, sandbox, runner) -> str | None:
    """Why this launch cannot run on the harness `h`, or None. An empty allowlist is a
    limit (it allows nothing); `restrict_tools` is only derived from `allowed_tools`."""
    limits = [
        name
        for name, on in (
            ("allowed_tools", allowed_tools is not None),
            ("deny_tools", bool(deny_tools)),
            ("permission_mode", bool(permission_mode)),
        )
        if on
    ]
    if limits:
        return TOOL_LIMITS_REFUSAL.format(what=", ".join(limits))
    if sandbox:
        return (
            f"harness {h.id!r} is an ACP agent, which cannot run in a sandbox until "
            "sandboxed ACP launches land (ACP part 3)"
        )
    if runner:
        return (
            f"harness {h.id!r} is an ACP agent, which cannot run on a remote machine until "
            "remote ACP launches land (ACP part 3)"
        )
    return None


def agent_argv(h, command: str | None) -> list[str]:
    """The agent's own argv: the profile's `executable:` replaces only its first word."""
    return [*shlex.split(command), *h.command[1:]] if command else list(h.command)


def command(h, *, files, agent_argv, cwd, prompt, context, resume, model, effort) -> list[str]:
    """Write the launch spec into the session's own folder (Kraft's, never mounted, which
    also keeps a long prompt out of argv) and return the client's command."""
    from kraft.worker.ca import write_whole

    spec = LaunchSpec(
        agent_argv=agent_argv,
        cwd=str(cwd),
        prompt=prompt,
        context=context,
        result_path=str(files.result),
        resume=resume,
        model=model,
        effort=effort,
        options=dict(h.acp.options),
    )
    path: Path = files.instruction.with_name("acp-launch.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_whole(path, spec.model_dump_json().encode(), 0o600)
    return [*client_prefix(), "--spec", str(path)]


def cap_refusal(h, caps, model) -> str | None:
    """Why a launch under `caps` cannot be counted against, or None. A token cap needs the
    entry to report tokens; a dollar cap needs its cost, else its tokens on a model
    `prices.json` prices (the order the settled cost is chosen in)."""
    from kraft import usage

    reports = h.acp.reports
    if caps.tokens and "tokens" not in reports:
        return (
            f"a token_budget is in effect, and ACP harness {h.id!r} does not declare "
            "that it reports per-turn tokens (`reports: [tokens]` in its entry), so Kraft "
            "cannot count its spend"
        )
    if caps.usd and "cost" not in reports and not ("tokens" in reports and usage.priced(model)):
        return (
            f"a dollar cap is in effect, and ACP harness {h.id!r} reports neither a cost "
            "nor tokens on a model prices.json prices (its entry's `reports`, and the "
            f"launch's model {model!r}), so Kraft cannot count its spend"
        )
    return None
