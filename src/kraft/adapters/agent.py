from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import NamedTuple

from kraft import skill as _skill
from kraft import steering as _steering
from kraft.adapters import subprocess as _subprocess

_CTX = (
    "You are working on a Kraft work item.\n"
    "Title: {title}\n"
    "Task: {task_instruction}\n"
    "Repo: {repo_path}\n"
    "Work item: {work_item_id}\n"
    "Node: {node_id}\n"
    "Hook point: {hook_point}\n"
    "Worker session: {session_id}\n"
    "\n"
    "When you are done, write a short session summary to "
    ".engineering/sessions/{session_id}.md under the repo, starting with YAML "
    "front-matter carrying exactly these keys and values:\n"
    "---\n"
    "work_item_ids: [{work_item_id}]\n"
    "node_id: {node_id}\n"
    "hook_point: {hook_point}\n"
    "worker_session_id: {session_id}\n"
    "---\n"
    'Then write that path, relative to the repo root, as "session_summary_ref" '
    "in the JSON result file at $KRAFT_RESULT_PATH.\n"
    "\n"
    'Report how it went as "status" in that same result file: "done" if you '
    'finished the work; "done_with_concerns" if you finished it but have '
    'doubts about correctness, with why in a "concerns" field; "failed" if you '
    'could not complete the task; or "needs_context" if you were missing '
    'information nobody gave you, with what you need in a "question" field. '
    "needs_context stops this run and costs a full relaunch to pick it back "
    "up, so if you are missing more than one fact, ask for all of them in "
    "that one question rather than stopping once per fact.\n"
    "\n"
    "You are working in a git worktree cut for this work item, on its own "
    "branch. Commit everything you change before you exit — `git add` and "
    "`git commit`, in this worktree. Work left uncommitted never reaches the "
    "merge request this chain opens, and is destroyed with the worktree. Do "
    "not push and do not merge: Kraft pushes the branch and opens, updates "
    "and merges the merge request itself."
)


def artifact_path(kind: str, work_item_id: str) -> str:
    """Where a hook with `artifact: <kind>` must write its document.

    Derived on both sides — injected here, read back by
    `GET /work-items/{wid}/artifact` — rather than stored on the work item.
    Nothing to migrate, nothing to go stale, and a rerun after a rejected gate
    revises the same file instead of leaving a stale pointer behind.
    """
    return f".engineering/{kind}s/{work_item_id}.md"


#: The contract a hook with an `artifact:` binding is held to. Not part of the
#: method: the method says *how* to think about a spec, this says where the
#: result goes and how a reviewer will find it. Swapping the method must not be
#: able to lose the contract.
_ARTIFACT = (
    "\n\nWrite your {kind} to {path}, relative to the repo root, creating that "
    "directory if it does not exist, and to no other path. Start it with YAML "
    "front matter carrying exactly these keys:\n"
    "---\n"
    "work_item_ids: [{work_item_id}]\n"
    "node_id: {node_id}\n"
    "hook_point: {hook_point}\n"
    "kind: {kind}s\n"
    "title: <a one-line title for this {kind}>\n"
    "---\n"
    "If that file already exists, a human has read it and asked for changes: "
    "revise it in place rather than starting a new one."
)


class Profile(NamedTuple):
    """How one agent CLI spells the options Kraft names neutrally.

    A fact about a CLI, not user configuration — a user editing this is a user
    reporting a bug. It exists so `registry.yaml` can say `model:` rather than
    `--model`, which is what stops one vendor's flags leaking into the config an
    operator edits by hand.
    """

    prompt: tuple[str, ...]
    system_prompt: tuple[str, ...]
    output_json: tuple[str, ...]
    model: tuple[str, ...]
    deny_tools: tuple[str, ...]
    permission_mode: tuple[str, ...]
    effort: tuple[str, ...]
    #: How this CLI spells an allowlist, and the flag for a per-node
    #: permission mode. `permission_mode` above is the *default* argv a binding
    #: that names no mode still gets; this is the flag on its own, for one that
    #: does.
    allowed_tools: tuple[str, ...]
    permission_mode_flag: tuple[str, ...]
    permission_prompt_tool: tuple[str, ...]
    #: How this CLI spells "resume that prior session" and "keep the resumed
    #: transcript bounded on your own" — the two flags an escalation turn adds
    #: that no chain dispatch ever needs.
    resume: tuple[str, ...] = ()
    autocompact: tuple[str, ...] = ()


