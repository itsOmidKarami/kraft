from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple

from kraft import harness as _harness
from kraft import policy as _policy
from kraft import skill as _skill
from kraft.adapters import subprocess as _subprocess
from kraft.paths import default_templates_dir
from kraft.policy import InstancePolicy
from kraft.templates.environment import (
    HarnessProfile,
    HarnessProfileTable,
    TemplateEnvironmentError,
)
from kraft.templates.models import AgentTask
from kraft.worker import steering as _steering

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
    "This session ends the moment your turn ends: nothing runs after you stop "
    "responding, and a turn that ends with a background job still running fails, "
    "naming the job. Run every command to completion in the foreground before you "
    "report status (the Monitor tool is disabled for the same reason). Run the "
    "tests your change touches, by path or name as this repo's own instructions "
    "say, not the whole suite: the verification after you runs that.\n"
    "\n"
    "You are working in a git worktree cut for this work item, on its own "
    "branch. Commit everything you change before you exit — `git add` and "
    "`git commit`, in this worktree. Work left uncommitted never reaches the "
    "merge request this chain opens, and is destroyed with the worktree. Do "
    "not push and do not merge: Kraft pushes the branch and opens, updates "
    "and merges the merge request itself.\n"
    "\n"
    "Exception: `.engineering/` (your session summary, and any spec/plan/"
    "review document a hook asked you to write) is gitignored on purpose. "
    "Kraft reads those files straight off disk, never from git, so they must "
    "not reach the merge request. If `git add` refuses one of those paths, "
    "that is git working as intended — do not `git add -f` it or otherwise "
    "force it in."
)

#: Kraft's own safety rules, part of the contract every agent launch carries
#: (`every-agent-launch-carries-kraft-safety-rules`). Not steering: nothing a
#: task, chain, repo or operator writes can select it away. A worker once
#: SIGKILLed the Kraft daemon it was running under (Kraft-f8u3); the legacy
#: registry answered with a default steering on every agent hook, and V1 has no
#: such default (Kraft-5x93b), so the rule lives here instead.
SAFETY_RULES = (
    "\n\nNever signal a process you did not start. If something is already "
    "listening on a port you need, it is not a stale leftover to clear -- it "
    "might be the Kraft daemon serving other work right now. Check "
    "$KRAFT_DAEMON_PID and $KRAFT_DAEMON_PORT in your environment before "
    "touching anything you find on a port: if the pid or the port matches, it "
    "is the daemon, and `kill`, `pkill`, or piping `lsof` into `xargs kill` "
    "would take down orchestration for every other work item on this install, "
    "including this one. Ask any server you start yourself for an ephemeral "
    "port (bind port 0, or leave KRAFT_PORT unset) rather than reuse the "
    "daemon's. If a task genuinely needs the daemon's own port, that is a "
    "question for a human, not something to resolve by killing what is "
    "already there."
)

#: Intent-process design §4. Kraft-authored, and its only input is the repo's
#: own repos.yaml entry: it names a directory and inlines no file from the repo,
#: so the context-injection boundary below is not crossed.
INTENT_HEADING = "\n\n## Intent tree\n\n"
_INTENT = (
    "This repository states its intended behaviour in `{dir}/`, one file per "
    "capability. Read `{dir}/README.md` for the format before you change "
    "anything there."
)

#: Asked only of a harness whose `usage` capability says `result_file` -- the
#: agent is then the only source of its own numbers, and without this the row
#: stores NULL tokens as well as NULL cost. `usage._from_usage_block` already
#: accepts exactly this shape.
_USAGE_REQUEST = (
    "\n\nAlso report your own token usage in that same result file, as a "
    '"usage" object with "input_tokens" and "output_tokens", and '
    '"cost_usd" if and only if you know what this session actually cost. '
    "Omit cost_usd rather than estimating it: a guessed number is worse than "
    "no number, because someone will decide whether this run was worth it by "
    "reading it."
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
    "{title_line}"
    "---\n"
    "If that file already exists, a human has read it and asked for changes: "
    "revise it in place rather than starting a new one."
)

#: The default `title:` line every other artifact kind gets.
_TITLE_LINE = "title: <a one-line title for this {kind}>\n"

