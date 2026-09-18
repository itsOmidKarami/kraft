from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import shlex
import sqlite3
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from kraft import builtins as _builtins
from kraft import config as _config
from kraft import events, store
from kraft import findings as _findings
from kraft import policy as _policy
from kraft import sandbox as _sandbox
from kraft.adapters import agent as _agent
from kraft.adapters import forge as _forge
from kraft.adapters import subprocess as _subprocess
from kraft.executor import entry, prompts, stops
from kraft.executor.context import (
    BUDGET,
    CONFIG_ERROR,
    INFRA_STOP,
    RATE_LIMITED,
    WAITING,
    LaunchContext,
    Steer,
)
from kraft.store import _now as _now
from kraft.templates import Registry

logger = logging.getLogger(__name__)


def _current_base_ref(db, work_item_id: str) -> str | None:
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    return row["base_ref"] if row else None


def _path_matches(path: str, patterns: list[str]) -> bool:
    """Gitignore-style precedence over one scope's own `paths` list: the last
    pattern `path` matches wins, so a later `!excl/**` overrides an earlier
    inclusive glob (test-scope design §3.4)."""
    matched = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if fnmatch.fnmatchcase(path, pattern[1:]):
                matched = False
        elif fnmatch.fnmatchcase(path, pattern):
            matched = True
    return matched


def _matched_scopes(scopes: list[dict], changed_paths: list[str]) -> list[dict]:
    """Scopes covering `changed_paths`, deduped and in `scopes`' own order.

    Fails open in both directions the design calls out: a changed path
    matching no scope at all, or an empty diff to begin with, runs every
    scope rather than guess. Under-testing is the bug this exists to close;
    it must never reopen it here (test-scope design §3.5).
    """
    hit: set[int] = set()
    for path in changed_paths:
        path_hit = {i for i, scope in enumerate(scopes) if _path_matches(path, scope["paths"])}
        if not path_hit:
            return list(scopes)
        hit |= path_hit
    return [scopes[i] for i in sorted(hit)] if hit else list(scopes)


#: Statuses a scope-loop iteration can return that say nothing about the code
#: under test -- a human paused the item, the scope's own command could not
#: launch, or a rate limit was hit. C2 (Kraft-s7c04.9) stops the scope loop
#: from short-circuiting on a genuine test failure, but these three still end
#: it early: none of them are evidence a later scope would tell us anything
#: about, and for `paused`/`CONFIG_ERROR` running more subprocesses after one
#: would be actively wrong (a human asked everything to stop; the next
#: scope's binary may be missing too).
_SCOPE_STOP_STATUSES = frozenset({"paused", CONFIG_ERROR, RATE_LIMITED})


def _last_own_round_head(
    db, work_item_id: str, node_id: str, task_hook: str, round: int
) -> str | None:
    """The head `round - 1`'s dispatch of `task_hook` on this exact `node_id`
    ran at, if every scope in that dispatch finished `done` -- otherwise
    `None` (C7 review fix, Kraft-s7c04.14).

    `prompts._last_reviewed_head` is the wrong source for this: it is keyed
    on `(work_item_id, hook_point)` alone, not `node_id`, and takes the
    latest `done` row regardless of its siblings. For a while (C1,
    Kraft-s7c04.8, reverted 2026-09-16) `on.test.run` was dispatched by both
    `implementation` and `verify` under that one hook_point, with one
    session row per scope -- so that query would have let one node's head
    stand in for the other's (verify's round 0 reading C1's head and seeing
    an empty diff, breaking the spec's "first round selects from the full
    branch diff"). That reason for scoping to `node_id` is gone with C1, but
    the other one is independent and still live: it also stops one passing
    scope's row from standing in for a dispatch whose sibling scope failed
    (the `retry_after_cap` path: scope A failed, scope B passed, the counter
    reset wipes the round, and a later HEAD move would otherwise be missed
    because a passing B's row still reads as "reviewed"). Scoping to this
    node's own previous round, and requiring every row in it to be `done`
    with the same `head_sha`, closes both. Coarser than tracking exactly
    which scope(s) failed: precise per-scope identity would need scope
    identity persisted on the session row, which this batch does not add.
    Treating *any* failure (or an inconsistent head across siblings) as "not
    clean" can only over-select, matching `_matched_scopes`' own fail-open
    posture.
    """
    rows = [
        r
        for r in db.read(lambda c: store.sessions_for_round(c, work_item_id, node_id, round - 1))
        if r["hook_point"] == task_hook
    ]
    if not rows or any(r["status"] != "done" for r in rows):
        return None
    heads = {r["head_sha"] for r in rows}
    return heads.pop() if len(heads) == 1 else None


