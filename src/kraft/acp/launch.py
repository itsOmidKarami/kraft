"""Assembling and refusing an ACP launch (spec §4, §7, part 1's scope)."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from kraft.acp import tools
from kraft.acp.spec import LaunchSpec


def client_prefix() -> list[str]:
    """Kraft's client as a command. `-I`, as `hook_install.hook_argv` has it: the worker
    writes the worktree the client starts in, and plain `-m` would put it first on
    `sys.path`."""
    return [sys.executable, "-I", "-m", "kraft", "admin", "acp-client"]


def tool_limit_refusal(h, *, allowed_tools, deny_tools, permission_mode, grants=()) -> str | None:
    """Why this launch's tool limits cannot be held over ACP, or None (spec §2 rule 2, §6)."""
    entry = h.acp
    if permission_mode:
        return (
            f"permission_mode {permission_mode!r} is a kind: cli harness's own mode; an ACP "
            f"agent's asking is its entry's `asks_everything`, so a task cannot set one on "
            f"{h.id!r}; remove it at the layer that sets it"
        )
    if allowed_tools is None and not deny_tools and not entry.always_deny:
        return None  # no limit: a grant (an escalation turn always holds some) widens nothing
    # A grant only widens what a limit would deny, and every grant is a git subcommand run
    # through Bash: refuse it only when this launch's limit would deny Bash, which a grant could
    # then change.
    bash_denied = (
        "Bash" in deny_tools
        or "Bash" in entry.always_deny
        or (allowed_tools is not None and "Bash" not in allowed_tools)
    )
    if grants and bash_denied:
        return (
            f"the task holds the named grant(s) {', '.join(grants)}, which match a shell call's "
            f"own text and would widen a limit that denies Bash; over ACP that text is only the "
            f"agent's description of the call, so ACP harness {h.id!r} cannot honour a grant: "
            "remove the grant or the limit at the layer that sets it, or use a kind: cli harness"
        )
    if entry.asks_everything is None:
        return (
            f"this launch holds a tool policy, and ACP harness {h.id!r} declares no "
            "`asks_everything`, so Kraft cannot make it ask before tool calls and the policy "
            "would be caught only after a call ran; add `asks_everything` to its entry, or "
            "remove the tool policy"
        )
    denied = {*deny_tools, *entry.always_deny}

    def stance(n):  # how the policy treats tool n: (denied, allowed-by-the-allowlist)
        return (n in denied, allowed_tools is None or n in allowed_tools)

    for key, names in h.tool_names.items():
        # Only a *kind* is ambiguous (spec §6.3): a call that carries a `name` is keyed on it,
        # and a name that is several Kraft tools at once is the gate's `also` rule (denied if
        # any is denied).
        if key in tools.KINDS and len({stance(n) for n in names}) > 1:
            return (
                f"ACP harness {h.id!r} maps one tool kind ({key!r}) onto {', '.join(names)}, and "
                "this launch's policy treats them differently; its entry cannot tell them apart, "
                "so the policy cannot be held. Treat them alike, or map each by its own `name`"
            )
    for n in entry.settles_itself:
        if stance(n) != (False, True):
            return (
                f"ACP harness {h.id!r} runs {n} without asking, so denying it, or leaving it out "
                "of an allowlist, cannot be held before the call"
            )
    return None


def launch_refusal(h, *, allowed_tools, deny_tools, permission_mode, grants=()) -> str | None:
    """Why this launch cannot run on the harness `h`, or None. An empty allowlist is a
    limit (it allows nothing); `restrict_tools` is only derived from `allowed_tools`. A sandbox or a
    machine is not this door's business: the client runs outside the sandbox over the agent's
    wrapped argv, and a machine without the client is refused by placement (`ACP_CLIENT`)."""
    return tool_limit_refusal(
        h,
        allowed_tools=allowed_tools,
        deny_tools=deny_tools,
        permission_mode=permission_mode,
        grants=grants,
    )


def marker_path(files) -> Path:
    """Where the client says it reached the spawn (`LaunchSpec.started_marker`)."""
    return files.instruction.with_name("acp-started")


def read_spec(path: Path) -> LaunchSpec:
    return LaunchSpec.model_validate_json(Path(path).read_text())


def rewrite_agent(path: Path, agent_argv: list[str]) -> None:
    """Put the sandbox's wrapped argv where the client spawns the agent from: whole or not at
    all, `0600`. The argv the log shows stays the unwrapped one (`logged_agent_argv`): the
    wrapped one carries env literals."""
    from kraft.worker.ca import write_whole

    spec = read_spec(path)
    spec.logged_agent_argv = spec.logged_agent_argv or list(spec.agent_argv)
    spec.agent_argv = list(agent_argv)
    write_whole(Path(path), spec.model_dump_json().encode(), 0o600)


def agent_argv(h, command: str | None) -> list[str]:
    """The agent's own argv: the profile's `executable:` replaces only its first word."""
    return [*shlex.split(command), *h.command[1:]] if command else list(h.command)


def spec_for(
    h,
    *,
    agent_argv,
    cwd,
    prompt,
    context,
    result_path,
    resume=None,
    model=None,
    effort=None,
    allowed_tools=None,
    deny_tools=(),
) -> LaunchSpec:
    """The client's spec for a launch of `h` (what `command` writes, what the probe runs)."""
    entry, asks = h.acp, h.acp.asks_everything
    return LaunchSpec(
        agent_argv=agent_argv,
        cwd=str(cwd),
        prompt=prompt,
        context=context,
        result_path=str(result_path),
        resume=resume,
        model=model,
        effort=effort,
        options=dict(entry.options),
        harness_id=h.id,
        agent_env=dict(asks.env) if asks else {},
        asks_config=dict(asks.config) if asks else {},
        asks_mode=asks.mode if asks else None,
        tool_names={k: list(v) for k, v in h.tool_names.items()},
        always_deny=list(entry.always_deny),
        deny_tools=list(deny_tools),
        settles_itself=list(entry.settles_itself),
        policy=allowed_tools is not None or bool(deny_tools) or bool(entry.always_deny),
        allowlist=allowed_tools is not None,
    )


def command(
    h,
    *,
    files,
    agent_argv,
    cwd,
    prompt,
    context,
    resume,
    model,
    effort,
    allowed_tools=None,
    deny_tools=(),
) -> list[str]:
    """Write the launch spec into the session's own folder (Kraft's, never mounted, which
    also keeps a long prompt out of argv) and return the client's command."""
    from kraft.worker.ca import write_whole

    spec = spec_for(
        h,
        agent_argv=agent_argv,
        cwd=cwd,
        prompt=prompt,
        context=context,
        result_path=files.result,
        resume=resume,
        model=model,
        effort=effort,
        allowed_tools=allowed_tools,
        deny_tools=deny_tools,
    )
    spec.started_marker = str(marker_path(files))
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