#: The extra front-matter keys an `mr_meta` artifact carries, replacing
#: `_TITLE_LINE` for this kind only. Kraft reads these straight into `glab mr
#: create` arguments, so they are part of the contract, not of the method --
#: the skill can be swapped without losing them.
_MR_META_KEYS = (
    "title: <the merge request's title, one line, in whatever pattern this "
    "repo's merged MRs already follow>\n"
    "labels: [<labels that already exist in this project, or an empty list>]\n"
    "assignees: [<a username the repo names, or an empty list>]\n"
    "reviewers: [<usernames CODEOWNERS names for these paths, or an empty list>]\n"
)


class Invocation(NamedTuple):
    command: str
    harness: str
    model: str | None
    deny_tools: tuple[str, ...]
    steering_texts: tuple[str, ...]
    method_text: str | None = None
    effort: str | None = None
    #: `None` is unbounded -- no layer of the task's policy set one -- and
    #: `()` allows nothing. Never conflate them: an absent allowlist silently
    #: reading as "every tool" is how a restriction goes missing.
    allowed_tools: tuple[str, ...] | None = None
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
    #: A harness profile's `defaults:` (`resolve_agent_task`): the lowest rung,
    #: filling only what the item, the binding and the repo all left unset.
    profile_defaults: dict | None = None,
    #: The harness profile id the launch runs on: the key the repo's
    #: per-profile `models:` is read at (Ruling 165). None reads no repo model.
    profile: str | None = None,
    #: A V1 task's own steering, already resolved to text from its item's
    #: snapshot (`resolve_agent_task`). Injected after the repo's, where a
    #: binding's `steering:` file names would go.
    steering_texts: tuple[str, ...] = (),
) -> Invocation:
    """Fold a hook binding, a repo entry, and an item's own override into one
    launch.

    Precedence lives here and only here. Spelling it out at each call site is how
    three features that touch the same twenty lines end up disagreeing.
    """
    repo = repo_entry or {}
    io = item_override or {}
    pd = profile_defaults or {}
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
    steering_texts = (_steering.read(steering_dir, names) if names else ()) + steering_texts
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
        command=binding.get("command", ""),
        harness=binding.get("harness", "claude"),
        # `escalate` is the fix loop asking for a capability bump, not naming a
        # model: an unset `escalate_model` falls through to the ordinary chain.
        model=(eff_escalate_model if escalate else None)
        or eff_model
        or ((repo.get("models") or {}).get(profile) if profile else None)
        or pd.get("model"),
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
        effort=next(
            (
                v
                for v in (io.get("effort"), binding.get("effort"), pd.get("effort"))
                if v is not None
            ),
            None,
        ),
        # Hook-level only, like `effort` and unlike `deny_tools`. Unioning a
        # repo allowlist with a hook's would *widen* the narrower one, which is
        # the opposite of what an allowlist is for; a deny list only ever
        # narrows, which is why that one unions.
        allowed_tools=(
            tuple(binding["allowed_tools"]) if binding.get("allowed_tools") is not None else None
        ),
        permission_mode=binding.get("permission_mode") or pd.get("permission_mode"),
    )


class HarnessUnavailable(Exception):
    """A task's `harness:` names no enabled profile this instance can launch
    (`unavailable-selected-harness-needs-human`). The message says why."""


class LaunchRefused(ValueError):
    """`run_agent_task` refused to start anything: the harness is unknown, or a
    merged option names a capability it does not declare. A configuration
    stop that names its cause, never a task failure a fix loop could repair
    (Kraft-hr0xr). A `ValueError` still, for callers that catch that."""


#: The profile defaults `resolve_agent_task` applies. Each is a scalar option
#: `run_agent_task` takes; a default outside this set would be dropped without
#: a word, so it is refused instead.
_PROFILE_DEFAULTS = ("model", "effort", "permission_mode")