def _select_scopes(
    db,
    work_item_id: str,
    worktree,
    node_id: str,
    task_hook: str,
    round: int,
    binding: dict,
    repo_entry: dict,
) -> tuple[list[dict], dict | None]:
    """The scopes a `kind: subprocess` `on.test.run`-style dispatch should
    run, and the sandbox to run them under (test-scope design §3.2-3.3, C7
    Kraft-s7c04.14).

    Pulled out of `dispatch_node`'s subprocess branch (batch-c1 spec Task 3).
    C1 briefly gave `implementation` its own `on.test.run` dispatch through
    this same function, so the two computed scopes identically by
    construction; C1 was reverted (Kraft-s7c04.8, 2026-09-16) because a gate
    dispatched after the session exits cannot be acted on by it. `verify` is
    this function's only caller now, and the same-selection property C1 was
    after is instead conveyed to the implementation agent as a prompt note
    (`prompts.scope_note`) built from the repo's scope table, so it can apply
    the same rule to its own diff before it finishes.

    The repo's own command(s) win over the registry's. The registry is per
    install and one command for every repo on it; test_scopes is a property
    of the repo, and a hardcoded single command is how verify ends up running
    something CI does not, or the wrong stack's suite entirely (Kraft-579,
    Kraft-9wzy). `config.load_repos` already wraps a legacy `test_command`
    into a single `["**"]` scope, so this is one shape regardless of which
    field an operator set.

    C7: round 0 (and a round-0 re-entry, e.g. after `retry_after_cap` wipes
    the round counter) always selects from the whole branch diff since
    `base_ref` -- the spec's first acceptance criterion, and the only choice
    that can't mistake a different node's gate dispatch, or a still-open
    round, for "already reviewed". Only a round that follows this exact
    `(node_id, task_hook)`'s own fully clean previous round (`done`, every
    sibling scope, one shared `head_sha` -- `_last_own_round_head`) narrows
    the diff to what changed since then, because `6c712ea8` ran a
    13-minute `just ci-test` for a frontend-only fix. That alone would
    under-test once C2 lets a round go red on more than one scope: a round
    following any failure at this hook does not trust the incremental diff
    at all and falls back to the whole-branch diff (`_last_own_round_head`
    itself returns `None` for an unclean round, which collapses to
    `base_ref` below), unioning in every scope from that round rather than
    only the one(s) that failed. Selecting less is only ever safe for a
    scope that passed.
    """
    sandbox = _sandbox.resolve(binding, repo_entry)
    repo_scopes = repo_entry.get("test_scopes")
    if not repo_scopes and repo_entry.get("test_command"):
        # `config.load_repos` already wraps a bare `test_command` into a
        # `test_scopes` entry for any repo it reads off disk -- this mirrors
        # that for a `LaunchContext` built by hand (tests, or any future
        # caller that skips the yaml round-trip).
        repo_scopes = [{"paths": ["**"], "command": repo_entry["test_command"]}]
    scopes = (
        [{"paths": s["paths"], "cmd": shlex.split(s["command"])} for s in repo_scopes]
        if repo_scopes
        else [{"paths": ["**"], "cmd": list(binding["command"])}]
    )
    base_ref = _current_base_ref(db, work_item_id)
    # `since` is the diff's lower bound: round <= 0 always measures the whole
    # branch (never reads another round or another node's head), and only a
    # round that follows this exact node's own fully clean previous round at
    # this hook narrows it -- everything else (an unclean previous round, a
    # `since` `git` no longer knows about, no previous round at all) falls
    # through to the whole branch.
    since = _last_own_round_head(db, work_item_id, node_id, task_hook, round) if round > 0 else None
    if (
        since
        and _config.git_read(Path(worktree), "rev-parse", "--verify", f"{since}^{{commit}}") is None
    ):
        since = None
    since = since or base_ref
    diff = (
        _config.git_read(Path(worktree), "diff", "--name-only", f"{since}...HEAD")
        if since
        else None
    )
    to_run = scopes if diff is None else _matched_scopes(scopes, diff.splitlines())
    return to_run, sandbox


