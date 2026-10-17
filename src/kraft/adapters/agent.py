from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from kraft import harness as _harness
from kraft import ids, inplace, item_paths, store
from kraft import permission_rules as _permission_rules
from kraft import policy as _policy
from kraft import registration as _registration
from kraft import skill as _skill
from kraft.adapters import artifact_notes as _artifact_notes
from kraft.adapters import hook_install as _hook_install
from kraft.adapters import subprocess as _subprocess
from kraft.adapters.profiles import (  # noqa: F401 -- re-exported: callers use `agent.<name>`
    HarnessUnavailable,
    ProfileUnavailable,
    harness_profile,
    harness_table,
    resolve_profile,
    select_profile,
)
from kraft.addons import load as addons_load
from kraft.config import RepoEntry, git_read
from kraft.policy import InstancePolicy
from kraft.templates.environment import HarnessProfileTable
from kraft.templates.models import AgentTask
from kraft.vocab import ADVANCING
from kraft.worker import backends as _backends
from kraft.worker import callback as _callback
from kraft.worker import sandbox as _sandbox
from kraft.worker import steering as _steering
from kraft.worker.worktree_read import open_dir_no_symlinks

logger = logging.getLogger(__name__)

_NETWORK_DOCS = "https://kraft-sdlc.dev/reference/configuration/sandbox/network-policy"

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
    "When you are done, write a short session summary to {summary_path}, "
    "starting with YAML front-matter carrying exactly these keys and values:\n"
    "---\n"
    'work_item_ids: ["{work_item_id}"]\n'
    "node_id: {node_id}\n"
    "hook_point: {hook_point}\n"
    'worker_session_id: "{session_id}"\n'
    "---\n"
    "\n"
    'Report how it went as "status" in the JSON result file at '
    '$KRAFT_RESULT_PATH: "done" if you finished the work; '
    '"done_with_concerns" if you finished it but have '
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
    "{checkout}"
    "\n"
    "Exception: `.engineering/` (any spec/plan/review document an earlier "
    "run left there) is gitignored on purpose. Kraft reads those files "
    "straight off disk, never from git, so they must not reach the merge "
    "request. If `git add` refuses one of those paths, that is git working as "
    "intended — do not `git add -f` it or otherwise force it in."
)