def harness_profile(profile_id: str, harnesses: _harness.HarnessSet) -> HarnessProfile:
    """The enabled `harnesses.yaml` profile `profile_id` names, or
    `HarnessUnavailable`.

    Read from the same templates directory the app loads the library from
    (`KRAFT_TEMPLATES_DIR`, else `$KRAFT_HOME/templates`) and on every call,
    like `kraft.harness.load(None)`: an edit to the file reaches the next
    launch. Never a fallback onto a provider of the same name -- a task selects
    a profile, and a missing one stops for a human.
    """
    path = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir()) / "harnesses.yaml"
    try:
        profiles = HarnessProfileTable.from_yaml(path, harnesses=harnesses.valid).profiles
    except TemplateEnvironmentError as exc:
        raise HarnessUnavailable(str(exc)) from exc
    profile = profiles.get(profile_id)
    if profile is None:
        raise HarnessUnavailable(f"{path} defines no such profile; known are {sorted(profiles)}")
    if not profile.is_available():
        raise HarnessUnavailable(f"profile {profile_id!r} is disabled in {path}")
    unapplied = sorted(set(profile.defaults) - set(_PROFILE_DEFAULTS))
    if unapplied:
        raise HarnessUnavailable(
            f"profile {profile_id!r} sets defaults {unapplied}, which Kraft does not "
            f"apply; only {list(_PROFILE_DEFAULTS)} are"
        )
    return profile


def resolve_agent_task(
    task: AgentTask,
    repo_entry: dict | None,
    steering_dir: Path | None,
    *,
    skills_dir: Path | None = None,
    escalate: bool = False,
    item_override: dict | None = None,
    harnesses: _harness.HarnessSet | None = None,
    steering: dict[str, str] | None = None,
    policy: InstancePolicy | None = None,
) -> Invocation:
    """One V1 `AgentTask`'s launch.

    The typed entry point to `resolve_invocation`'s precedence rules, so the
    executor hands over a model and never a hook dictionary. The dict is built
    here, inside the adapter, and only from fields the task actually sets: an
    absent `model`/`effort`/`skill` must stay absent so the repo's own default
    and the item's override still win where `resolve_invocation` says they do.

    `task.harness` is a *profile* id (`harness_profile`): the launch runs the
    profile's provider, from its `executable` when it sets one, and its
    `defaults` fill whatever nothing else chose -- they are the lowest rung,
    under the item's override, the task's own field and the repo's model for
    this profile (`models:`). Raises `HarnessUnavailable`.

    `policy` is the task's resolved policy (`MaterializedChain.policy_for`),
    the only source of its tool lists: `allowed_tools` is the policy's
    (unbounded only when no layer set it), `deny_tools` the policy's plus the
    repository entry's live ones (a later denial still applies). A profile
    outside the policy's `allowed_harnesses` raises `HarnessUnavailable`.
    `None` leaves the tool lists to the repository entry; no launch passes it
    (Kraft-l8ype: the escalation turn runs under its node's policy too). The
    sandbox is not an invocation's: every launch reads the item's from
    `dispatch.item_sandbox` (Ruling 189).

    `steering` is the item's snapshot's frozen steering (`ResolvedChain.steering`),
    and the only place a task's `steering:` names are read from -- never
    `library.yaml`, never `steering_dir` (which still serves `repos.yaml`'s own
    steering names). `None` is a snapshot stored before steering was frozen:
    a task selecting steering then raises `SteeringError` rather than run
    unsteered or on today's text. The profile is looked up after it, so a
    harness problem still reports as one.
    """
    if task.steering and steering is None:
        raise _steering.SteeringError(
            f"selects steering {list(task.steering)!r}, but this work item was materialized "
            "before steering was frozen into its snapshot, so there is no intake-time text "
            "to run it with. Re-file the item, or switch its chain template before it starts."
        )
    missing = [n for n in task.steering if n not in (steering or {})]
    if missing:
        raise _steering.SteeringError(
            f"selects steering {missing!r}, which this work item's snapshot does not carry"
        )
    allowed = policy.allowed_harnesses if policy is not None else None
    if allowed is not None and task.harness not in allowed:
        # A snapshot materialization never checked (an older build's, a
        # hand-edited row) is held to its policy here, at every launch.
        raise HarnessUnavailable(
            f"its policy's allowed_harnesses {sorted(allowed)!r} does not include it"
        )
    profile = harness_profile(
        task.harness, harnesses if harnesses is not None else _harness.load(None)
    )
    inv = resolve_invocation(
        {
            "kind": "agent",
            "harness": profile.provider,
            **({"command": profile.executable} if profile.executable else {}),
            **({"skill": task.skill} if task.skill is not None else {}),
            **({"model": task.model} if task.model is not None else {}),
            **({"effort": task.effort} if task.effort is not None else {}),
            **({"deny_tools": list(policy.deny_tools)} if policy is not None else {}),
        },
        repo_entry,
        steering_dir,
        skills_dir=skills_dir,
        escalate=escalate,
        item_override=item_override,
        profile_defaults=profile.defaults,
        profile=profile.id,
        steering_texts=tuple(steering[n] for n in task.steering) if task.steering else (),
    )
    if policy is None:
        return inv
    return inv._replace(allowed_tools=policy.allowed_tools)