async def dispatch_node(
    db,
    run_dirs,
    task_hook,
    node,
    work_item_row,
    registry: Registry,
    worktree,
    *,
    instruction_override: str | None = None,
    round: int = 0,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    budget: _policy.Budget = _policy.NO_BUDGET,
    escalate: bool = False,
    loop_severities: frozenset[str] = _policy.DEFAULT_LOOP_SEVERITIES,
) -> str:
    binding = registry.hooks[task_hook]
    session_id = uuid.uuid4().hex
    kind = binding["kind"]
    # The commit the measurement is about (Kraft-lu2). Resolved here, once, at
    # dispatch: a sha read later would be whatever HEAD moved to while the task
    # ran, which is the opposite of the question the gate asks. `git_read`
    # never raises -- None for on.env.prepare, whose worktree does not exist yet.
    common = dict(
        session_id=session_id,
        work_item_id=work_item_row["id"],
        node_id=node["id"],
        round=round,
        head_sha=_config.git_read(Path(worktree), "rev-parse", "HEAD"),
    )
    if kind == "builtin" and binding.get("handler") == "env_setup":
        return await _builtins.env_setup(
            db,
            run_dirs,
            repo=work_item_row["repo"],
            attachments=entry.attachments_of(work_item_row),
            repo_entry=launch.repo_entry if launch else None,
            **common,
        )
    if kind == "builtin" and binding.get("handler") == "noop":
        return await _builtins.noop(db, run_dirs, hook_point=task_hook, **common)
    if kind == "builtin" and binding.get("handler") == "scan_submodules":
        return await _builtins.scan_submodules(
            db,
            run_dirs,
            hook_point=task_hook,
            repo=work_item_row["repo"],
            worktree=str(worktree),
            **common,
        )
    if kind == "builtin" and binding.get("handler") == "mr_rebase":
        return await _builtins.mr_rebase(
            db,
            run_dirs,
            hook_point=task_hook,
            repo=work_item_row["repo"],
            worktree=str(worktree),
            branch=store.branch_for(work_item_row),
            **common,
        )
    if kind == "agent":
        # Only agent tasks. A subprocess or builtin costs nothing, and stopping
        # `on.test.run` for a budget would strand the item mid-node for no saving.
        if stops.budget_breach(db, work_item_row["id"], budget) is not None:
            return BUDGET
        # Authorship travels with the note, not with the caller: a seeded
        # steer is Kraft's own recap of the last review's unresolved findings,
        # and the human templates in `steer_prefix` would tell the agent a
        # person wrote it (the same misattribution `Steer.human` keeps out of
        # the fix-loop judge). Read before `take()`, and on `is not None`, not
        # truthiness: `Steer.__bool__` is about having text left, and `take()`
        # has just emptied it.
        note_source = steer.source if steer is not None else "human"
        note = steer.take() if steer else None
        # A binding with a `skill:` already states its own job, so it must not
        # also be told to implement the work item from the plan.
        # `on.implementation.start` is the one agent hook with no skill,
        # because for it the brief IS the method; it gets today's wording
        # unchanged. Selected from the binding rather than a list of hook
        # names so a plugin's own agent hook is classified by what it
        # declares (Kraft-s7c04.52). The brief itself is untouched for every
        # hook: `skills/spec` tells that agent the description is its
        # requirement.
        method_is_own = bool(binding.get("skill"))
        instruction = (
            instruction_override
            or (
                prompts.brief(work_item_row)
                + prompts.attachment_note(
                    entry.attachments_of(work_item_row), method_is_own=method_is_own
                )
                + (prompts.METHOD_NOTE if method_is_own else "")
                + prompts.scope_note(task_hook, launch.repo_entry if launch else None)
                + prompts.progress_note(task_hook, work_item_row, worktree)
            )
        ) + prompts.BEAD_NOTE
        # What this reviewer said last round, with the tags that let it say
        # "this is that one again" (Kraft-s7c04.1 + .2). Before this, every
        # round was measured cold: on 6c712ea8 findings went 3 -> 2 -> 3 -> 4,
        # all new fingerprints, and the loop exited only because one round
        # happened to grade everything minor. "" on round 0, so a work item's
        # first review is unchanged.
        if task_hook in prompts.REVIEW_HOOKS:
            previous, _, _ = last_measurement(db, work_item_row["id"], node["id"])
            instruction += prompts.carried_findings_note(previous or [])
        # The findings that never entered the fix loop, for the brief the human
        # actually reads (Kraft-s7c04.4). `skills/review-brief/SKILL.md` already
        # promises them -- "the local review findings, including the ones ruled
        # minor" -- and the dispatch gave the agent no way to know them, so on
        # 6c712ea8 four real minor defects never reached the brief and a human
        # later hand-filed three different ones. Keyed on the artifact rather
        # than the hook name, like `chain_review` below, so a chain that binds a
        # different hook to the brief still gets them.
        if binding.get("artifact") == "review_brief":
            instruction += prompts.deferred_findings_note(
                deferred_findings(db, work_item_row["id"], loop_severities)
            )
        if binding.get("artifact") == "chain_review":
            chain_nodes = json.loads(work_item_row["chain_definition"])["nodes"]
            at = next((i for i, n in enumerate(chain_nodes) if n["id"] == node["id"]), None)
            tail = chain_nodes[at + 1 :] if at is not None else []
            preceding = tuple(n["id"] for n in chain_nodes[: at + 1]) if at is not None else ()
            instruction += prompts.chain_review_context(tail, registry, preceding)
        item_override = (
            json.loads(work_item_row["agent_overrides"]) if work_item_row["agent_overrides"] else {}
        )
        node_override = store.node_overrides_of(work_item_row).get(node["id"], {})
        model_effort = {
            k: v for k, v in node_override.items() if k in ("model", "escalate_model", "effort")
        }
        merged_override = {**item_override, **model_effort}
        pkg = prompts.review_package(
            db, run_dirs, work_item_row["id"], worktree, task_hook, session_id
        )
        # A review hook with nothing to review is a configuration problem no
        # agent can fix by writing code (Kraft-579's posture), not a task to
        # launch anyway and let read an empty package. `review_package`
        # returns None for three reasons -- a non-review hook (excluded by the
        # `REVIEW_HOOKS` check itself), an item legitimately with no
        # `base_ref` yet (pre-migration, or a template with no `env_setup`
        # node -- must still run unchanged), and a git failure. The last two
        # are not distinguished here and collapse into the same stop: telling
        # them apart needs `review_package` to say why it returned None, which
        # is more than this fix needs.
        if (
            task_hook in prompts.REVIEW_HOOKS
            and pkg is None
            and _current_base_ref(db, work_item_row["id"]) is not None
        ):
            _, log_path, result_path = await _builtins.start_session(
                db, run_dirs, hook_point=task_hook, **common
            )
            return await _builtins.finish_session(
                db,
                log_path,
                result_path,
                session_id=session_id,
                status=CONFIG_ERROR,
                log=f"could not build a review package for {task_hook} — no diff to review\n",
            )
        inv = _agent.resolve_invocation(
            binding,
            launch.repo_entry if launch else None,
            launch.steering_dir if launch else None,
            skills_dir=launch.skills_dir if launch else None,
            escalate=escalate,
            item_override=merged_override or None,
        )
        status = await _agent.run_agent_task(
            db,
            run_dirs,
            hook_point=task_hook,
            review_package=pkg,
            command=inv.command,
            harness=inv.harness,
            model=inv.model,
            deny_tools=inv.deny_tools,
            effort=inv.effort,
            allowed_tools=inv.allowed_tools,
            permission_mode=inv.permission_mode,
            sandbox=inv.sandbox,
            steering_texts=inv.steering_texts,
            artifact=binding.get("artifact"),
            method_text=inv.method_text,
            title=work_item_row["title"],
            task_instruction=(
                prompts.steer_prefix(binding, work_item_row, worktree, note, source=note_source)
                if note
                else ""
            )
            + instruction,
            repo_path=work_item_row["repo"],
            cwd=worktree,
            repo_entry=launch.repo_entry if launch else None,
            **common,
        )
        # The agent is told to commit everything it changes before it exits.
        # When it does not, the work is still on disk -- so `verify` passes,
        # and only `_assert_clean` two nodes later notices, by which point the
        # failure names a hook rather than the cause and a human has to type
        # `git commit` in someone else's worktree (Kraft-7fip). Kraft owns the
        # worktree, so it takes the work rather than reporting it missing.
        #
        # Never at the cost of the run itself: an index lock a co-task holds, a
        # submodule that `add -A` finds nothing to stage in -- either of those
        # would turn a *successful* agent task into a failed node, and on the
        # fix-loop's direct dispatch would escape `run()` entirely. (Unset
        # `user.email` used to be on this list too; `ensure_worktree` now pins
        # identity before any node dispatches, so it is structurally prevented
        # rather than tolerated here -- Kraft-cppp.) Losing the sweep only puts
        # us back where Kraft-7fip found us: the work is still on disk and
        # `_assert_clean` names it at open_mr.
        # Before the sweep, not after: a straggler committed while HEAD sat on
        # a diagnostic branch an agent forgot to check out of would land on
        # that branch instead of the item's own (Kraft-v5qd).
        _builtins.restore_branch(Path(worktree), store.branch_for(work_item_row))
        try:
            await _forge.commit_stragglers(
                Path(worktree), message=f"wip: uncommitted work from {node['id']}"
            )
        except _forge.ForgeError as exc:
            logger.warning("could not commit stragglers after %s: %r", task_hook, exc)
            # A log line only reaches whoever is tailing the server at the
            # time. The failure it describes doesn't surface again until
            # `_assert_clean` refuses `open_mr`, nodes later, with no trail
            # back to why the work was left uncommitted (Kraft-hf12) -- so a
            # human debugging that refusal has something to find.
            await db.write(
                lambda c, task_hook=task_hook, exc=exc: events.append(
                    c,
                    work_item_row["id"],
                    "sweep_failed",
                    {"node_id": node["id"], "task_hook": task_hook, "error": str(exc)},
                )
            )
        return status
    if kind == "subprocess":
        repo_entry = (launch.repo_entry or {}) if launch else {}
        to_run, sandbox = _select_scopes(
            db, work_item_row["id"], worktree, node["id"], task_hook, round, binding, repo_entry
        )
        # C2 (Kraft-s7c04.9): every matched scope runs, not just the ones
        # before the first failure. On 49c0cefd the third scope (just
        # ci-test -- ruff + 2,294 tests + intent) never ran until 5h49m into
        # verify because an earlier scope's failure short-circuited it, and
        # every commit before that point was silently unverified. The
        # aggregate below still fails the node on any scope's failure -- it
        # just no longer costs a whole extra round to find out about a
        # second, unrelated failure a first scope's break used to hide.
        #
        # Infra-level statuses are the one exception: they say nothing about
        # the code, and running further scopes after one can't be trusted
        # either (a human paused the item; the next scope's own binary might
        # be missing too; a rate limit applies to every scope alike).
        status = "done"
        for scope in to_run:
            scope_status = await _subprocess.run_task(
                db,
                run_dirs,
                hook_point=task_hook,
                cmd=scope["cmd"],
                cwd=worktree,
                repo_entry=repo_entry,
                # The fix loop re-runs the test command after an agent edits source in
                # the same worktree. A .pyc written on an earlier cycle has the same
                # second-resolution mtime and (often) size as the fixed source, so
                # CPython would import the stale bytecode and the re-measure would
                # never see the fix. Never writing bytecode keeps every cycle honest.
                env={"PYTHONDONTWRITEBYTECODE": "1"},
                sandbox=sandbox,
                **{**common, "session_id": uuid.uuid4().hex},
            )
            if scope_status in _SCOPE_STOP_STATUSES:
                return scope_status
            if status == "done":
                status = scope_status  # the first failure wins; later scopes still run
        return status
    if kind == "forge":
        # Only what the binding actually sets, so the adapter's constants stay
        # the one place a default lives.
        poll = {k: binding[k] for k in ("poll_timeout", "poll_interval") if k in binding}
        return await _forge.run_task(
            db,
            run_dirs,
            hook_point=task_hook,
            handler=binding["handler"],
            backend=binding["backend"],
            # `backend: auto` resolves against the forge recorded for this repo
            # (repos.yaml), because the registry is per install and the forge is
            # a property of the repo. Same source the agent branch reads above.
            repo_forge=(launch.repo_entry or {}).get("forge") if launch else None,
            # The worktree, not the repo: every forge CLI resolves the merge
            # request from the *current branch*, and the repo is on whatever
            # the human has checked out.
            repo=worktree,
            # The original repo path, not the worktree: a confirmed-conflict
            # rebase (Kraft-9h7v) needs origin's current default branch tip,
            # which `refresh_worktree_base` fetches from here.
            orig_repo=Path(work_item_row["repo"]),
            branch=store.branch_for(work_item_row),
            title=work_item_row["title"],
            # `merge`'s conflict-rebase shortcut may only report "done"
            # without calling forge.merge when this node's own frozen
            # chain_definition will actually bounce the walk back to verify
            # afterwards (code-review) -- not whatever the current
            # templates/default.yaml happens to say.
            has_rebase_bounce=bool(node.get("rebase_bounce_to")),
            **poll,
            **common,
        )
    raise RuntimeError(
        f"unhandled binding for {task_hook!r}: kind={kind!r} handler={binding.get('handler')!r}"
    )