PROFILES: dict[str, Profile] = {
    "claude": Profile(
        prompt=("-p",),
        system_prompt=("--append-system-prompt",),
        # NDJSON as the agent works, not one object at exit. `--verbose` is
        # mandatory, not decoration: without it the CLI exits 1 with
        # "When using --print, --output-format=stream-json requires --verbose"
        # (measured against claude 2.1.260). The last line is still the result
        # envelope, so `_envelope_is_error` and `usage.read_envelope` keep
        # reading `lines[-1]` unchanged.
        output_json=("--output-format", "stream-json", "--verbose"),
        model=("--model",),
        # The CLI polices itself at Kraft's request, not a sandbox: this flag does
        # not stop the agent running a shell that ignores it, only tools the CLI
        # itself dispatches. The real blast-radius containment is the per-item
        # worktree (spec §3) — deny_tools is defense in depth on top of it.
        deny_tools=("--disallowed-tools",),
        # Without this the CLI runs in its default mode, which *asks* — and
        # `-p` has nobody to ask, since `--permission-prompts` defaults to a
        # host Kraft does not supply. Every ask becomes a silent denial: the
        # spec worker on work item 6363c65e was refused the Write of its own
        # artifact and still exited 0. `auto` decides for itself instead.
        #
        # Model-dependent, because the judgement is the model's own: sonnet-5
        # and opus-5 complete a headless Write+Bash under `auto` with no
        # denials, haiku-4.5 under identical flags is refused. A binding that
        # pins a small model can still lose its artifact — which is why the
        # artifact check in `run_agent_task` is the load-bearing half.
        permission_mode=("--permission-mode", "auto"),
        effort=("--effort",),
        allowed_tools=("--allowedTools",),
        permission_mode_flag=("--permission-mode",),
        # The CLI calls this MCP tool instead of prompting a human whenever
        # `auto` decides it wants to ask. Kraft's own server (`kraft admin mcp`,
        # registered by `kraft admin init`) answers it. On an install where that
        # registration was never done the flag names a tool that does not
        # exist, and the CLI completes normally (measured) -- degrading to
        # today's behaviour rather than crashing, which is why no --mcp-config
        # is passed.
        permission_prompt_tool=("--permission-prompt-tool",),
        resume=("--resume",),
        autocompact=("--autocompact",),
    ),
}

#: The MCP tool `--permission-prompt-tool` names. `mcp__<server>__<tool>` is the
#: CLI's addressing scheme; `kraft` is the server name `mcp.build()` registers.
PERMISSION_TOOL = "mcp__kraft__permission_request"


class Invocation(NamedTuple):
    command: str
    profile: str
    model: str | None
    deny_tools: tuple[str, ...]
    steering_texts: tuple[str, ...]
    method_text: str | None = None
    effort: str | None = None
    allowed_tools: tuple[str, ...] = ()
    permission_mode: str | None = None