def _envelope_is_error(
    _base_status: str, log_path: Path, _returncode: int, reader: str | None
) -> str:
    # `reader is None` means this harness declared no log-based envelope
    # schema -- there is nothing here to parse, and a harness with no
    # envelope reports failure through its exit code and its result file,
    # which `require_result_file=True` already enforces.
    if reader is None:
        return _base_status
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


def _resolve_status(artifact: str | None, work_item_id: str, cwd: Path, reader: str | None):
    """`post_resolve` for an agent task: a bad envelope *or* a missing artifact.

    A binding that declares `artifact:` is held to producing it — the contract
    `_ARTIFACT` states in the prompt is otherwise only a request, and a worker
    that ignores it reports success while leaving the gate with nothing to
    approve. Checked here, in the one function every agent task returns
    through, so no caller can forget it (Kraft-7lu).
    """

    def resolve(base_status: str, log_path: Path, returncode: int) -> str:
        status = _envelope_is_error(base_status, log_path, returncode, reader)
        # Only a *claim of success* is held to the artifact. A worker that
        # stopped to ask a question wrote nothing precisely because it
        # stopped, and `kraft.executor.dispatch.needs_context_question` matches
        # on this status: downgrading it to `failed` loses the question, makes the
        # stop reason generic, and 409s both /steer and /resume.
        if artifact is None or status not in ("done", "done_with_concerns"):
            return status
        if (Path(cwd) / artifact_path(artifact, work_item_id)).is_file():
            return status
        return "failed"

    return resolve


def build_context(
    *,
    usage_source: str,
    context_channel: str,
    title: str,
    task_instruction: str,
    repo_path: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    session_id: str,
    artifact: str | None = None,
    #: The path `executor.prompts.review_package` wrote, for a task declaring
    #: `inputs: [review_package]` (`AgentTask.inputs`); None for every other.
    review_package: str | None = None,
    method_text: str | None = None,
    #: The repo's `intent_dir` (`RepoEntry.intent_dir`); None names no tree.
    intent_dir: str | None = None,
    steering_texts: tuple[str, ...] = (),
) -> str:
    """Kraft's contract, method, intent tree and steering, folded into one
    block of text.

    A pure function of its arguments -- it takes the two resolved harness
    *facts* (`usage_source`, `context_channel`) rather than a harness id, so a
    test can assert on it without loading `harness.py` or launching a process,
    and this module stays unaware of which harness produced those facts.
    `context_channel` does not change what is built here: whether the result
    rides in `--append-system-prompt` or is folded into the prompt itself is
    `harness.build_argv`'s job, not this one's.
    """
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
        title_line = _MR_META_KEYS if artifact == "mr_meta" else _TITLE_LINE.format(kind=artifact)
        ctx += _ARTIFACT.format(
            kind=artifact,
            path=artifact_path(artifact, work_item_id),
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            title_line=title_line,
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
            "separately unless a hunk you must judge is cut off mid-function. "
            # From round 1 of a fix loop the package is narrowed to the change
            # since the last review (Kraft-s7c04.1), and its own header says so
            # and names the command for the rest. Without this clause the
            # sentence above forbids the escape hatch that makes narrowing safe,
            # and a compliant agent reviews one round's edit believing it has
            # seen the whole change.
            "If the package's header says its range is narrowed to the change "
            "since an earlier round, the rest of the branch is still yours to "
            "read with the git command that header names -- use it when you "
            "need to judge the change as a whole.\n"
        )
    # Unconditional, and here because every agent launch -- chain dispatch,
    # gate auto-review, escalation -- builds its context through this function
    # and `run_agent_task` is `harness.build_argv`'s only caller.
    ctx += SAFETY_RULES
    if method_text:
        # After the contract, before steering: the agent reads what it must
        # produce, then how to produce it, then the house rules that apply to
        # everything. Steering stays last so it is never buried.
        ctx += _skill.HEADING + method_text + _skill.UNAVAILABLE
    if intent_dir:
        # After the method, before steering: a property of the repo, like
        # steering, but Kraft's own text (design §4).
        ctx += INTENT_HEADING + _INTENT.format(dir=intent_dir.rstrip("/"))
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
    if usage_source == "result_file":
        ctx += _USAGE_REQUEST
    return ctx