async def measure_node(
    db,
    run_dirs,
    work_item_id,
    node,
    row,
    registry,
    worktree,
    *,
    round: int = 0,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    budget: _policy.Budget = _policy.NO_BUDGET,
    loop_severities: frozenset[str] = _policy.DEFAULT_LOOP_SEVERITIES,
) -> tuple[str, list[str], list[BaseException]]:
    await db.write(lambda c, node=node: store.enter_node(c, work_item_id, node["id"]))
    tasks = node["tasks"]

    # Kraft-37myi: read per dispatch, not once per node. The old single read
    # above this loop carried the comment "the worktree's HEAD does not move
    # while this node's own tasks are still being measured", which stopped
    # being true the moment a task-level repair (spec §4) could commit a fix
    # mid-node -- and stops being true again when an ordered step rebases.
    # `git_read` never raises; None just means "never reusable", same as
    # before. `worktree` is None only in a unit test that stubs `dispatch_node`
    # out entirely.
    def _head() -> str | None:
        return _config.git_read(Path(worktree), "rev-parse", "HEAD") if worktree else None

    async def _measure(t: str) -> str:
        # Kraft-gl9d: a crash/resume re-entry into this same (node, round) must
        # not re-spend an agent session on a task whose session already reached
        # 'done' against the worktree as it stands right now.
        head_sha = _head()
        # A null head_sha is never reusable (`reusable_session` itself would
        # say so) -- skip the read entirely rather than asking a test double
        # that has no worktree, and thus no HEAD, to answer it.
        reused = (
            db.read(
                lambda c: store.reusable_session(c, work_item_id, node["id"], t, round, head_sha)
            )
            if head_sha is not None
            else None
        )
        if reused is not None:
            return reused["status"]
        return await dispatch_node(
            db,
            run_dirs,
            t,
            node,
            row,
            registry,
            worktree,
            loop_severities=loop_severities,
            round=round,
            steer=steer,
            launch=launch,
            budget=budget,
        )

    results = await asyncio.gather(*(_measure(t) for t in tasks), return_exceptions=True)
    # A pause stops the walk where it stands: the node is neither done nor failed,
    # and resume relaunches it. It outranks a co-task's failure, which was almost
    # certainly the same SIGTERM arriving on a different row.
    if any(r == "paused" for r in results):
        return "paused", [], []
    # A human's interruption still outranks this, but a task that never
    # launched outranks a rate limit, a budget breach and a co-task's failure:
    # none of those are evidence about anything while a task in this node
    # could not even start (Kraft-579).
    if any(r == CONFIG_ERROR for r in results):
        failed = [tasks[i] for i, r in enumerate(results) if r == CONFIG_ERROR]
        return CONFIG_ERROR, failed, []
    if any(r == RATE_LIMITED for r in results):
        return RATE_LIMITED, [], []
    if any(r == WAITING for r in results):
        return WAITING, [], []
    if any(r == INFRA_STOP for r in results):
        return INFRA_STOP, [], []
    # Logged before the BUDGET rung returns: a co-task can raise in the same node
    # as a budget-refused agent, and that traceback is the only record of it.
    excs = [r for r in results if isinstance(r, BaseException)]
    for exc in excs:
        logger.error("measuring task raised in node %s: %r", node["id"], exc)
    # paused > rate_limited > budget > failed. A pause is a human's instruction
    # and outranks everything. A rate limit and a budget breach both outrank a
    # co-task's failure because the agent's "failure" is not evidence about the
    # code; a rate limit outranks a budget breach because it is Kraft's own
    # spend policy refusing to start, not an external constraint the agent hit.
    if any(r == BUDGET for r in results):
        return BUDGET, [], []
    failed = [
        tasks[i]
        for i, r in enumerate(results)
        if isinstance(r, BaseException) or r in ("failed", "needs_context", "conflict")
    ]
    if failed:
        return "failed", failed, excs
    return "ok", [], []