def resolve_invocation(
    binding: dict,
    repo_entry: dict | None,
    steering_dir: Path | None,
    *,
    skills_dir: Path | None = None,
    escalate: bool = False,
    #: A work item's own model/effort override (Kraft-4k6l), already
    #: validated by `validate_agent_overrides`. `None` or `{}` both mean "no
    #: override", so a caller does not have to special-case an unset column.
    item_override: dict | None = None,
) -> Invocation:
    """Fold a hook binding, a repo entry, and an item's own override into one
    launch.

    Precedence lives here and only here. Spelling it out at each call site is how
    three features that touch the same twenty lines end up disagreeing.
    """
    repo = repo_entry or {}
    io = item_override or {}
    deny: list[str] = []
    for name in (*repo.get("deny_tools", ()), *binding.get("deny_tools", ())):
        if name not in deny:
            deny.append(name)
    names = [*repo.get("steering", ()), *binding.get("steering", ())]
    if names and steering_dir is None:
        # A configured `steering:` key evaporating silently is worse than a
        # raise — unreachable in production (every real caller resolves a
        # steering_dir), but a caller that passes None with names configured
        # has a bug worth surfacing, not a launch worth degrading quietly.
        raise _steering.SteeringError(
            f"resolve_invocation: steering {names!r} configured but no steering_dir was given"
        )
    # repo first, then hook: the wider context before the narrower one, and
    # fixed rather than merged cleverly — a reader debugging a prompt has to
    # be able to predict what the agent saw.
    steering_texts = _steering.read(steering_dir, names) if names else ()
    if steering_texts:
        # `steering.validate` (config load) checked repos.yaml's names and the
        # hook's names as two separate lists, each against the budget on its
        # own — two individually-valid lists can still blow the shared budget
        # once combined here, which is the only place the real concatenation
        # exists. Re-check it here, over what run_agent_task actually injects.
        total = _steering.assembled_bytes(steering_texts)
        if total > _steering.MAX_BYTES:
            raise _steering.SteeringError(
                f"resolve_invocation: steering {names!r} totals {total} bytes combined, "
                f"over the {_steering.MAX_BYTES} byte budget"
            )
    # Hook-level only, deliberately: a method is what this *hook* does, where
    # steering is what a repo demands of every hook. A repo-level default would
    # make one hook's method depend on which repo it ran in.
    method_text = _skill.read(skills_dir, binding["skill"]) if binding.get("skill") else None
    # The item's own override wins over the binding's, for both the plain and
    # the escalate model -- but the two never compete with each other: while
    # escalating, only an escalate model (the item's if it set one, else the
    # binding's) can win, so a cheap item override can never suppress the
    # escalation valve (the bead's own worked case).
    eff_model = io.get("model") if io.get("model") is not None else binding.get("model")
    eff_escalate_model = (
        io.get("escalate_model")
        if io.get("escalate_model") is not None
        else binding.get("escalate_model")
    )
    return Invocation(
        command=binding["command"],
        profile=binding.get("profile", "claude"),
        # `escalate` is the fix loop asking for a capability bump, not naming a
        # model: an unset `escalate_model` falls through to the ordinary chain.
        model=(eff_escalate_model if escalate else None) or eff_model or repo.get("default_model"),
        deny_tools=tuple(deny),
        steering_texts=steering_texts,
        method_text=method_text,
        # Hook-level only, like `skill` and unlike `model`: effort is a property
        # of the work the node does — a spec is deliberated, an env_setup is
        # mechanical — not of the repo it runs in. A repo-wide default would
        # make the same node think harder in one checkout than another.
        # Deliberately no `escalate_effort`: `escalate_model` is already the fix
        # loop's capability bump, and two bump knobs is one too many. The
        # item's own override, when set, wins over the binding's either way.
        effort=io.get("effort") if io.get("effort") is not None else binding.get("effort"),
        # Hook-level only, like `effort` and unlike `deny_tools`. Unioning a
        # repo allowlist with a hook's would *widen* the narrower one, which is
        # the opposite of what an allowlist is for; a deny list only ever
        # narrows, which is why that one unions.
        allowed_tools=tuple(binding.get("allowed_tools", ())),
        permission_mode=binding.get("permission_mode"),
    )


def _envelope_is_error(_base_status: str, log_path: Path, _returncode: int) -> str:
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return _base_status
    if not lines:
        return _base_status
    try:
        envelope = json.loads(lines[-1])
    except json.JSONDecodeError:
        return _base_status
    if isinstance(envelope, dict) and envelope.get("is_error") is True:
        return "failed"
    return _base_status


def _resolve_status(artifact: str | None, work_item_id: str, cwd: Path):
    """`post_resolve` for an agent task: a bad envelope *or* a missing artifact.

    A binding that declares `artifact:` is held to producing it — the contract
    `_ARTIFACT` states in the prompt is otherwise only a request, and a worker
    that ignores it reports success while leaving the gate with nothing to
    approve. Checked here, in the one function every agent task returns
    through, so no caller can forget it (Kraft-7lu).
    """

    def resolve(base_status: str, log_path: Path, returncode: int) -> str:
        status = _envelope_is_error(base_status, log_path, returncode)
        # Only a *claim of success* is held to the artifact. A worker that
        # stopped to ask a question wrote nothing precisely because it
        # stopped, and `executor._needs_context_question` matches on this
        # status: downgrading it to `failed` loses the question, makes the
        # stop reason generic, and 409s both /steer and /resume.
        if artifact is None or status not in ("done", "done_with_concerns"):
            return status
        if (Path(cwd) / artifact_path(artifact, work_item_id)).is_file():
            return status
        return "failed"

    return resolve