#: Where an item's agent works: `_CTX`'s `{checkout}`, a worktree's or a person's own.
_WORKTREE = (
    "You are working in a git worktree cut for this work item, on its own "
    "branch. Commit everything you change before you exit — `git add` and "
    "`git commit`, in this worktree. Work left uncommitted never reaches the "
    "merge request this chain opens, and is destroyed with the worktree. Do "
    "not push and do not merge: Kraft pushes the branch and opens, updates "
    "and merges the merge request itself.\n"
)
_IN_PLACE = (
    "You are working in a person's own checkout, on this work item's branch "
    "`{branch}`. They may have uncommitted changes here that are not yours. "
    "Commit only the files you changed, by literal path: `git --literal-pathspecs "
    'add -- <paths>`, then `git --literal-pathspecs commit -m "..." -- <paths>` '
    "(a name like `[slug].tsx` is otherwise a pattern that can match theirs). "
    "Never `git add -A`, `git add .`, "
    "`git commit -a`, `git stash`, `git checkout`, `git switch` or `git reset`, "
    "and never touch a file you did not change. Commit everything you change "
    "before you exit: work left uncommitted never reaches the merge request. Do "
    "not push and do not merge: Kraft pushes the branch and opens, updates and "
    "merges the merge request itself.\n"
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


def artifact_path(run_dirs, kind: str, work_item_id: str) -> Path:
    """Where a task producing `kind` must write its document: its item's
    `docs/<kind>.md`, outside the repository, absolute.

    Derived on both sides — injected here, read back by
    `GET /work-items/{wid}/artifact` through `item_paths.document` — rather
    than stored on the work item. Nothing to migrate, nothing to go stale, and
    a rerun after a rejected gate revises the same file instead of leaving a
    stale pointer behind. An item that ran before the upgrade may still have
    its document at `item_paths.legacy_document_name`, in its worktree; the
    dispatch seeds `docs/` from it before a revision (`item_paths.keep_version`).
    """
    return item_paths.path_of(run_dirs, work_item_id, item_paths.document_name(work_item_id, kind))


#: The contract a hook with an `artifact:` binding is held to. Not part of the
#: method: the method says *how* to think about a spec, this says where the
#: result goes, how a reviewer finds it and what the chain does with it next
#: (`artifact_notes`). Swapping the method must not be able to lose the contract.
_ARTIFACT = (
    "\n\nWrite your {kind} to {path}, creating that directory if it does not "
    "exist, and to no other path. Start it with YAML "
    "front matter carrying exactly these keys:\n"
    "---\n"
    'work_item_ids: ["{work_item_id}"]\n'
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

#: Every launch's list of its item's documents (`documents_note`).
_DOCUMENTS = "\n\nThis work item's documents so far, by path:\n{lines}\n"


def documents_note(run_dirs, work_item_row, *, worktree: Path | None = None) -> str:
    """Where this item's documents are: each kind its chain produces or a gate
    decides that is on disk, by absolute path (`item_paths.document`, the new
    place first), then each document attached at intake, by its path in the
    worktree, where `_copy_attachments` puts it. A later node used to find a
    gate-written document by its place in the worktree; it is not there now.
    "" when there is none, or no row."""
    if work_item_row is None:
        return ""
    wid = work_item_row["id"]
    chain = store.materialized_chain_of(work_item_row)
    nodes = chain.chain.nodes if chain is not None else ()
    kinds = dict.fromkeys(
        [k for n in nodes for k in sorted(k for k in n.produces() if k)]
        + [a for n in nodes if (a := getattr(n.node, "artifact", None))]
    )
    lines = [
        f"{kind}: {item_paths.path_of(run_dirs, wid, name, worktree=worktree)}"
        for kind in kinds
        if (name := item_paths.document(run_dirs, wid, kind, worktree=worktree)) is not None
    ]
    raw = work_item_row["attachments"] if "attachments" in work_item_row.keys() else None
    lines += [
        f"{a['kind']}, attached at intake: {a['path']} in the worktree"
        for a in (json.loads(raw) if raw else [])
    ]
    return _DOCUMENTS.format(lines="\n".join(lines)) if lines else ""


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
    #: The task's named grants (`kraft.policy.GRANTS`), honoured by the gate.
    grants: tuple[str, ...] = ()


def resolve_invocation(
    binding: dict,
    repo_entry: RepoEntry | None,
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
    #: The launch's steering texts, the repository's then the task's, already
    #: resolved from the item's snapshot (`resolve_agent_task`).
    steering_texts: tuple[str, ...] = (),
    #: Each Kraft add-on namespace this launch knows, to its store; None for
    #: one that is not loaded. A skill named in one is read there, or refused.
    addon_dirs: Mapping[str, Path | None] | None = None,
) -> Invocation:
    """Fold a hook binding, a repo entry, and an item's own override into one
    launch.

    Precedence lives here and only here. Spelling it out at each call site is how
    three features that touch the same twenty lines end up disagreeing.
    """
    io = item_override or {}
    pd = profile_defaults or {}
    repo_deny = repo_entry.deny_tools if repo_entry else []
    deny = list(dict.fromkeys([*repo_deny, *binding.get("deny_tools", ())]))
    # The repository's and the task's lists were each checked on their own
    # (repo save, intake); joined here, two that each fit can still blow the
    # shared budget, and this is the only place the real concatenation exists.
    _steering.Steering.check_budget(steering_texts, where="resolve_invocation: steering combined")
    # Hook-level only, deliberately: a method is what this *hook* does, where
    # steering is what a repo demands of every hook. A repo-level default would
    # make one hook's method depend on which repo it ran in.
    method_text = (
        _skill.read(skills_dir, binding["skill"], addon_dirs) if binding.get("skill") else None
    )
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
        or (repo_entry.models.get(profile) if profile and repo_entry is not None else None)
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


class LaunchRefused(ValueError):
    """`run_agent_task` refused to start anything: the harness is unknown, or a
    merged option names a capability it does not declare. A configuration
    stop that names its cause, never a task failure a fix loop could repair
    (Kraft-hr0xr). A `ValueError` still, for callers that catch that."""


def resolve_agent_task(
    task: AgentTask,
    repo_entry: RepoEntry | None,
    library_steering: Mapping[str, str] | None = None,
    *,
    skills_dir: Path | None = None,
    escalate: bool = False,
    item_override: dict | None = None,
    harnesses: _harness.HarnessSet | None = None,
    steering: dict[str, str] | None = None,
    repository_steering: Mapping[str, Mapping[str, str]] | None = None,
    item_repo: str | None = None,
    policy: InstancePolicy | None = None,
    addons: Mapping[str, Mapping[str, str | None]] | None = None,
    repo_id: str | None = None,
) -> Invocation:
    """One V1 `AgentTask`'s launch.

    `addons` are the item's snapshot's add-on pins (`ResolvedChain.addons`):
    an add-on skill and an add-on agent profile are read from the version the
    item started with, and from the lock for an add-on it did not pin.

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
    `library.yaml`. `None` is a snapshot stored before steering was frozen:
    a task selecting steering then raises `SteeringError` rather than run
    unsteered or on today's text. `repository_steering` is the snapshot's
    frozen repository steering, injected first (`steering.for_repository`);
    `library_steering`, the live library's profiles, is read only for a
    snapshot stored before that was frozen. The profile is looked up after
    both, so a harness problem still reports as one. `item_repo` is the
    item's own repo as recorded at intake (Kraft-jzdyp); pass it for the
    item's own repository, and leave it `None` for a fanned-out member
    repository, whose frozen key is its own live `repo_entry.path`, not the
    item's.
    """
    repo_texts = _steering.for_repository(
        repo_entry, repository_steering, library_steering, item_repo=item_repo
    )
    if task.steering and steering is None:
        raise _steering.SteeringError(
            f"selects steering {list(task.steering)!r}, but this work item was materialized "
            "before steering was frozen into its snapshot, so there is no intake-time text "
            "to run it with. Re-file the item, or switch its chain before it starts."
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
    harnesses = harnesses if harnesses is not None else _harness.load(None)
    table, path = harness_table(harnesses, addons, repo_id)
    known = addons_load.for_item(addons, path.parent, repo_id=repo_id)
    for addon in known:
        if addon.left_out is None or not _reads_namespace(task, table, addon.namespace):
            continue
        if addon.namespace in (addons or {}):
            raise HarnessUnavailable(
                f"add-on {addon.id} {addon.version} could not be read: {addon.left_out}"
            )
        if addon.repo_id is not None:
            # Another repository's library, or a connected id with none: its
            # own reason, never a guess that it is another tool's plugin.
            if task.skill is not None and task.skill.startswith(f"{addon.namespace}:"):
                raise _skill.SkillError(f"skill: {task.skill}: {addon.left_out}")
            raise ProfileUnavailable(
                f"{_profile_in(task, table, addon.namespace)}: {addon.left_out}"
            )
        if not addon.quiet:
            # Left out by a fault, not pinned: the table knows its namespace
            # (a fallback into it), so stop here, never at the fallback.
            raise HarnessUnavailable(
                f"add-on {addon.id} {addon.version} could not be read: {addon.left_out}"
            )
    profile = select_profile(table.profiles, task.harness, path)
    # The task's own rung: its agent profile, read live, or its own fields.
    model, effort = task.model, task.effort
    if task.profile is not None:
        model, effort = resolve_profile(task.profile, profile, table, harnesses.valid)
    inv = resolve_invocation(
        {
            "kind": "agent",
            "harness": profile.provider,
            **({"command": profile.executable} if profile.executable else {}),
            **({"skill": task.skill} if task.skill is not None else {}),
            **({"model": model} if model is not None else {}),
            **({"effort": effort} if effort is not None else {}),
            **({"deny_tools": list(policy.deny_tools)} if policy is not None else {}),
        },
        repo_entry,
        skills_dir=skills_dir,
        addon_dirs={p.namespace: p.root if p.left_out is None else None for p in known},
        escalate=escalate,
        item_override=item_override,
        profile_defaults=profile.defaults,
        profile=profile.id,
        # Repository first, then task: the wider context before the narrower
        # one, fixed rather than merged, so a reader debugging a prompt can
        # predict what the agent saw.
        steering_texts=repo_texts + tuple((steering or {})[n] for n in task.steering),
    )
    if policy is None:
        return inv
    return inv._replace(allowed_tools=policy.allowed_tools, grants=tuple(policy.grants))


def _read_names(task: AgentTask, table: HarnessProfileTable) -> list[str | None]:
    """What a launch of `task` reads by name: its skill, its agent profile,
    and each `fallback:` entry of the task or of that profile."""
    profile = table.agent_profiles.get(task.profile) if task.profile else None
    names = [task.skill, task.profile]
    names += [e.profile for e in (*(profile.fallback if profile else ()), *(task.fallback or ()))]
    return names


def _reads_namespace(task: AgentTask, table: HarnessProfileTable, namespace: str) -> bool:
    """Whether this launch reads from add-on `namespace`: only through its
    skill or its agent profile (extends and steering were frozen at
    materialization), or a `fallback:` entry of the task or of that profile."""
    return any(n is not None and n.startswith(f"{namespace}:") for n in _read_names(task, table))


def _profile_in(task: AgentTask, table: HarnessProfileTable, namespace: str) -> str:
    """The first agent profile (or fallback) of `task` in `namespace`."""
    names = _read_names(task, table)[1:]
    return next(n for n in names if n is not None and n.startswith(f"{namespace}:"))


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
    except (json.JSONDecodeError, RecursionError):
        return _base_status
    if isinstance(envelope, dict) and envelope.get("is_error") is True:
        return "failed"
    return _base_status


def _resolve_status(
    artifact: str | None, work_item_id: str, run_dirs, cwd: Path, reader: str | None
):
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
        if artifact is None or status not in ADVANCING:
            return status
        # Either place: a worker re-adopted after the upgrade may be on
        # instructions that named the worktree's `.engineering/`. Never
        # through a link (`item_paths.document`).
        if item_paths.document(run_dirs, work_item_id, artifact, worktree=Path(cwd)) is not None:
            return status
        return "failed"

    return resolve


def build_context(
    *,
    usage_source: str,
    context_channel: str,
    steering_texts: tuple[str, ...] = (),
    **kwargs,
) -> str:
    """Kraft's contract, method, intent tree and steering, folded into one
    block of text: `context_sections` joined, so the launch and the steering
    preview cannot disagree about what an agent reads.

    A pure function of its arguments -- it takes the two resolved harness
    *facts* (`usage_source`, `context_channel`) rather than a harness id, so a
    test can assert on it without loading `harness.py` or launching a process,
    and this module stays unaware of which harness produced those facts.
    `context_channel` does not change what is built here: whether the result
    rides in `--append-system-prompt` or is folded into the prompt itself is
    `harness.build_argv`'s job, not this one's.
    """
    sections = context_sections(
        usage_source=usage_source, steering=[("", t) for t in steering_texts], **kwargs
    )
    return "".join(text for _, _, text in sections)


def context_sections(
    *,
    usage_source: str,
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
    #: The skill `method_text` was read from: the skill section's source only.
    skill: str | None = None,
    #: The repo's `intent_dir` (`RepoEntry.intent_dir`); None names no tree.
    intent_dir: str | None = None,
    #: `(source, text)` per steering profile, the repository's then the task's.
    steering: Sequence[tuple[str, str]] = (),
    #: Where the session writes its summary (`SessionFiles.summary`, an
    #: escalation thread's shared one), absolute; None shows a placeholder
    #: (the steering preview, which has no session).
    summary_path: str | None = None,
    #: Where it writes the `artifact` document (`artifact_path`), absolute;
    #: None shows a placeholder, as for `summary_path`.
    document_path: str | None = None,
    #: The item's documents so far (`documents_note`), "" for none.
    documents: str = "",
    #: The item runs in a person's own checkout, on `branch`.
    in_place: bool = False,
    branch: str = "<branch>",
) -> list[tuple[str, str, str]]:
    """A launch's context as `(kind, source, text)` sections, in the order the
    agent reads them; joined, they are exactly `build_context`'s text."""
    sections = [
        (
            "contract",
            "kraft",
            _CTX.format(
                title=title,
                task_instruction=task_instruction,
                repo_path=repo_path,
                work_item_id=work_item_id,
                node_id=node_id,
                hook_point=hook_point,
                session_id=session_id,
                summary_path=summary_path or "<session summary path>",
                checkout=_IN_PLACE.format(branch=branch) if in_place else _WORKTREE,
            ),
        )
    ]
    if artifact:
        title_line = _MR_META_KEYS if artifact == "mr_meta" else _TITLE_LINE.format(kind=artifact)
        sections.append(
            (
                "document",
                artifact,
                _ARTIFACT.format(
                    kind=artifact,
                    path=document_path or "<document path>",
                    work_item_id=work_item_id,
                    node_id=node_id,
                    hook_point=hook_point,
                    title_line=title_line,
                )
                + _artifact_notes.NOTES.get(artifact, ""),
            )
        )
    if documents:
        # Every launch, not only the node's own default instruction: a later
        # node used to find a document by its place in the worktree, and it
        # is not there any more.
        sections.append(("documents", "kraft", documents))
    if review_package:
        # By path, like $KRAFT_RESULT_PATH. A diff pasted into every review of
        # every cycle of every work item is the token cost sub-project G §4
        # already refuses for result files.
        sections.append(
            (
                "review_package",
                "kraft",
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
                "need to judge the change as a whole.\n",
            )
        )
    if method_text:
        # After the contract, before steering: the agent reads what it must
        # produce, then how to produce it, then the house rules that apply to
        # everything. Steering stays last so it is never buried.
        sections.append(("skill", skill or "", _skill.HEADING + method_text + _skill.UNAVAILABLE))
    if intent_dir:
        # After the method, before steering: a property of the repo, like
        # steering, but Kraft's own text (design §4).
        sections.append(
            ("intent", intent_dir, INTENT_HEADING + _INTENT.format(dir=intent_dir.rstrip("/")))
        )
    # The context-injection boundary (00_overview.md glossary) bans
    # CLAUDE.md, AGENTS.md and any repo file as a context channel. That
    # rule governs the *channel*: this is the sanctioned one — the
    # per-invocation system prompt — carrying the steering profiles of
    # Kraft's own library.yaml. Kraft reads nothing from inside
    # the target repo to build this. Written here because a future
    # reader finding a steering feature beside a rule banning steering
    # files would otherwise assume the rule was forgotten.
    # One section per profile, `Steering.block` split at its separators: the
    # first carries the heading, each later one the separator before it.
    for i, (source, text) in enumerate(steering):
        lead = _steering.Steering.HEADING if i == 0 else _steering.Steering.SEP
        sections.append(("steering", source, lead + text))
    if usage_source == "result_file":
        sections.append(("usage", "kraft", _USAGE_REQUEST))
    return sections


#: The most of a task's instruction that rides in the launch argv. The
#: instruction is the prompt argument *and* part of the context argument, and
#: much of it is runtime-grown with no bound of its own: the steer and
#: rejection note, the work item's description, carried and deferred findings,
#: a fix cycle's findings and round history, the judge's history. Linux refuses
#: any single argument over 128 KiB and macOS a whole argv over 1 MiB, so an
#: instruction past this is written to a file and the argv carries its head and
#: the file's path instead (Kraft-rmz4g).
INSTRUCTION_MAX_BYTES = 24 * 1024


def _writable_dirs(run_dirs, files: item_paths.SessionFiles, work_item_id: str, cwd) -> str:
    """The `writable_dirs` value: a JSON array (also a TOML inline array) of
    the directories outside the worktree a worker must write (Kraft-rs9pk).

    The session's `out/`, which holds its result file and summary, and its
    item's `docs/`. Never the session folder itself: it holds the log Kraft
    reads usage and rate limits from and the exit code the host writes with
    a plain `>`, and for `codex` this list is its OS sandbox's writable roots
    (`harnesses/codex.yaml`), so a worker granted it could plant a link
    there. And the worktree's git common dir, where every commit writes --
    the main checkout's `.git`, outside a linked worktree -- asked of git
    rather than guessed, and left out when git has none to give."""
    dirs = [str(files.writable), str(item_paths.docs_dir(run_dirs, work_item_id))]
    common = git_read(
        Path(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir", expected_failure=True
    )
    if common:
        dirs.append(common)
    return json.dumps(dirs, ensure_ascii=False)


def log_reader(h: _harness.Harness) -> str | None:
    """The `usage.READERS` schema `h`'s log is read with, or None for a harness
    that declares none.

    One name serves usage-envelope reading, live progress, rate-limit
    detection and the resumable session id alike: every shipped harness that
    declares either gives it the same reader, and `Harness.from_input` requires
    `structured_log` behind both, so there is one schema to pick from."""
    usage_cap = h.capabilities["usage"]
    rate_limit_cap = h.capabilities.get("rate_limit_signal")
    reader = usage_cap.reader if usage_cap.source == "envelope" else None
    if reader is None and rate_limit_cap is not None:
        reader = rate_limit_cap.reader
    return reader


def _config_dir(
    run_dirs, h: _harness.Harness, name: str, sandbox_home: Path | None = None
) -> dict[str, str]:
    """The env pointing `h`'s CLI at a config directory Kraft owns, freshly
    written, or `{}` for a harness that declares none (Kraft-bosip). `name`
    is the session's `ids.qualified` name, which tags each temporary file in
    the shared directory: it is every item's, and a session id is unique only
    within its item.

    One directory per harness under $KRAFT_HOME/run, not per launch: the CLI
    keeps its chats there too, and `resume` must find the chat an earlier
    launch wrote. The files are rewritten on every launch, so whatever the CLI
    changed in them never outlives a session; each write is a rename, so two
    launches at once never read half a file. The user's own config is never
    read or touched.

    A sandboxed item gets a directory of its own inside its `sandbox_home`,
    which its sandboxes already mount: the shared one would be a directory
    one item's worker could rewrite under every other item's launches,
    sandboxed or not. That home is the worker's to write, so nothing in it is
    trusted: the directory is opened one component at a time with no link
    followed (`open_dir_no_symlinks`), and each file is written relative to
    it, so a link a worker planted is refused or replaced, never written
    through. A refusal is `LaunchRefused`, naming the directory."""
    if h.config_env is None:
        return {}
    if sandbox_home is None:
        directory = run_dirs.base / "harness-config" / h.id
        directory.mkdir(parents=True, exist_ok=True)
        for file, text in h.config_files.items():
            tmp = directory / f".{file}.{name}"
            tmp.write_text(text)
            os.replace(tmp, directory / file)
        return {h.config_env: str(directory)}
    directory = sandbox_home / ".kraft-harness-config" / h.id
    try:
        # The home itself is Kraft's (`item_paths.sandbox_home`, which takes
        # no link for the old one); everything below it is the worker's.
        sandbox_home.mkdir(parents=True, exist_ok=True)
        dir_fd = open_dir_no_symlinks(sandbox_home, (".kraft-harness-config", h.id), create=True)
        try:
            for file, text in h.config_files.items():
                _replace_at(dir_fd, file, text)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        raise LaunchRefused(
            f"cannot write {h.id}'s config into {directory}: {exc}; something in the "
            f"sandbox home is not a plain folder or file (a link is never followed), "
            f"so remove it and retry"
        ) from exc
    return {h.config_env: str(directory)}


def _replace_at(dir_fd: int, name: str, text: str) -> None:
    """Write `text` to `name` in the folder `dir_fd` by way of a temporary
    file, a rename so a reader never sees half a file. The temporary file has
    a random name, so two launches at once never share one, and is created
    with `O_CREAT | O_EXCL | O_NOFOLLOW`, so a file or link already holding
    that name refuses the write rather than being written through. The rename
    replaces a link at `name` rather than writing through it."""
    tmp = f".{name}.{secrets.token_hex(8)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise


def _bounded_instruction(path: Path, task_instruction: str) -> str:
    """`task_instruction`, or its head plus where the whole of it is written.

    One place for every note an agent is launched with, because
    `run_agent_task` is the one door into `harness.build_argv`: no caller has
    to know which of its notes might grow."""
    raw = task_instruction.encode()
    if len(raw) <= INSTRUCTION_MAX_BYTES:
        return task_instruction
    # The session's own folder, which a worker never writes: a sandbox mounts
    # the file read-only.
    path.parent.mkdir(parents=True, exist_ok=True)
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


async def _cli_version(exe: list[str], name: str, cwd: str | Path, runner=None) -> str | None:
    """`exe --version`, parsed; None when it cannot be told -- not installed,
    no answer, no version in it -- and the launch goes ahead as ever. `runner`
    is a sandbox backend's `oneshot`, asking the image's own CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *(runner.argv() if runner is not None else ()),
            *exe,
            "--version",
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 60)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None
    except OSError:
        return None
    finally:
        if runner is not None:
            await runner.close()
    return _harness.find_version(out.decode(errors="replace"), name)


def _install_hook(
    h: _harness.Harness,
    cwd: Path,
    allowed: tuple[str, ...] | None,
    deny: tuple[str, ...],
    grants: tuple[str, ...],
    sandbox: dict | None,
    *,
    in_place: bool = False,
) -> None:
    """Kraft's pre-tool hook in the worktree when there is policy to enforce
    (Kraft-4in7z). The entry is the same for every launch and never removed:
    fail-closed is per session (`FAIL_CLOSED_ENV`), so a sibling launch in the
    same worktree cannot loosen or drop another's hook. Never written into a
    person's own checkout: refused instead."""
    if not _hook_install.needs_hook(allowed, deny, grants):
        return
    if h.permission_hook == "cursor" and in_place:
        raise LaunchRefused(
            "cursor's permission hook writes .cursor/hooks.json into the folder it runs in; "
            "an in-place item cannot use it. Pick another harness for this item."
        )
    # ponytail: a hook `allow` does not outrank cursor's --auto-review
    # (Task 1, probe B), so a grant beyond git-commit is enforced only as
    # far as the gate's denies go; its launch-time allow rule is Kraft-4in7z.6.
    if h.permission_hook == "cursor":
        try:
            _hook_install.install_cursor_hook(cwd, _hook_install.hook_argv(h.id, sandbox))
        except _hook_install.HookFileError as exc:
            # Its policy needs the hook; without it the launch runs unenforced.
            raise LaunchRefused(str(exc)) from exc


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
    #: The task's named grants; any beyond `git-commit` need the hook (Kraft-4in7z).
    grants: tuple[str, ...] = (),
    permission_mode: str | None = None,
    sandbox: dict | None = None,
    #: The sandbox's whole checkout (`subprocess.run_task`'s `checkout`).
    checkout=None,
    steering_texts: tuple[str, ...] = (),
    #: PARKED: see `context_sections`' own note on this parameter.
    review_package: str | None = None,
    artifact: str | None = None,
    method_text: str | None = None,
    #: `--resume <id>`: an escalation turn continuing its item's thread.
    resume_session_id: str | None = None,
    #: `--autocompact <value>`; only `escalate.dispatch` sets it.
    autocompact: str | None = None,
    #: `False` only for an escalation turn: the child then gets no
    #: `KRAFT_WORK_ITEM_ID`, so `client.resolve_context()` resolves it as a
    #: human's own session rather than a worker's, and the self-action guard
    #: (`client.context._forbid_self_action`) lets it act on the very item it
    #: is escalating — see spec "The self-resume trick".
    identify_as_worker: bool = True,
    head_sha: str | None = None,
    thread: int = 1,
    repository: str | None = None,
    repo_entry: RepoEntry | None = None,
    time_cap=None,
    #: The session's files, else its own folder's (`item_paths.session_files`):
    #: an escalation turn's result and summary are its thread's (Kraft-s7c04.54).
    files: item_paths.SessionFiles | None = None,
    harness_id: str | None = None,  # the harnesses.yaml id; keys `rate_limit_hit`
    #: An agent outside the walk: `store.CheckoutBusy` while another in-place
    #: item uses the checkout (`store.create_session`).
    takes_checkout: bool = False,
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

    files = files or item_paths.session_files(run_dirs, work_item_id, session_id)
    task_instruction = _bounded_instruction(files.instruction, task_instruction)
    # Every launch is told where its item's documents are, whoever launched
    # it: a node's own pass, a fix loop, an override, an escalation turn or a
    # gate review.
    item_row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    in_place = item_row is not None and store.is_in_place(item_row)
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
        intent_dir=repo_entry.intent_dir if repo_entry is not None else None,
        steering_texts=steering_texts,
        summary_path=str(files.summary),
        document_path=str(artifact_path(run_dirs, artifact, work_item_id)) if artifact else None,
        documents=documents_note(run_dirs, item_row, worktree=Path(cwd)),
        in_place=in_place,
        branch=store.branch_for(item_row) if in_place else "<branch>",
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
    # A harness whose hook holds the tool lists needs no restriction flag, nor
    # does one whose own permission config is written with them.
    hooked = h.permission_hook is not None
    ruled = h.permission_rules is not None
    if allowed_tools is not None and not hooked and not ruled:
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
    if blind := [
        t
        for t in h.unhooked_tools
        if t in deny_tools or (allowed_tools is not None and t not in allowed_tools)
    ]:
        raise LaunchRefused(
            f"harness {harness!r} ({h.path}) never sees a {'/'.join(h.unhooked_tools)} call "
            f"in its hook, so it cannot deny {blind!r} as this launch's policy requires; "
            f"list them in allowed_tools and keep them out of deny_tools, or use another harness"
        )
    channel = bool(sandbox and sandbox.get("network"))
    if sandbox and not channel:
        if not sandbox.get("unrestricted_network"):
            # Kraft sets up no channel and does not restrict this container's
            # network, so nothing keeps the worker from dialing Kraft's API
            # (Kraft-1q6d7), whatever its harness.
            raise LaunchRefused(
                f"this launch is sandboxed in {repo_path} with no `network:` policy, which "
                f"leaves the worker's network unrestricted and gates not enforced; add a "
                f"`network:` policy to the sandbox ({_NETWORK_DOCS}), or, if you accept "
                f"that, set `unrestricted_network: true` on it"
            )
        logger.warning(
            "%s: sandboxed with `unrestricted_network: true`: the worker's network is not "
            "restricted and gates are not enforced",
            repo_path,
        )
    asks = h.capabilities.get("approval_channel")
    if sandbox and not channel and asks is not None and asks.always:
        # Every launch names Kraft's MCP permission tool, and only a channel
        # reaches a Kraft MCP server from a sandbox: without one the CLI exits
        # at start naming the missing tool (spike 5.5).
        raise LaunchRefused(
            f"harness {harness!r} asks Kraft's permission gate through an MCP tool on every "
            f"launch, which a sandbox without `network:` cannot reach (the CLI would exit at "
            f"start); give the sandbox a `network:`, or run this task unsandboxed"
        )
    if (
        sandbox
        and not channel
        and hooked
        and _hook_install.needs_hook(allowed_tools, deny_tools, grants)
    ):
        # The hook reaches Kraft only through the session's channel (the
        # `kraft` shim), and a sandbox without `network:` has none: it would
        # crash, and a crashed hook allows the call (codex, measured; cursor
        # by its docs).
        raise LaunchRefused(
            f"harness {harness!r} holds this launch's tool policy with Kraft's permission "
            f"hook, which cannot run inside a sandbox without `network:` (its only route "
            f"to Kraft), and a sandboxed launch never runs with its policy unenforced; "
            f"give the sandbox a `network:`, use a harness whose own rules the container "
            f"holds (amp, opencode), or remove the tool policy (allowed_tools, "
            f"deny_tools, or a grant beyond git-commit) at the layer that sets it"
        )
    if sandbox and sandbox.get("network") and not h.proxy_aware:
        # Its only route out is the relay's proxy, which this CLI ignores: it
        # would fail closed, and mysteriously (spec §4).
        raise LaunchRefused(
            f"harness {harness!r} ({h.path}) declares `proxy_aware: false`: it ignores "
            f"HTTP(S)_PROXY, and a sandbox with `network:` has no other route out; use "
            f"another harness, or remove the sandbox's `network`"
        )
    if h.min_version is not None:
        exe = shlex.split(command) if command else [h.command[0]]
        # ponytail: a sandboxed launch asks its image on every launch (one short
        # container); cache on the image id if that shows.
        try:
            runner = (
                await asyncio.to_thread(
                    _backends.for_sandbox(sandbox).oneshot,
                    sandbox,
                    cwd,
                    _backends.for_sandbox(sandbox).home(run_dirs, work_item_id),
                )
                if sandbox
                else None
            )
        except _sandbox.SandboxNotReady:
            found = None  # the backend's own probe refuses this launch, by name
        else:
            found = await _cli_version(exe, h.id, cwd, runner)
        if found is not None and _harness.version_key(found) < _harness.version_key(h.min_version):
            raise LaunchRefused(
                f"harness {harness!r} ({h.path}) needs {shlex.join(exe)} {h.min_version} or newer, "
                f"and {'the sandbox image has' if sandbox else 'this machine has'} "
                f"{found}; older releases refuse its command line"
            )
    if sandbox and h.container_permission_mode is not None:
        default = h.capabilities["permission_mode"].always
        defaults = (default,) if isinstance(default, str) else tuple(default or ())
        if options.get("permission_mode") in (None, *defaults):
            options["permission_mode"] = h.container_permission_mode
    if hooked:
        _install_hook(h, Path(cwd), allowed_tools, deny_tools, grants, sandbox, in_place=in_place)
    hook_argv: tuple[str, ...] = ()
    if h.permission_hook == "codex" and _hook_install.needs_hook(allowed_tools, deny_tools, grants):
        exe = shlex.split(command) if command else [h.command[0]]
        try:
            # The session's own codex vouches for the hook: a sandbox's is the
            # image's, and another version may hash it otherwise (skipped).
            runner = (
                await asyncio.to_thread(
                    _backends.for_sandbox(sandbox).oneshot,
                    sandbox,
                    cwd,
                    _backends.for_sandbox(sandbox).home(run_dirs, work_item_id),
                )
                if sandbox
                else None
            )
            hook_argv = await _hook_install.codex_hook_flags(
                exe, _hook_install.hook_argv(h.id, sandbox), Path(cwd), runner
            )
        except (_hook_install.CodexTrustError, _sandbox.SandboxNotReady) as exc:
            # Never launched unenforced, never with every hook trusted.
            raise LaunchRefused(
                f"harness {harness!r} cannot run Kraft's permission hook, which this "
                f"launch's policy needs: {exc}"
            ) from exc
    rules_env: dict[str, str] = {}
    rules_argv: tuple[str, ...] = ()
    rules_files: tuple[str, ...] = ()
    if ruled:
        if missing := _permission_rules.unmapped(h.tool_names, allowed_tools, deny_tools):
            raise LaunchRefused(
                f"harness {harness!r} ({h.path}) has no tool that {missing!r} maps to "
                f"(its tool_names), so its permission rules cannot hold them as this "
                f"launch's policy requires; drop them from the policy or use another harness"
            )
        rules_dir = run_dirs.base / "harness-config" / h.id
        rules_env, rules_argv = _permission_rules.render(
            h.permission_rules,
            h.tool_names,
            allowed_tools,
            tuple(deny_tools),
            directory=rules_dir,
            # The folder is every item's: named for the item too.
            session_id=ids.qualified(work_item_id, session_id),
        )
        rules_files = _permission_rules.files_in(rules_argv, rules_dir)
    # Kraft's own value, not a task's, so the check above never sees it.
    if h.supports("writable_dirs"):
        options["writable_dirs"] = _writable_dirs(run_dirs, files, work_item_id, cwd)
    if channel and h.supports("mcp_config"):
        # Its only MCP server is the session's own, through the channel: the
        # host's registration is not in the sandbox (spike 5.5).
        url = f"{_callback.address(sandbox['kind'])}/mcp"
        options["mcp_config"] = json.dumps({"mcpServers": {"kraft": {"type": "http", "url": url}}})
    elif not sandbox and asks is not None and asks.always == _registration.DIRECT:
        # On the host the tool's name is whatever registered the server: a
        # plugin-only install namespaces it, and a name that does not exist
        # fails the session's first permission ask, not its start.
        found = await asyncio.to_thread(_registration.permission_tool, Path(cwd))
        if found is None:
            raise LaunchRefused(
                f"harness {harness!r} asks Kraft's permission gate through the `kraft` MCP "
                f"server, and nothing registers it for Claude Code: {_registration.FIX}"
            )
        options["approval_channel"] = found[0]
    cmd = _harness.build_argv(
        h,
        command=command or None,
        prompt=task_instruction,
        context=ctx,
        resume=resume_session_id,
        options=options,
        extra=(*hook_argv, *rules_argv),
    )
    reader = log_reader(h)
    config_env = _config_dir(
        run_dirs,
        h,
        ids.qualified(work_item_id, session_id),
        _backends.for_sandbox(sandbox).home(run_dirs, work_item_id)
        if sandbox and h.config_env is not None
        else None,
    )
    # An agent outside the walk does, in place, what the walk does around a
    # task (`dispatch._dispatch_task`): take the checkout once the session
    # holds its lease, mark it, and check it after.
    borrows = takes_checkout and in_place

    async def take_and_mark() -> None:
        await inplace.take(db, run_dirs, item_row, attachments=inplace.attachments_of(item_row))
        await inplace.mark_task(db, run_dirs, item_row, Path(cwd), node_id, hook_point)

    status = await _subprocess.run_task(
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
            # `<wid>/<sid>`: a session id is unique only within its item, and
            # an escalation turn has no KRAFT_WORK_ITEM_ID to say which.
            "KRAFT_SESSION_ID": ids.session_ref(work_item_id, session_id),
            # Where its documents go: a sandbox mounts this folder read-write.
            "KRAFT_DOCS_DIR": str(item_paths.docs_dir(run_dirs, work_item_id)),
            **(
                {_hook_install.FAIL_CLOSED_ENV: "1"} if hooked and allowed_tools is not None else {}
            ),
            **({"KRAFT_REVIEW_PACKAGE": review_package} if review_package else {}),
            **config_env,
            **rules_env,
        },
        post_resolve=_resolve_status(artifact, work_item_id, run_dirs, cwd, reader),
        round=round,
        head_sha=head_sha,
        thread=thread,
        repository=repository,
        sandbox=sandbox,
        checkout=checkout,
        require_result_file=True,
        repo_entry=repo_entry,
        reader=reader,
        time_cap=time_cap,
        network_requires=h.network_requires,
        declared=h.credentials,
        files=files,
        rate_limit_key={"harness": harness_id, "model": model} if harness_id else None,
        harness=harness,
        takes_checkout=takes_checkout,
        model=model,
        ro_paths=rules_files,
        before_launch=take_and_mark if borrows else None,
    )
    if borrows:
        await inplace.check_task(db, run_dirs, item_row, Path(cwd), node_id, hook_point)
    return status