#: A task in one of these states failed outright -- the same set
#: `measure_node` treats as failed.
_FAILING_STATUSES = ("failed", "needs_context", "conflict")


def collect_findings(db, work_item_id: str, node: dict, round: int, registry: Registry):
    """(findings, hook points that reported at least one) for one cycle.

    Only the node's own measuring tasks: the fix task is dispatched with
    `round=count` and the next measuring pass runs at that same round, so an
    unfiltered query folds the fix agent's result file into the cycle. Only the
    most recent row per hook point, because a re-entry can measure at a round a
    previous pass already used -- `round` seeds from the persisted fix-loop
    counter, which a gate rejection or a `ci_wait` poll does not clear, and a
    `/retry` deletes the counter row so the next pass restarts at 1 instead.
    Either way stale rows sit at the same number.

    A hook that failed without writing a findings file at all -- `on.test.run`
    is the common case, `kind: subprocess` with no findings schema to write to
    -- gets a synthesized `Finding` via `_findings.from_blind_failure` instead
    of vanishing (traced live on a work item that spun for 7 cycles on an
    identical, invisible test failure). `reported` is not extended for it:
    that set means "wrote a real, parseable result file", which a synthesized
    finding does not change -- `walk.py`'s `blind_failures` still computes the
    same way and still forces the loop open, now redundantly with `eligible`,
    which is harmless.

    One hook_point can carry more than one row in a single round --
    `on.test.run`'s scope loop mints one session per matching scope, all under
    this same hook_point (the identity problem, batch-c1 spec's "The identity
    problem underneath all three"). A bare "last wins" read would let a later
    scope's pass silently overwrite an earlier scope's real failure once C2
    stops the loop from short-circuiting on it. Every row for the *latest*
    `head_sha` this hook_point dispatched at is read instead of just the last
    one -- every scope from one dispatch shares that head_sha (stamped once,
    before the loop, in `dispatch_node`'s subprocess branch), so this still
    collapses to the single most recent row for a hook that only ever mints
    one (a re-entry at a *different* head still keeps last-wins there).

    That multi-row read is scoped to `kind: subprocess` hooks -- the only
    ones a scope loop mints more than one session for -- and not to an agent
    reviewer. A reviewer that exits `needs_context` stops before
    `bump_counter`, so a resume re-enters the same round at the same head:
    without this filter, the stale `needs_context` row (no findings file)
    would still share the latest head_sha, hit the `_FAILING_STATUSES` branch
    below, and mint a bogus `from_blind_failure` critical finding alongside
    the real, later row. An agent hook keeps a plain last-wins read instead.

    Every finding this returns -- parsed or synthesized -- carries a
    `Finding.jobs` entry per session that produced it, so a fix agent can
    always pull a job's full session output directly (Kraft-s7c04.34/.35
    brainstorm). A hook's several failing rows in one round (the scope-loop
    case above) become ONE synthesized `Finding` with one `JobRef` per
    failing job, not several near-identical findings -- the fix agent gets
    one coherent notice about the hook, not a fragmented list.
    """
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    by_hook: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        if row["hook_point"] in node["tasks"]:
            by_hook.setdefault(row["hook_point"], []).append(row)  # ordered by created_at
    found: list[_findings.Finding] = []
    reported: set[str] = set()
    for hook, hook_rows in by_hook.items():
        is_scope_loop = registry.hooks.get(hook, {}).get("kind") == "subprocess"
        latest_head = hook_rows[-1]["head_sha"]
        rows_to_read = (
            [row for row in hook_rows if row["head_sha"] == latest_head]
            if is_scope_loop
            else hook_rows[-1:]
        )
        blind_jobs: list[_findings.BlindJob] = []
        for row in rows_to_read:
            parsed = _findings.parse(row["result_path"])
            if parsed:
                reported.add(hook)
                job = _findings.JobRef(
                    label=hook, log_ref=_findings.session_log_ref(work_item_id, row["id"])
                )
                found.extend(replace(f, jobs=(job,)) for f in parsed)
            elif row["status"] in _FAILING_STATUSES:
                binding = registry.hooks.get(hook, {})
                command = row["command"] or (
                    shlex.join(binding["command"])
                    if binding.get("kind") == "subprocess" and binding.get("command")
                    else None
                )
                blind_jobs.append(
                    _findings.BlindJob(
                        session_id=row["id"], log_path=row["log_path"], command=command
                    )
                )
        if blind_jobs:
            found.append(_findings.from_blind_failure(hook, work_item_id, blind_jobs))
    return found, reported