async def run_agent_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    command: str,
    title: str,
    task_instruction: str,
    repo_path: str,
    cwd,
    round: int = 0,
    profile: str = "claude",
    model: str | None = None,
    deny_tools: tuple[str, ...] = (),
    effort: str | None = None,
    allowed_tools: tuple[str, ...] = (),
    permission_mode: str | None = None,
    steering_texts: tuple[str, ...] = (),
    review_package: str | None = None,
    artifact: str | None = None,
    method_text: str | None = None,
    #: `--resume <id>` when set — an escalation turn continuing its item's
    #: existing thread. `None` (every chain dispatch) omits the flag entirely,
    #: same as today.
    resume_session_id: str | None = None,
    #: `--autocompact <value>` when set. Paired with `resume_session_id` by
    #: `escalate.dispatch`; no chain dispatch sets it.
    autocompact: str | None = None,
    #: `False` only for an escalation turn: the child then gets no
    #: `KRAFT_WORK_ITEM_ID`, so `client.resolve_context()` resolves it as a
    #: human's own session rather than a worker's, and the existing
    #: self-action guard (`client._forbid_self_action`) lets it act on the
    #: very item it is escalating — see spec "The self-resume trick". Every
    #: existing caller keeps today's behavior by leaving this `True`.
    identify_as_worker: bool = True,
) -> str:
    ctx = _CTX.format(
        title=title,
        task_instruction=task_instruction,
        repo_path=repo_path,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        session_id=session_id,
    )
    if artifact:
        ctx += _ARTIFACT.format(
            kind=artifact,
            path=artifact_path(artifact, work_item_id),
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
        )
    if review_package:
        # By path, like $KRAFT_RESULT_PATH. A diff pasted into every review of
        # every cycle of every work item is the token cost sub-project G §4
        # already refuses for result files.
        ctx += (
            "\n\nThe change you are reviewing is written out at "
            "$KRAFT_REVIEW_PACKAGE: commit list, files changed, and the diff "
            "with ten lines of context per hunk. Read that file first. Its "
            "context lines ARE the changed files -- do not read a changed file "
            "separately unless a hunk you must judge is cut off mid-function.\n"
        )
    if method_text:
        # After the contract, before steering: the agent reads what it must
        # produce, then how to produce it, then the house rules that apply to
        # everything. Steering stays last so it is never buried.
        ctx += _skill.HEADING + method_text + _skill.UNAVAILABLE
    if steering_texts:
        # The context-injection boundary (00_overview.md glossary) bans
        # CLAUDE.md, AGENTS.md and any repo file as a context channel. That
        # rule governs the *channel*: this is the sanctioned one — the
        # per-invocation system prompt — carrying files Kraft owns under
        # $KRAFT_HOME/templates/steering/. Kraft reads nothing from inside
        # the target repo to build this. Written here because a future
        # reader finding a steering feature beside a rule banning steering
        # files would otherwise assume the rule was forgotten.
        ctx += _steering.HEADING + "\n\n".join(steering_texts)
    try:
        prof = PROFILES[profile]
    except KeyError:
        raise ValueError(f"unknown agent profile {profile!r}; known: {sorted(PROFILES)}") from None

    cmd = [
        *shlex.split(command),
        *prof.prompt,
        task_instruction,
        *prof.system_prompt,
        ctx,
        *prof.output_json,
        # The binding's mode when it names one, the profile's default when it
        # does not -- so every shipped node keeps today's `auto` grant.
        *(
            [*prof.permission_mode_flag, permission_mode]
            if permission_mode
            else list(prof.permission_mode)
        ),
        *prof.permission_prompt_tool,
        PERMISSION_TOOL,
    ]
    if resume_session_id:
        cmd += [*prof.resume, resume_session_id]
    if autocompact:
        cmd += [*prof.autocompact, autocompact]
    if model:
        cmd += [*prof.model, model]
    if deny_tools:
        cmd += [*prof.deny_tools, ",".join(deny_tools)]
    if allowed_tools:
        cmd += [*prof.allowed_tools, ",".join(allowed_tools)]
    if effort:
        cmd += [*prof.effort, effort]
    return await _subprocess.run_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        cmd=cmd,
        cwd=cwd,
        # Identity, not configuration. `client.resolve_context()` keys `origin`
        # on KRAFT_WORK_ITEM_ID, and `origin` is the whole of the design §6 rule
        # 2 guard: without this a worker in its own worktree reads as a human and
        # may approve its own gate. Deliberately no MCP config here — that would
        # make this adapter vendor-aware, against conceptual model §1.2.
        env={
            **({"KRAFT_WORK_ITEM_ID": work_item_id} if identify_as_worker else {}),
            "KRAFT_SESSION_ID": session_id,
            **({"KRAFT_REVIEW_PACKAGE": review_package} if review_package else {}),
        },
        post_resolve=_resolve_status(artifact, work_item_id, cwd),
        round=round,
    )