#: The most of a task's instruction that rides in the launch argv. The
#: instruction is the prompt argument *and* part of the context argument, and
#: much of it is runtime-grown with no bound of its own: the steer and
#: rejection note, the work item's description, carried and deferred findings,
#: a fix cycle's findings and round history, the judge's history. Linux refuses
#: any single argument over 128 KiB and macOS a whole argv over 1 MiB, so an
#: instruction past this is written to a file and the argv carries its head and
#: the file's path instead (Kraft-rmz4g).
INSTRUCTION_MAX_BYTES = 24 * 1024


def _bounded_instruction(run_dirs, session_id: str, task_instruction: str) -> str:
    """`task_instruction`, or its head plus where the whole of it is written.

    One place for every note an agent is launched with, because
    `run_agent_task` is the one door into `harness.build_argv`: no caller has
    to know which of its notes might grow."""
    raw = task_instruction.encode()
    if len(raw) <= INSTRUCTION_MAX_BYTES:
        return task_instruction
    path = run_dirs.results / f"{session_id}.instruction.md"
    path.write_text(task_instruction)
    head = raw[:INSTRUCTION_MAX_BYTES].decode(errors="ignore")
    head = head[: head.rfind("\n")] if "\n" in head else head
    return (
        f"{head}\n\n[Kraft cut this instruction at {len(head.encode())} of {len(raw)} "
        f"bytes to keep the launch within the OS argument limit. The whole instruction, "
        f"including everything after this point, is at {path}. Read that file before "
        f"you start: the part cut here is as much your task as the part above.]"
    )