def needs_context_question(
    db, work_item_id: str, node: dict, round: int, *, first_iteration: bool = False
) -> str | None:
    """The question from a `needs_context` row in this round, or None.

    Same latest-row-per-hook-point read as `collect_findings` (a resume or
    `/retry` re-enters with stale rows still sitting there, so a first-match
    scan could re-stop the item on a historical row forever) but deliberately
    NOT its `row["hook_point"] in node["tasks"]` filter: the fix task
    (`on.implementation.start`) is dispatched with this same round, and
    including it is exactly how a fix task's own `needs_context` is meant to
    surface, one iteration later.

    `first_iteration` is the exception to that, and only that: on the entry's
    very first pass there is by definition no fix from *this* entry yet, so
    any fix row at this round belongs to a bygone one. It matters because
    `walk_node` now seeds `round` from the persisted counter, and it is also
    re-entered on paths that are not resumes -- a gate rejection walking back
    to a fix_loop node, and the `ci_wait` poller -- where the `retry_counters`
    row survives (only `retry_after_cap` deletes it). Without this, the last
    pass's fix question would stop the new pass before it ran a single cycle:
    the same stranding `ESCALATION_HOOK` below closes, through a third door.
    Defaults off so every other caller keeps today's behaviour.

    `JUDGE_HOOK` is the one exception. The judge is a brake bolted onto the
    cap and never a second way to get stuck (`_judge_result` fails open
    in-process), but its session row persists -- so without this filter a
    judge that exited `needs_context` would strand the item on the judge's
    own question at the next re-entry, which is exactly the design's
    forbidden case arriving one iteration late.

    `ESCALATION_HOOK` is excluded for the same reason, through a different
    door. An escalation is a conversation with a human, not a measurement:
    its `needs_context` *is* the question that opened the conversation, so
    re-reading it once the human has answered re-stops the node on a question
    that has already been settled -- forever, because nothing ever rewrites
    that row. On e983d85c that cost $4.86 and 38 minutes, the human's answer
    reviewed and discarded twice with no code changed, and the item was then
    skipped with a CRITICAL finding still open (Kraft-7itv follow-up).
    """
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["hook_point"] in (JUDGE_HOOK, ESCALATION_HOOK):
            continue
        if first_iteration and row["hook_point"] == "on.implementation.start":
            continue
        latest[row["hook_point"]] = row  # ordered by created_at, so last wins
    for row in latest.values():
        if row["status"] == "needs_context":
            return _subprocess.read_question(Path(row["result_path"])) or "(no question given)"
    return None


def last_measurement(
    db, work_item_id: str, node_id: str
) -> tuple[list[_findings.Finding] | None, bool, str | None]:
    """This node's most recent `findings_measured` findings, whether a
    `fix_cycle_started` for the node followed it, and the head it was taken at.

    Three answers to three different questions, which is why they are not
    collapsed. The findings are what the next reviewer is handed
    (Kraft-s7c04.1); `fix_ran` is no-progress escalation's extra condition; the
    head is how `walk._carry_severity` tells a re-rating of an untouched tree
    from a genuine reduction (Kraft-s7c04.3). `None` for the head on any event
    written before that field existed, which floors nothing rather than
    guessing.

    The whole findings, not their fingerprints: the caller derives the tags it
    used to get, and the messages and severities are what `prompts.
    carried_findings_note` hands the next reviewer (Kraft-s7c04.1) and what
    `walk._carry_severity` floors a repeat's rating against (Kraft-s7c04.3).
    Returning both would make this the second reader of `findings_measured` in
    this module, which `unresolved_findings_steer`'s docstring forbids for good
    reason -- three readers of one event is three things to keep in step.

    Deliberately **not** bounded at a `work_item_retried`/`gate_*` boundary, the
    way `store.last_rejection` is. A `/retry` does not change the tree, and
    Kraft-m2q exists precisely because the first fix cycle after a steered retry
    lost the REPEAT tag on the very finding that caused the stop -- "the part
    that matters more than the findings themselves". Only the *most recent*
    measurement is ever returned, so nothing older than one round can leak into
    `resolve_identity`'s `known` however long the item's history is.

    Read from the event log rather than carried in a local: `kraft.executor.
    resuming.reconcile_current_node` re-enters `kraft.executor.walk.walk_node`
    after a crash or a resume with the counter intact, and a loop holding its
    history in the stack frame forgets everything it has seen — on exactly the
    path that motivates escalation.

    REPEAT marking asks only "was this finding in the
    last measurement", so it uses the fingerprints unconditionally. No-progress
    escalation additionally requires the fix flag: spec §4's "no progress" means
    a fix cycle ran and changed nothing — not merely that the same code was
    measured twice in a row. A `POST /retry` on a no-progress stop (or a
    crash/resume between the escalating findings_measured and the fix it never
    got to dispatch) re-enters at round 0 and measures before it fixes; without
    the flag, a deterministic reviewer seeing unchanged code would report the
    same fingerprints and the loop would escalate straight back to needs_human
    without ever giving the steered retry a chance to run.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    fix_seen = False
    for e in reversed(evts):
        if e["type"] == "fix_cycle_started" and e["payload"].get("node_id") == node_id:
            fix_seen = True
        elif e["type"] == "findings_measured" and e["payload"].get("node_id") == node_id:
            return (
                [_findings.from_payload(f) for f in e["payload"].get("findings", [])],
                fix_seen,
                e["payload"].get("head_sha"),
            )
    return None, False, None


#: The fix loop's judge hook (2026-09-12-verify-fix-loop-judge-design):
#: dispatched directly by `kraft.executor.walk.walk_node`, the same way
#: `on.implementation.start` is -- not from a node's own `tasks` list, so it
#: never contaminates `collect_findings`/`needs_context_question`'s per-task
#: reads, and every node with a `fix_loop` gets it unconditionally (spec
#: decision 4: no new policy field).
JUDGE_HOOK = "on.fix_loop.judge"

#: The escalation hook's own `hook_point`. Not a node task and not dispatched
#: from a `tasks` list -- `escalate.dispatch` writes it directly -- so, like
#: `JUDGE_HOOK`, its session rows sit in the same `(node, round)` scan that
#: `needs_context_question` reads and must be skipped there. Named rather than
#: spelled out at each site because `reattach` compares against it too.
ESCALATION_HOOK = "escalation"

#: Statuses that mean the judge session actually finished thinking -- the
#: same "was this a real judgement" gate `gate_review._UNTRUSTWORTHY`
#: applies, phrased as the allowlist its own `VERDICTS` check mirrors.
_JUDGE_TRUSTED_STATUS = frozenset({"done", "done_with_concerns"})
_JUDGE_VERDICTS = frozenset({"continue", "stop_needs_human", "stop_downgrade"})


def _judge_result(status: str, verdict: str | None) -> str:
    """Fail open to "continue" for anything the judge cannot be trusted on --
    an untrusted status, a missing or unknown verdict. The judge is a brake
    bolted onto the existing fix-loop cap, never a second way to get stuck:
    whatever this returns, the cap and the stuck detector in
    `kraft.executor.walk.walk_node` run exactly as they do today.
    """
    if status in _JUDGE_TRUSTED_STATUS and verdict in _JUDGE_VERDICTS:
        return verdict
    return "continue"


def judge_history(
    db,
    work_item_id: str,
    node_id: str,
    loop_severities: frozenset[str],
    evts: list | None = None,
) -> list[dict]:
    """Every round measured so far for this node, oldest first: the judge's
    cross-round view (spec's "cheap pointers, not full transcripts" input) --
    each round's eligible findings, with the fingerprint stable across
    rounds already computed, and that round's fix session's own result-file
    path.

    Reconstructed from the event log and `worker_sessions` rather than
    carried in a local across calls, the same resume-safety reasoning
    `last_measurement` gives for reading its own history back from events
    instead of a stack frame.
    """
    # Kept in event order in a list, never keyed by the event's own `cycle`:
    # `walk_node` seeds `round` from the loop counter on every re-entry
    # (ci_wait poller, crash resume, /retry), and that counter persists across
    # the re-entries that are not resumes -- so a new entry's first measurement
    # lands on a cycle number the previous entry already measured at, and a
    # /retry that cleared the counter lands back on 0 where the first entry
    # was. A dict keyed on that number silently dropped the older one and then
    # `sorted()` re-labelled the *newest* measurement as the oldest -- handing
    # the judge a truncated trend pointing the wrong way, on
    # mr_checks/on.ci.poll every time. Seeding narrowed the collision; it did
    # not remove it, so this stays a list.
    # _REPAIR_ROUND (-1) is a repair pass, not a paid fix cycle -- it has
    # nothing to do with the budget the judge is weighing.
    #: `evts`, when given, is a timeline the caller already fetched -- the same
    #: shape `escalate._reason` accepts. Read fresh when nothing is handed in,
    #: which is every existing caller including the judge's own.
    measured: list[tuple[int, list[_findings.Finding]]] = []
    for e in evts if evts is not None else db.read(lambda c: events.read_after(c, 0, work_item_id)):
        if e["type"] == "findings_measured" and e["payload"].get("node_id") == node_id:
            cycle = e["payload"]["cycle"]
            if cycle < 0:
                continue
            measured.append(
                (
                    cycle,
                    [
                        _findings.from_payload(f)
                        for f in e["payload"].get("findings", [])
                        if f.get("severity") in loop_severities
                    ],
                )
            )
    fix_rows = db.read(
        lambda c: c.execute(
            "SELECT round, result_path FROM worker_sessions WHERE work_item_id = ? "
            "AND node_id = ? AND hook_point = 'on.implementation.start' ORDER BY round",
            (work_item_id, node_id),
        ).fetchall()
    )
    # Fix rounds come from `bump_counter`, which does not reset across
    # re-entries, so unlike the measurements these are collision-free.
    fix_by_round = {r["round"]: r["result_path"] for r in fix_rows}
    return [
        {"round": i, "findings": found, "fix_result_path": fix_by_round.get(cycle)}
        for i, (cycle, found) in enumerate(measured)
    ]


def stuck_fingerprint(history: list[dict], min_repeats: int) -> str | None:
    """The first fingerprint (of the latest round's) that has survived
    `min_repeats` consecutive fix attempts unchanged, or None.

    Generalizes `walk.walk_node`'s whole-set stuck check (`prints ==
    previous_prints`) to a single recurring fingerprint: a blind-failure
    fingerprint (Kraft-0i6z4) can persist for many rounds while a
    co-occurring review finding keeps changing shape, so the set as a whole
    never repeats -- but this one thing never moved. A round only extends a
    streak from the one before it if its OWN `fix_result_path` is set --
    that is the fix which ran and produced this measurement (see
    `judge_history`'s round/cycle alignment); two measurements taken back to
    back with no fix in between (crash/resume) never had a chance to change
    and must not count as "no progress".
    """
    streaks: dict[str, int] = {}
    for round_ in history:
        prints = {f.fingerprint for f in round_["findings"]}
        fix_ran = round_["fix_result_path"] is not None
        streaks = {fp: streaks.get(fp, 0) + 1 if fix_ran and fp in streaks else 1 for fp in prints}
    return next((fp for fp, n in streaks.items() if n >= min_repeats), None)


def deferred_findings(db, work_item_id: str, loop_severities: frozenset[str]) -> list[dict]:
    """Every finding this item measured that never entered the fix loop, as raw
    payload dicts, deduped by fingerprint and oldest-first.

    Two readers, one computation: the human at the gate
    (`api.routes.board._deferred_findings`) and the review brief written for
    them (`prompts.deferred_findings_note`, Kraft-s7c04.4). A brief that listed
    a different set from the card above it would be worse than one that listed
    nothing.

    Payload dicts rather than `Finding`s, because the board serialises these
    straight to JSON and has since they existed.
    """
    seen: dict[str, dict] = {}
    for e in db.read(lambda c: events.read_after(c, 0, work_item_id)):
        if e["type"] != "findings_measured":
            continue
        for raw in e["payload"].get("findings", []):
            if raw.get("severity") in loop_severities:
                continue
            seen.setdefault(_findings.from_payload(raw).fingerprint, raw)
    return list(seen.values())


def regressed_fingerprints(history: list[dict]) -> list[str]:
    """Tags in the latest round that an earlier round had and a round between
    them did not -- a fix in this loop undoing a fix from earlier in the same
    loop (Kraft-s7c04.7).

    Distinct from `stuck_fingerprint`, which finds what was never fixed. This
    finds what was fixed and then broken again, and the two need opposite
    instructions: stop repeating an approach, versus stop alternating between
    two and root-cause the conflict.

    Every round in the gap must carry a `fix_result_path` -- the same guard
    `stuck_fingerprint` applies, for the same reason. A finding that vanished
    because a round crashed or a resume re-measured never had a chance to be
    fixed, so its reappearance reverts nothing, and telling a fixer otherwise
    would steer it away from the correct fix on a loop that is converging.

    Only a fingerprint comparison, so none of this was visible before
    Kraft-s7c04.2 made a reworded repeat carry the same tag: seven reports of
    one oscillating defect read as seven unrelated findings.
    """
    if len(history) < 3:
        return []
    per_round = [{f.fingerprint for f in r["findings"]} for r in history]
    out = []
    for fp in sorted(per_round[-1]):
        seen = [i for i, prints in enumerate(per_round) if fp in prints]
        if len(seen) < 2 or seen[-1] - seen[0] == len(seen) - 1:
            continue  # never absent in between: persistent, not regressed
        gap = [i for i in range(seen[0] + 1, seen[-1]) if fp not in per_round[i]]
        if all(history[i]["fix_result_path"] is not None for i in gap):
            out.append(fp)
    return out


def unresolved_findings_steer(
    db, work_item_id: str, node_id: str, policy: _policy.Policy | None
) -> str | None:
    """A retry's steer when the caller gave none and there is no rejection to
    fall back on (Kraft-7sec, second half): the node's most recent
    measurement, formatted the same way a fix cycle already receives it.

    `judge_history`'s last entry already is the unresolved set -- reused
    rather than a third reader of `findings_measured` (spec section 2
    forbids one, and `last_measurement` is already the second): a measurement
    re-reports what is still there every round, so the most recent one needs
    no `findings_resolved` counterpart to subtract, and a finding fixed in an
    earlier cycle is already absent from it.

    None when there is nothing to seed -- no measurement recorded yet for
    this node, or the last one had no eligible finding at all (a blind task
    failure with nothing structured to report) -- so the caller's own
    `last_rejection` fallback still applies.
    """
    severities = policy.loop_severities if policy else _policy.DEFAULT_LOOP_SEVERITIES
    history = judge_history(db, work_item_id, node_id, severities)
    if not history:
        return None
    found = history[-1]["findings"]
    if not found:
        return None
    return prompts.seeded_findings_note(found)


async def judge_verdict(
    db,
    run_dirs,
    work_item_id: str,
    node: dict,
    row,
    registry: Registry,
    worktree,
    *,
    round: int,
    key: str,
    eligible: list[_findings.Finding],
    policy: _policy.Policy,
    launch: LaunchContext | None,
    budget: _policy.Budget,
) -> tuple[str, str]:
    """Ask the fix loop's judge whether the cycle about to be dispatched is
    still worth it (2026-09-12-verify-fix-loop-judge-design). Returns
    `(verdict, reasoning)`; `verdict` is `"paused"` (propagate, no event) or
    one of `"continue"`/`"stop_needs_human"`/`"stop_downgrade"`, never
    anything else -- `_judge_result` is the only place that decides which.

    A registry with no `JUDGE_HOOK` binding at all -- a hand-built test
    registry, or a real install's `registry.yaml` from before this feature
    landed -- fails open the same as any other untrusted outcome, rather
    than a `KeyError` out of `dispatch_node`'s own lookup.
    """
    if JUDGE_HOOK not in registry.hooks:
        return "continue", ""
    cap = _policy.resolve_cap(policy, key, store.node_overrides_of(row).get(node["id"]))
    counter = db.read(lambda c: store.read_counter(c, work_item_id, key))
    attempts_used = counter["count"] if counter else 0
    started_at = counter["started_at"] if counter else _now()
    elapsed = (datetime.fromisoformat(_now()) - datetime.fromisoformat(started_at)).total_seconds()
    history = judge_history(db, work_item_id, node["id"], policy.loop_severities)
    instruction = prompts.JUDGE_PROMPT.format(
        node_id=node["id"],
        history=prompts.format_judge_history(history),
        attempts_used=attempts_used,
        cap_attempts=cap.attempts,
        elapsed_s=int(elapsed),
        cap_wall_clock_s=cap.wall_clock_s,
    )
    status = await dispatch_node(
        db,
        run_dirs,
        JUDGE_HOOK,
        node,
        row,
        registry,
        worktree,
        instruction_override=instruction,
        round=round,
        launch=launch,
        budget=budget,
    )
    if status == "paused":
        return "paused", ""
    session = None
    for s in db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round)):
        if s["hook_point"] == JUDGE_HOOK:
            session = s  # ordered by created_at -- last one wins
    if session is None:
        return "continue", ""
    result_path = Path(session["result_path"])
    verdict = _judge_result(status, _subprocess.read_verdict(result_path))
    reasoning = _subprocess.read_concerns(result_path) or ""
    return verdict, reasoning