def _restricted(
    h: _harness.Harness, harness: str, allowed: tuple[str, ...], mode: str | None
) -> dict[str, str | tuple[str, ...]]:
    """The options that hold a launch to its allowlist (Kraft-nt6tt), or
    `LaunchRefused`: a tool outside `allowed` must not run, and pre-approving
    the listed ones (`allowed_tools`) is not that. The harness's own tool
    restriction removes the unlisted built-ins, and its `under_allowlist` mode
    sends every other ask to the permission gate instead of approving it. A
    harness missing either half (no `restrict_tools` is refused with every
    other undeclared option), or a launch that chose a mode of its own, is
    refused rather than run with the bound unenforced."""
    cap = h.capabilities.get("permission_mode")
    asking = cap.under_allowlist if cap is not None else None
    # No `permission_mode` at all is no asking mode either (Kraft-pdrsi).
    if asking is None:
        raise LaunchRefused(
            f"harness {harness!r} ({h.path}) cannot hold an agent to a tool list, and this "
            f"launch's policy sets allowed_tools={list(allowed)!r}"
        )
    if mode is not None and mode != asking:
        raise LaunchRefused(
            f"this launch's policy sets allowed_tools={list(allowed)!r}, but it asks for "
            f"permission_mode={mode!r}; under an allowlist harness {harness!r} runs in "
            f"{asking!r}, the mode that asks the permission gate"
        )
    # Built-in names only: an `mcp__` tool is not the restriction flag's to
    # govern. Policy holds tool names, never rules (Kraft-9i6xy).
    names = tuple(t for t in allowed if not t.startswith("mcp__"))
    return {"restrict_tools": names, **({"permission_mode": asking} if asking else {})}


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
    harness: str = "claude",
    harnesses: _harness.HarnessSet | None = None,
    model: str | None = None,
    deny_tools: tuple[str, ...] = (),
    effort: str | None = None,
    allowed_tools: tuple[str, ...] | None = None,
    permission_mode: str | None = None,
    sandbox: dict | None = None,
    steering_texts: tuple[str, ...] = (),
    #: PARKED: see `build_context`'s own note on this parameter.
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
    #: self-action guard (`client.context._forbid_self_action`) lets it act on the
    #: very item it is escalating — see spec "The self-resume trick". Every
    #: existing caller keeps today's behavior by leaving this `True`.
    identify_as_worker: bool = True,
    head_sha: str | None = None,
    thread: int = 1,
    repo_entry: dict | None = None,
    time_cap=None,
) -> str:
    # A snapshot frozen before Kraft-9i6xy may still carry a rule: it reads
    # (`policy.FROZEN`), but it never reaches an agent (Kraft-9ct4q).
    for field, names in (("allowed_tools", allowed_tools or ()), ("deny_tools", deny_tools)):
        if why := _policy.tool_name_refusal(field, names):
            raise LaunchRefused(f"this launch's policy {why}")
    hs = harnesses if harnesses is not None else _harness.load(None)
    try:
        h = hs.valid[harness]
    except KeyError:
        raise LaunchRefused(
            f"unknown agent harness {harness!r}; known: {sorted(hs.valid)}"
        ) from None

    task_instruction = _bounded_instruction(run_dirs, session_id, task_instruction)
    ctx = build_context(
        usage_source=h.capabilities["usage"].source,
        context_channel=h.capabilities["context"].channel,
        title=title,
        task_instruction=task_instruction,
        repo_path=repo_path,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        session_id=session_id,
        artifact=artifact,
        review_package=review_package,
        method_text=method_text,
        intent_dir=(repo_entry or {}).get("intent_dir"),
        steering_texts=steering_texts,
    )
    options = {
        k: v
        for k, v in (
            ("model", model),
            ("effort", effort),
            ("permission_mode", permission_mode),
            ("deny_tools", tuple(deny_tools) or None),
            # `()` -- allow nothing -- pre-approves nothing, so it passes no
            # flag; `_restricted` below is what holds it to nothing.
            ("allowed_tools", tuple(allowed_tools or ()) or None),
            ("autocompact", autocompact),
        )
        if v
    }
    if allowed_tools is not None:
        options |= _restricted(h, harness, allowed_tools, permission_mode)
    # Loading the library and `harnesses.yaml` checks that a task's or a
    # profile's own options name capabilities its harness declares -- but
    # `resolve_invocation` folds in values that load never sees: a
    # repo's `deny_tools`/`models` (repos.yaml, editable in Settings ->
    # Repos) and a work item's own `agent_overrides` (model/effort). This is
    # the one place the fully merged value and the resolved harness are both
    # in hand, so a capability the harness does not declare is refused loudly
    # here rather than dropped flagless by `build_argv`. Not a value-pattern
    # check (`h.value_ok`): unlike a task's own fields, an override's
    # value is never checked against a harness's `values:` pattern anywhere
    # in this codebase -- only that the capability itself exists.
    for name, value in options.items():
        if name == "autocompact":
            continue
        if not h.supports(name):
            raise LaunchRefused(
                f"harness {harness!r} ({h.path}) declares no {name!r} capability, "
                f"but this launch asked for {name}={value!r}"
            )
    cmd = _harness.build_argv(
        h,
        command=command or None,
        prompt=task_instruction,
        context=ctx,
        resume=resume_session_id,
        options=options,
    )
    # One name serves usage-envelope reading, live progress and rate-limit
    # detection alike (usage.READERS): every shipped harness that declares
    # either gives it the same reader, and `harness.parse` requires
    # `structured_log` behind both, so there is one schema to pick from.
    usage_cap = h.capabilities["usage"]
    rate_limit_cap = h.capabilities.get("rate_limit_signal")
    reader = usage_cap.reader if usage_cap.source == "envelope" else None
    if reader is None and rate_limit_cap is not None:
        reader = rate_limit_cap.reader
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
        post_resolve=_resolve_status(artifact, work_item_id, cwd, reader),
        round=round,
        head_sha=head_sha,
        thread=thread,
        sandbox=sandbox,
        require_result_file=True,
        repo_entry=repo_entry,
        reader=reader,
        time_cap=time_cap,
    )
