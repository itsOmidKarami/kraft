from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import shlex
import sqlite3
import traceback
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from kraft import builtins as _builtins
from kraft import caps as _caps
from kraft import config as _config
from kraft import events, store
from kraft import findings as _findings
from kraft import harness as _harness
from kraft import overrides as _overrides
from kraft import policy as _policy
from kraft import skill as _skill
from kraft import usage as _usage
from kraft.adapters import agent as _agent
from kraft.adapters import forge as _forge
from kraft.adapters import subprocess as _subprocess
from kraft.automated_review import AutomatedReview
from kraft.config import RepoEntry
from kraft.executor import entry, prompts, stops
from kraft.executor import read_only as _read_only
from kraft.executor.context import (
    _ADVANCING,
    BASE_MOVED,
    BUDGET,
    CONFIG_ERROR,
    CONFLICT,
    INFRA_STOP,
    RATE_LIMITED,
    READ_ONLY_VIOLATED,
    REPAIR_DOUBTED,
    SCOPE,
    TIME_CAPPED,
    WAIT_TIMED_OUT,
    WAITING,
    LaunchContext,
    Steer,
)
from kraft.store import _now as _now
from kraft.templates import revision
from kraft.templates.models import (
    AgentInput,
    AgentTask,
    BuiltinTask,
    ExecutionMode,
    ForgeTask,
    ResolvedNode,
    ResolvedStep,
    ResolvedTask,
    SubprocessTask,
    TaskScope,
)
from kraft.worker import sandbox as _sandbox
from kraft.worker import steering as _steering

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
_SCOPE_STOP_STATUSES = frozenset(s for s, tier in SCOPE.items() if tier == "stop")


def _last_own_round_head(
    db, work_item_id: str, node_id: str, task_hook: str, round: int
) -> str | None:
    """The head `round - 1`'s dispatch of `task_hook` on this exact `node_id`
    ran at, if every scope in that dispatch finished `done` -- otherwise
    `None` (C7 review fix, Kraft-s7c04.14).

    `prompts.last_review_session` is the wrong source for this: it is keyed
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
    repo_entry: RepoEntry | None,
) -> list[dict]:
    """The scopes the `kraft.verify_changed_test_scopes` builtin should run
    (test-scope design §3.2-3.3, C7 Kraft-s7c04.14). Their sandbox is the
    item's (`item_sandbox`).

    Pulled out of `dispatch_node`'s subprocess branch (batch-c1 spec Task 3).
    C1 briefly gave `implementation` its own `on.test.run` dispatch through
    this same function, so the two computed scopes identically by
    construction; C1 was reverted (Kraft-s7c04.8, 2026-09-16) because a gate
    dispatched after the session exits cannot be acted on by it. `verify` is
    this function's only caller now, and the same-selection property C1 was
    after is instead conveyed to the implementation agent as a prompt note
    (`prompts.scope_note`) built from the repo's scope table, so it can apply
    the same rule to its own diff before it finishes.

    The commands are the repo's, and only the repo's. V1 has no registry to
    fall back to and deliberately no command on the task: the task names an
    action Kraft owns, and what that action runs is a property of the
    repository (`repository-area-can-declare-setup-and-test-scopes`). A
    hardcoded single command is how verify ends up running something CI does
    not, or the wrong stack's suite entirely (Kraft-579, Kraft-9wzy).
    A bare `test_command` is read as a single `["**"]` scope, so this is one
    shape regardless of which field an operator set; a repo that declares
    neither returns no scopes at all and the caller stops for a human rather
    than inventing a command.

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
    if repo_entry is None:
        return []
    repo_scopes = [s.model_dump() for s in repo_entry.test_scopes or ()]
    if not repo_scopes and repo_entry.test_command:
        # Wrapped here, at the point of use, never in the loaded entry: a
        # re-save would persist it (`config.TestScope`, Kraft-9wzy).
        repo_scopes = [{"paths": ["**"], "command": repo_entry.test_command}]
    # One table after resolution: the repository's scopes, then each area's,
    # every area scope carrying the setup it needs first
    # (`repository-area-can-declare-setup-and-test-scopes`).
    area_scopes = [
        {**scope, "area": name, "setup": area.get("setup")}
        for name, area in repo_entry.areas.items()
        for scope in (area.get("verification") or {}).get("test_scopes") or []
    ]
    if not repo_scopes and not area_scopes:
        return []
    scopes = [
        {
            "paths": s["paths"],
            "cmd": shlex.split(s["command"]),
            "area": s.get("area"),
            "setup": shlex.split(s["setup"]) if s.get("setup") else None,
        }
        for s in [*(repo_scopes or []), *area_scopes]
    ]
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
    return to_run


async def config_error_session(db, run_dirs, common: dict, log: str) -> str:
    """A task that could not start, recorded as its own session (Kraft-579).

    `CONFIG_ERROR` is terminal at every tier (`context.SCOPE`), so the row is
    what a human reads to find out why -- without it the stop would name a task
    with no session to open.
    """
    _, log_path, result_path = await _builtins.start_session(db, run_dirs, **common)
    return await _builtins.finish_session(
        db,
        log_path,
        result_path,
        session_id=common["session_id"],
        status=CONFIG_ERROR,
        log=log,
    )


async def time_capped_session(db, run_dirs, common: dict, hit: _caps.Hit) -> str:
    """A launch a spent time cap refused, recorded as its own session: it
    exits `capped_out`, and `caps.REACHED` names the scope and the cap, so the
    stop and analytics read it the same as a run killed at its deadline."""
    _, log_path, result_path = await _builtins.start_session(db, run_dirs, **common)
    await _builtins.finish_session(
        db,
        log_path,
        result_path,
        session_id=common["session_id"],
        status="capped_out",
        log=f"not started: {hit.reason}\n",
    )
    await db.write(
        lambda c: events.append(
            c,
            common["work_item_id"],
            _caps.REACHED,
            hit.payload(
                node_id=common["node_id"],
                task=common["hook_point"],
                session_id=common["session_id"],
            ),
        )
    )
    return TIME_CAPPED


def scope_policy(
    row, scope: ResolvedNode | ResolvedStep | ResolvedTask, repository: str | None = None
) -> _policy.InstancePolicy:
    """The effective policy `scope` runs under, from its item's snapshot
    (`MaterializedChain.policy_for`) -- `repository`'s own when the task is
    fanned out to one (Kraft-jc39p). A V1 walk only ever runs a chain read out
    of that snapshot, so a row without one has no policy anyone could know --
    and running it unbounded is the silent reading this refuses."""
    snapshot = store.materialized_chain_of(row)
    if snapshot is None:
        raise LookupError(f"work item {row['id']} has no materialized chain to read policy from")
    return snapshot.policy_for(scope, repository)


class SandboxUnresolved(RuntimeError):
    """`item_sandbox` could not tell which sandbox an item runs in (Kraft-9t2dp).
    A `RuntimeError`, so the walk's entry keeps stopping on it as before; every
    launch door (`dispatch_node`, `gate_review.review`, `escalate.dispatch`)
    catches this type and records a `config_error` session naming the cause,
    never a host launch and never an unattributed crash."""


def item_sandbox(row, launch: LaunchContext | None) -> dict | None:
    """The sandbox every project-controlled launch of this item runs in, or
    None for an item nothing sandboxes -- the one resolution (Ruling 189).
    Every task, recovery, judge, escalation turn, gate review, test scope,
    area setup and the repository's `setup_command` reads it here: a sandbox
    wraps the item, not the scope that set it, because once one task has run
    in it the worktree is the worker's to write (Kraft-p8nem, Kraft-h10e5).

    Whichever scope of the snapshot froze one wins, and the entry's live value
    -- `false` included -- cannot turn it off (Ruling 105: `sandbox` only
    tightens); without one, the entry's live value applies.
    `SandboxUnresolved` when that cannot be told: a snapshot freezing two
    (filed before the ruling), repositories setting two live, or a poisoned
    `repos.yaml` -- unreadable is never "no sandbox"."""
    snapshot = store.materialized_chain_of(row)
    try:
        frozen = snapshot.item_sandbox() if snapshot is not None else None
        if frozen is not None:
            return frozen.model_dump()
        # Every repository the item launches against, members included: a
        # member's live sandbox wraps the root's runs too.
        entries = [launch.repo_entry, *launch.repositories.values()] if launch else []
        live = list(dict.fromkeys(s for e in entries if e and (s := e.effective_sandbox)))
    except (_policy.PolicyError, _config.ConfigError) as exc:
        raise SandboxUnresolved(f"cannot tell whether {row['id']} runs sandboxed: {exc}") from exc
    if len(live) > 1:
        raise SandboxUnresolved(
            f"{row['id']}'s repositories set different sandboxes "
            f"{[s.model_dump() for s in live]!r} in repos.yaml: "
            "a sandbox wraps the whole work item (Ruling 189), so they must agree"
        )
    return live[0].model_dump() if live else None


def _frozen_steering(row) -> dict[str, str] | None:
    """The steering text frozen into this item's snapshot at intake."""
    snapshot = store.materialized_chain_of(row)
    return snapshot.chain.steering if snapshot is not None else None


def _fan_out(row, worktree, launch: LaunchContext | None) -> list[tuple[str, Path, LaunchContext]]:
    """Where a `scope: each_repository` task runs: once per repository the
    item's frozen target selects -- the root in the assembled checkout, each
    member in its own mount -- with that repository's own entry
    (`task-may-explicitly-fan-out-by-repository`). One repository is one
    run, in the ordinary context: returns `[]`."""
    snapshot = store.materialized_chain_of(row)
    if snapshot is None or snapshot.target.kind != "workspace":
        return []
    target = snapshot.target
    entries = launch.repositories if launch is not None else {}
    base = launch or LaunchContext(repo_entry=None, steering_dir=None)
    runs = [(target.root, Path(worktree), base)] if target.root else []
    for mount in target.mounts.values():
        entry = entries.get(mount.repository)
        runs.append(
            (mount.repository, Path(worktree) / mount.path, replace(base, repo_entry=entry))
        )
    return runs


async def _run_changed_test_scopes(
    db,
    run_dirs,
    task: ResolvedTask,
    node: ResolvedNode,
    work_item_row,
    worktree,
    *,
    common: dict,
    execution: ExecutionMode,
    launch: LaunchContext | None,
    round: int,
    sandbox: dict | None,
    time_cap: _caps.Deadline | None = None,
) -> str:
    """`kraft.verify_changed_test_scopes`: run the repo's own test scopes that
    the branch's changed paths select, and report one aggregate result.

    Every selected scope runs, not just the ones before the first failure (C2,
    Kraft-s7c04.9): on 49c0cefd the third scope -- ruff + 2,294 tests + intent
    -- never ran until 5h49m into verify because an earlier scope's failure
    short-circuited it, and every commit before that point was silently
    unverified. The aggregate still fails the node on any scope's failure; it
    just no longer costs a whole extra round to find out about a second,
    unrelated failure.

    Infra-level statuses are the one exception (`_SCOPE_STOP_STATUSES`): they
    say nothing about the code, and running further scopes after one cannot be
    trusted either -- a human paused the item, the next scope's own binary may
    be missing too, a rate limit applies to every scope alike.

    Sequential unless the task asks for parallel
    (`changed-test-scope-verification-is-sequential-by-default`): the scopes
    share one worktree, and two suites writing the same build artifacts is a
    flake nobody can reproduce. `parallel` is bounded by the number of selected
    scopes, which the repo's own table bounds.
    """
    repo_entry = launch.repo_entry if launch else None
    to_run = _select_scopes(
        db, work_item_row["id"], worktree, node.id, task.path, round, repo_entry
    )
    if not to_run:
        return await config_error_session(
            db,
            run_dirs,
            common,
            f"{task.path} verifies changed test scopes, but {work_item_row['repo']} declares "
            "neither test_scopes nor test_command in repos.yaml — there is nothing to run "
            "and Kraft will not guess a command\n",
        )

    async def _run(cmd: list[str]) -> str:
        return await _subprocess.run_task(
            db,
            run_dirs,
            cmd=cmd,
            cwd=worktree,
            repo_entry=repo_entry,
            # The fix loop re-runs the test command after an agent edits source in
            # the same worktree. A .pyc written on an earlier cycle has the same
            # second-resolution mtime and (often) size as the fixed source, so
            # CPython would import the stale bytecode and the re-measure would
            # never see the fix. Never writing bytecode keeps every cycle honest.
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            sandbox=sandbox,
            time_cap=time_cap,
            **{**common, "session_id": uuid.uuid4().hex},
        )

    # An area's setup runs once, before the first of its scopes -- including
    # an area nobody chose at intake that the changed paths selected anyway
    # (`selected-test-scope-activates-its-area-setup`, `unexpected-area-
    # changes-are-tested`). A setup that does not finish is its scopes' result.
    setups: dict[str, asyncio.Task] = {}

    async def _scope(scope: dict) -> str:
        if scope["setup"]:
            if scope["area"] not in setups:
                setups[scope["area"]] = asyncio.ensure_future(_run(scope["setup"]))
            ready = await setups[scope["area"]]
            if ready != "done":
                return ready
        return await _run(scope["cmd"])

    if execution is ExecutionMode.PARALLEL:
        results = list(await asyncio.gather(*(_scope(s) for s in to_run)))
    else:
        results = []
        for scope in to_run:
            status = await _scope(scope)
            results.append(status)
            if status in _SCOPE_STOP_STATUSES:
                break
    stopped = next((s for s in results if s in _SCOPE_STOP_STATUSES), None)
    if stopped is not None:
        return stopped
    return next((s for s in results if s != "done"), "done")


def _automated_review(launch: LaunchContext | None) -> AutomatedReview | None:
    """The repository's named automated reviewer (Ruling 171), if any."""
    return launch.repo_entry.automated_review if launch and launch.repo_entry else None


async def dispatch_node(
    db, run_dirs, task: ResolvedTask, node: ResolvedNode, work_item_row, worktree, **kw
) -> str:
    """`_dispatch_task`, and a session row for whatever it raised.

    `measure_node` folds a raised exception into the node's verdict, so the
    chain stops correctly -- but a task that raised before its session row
    existed (a rebase conflict, a git error) left nothing for `kraft view
    logs` or the Tasks tab, and one that raised after left its row running
    (Kraft-s7c04.55; forge/run.py's Kraft-41b is the same wound). One door
    for every task kind: the rows this dispatch made that are still open are
    closed, or one is made, with the traceback as the log. Then it re-raises,
    so the verdict is unchanged.
    """
    since = db.read(
        lambda c: c.execute("SELECT COALESCE(MAX(rowid), 0) FROM worker_sessions").fetchone()[0]
    )
    try:
        return await _dispatch_task(db, run_dirs, task, node, work_item_row, worktree, **kw)
    except Exception as exc:
        try:
            await _record_raised(db, run_dirs, task, node, work_item_row, worktree, kw, since, exc)
        except Exception:
            logger.exception("could not record the session %s raised", task.path)
        raise


async def _record_raised(db, run_dirs, task, node, work_item_row, worktree, kw, since, exc) -> None:
    status = "conflict" if isinstance(exc, _builtins.RebaseConflict) else "failed"
    log = "".join(traceback.format_exception(exc))
    # This dispatch's own rows, and the `waiting` row a forge wait resumes
    # rather than mints (Kraft-ivh1): older than `since`, but this episode's
    # (Kraft-evyc7).
    rows = db.read(
        lambda c: c.execute(
            "SELECT id, status, log_path, result_path FROM worker_sessions "
            "WHERE work_item_id = ? AND hook_point = ? AND (rowid > ? OR "
            "(status = 'waiting' AND node_id = ? AND round = ?))",
            (work_item_row["id"], task.path, since, node.id, kw.get("round", 0)),
        ).fetchall()
    )
    open_rows = [r for r in rows if r["status"] in ("pending", "running", "waiting")]
    if not rows:
        sid, log_path, result_path = await _builtins.start_session(
            db,
            run_dirs,
            session_id=uuid.uuid4().hex,
            work_item_id=work_item_row["id"],
            node_id=node.id,
            hook_point=task.path,
            round=kw.get("round", 0),
            head_sha=_config.git_read(Path(worktree), "rev-parse", "HEAD"),
        )
        open_rows = [{"id": sid, "log_path": log_path, "result_path": result_path}]
    for r in open_rows:
        await _builtins.finish_session(
            db,
            Path(r["log_path"]),
            Path(r["result_path"]),
            session_id=r["id"],
            status=status,
            log=log,
            reused=True,
        )


async def _dispatch_task(
    db,
    run_dirs,
    task: ResolvedTask,
    node: ResolvedNode,
    work_item_row,
    worktree,
    *,
    instruction_override: str | None = None,
    round: int = 0,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    budget: _policy.Budget = _policy.NO_BUDGET,
    escalate: bool = False,
    loop_severities: frozenset[str] = _policy.DEFAULT_LOOP_SEVERITIES,
    #: The repository a fanned-out run is for (`_fan_out`): its policy is
    #: that repository's, and it does not fan out again.
    repository: str | None = None,
) -> str:
    """Run one resolved task and report its status.

    The task's *type* chooses the adapter (`task-kinds-are-discriminated`):
    there is no name to look up and no binding to resolve, so a task Kraft
    cannot run is unrepresentable rather than a `KeyError` at dispatch. Every
    adapter still does its own side effects; this function only picks one and
    hands it the task's own fields.

    The task is identified everywhere by its canonical path --
    `worker_sessions.hook_point`, every event payload, every steering and
    session lookup. A path is unique within a resolved chain
    (`component-identifiers-are-qualified-by-node-instance`), which a hook name
    reused by two nodes never was.
    """
    t = task.task
    # The policy this task runs under, resolved at the scope it sits in
    # (`MaterializedChain.policy_for`): its sandbox, tool lists, harness
    # allowlist and token budget are all read from here.
    if t.scope is TaskScope.EACH_REPOSITORY and repository is None and not isinstance(t, ForgeTask):
        # A forge task is exempt: `forge.run_task` already walks every
        # selected repository itself, in publication order.
        runs = _fan_out(work_item_row, worktree, launch)
        if runs:
            status = "done"
            for rid, cwd, member_launch in runs:
                status = await dispatch_node(
                    db,
                    run_dirs,
                    task,
                    node,
                    work_item_row,
                    cwd,
                    instruction_override=instruction_override,
                    round=round,
                    steer=steer,
                    launch=member_launch,
                    budget=budget,
                    escalate=escalate,
                    loop_severities=loop_severities,
                    repository=rid,
                )
                if status != "done":
                    break
            return status
    task_policy = scope_policy(work_item_row, task, repository)
    session_id = uuid.uuid4().hex
    # The commit the measurement is about (Kraft-lu2). Resolved here, once, at
    # dispatch: a sha read later would be whatever HEAD moved to while the task
    # ran, which is the opposite of the question the gate asks. `git_read`
    # never raises.
    common = dict(
        session_id=session_id,
        work_item_id=work_item_row["id"],
        node_id=node.id,
        hook_point=task.path,
        round=round,
        head_sha=_config.git_read(Path(worktree), "rev-parse", "HEAD"),
    )
    # Before any task runs host git here: a sandboxed worker earlier in this
    # walk may have left a repository of its own in the worktree (Kraft-nx4id).
    # `rev-parse` above reads HEAD alone and never looks at a gitlink.
    try:
        stops.refuse_planted_repos(work_item_row, launch, Path(worktree))
    except RuntimeError as exc:
        return await config_error_session(db, run_dirs, common, f"{task.path}: {exc}\n")
    # Every enclosing scope's time cap, and this task's own (`kraft.caps`):
    # spent already refuses the launch; otherwise the one run is killed at
    # the tightest deadline. One deadline for every process the task starts.
    hit = db.read(lambda c: _caps.at_launch(c, work_item_row, task))
    if hit is not None and hit.remaining_s <= 0:
        return await time_capped_session(db, run_dirs, common, hit)
    time_cap = _caps.Deadline(_caps.monotonic() + hit.remaining_s, hit) if hit else None
    if not isinstance(t, ForgeTask):
        # Resolved here, once, for every launch this task makes (Ruling 189):
        # a sandbox nobody can resolve stops this task for a human, recorded
        # as its session, and nothing launches (Kraft-9t2dp).
        try:
            sandbox = item_sandbox(work_item_row, launch)
        except SandboxUnresolved as exc:
            return await config_error_session(db, run_dirs, common, f"{task.path}: {exc}\n")
    if isinstance(t, BuiltinTask):
        # `BuiltinAction` has exactly one member, so there is no branch to take
        # on `ref`: a reference Kraft does not own was rejected by the type
        # long before this (`builtin-task-references-code-owned-actions`).
        return await _run_changed_test_scopes(
            db,
            run_dirs,
            task,
            node,
            work_item_row,
            worktree,
            common=common,
            execution=t.execution,
            launch=launch,
            round=round,
            sandbox=sandbox,
            time_cap=time_cap,
        )

    if isinstance(t, SubprocessTask):
        try:
            cmd = shlex.split(t.command)
        except ValueError as exc:
            # Never started, so not a failure a fix loop could repair
            # (Kraft-hr0xr): it stops naming the command it could not read.
            return await config_error_session(
                db, run_dirs, common, f"{task.path}: cannot parse command {t.command!r}: {exc}\n"
            )
        return await _subprocess.run_task(
            db,
            run_dirs,
            cmd=cmd,
            cwd=worktree,
            repo_entry=launch.repo_entry if launch else None,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            sandbox=sandbox,
            time_cap=time_cap,
            **common,
        )

    if isinstance(t, ForgeTask):
        return await _forge.run_task(
            db,
            run_dirs,
            handler=_forge.handler_for(t.target.value),
            # The forge is a property of the repo (repos.yaml), never of the
            # template: one install's chains run against whatever forge each
            # repo is on.
            backend="auto",
            repo_forge=launch.repo_entry.forge if launch and launch.repo_entry else None,
            automated_review=_automated_review(launch),
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
            # A node that declares `on_base_changed` restarts its span when a
            # rebase moves the base, so the forge may report the move instead
            # of re-verifying the rebased head itself (`merge`'s conflict
            # rebase leans on this).
            has_rebase_bounce=getattr(node.node, "on_base_changed", None) is not None,
            # Resolved through this task's own policy (Kraft-5p69g); already
            # checked against its maximum when the item was filed.
            wait=t.wait_bounds(task_policy) if t.target.waits else None,
            **common,
        )

    if not isinstance(t, AgentTask):  # pragma: no cover -- the union is closed
        raise RuntimeError(f"unhandled task kind for {task.path!r}: {t!r}")

    # Only agent tasks. A subprocess or builtin costs nothing, and stopping
    # verification for a budget would strand the item mid-node for no saving.
    breach = stops.budget_breach(db, work_item_row["id"], budget, row=work_item_row, path=task.path)
    if breach is not None:
        # A scope's own cap (Ruling 195) is known only here, where the scope
        # is: recorded for the stop to name.
        if breach["scope"] in ("tokens", "usd"):
            await db.write(
                lambda c: events.append(
                    c, work_item_row["id"], "scope_budget_reached", {**breach, "task": task.path}
                )
            )
        return BUDGET
    # A harness the runtime cannot offer stops for a human and never silently
    # substitutes another (`unavailable-selected-harness-needs-human`): the
    # profile lookup in `resolve_agent_task` raises `HarnessUnavailable`,
    # caught below, rather than a `ValueError` from `run_agent_task` that
    # would surface as a crash in whatever gathered this task.
    harnesses = _harness.load(None)
    # Authorship travels with the note, not with the caller: a seeded
    # steer is Kraft's own recap of the last review's unresolved findings,
    # and the human templates in `steer_prefix` would tell the agent a
    # person wrote it (the same misattribution `Steer.human` keeps out of
    # the fix-loop judge). Read before `take()`, and on `is not None`, not
    # truthiness: `Steer.__bool__` is about having text left, and `take()`
    # has just emptied it.
    note_source = steer.source if steer is not None else "human"
    note = steer.take(task.path) if steer else None
    # A task with a `skill:` already states its own job, so it must not also be
    # told to implement the work item from the plan. A task without one is the
    # one doing the work from the brief, and gets the implementer's notes.
    method_is_own = t.skill is not None
    instruction = (
        instruction_override
        or (
            f"{t.prompt}\n\n"
            + prompts.brief(work_item_row)
            + prompts.attachment_note(
                entry.attachments_of(work_item_row), method_is_own=method_is_own
            )
            + (prompts.METHOD_NOTE if method_is_own else "")
            + prompts.scope_note(t, launch.repo_entry if launch else None)
            + prompts.progress_note(t, work_item_row, worktree)
        )
    ) + prompts.BEAD_NOTE
    # The findings that never entered the fix loop, for the brief the human
    # actually reads (Kraft-s7c04.4). `skills/review-brief/SKILL.md` already
    # promises them -- "the local review findings, including the ones ruled
    # minor" -- and the dispatch gave the agent no way to know them, so on
    # 6c712ea8 four real minor defects never reached the brief and a human
    # later hand-filed three different ones. Keyed on the artifact the task
    # produces, not on its path, so a chain that writes the brief from a
    # differently-named task still gets them.
    if t.produces == "review_brief":
        instruction += prompts.deferred_findings_note(
            deferred_findings(db, work_item_row["id"], loop_severities)
        )
    if t.produces == revision.CHAIN_REVISION:
        instruction += revision.context_note(store.materialized_chain_of(work_item_row), node.id)
    # A reviewing task's continuity, delivered only when it declares it
    # (`AgentTask.inputs`): what the node's last measurement found, tagged so a
    # repeat keeps its identity (the tags `walk` then trusts, and no others --
    # `findings.resolve_identity`), and where its own last review wrote. Only on
    # its own pass: a fix loop's or recovery's override is a new job.
    if instruction_override is None:
        if AgentInput.CARRIED_FINDINGS in t.inputs:
            carried, _, _ = last_measurement(db, work_item_row["id"], node.id)
            instruction += prompts.carried_findings_note(carried or [])
        if AgentInput.PREVIOUS_REVIEW in t.inputs:
            instruction += prompts.previous_review_note(
                prompts.last_review_session(db, work_item_row["id"], task.path)
            )
    item_override = (
        json.loads(work_item_row["agent_overrides"]) if work_item_row["agent_overrides"] else {}
    )
    node_override = store.node_overrides_of(work_item_row).get(node.id, {})
    instruction += _overrides.extra_prompt_note(node_override.get("extra_prompt"))
    keys = ("model", "escalate_model", "effort")
    merged_override = {**item_override, **{k: v for k, v in node_override.items() if k in keys}}
    try:
        inv = _agent.resolve_agent_task(
            t,
            launch.repo_entry if launch else None,
            launch.steering_dir if launch else None,
            skills_dir=launch.skills_dir if launch else None,
            escalate=escalate,
            item_override=merged_override or None,
            harnesses=harnesses,
            steering=_frozen_steering(work_item_row),
            policy=task_policy,
        )
    except _agent.HarnessUnavailable as exc:
        why = (
            f"{task.path}: {exc}"  # its harness is there; its agent profile is not
            if isinstance(exc, _agent.ProfileUnavailable)
            else f"{task.path} selects harness {t.harness!r}, which is not available: {exc}"
        )
        return await config_error_session(db, run_dirs, common, why + "\n")
    except (_skill.SkillError, _steering.SteeringError) as exc:
        # A selected skill the environment cannot load stops for a human and
        # never substitutes a method (`selected-skill-must-be-available`). A
        # plugin-qualified reference is not checked here -- Kraft cannot read
        # another tool's plugin cache, so `skill.UNAVAILABLE` tells the agent
        # to stop with `needs_context` instead. A steering selection the
        # snapshot cannot supply stops the same way rather than run unsteered.
        return await config_error_session(db, run_dirs, common, f"{task.path}: {exc}\n")
    # A task an operator paused mid-turn resumes its own provider session when
    # it can, told to carry on with any steer in hand; otherwise it restarts
    # with its original instruction (`resumed-agent-task-preserves-its-
    # session-when-possible`). Never on a fix loop's or a recovery's own
    # instruction, which is a new job, not the paused one.
    resumed = (
        _resumable_session(db, work_item_row["id"], node, task, harnesses.valid.get(inv.harness))
        if instruction_override is None
        else None
    )
    if resumed is not None:
        await db.write(
            lambda c: events.append(
                c,
                work_item_row["id"],
                "agent_session_resumed",
                {"task": task.path, "session_id": resumed[0]},
            )
        )
    # Delivered only to a task that declares it (`AgentTask.inputs`, Ruling 47):
    # the change under review, written out for this session.
    package = None
    if AgentInput.REVIEW_PACKAGE in t.inputs:
        # The package is read off the worktree with host git, which must not
        # race a sandboxed co-task still writing it (Kraft-69rwp).
        try:
            stops.refuse_live_sandboxed_session(
                db, work_item_row, launch, what="the review package"
            )
        except RuntimeError as exc:
            return await config_error_session(db, run_dirs, common, f"{task.path}: {exc}\n")
        package = prompts.review_package(
            db, run_dirs, work_item_row["id"], worktree, task.path, session_id
        )
    try:
        status = await _agent.run_agent_task(
            db,
            run_dirs,
            command=inv.command,
            harness=inv.harness,
            harnesses=harnesses,
            model=inv.model,
            deny_tools=inv.deny_tools,
            effort=inv.effort,
            allowed_tools=inv.allowed_tools,
            permission_mode=inv.permission_mode,
            sandbox=sandbox,
            steering_texts=inv.steering_texts,
            artifact=t.produces,
            method_text=inv.method_text,
            title=work_item_row["title"],
            task_instruction=(
                prompts.steer_prefix(t.produces, work_item_row, worktree, note, source=note_source)
                if note
                else ""
            )
            + (prompts.AGENT_RESUMED_NOTE if resumed is not None else instruction),
            resume_session_id=resumed[1] if resumed is not None else None,
            repo_path=work_item_row["repo"],
            cwd=worktree,
            repo_entry=launch.repo_entry if launch else None,
            review_package=package,
            time_cap=time_cap,
            **common,
        )
    except _agent.LaunchRefused as exc:
        # Refused before anything started (Kraft-hr0xr): the same stop as an
        # unavailable harness, never a failed task for a fix loop to relaunch
        # into the same refusal.
        return await config_error_session(db, run_dirs, common, f"{task.path}: {exc}\n")
    # The agent is told to commit everything it changes before it exits.
    # When it does not, the work is still on disk -- so verification passes,
    # and only `_assert_clean` two nodes later notices, by which point the
    # failure names a task rather than the cause and a human has to type
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
    # A fanned-out member run stands in the member's checkout, whose base is
    # its own default branch; every other run is on the item's base branch.
    snapshot = store.materialized_chain_of(work_item_row)
    member = repository is not None and snapshot is not None and repository != snapshot.target.root
    base = await _builtins.base_branch(
        db, work_item_row["id"], Path(worktree if member else work_item_row["repo"]), member=member
    )
    #
    # Not after a sandboxed session that left a repository of its own in the
    # worktree (Kraft-nx4id): the checkout and the commit would both read its
    # config. The next dispatch, or the door a human uses, stops the item on
    # the same check and names the paths.
    #
    # Nor while a sandboxed co-task is still live (Kraft-69rwp): it can write
    # the worktree under the sweep. The last task of the step to finish
    # sweeps for all of them.
    try:
        stops.refuse_live_sandboxed_session(db, work_item_row, launch, what="the straggler sweep")
    except RuntimeError as exc:
        await db.write(
            lambda c, exc=exc: events.append(
                c,
                work_item_row["id"],
                "sweep_failed",
                {"node_id": node.id, "task": task.path, "error": f"deferred: {exc}"},
            )
        )
        return status
    if sandbox and _sandbox.planted_repos(Path(worktree), work_item_row["base_ref"]) != []:
        await db.write(
            lambda c: events.append(
                c,
                work_item_row["id"],
                "sweep_failed",
                {
                    "node_id": node.id,
                    "task": task.path,
                    "error": "skipped: the sandboxed worktree holds a git repository "
                    "Kraft did not create",
                },
            )
        )
        return status
    _builtins.restore_branch(Path(worktree), store.branch_for(work_item_row), base)
    try:
        await _forge.commit_stragglers(
            Path(worktree),
            base=base,
            message=f"wip: uncommitted work from {node.id}",
            mounts=_builtins.item_mounts(work_item_row),
        )
    except _forge.ForgeError as exc:
        logger.warning("could not commit stragglers after %s: %r", task.path, exc)
        # A log line only reaches whoever is tailing the server at the
        # time. The failure it describes doesn't surface again until
        # `_assert_clean` refuses `open_mr`, nodes later, with no trail
        # back to why the work was left uncommitted (Kraft-hf12) -- so a
        # human debugging that refusal has something to find.
        await db.write(
            lambda c, exc=exc: events.append(
                c,
                work_item_row["id"],
                "sweep_failed",
                {"node_id": node.id, "task": task.path, "error": str(exc)},
            )
        )
    return status


def _resumable_session(db, work_item_id: str, node, task, harness) -> tuple[str, str] | None:
    """`(session id, provider session id)` of the paused session `task`
    resumes, or None to restart it. Only a harness that can resume, only the
    task's latest session and only if an operator paused it, never from before
    a retry or a restart (7a's rule: a new pass never reuses a pre-retry
    session), and only when its log names the provider's own session id."""
    if harness is None or not harness.supports("resume"):
        return None
    paused = db.read(lambda c: store.resumable_agent_session(c, work_item_id, node.id, task.path))
    if paused is None:
        return None
    provider = _usage.READERS["claude-stream-json"].session_id(Path(paused["log_path"]))
    return (paused["id"], provider) if provider else None


async def measure_node(
    db,
    run_dirs,
    work_item_id,
    node: ResolvedNode,
    row,
    worktree,
    *,
    steps: tuple[ResolvedStep, ...] | None = None,
    instruction_override: str | None = None,
    round: int = 0,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    budget: _policy.Budget = _policy.NO_BUDGET,
    loop_severities: frozenset[str] = _policy.DEFAULT_LOOP_SEVERITIES,
    start_step: int = 0,
    spent: set[str] | None = None,
    preserve: frozenset[str] = frozenset(),
) -> tuple[str, list[ResolvedTask], list[BaseException]]:
    """Run `steps` (the node's own, by default) and report one verdict.

    `steps` is what makes one execution shape enough: a recovery plan and a fix
    loop are the same ordered-steps walk over a different group of steps from
    the same node, so `walk` hands its own group in rather than this function
    growing a second branch for each.

    `spent` arms task- and step-level recovery for the node's own steps
    (`nearest-recovery-handler-wins`): once a step has settled
    (`parallel-step-settles-before-recovery`), each failed task's nearest
    handler below the node runs -- task handlers one at a time
    (`recovery-tasks-run-after-a-concurrent-step-settles`) and each followed by
    a retry of that task alone (`task-recovery-retries-only-the-task`), then
    the step's handler for the failed tasks that declare none, followed by a
    retry of the whole step (`step-recovery-retries-the-entire-step`). Each
    handler's path goes into `spent`, so it runs at most once for as long as
    the caller keeps the set -- `walk.walk_node` keeps one per entry into the
    node. A node-level handler is `walk`'s, not this function's. `None`, as
    for a recovery plan or a fix loop, means no handler here runs at all.
    """
    groups = node.steps if steps is None else steps
    # Only the node's own steps are the node's progress. A recovery plan or a
    # fix loop is work *about* the node, so it neither re-enters it nor moves
    # the resume cursor a later measuring pass reads back.
    own = steps is None
    if own:
        await db.write(lambda c: store.enter_node(c, work_item_id, node.id))

    # Kraft-37myi: read per dispatch, not once per node. The old single read
    # above this loop carried the comment "the worktree's HEAD does not move
    # while this node's own tasks are still being measured", which stopped
    # being true the moment a fix cycle could commit mid-node.
    # `git_read` never raises; None just means "never reusable", same as
    # before. `worktree` is None only in a unit test that stubs `dispatch_node`
    # out entirely.
    def _head() -> str | None:
        return _config.git_read(Path(worktree), "rev-parse", "HEAD") if worktree else None

    def _skipped(task: ResolvedTask) -> bool:
        """Under a task or step path an operator skipped in this run."""
        if not own:
            return False
        skipped = db.read(lambda c: store.skipped_paths(c, work_item_id))
        return any(task.path == p or task.path.startswith(p + ".") for p in skipped)

    async def _measure(task: ResolvedTask, *, retry: bool = False) -> str:
        if _skipped(task):
            return "done"
        verdict = await _measure_task(task, retry=retry)
        # Skipped while it ran: the skip stopped its session, which ends
        # `paused`, and a skip is not a pause of the walk.
        return "done" if verdict == "paused" and _skipped(task) else verdict

    async def _measure_task(task: ResolvedTask, *, retry: bool = False) -> str:
        if task.path in preserve and not retry:
            # A task retry's completed sibling (`RunFork.preserved`): its latest
            # outcome stands, whatever HEAD the retried task later moves.
            kept = db.read(
                lambda c: store.latest_session_per_task(c, work_item_id, node.id, [task.path])
            )
            if kept and kept[0]["status"] in _ADVANCING:
                return kept[0]["status"]
        # Kraft-gl9d: a crash/resume re-entry into this same (node, round) must
        # not re-spend an agent session on a task whose session already reached
        # 'done' against the worktree as it stands right now.
        head_sha = _head()
        # A null head_sha is never reusable (`reusable_session` itself would
        # say so) -- skip the read entirely rather than asking a test double
        # that has no worktree, and thus no HEAD, to answer it. Nor is a
        # recovery's retry: it exists to run the task again, and a sibling's
        # `done` from the settled step is exactly what it must not stand in for.
        # Whether a session from before a `/retry` is stale is
        # `reusable_session`'s own call (Kraft-znsvg), for every caller alike.
        reused = (
            db.read(
                lambda c: store.reusable_session(
                    c, work_item_id, node.id, task.path, round, head_sha
                )
            )
            if head_sha is not None and not retry
            else None
        )
        if reused is not None:
            return reused["status"]
        return await dispatch_node(
            db,
            run_dirs,
            task,
            node,
            row,
            worktree,
            instruction_override=instruction_override,
            loop_severities=loop_severities,
            round=round,
            steer=steer,
            launch=launch,
            budget=budget,
        )

    # One step at a time, concurrently within a step
    # (`exec-node-orders-concurrent-task-groups`).
    #
    # (task, result) pairs, never positional indices into a flat task list: a
    # step that stops the node leaves `results` shorter than the task list, and
    # `tasks[i]` would then name the wrong task in `failed` -- silently, into
    # the fix loop and the on_failure repair.
    async def _wrote(before, scope: str) -> bool:
        return await _read_only.violation(
            db, row, launch, worktree, before, node_id=node.id, scope=scope
        )

    def _snapshot(flagged: bool):
        return _read_only.snapshot(db, row, launch, worktree) if flagged else None

    async def _settle(tasks, step: ResolvedStep, *, retry: bool = False) -> list:
        # A read_only step is checked around every run of its tasks, a
        # recovery's retry included; the handler itself runs outside it.
        before = _snapshot(step.read_only)
        results = await asyncio.gather(
            *(_measure(t, retry=retry) for t in tasks), return_exceptions=True
        )
        # An `AssertionError` is Kraft's own broken invariant (and, under
        # pytest, the real-agent guard), never evidence about the code a task
        # measured -- so it is not a failed task for a fix loop to spend paid
        # cycles on. It propagates, once every co-task has settled: the daemon's
        # `deps` crash handler stops the item naming it, and a test fails
        # (Kraft-cpotk).
        for r in results:
            if isinstance(r, AssertionError):
                raise r
        if await _wrote(before, step.path):
            return [READ_ONLY_VIOLATED] * len(tasks)
        return list(results)

    async def _handle(handler, scope: str, failed: list[ResolvedTask]):
        if scope == "step":
            note = prompts.failure_note(node, [t.task.id for t in failed])
        else:
            session = _latest_session(db, work_item_id, node, failed[0])
            status = session["status"] if session is not None else "failed"
            note = prompts.task_failure_note(failed[0].task.id, status, session)
        return await run_recovery(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            worktree,
            handler=handler,
            scope=scope,
            failed=failed,
            note=note,
            steer=steer,
            launch=launch,
            budget=budget,
            measured_round=round,
            round=round,
            loop_severities=loop_severities,
        )

    async def _recover(step: ResolvedStep, results: list) -> tuple[list, tuple | None]:
        """`results` after this step's own handlers ran, or the handler's own
        stop when one could not finish (a pause, a config error, a rate limit
        ...): that is the verdict, not the task failure it was answering."""
        if spent is None or any(_is_stop(r) for r in results):
            return results, None
        status = {t.path: r for t, r in zip(step.tasks, results, strict=True)}
        failed = [t for t in step.tasks if _recoverable(status[t.path], node)]
        if not failed:
            return results, None
        for task in failed:
            if not task.on_failure or task.path in spent:
                continue
            spent.add(task.path)
            verdict, h_failed, h_excs = await _handle(task.on_failure, "task", [task])
            if verdict == "ok":
                (status[task.path],) = await _settle([task], step, retry=True)
            elif verdict != "failed":
                return results, (verdict, h_failed, h_excs)
        rest = [t for t in failed if not t.on_failure]
        if rest and step.on_failure and step.path not in spent:
            spent.add(step.path)
            verdict, h_failed, h_excs = await _handle(step.on_failure, "step", rest)
            if verdict == "ok":
                # Every task, a task-recovered one too (step-recovery-retries-the-entire-step).
                return await _settle(step.tasks, step, retry=True), None
            if verdict != "failed":
                return results, (verdict, h_failed, h_excs)
        return [status[t.path] for t in step.tasks], None

    # A read_only node is checked around its own steps, a step around its
    # tasks (`_settle`); recovery and fix loops run outside either check.
    node_before = _snapshot(own and getattr(node.node, "read_only", False))
    outcomes: list[tuple[ResolvedTask, object]] = []
    for index, step in enumerate(groups):
        # A resumed node skips the steps before `start_step` -- they already
        # passed.
        if index < start_step:
            continue
        if own:
            # Recorded before the step runs, not after, so a crash mid-step
            # resumes at that step rather than past it.
            await db.write(lambda c, i=index: store.set_current_step(c, work_item_id, i))
        results = await _settle(step.tasks, step)
        if own:
            results, stopped = await _recover(step, results)
            if stopped is not None:
                return stopped
        outcomes.extend(zip(step.tasks, results, strict=True))
        # Anything but a clean pass stops the node: a later step exists
        # precisely because it must not run against an unsettled earlier one.
        # `_ADVANCING` (executor/context.py) is the existing definition of
        # "this task moved the node forward": ("done", "done_with_concerns").
        if any(isinstance(r, BaseException) or r not in _ADVANCING for r in results):
            break
    if all(r != READ_ONLY_VIOLATED for _, r in outcomes) and await _wrote(node_before, node.id):
        outcomes = [(t, READ_ONLY_VIOLATED) for t, _ in outcomes]
    results = [r for _, r in outcomes]
    # Outranks every other outcome, a pause too: whatever else happened, the
    # worktree a read_only scope promised to leave alone is not the one it found.
    if READ_ONLY_VIOLATED in results:
        return READ_ONLY_VIOLATED, [t for t, r in outcomes if r == READ_ONLY_VIOLATED], []
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
        return CONFIG_ERROR, [t for t, r in outcomes if r == CONFIG_ERROR], []
    # A scope's time ran out (Ruling 194): nothing about the code failed, and
    # no recovery or fix cycle may spend more of the time that is gone.
    if any(r == TIME_CAPPED for r in results):
        return TIME_CAPPED, [t for t, r in outcomes if r == TIME_CAPPED], []
    if any(r == RATE_LIMITED for r in results):
        return RATE_LIMITED, [], []
    if any(r == WAIT_TIMED_OUT for r in results):
        return WAIT_TIMED_OUT, [t for t, r in outcomes if r == WAIT_TIMED_OUT], []
    if any(r == WAITING for r in results):
        # The waiting tasks, so the stop can park the item until the earliest
        # of their next observations.
        return WAITING, [t for t, r in outcomes if r == WAITING], []
    if any(r == INFRA_STOP for r in results):
        return INFRA_STOP, [], []
    # Not a failure, so it must not reach `failed` and open a fix loop or an
    # on_failure repair. The node stopped on purpose.
    if any(r == BASE_MOVED for r in results):
        return BASE_MOVED, [], []
    # A rebase conflict, in a node that declares the handler for one
    # (`rebase-conflict-requires-explicit-handler`): that handler, not a
    # recovery or a fix cycle, answers it. Without one it is a task failure
    # like any other, below.
    if own and node.on_conflict:
        conflicted = [
            t for t, r in outcomes if r == CONFLICT or isinstance(r, _builtins.RebaseConflict)
        ]
        if conflicted:
            return CONFLICT, conflicted, [r for r in results if isinstance(r, BaseException)]
    # Logged before the BUDGET rung returns: a co-task can raise in the same node
    # as a budget-refused agent, and that traceback is the only record of it.
    excs = [r for r in results if isinstance(r, BaseException)]
    for exc in excs:
        logger.error("measuring task raised in node %s: %r", node.id, exc)
    # paused > rate_limited > budget > failed. A pause is a human's instruction
    # and outranks everything. A rate limit and a budget breach both outrank a
    # co-task's failure because the agent's "failure" is not evidence about the
    # code; a rate limit outranks a budget breach because it is Kraft's own
    # spend policy refusing to start, not an external constraint the agent hit.
    if any(r == BUDGET for r in results):
        return BUDGET, [], []
    # Fail closed: only an advancing status is a pass. `failed`, `needs_context`
    # and `conflict` are the statuses a task is expected to fail with, and
    # anything else -- `infra`, `unknown`, a status some adapter invents later --
    # is a result nobody here understands, which must never read as success
    # (Kraft-tfnjt). Every stop sentinel has already returned above.
    failed = [t for t, r in outcomes if isinstance(r, BaseException) or r not in _ADVANCING]
    if failed:
        return "failed", failed, excs
    return "ok", [], []


#: A task in one of these states failed outright. `measure_node` fails these
#: and, closed, any status it does not recognize too.
_FAILING_STATUSES = tuple(s for s, tier in SCOPE.items() if tier == "task")

#: Every status that stops a node rather than failing it (`context.SCOPE`'s
#: `stop` and `chain` tiers). None of them is evidence about the code, so none
#: of them may spend a recovery.
_STOPS = frozenset(s for s, tier in SCOPE.items() if tier in ("stop", "chain"))


def _is_stop(result: object) -> bool:
    return not isinstance(result, BaseException) and result in _STOPS


def _recoverable(result: object, node: ResolvedNode) -> bool:
    """Whether a task's result is a failure a recovery handler answers.

    Not a stop (`_STOPS`), not a pass, and not `needs_context`: a question an
    agent asked is addressed to a human, and no repair task can answer it
    (Kraft-rv6i). A rebase `conflict` belongs to the node's explicit conflict
    handler when it declares one (`rebase-conflict-requires-explicit-
    handler`), so no recovery handler spends itself on it first."""
    if isinstance(result, BaseException) and not isinstance(result, _builtins.RebaseConflict):
        return True
    if result in _ADVANCING or result in _STOPS or result == "needs_context":
        return False
    conflict = result == CONFLICT or isinstance(result, _builtins.RebaseConflict)
    return not (conflict and node.on_conflict)


def _latest_session(db, work_item_id: str, node: ResolvedNode, task: ResolvedTask):
    return db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND hook_point = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (work_item_id, node.id, task.path),
        ).fetchone()
    )


async def run_recovery(
    db,
    run_dirs,
    work_item_id: str,
    node: ResolvedNode,
    row,
    worktree,
    *,
    handler: tuple[ResolvedStep, ...],
    scope: str,
    failed: list[ResolvedTask],
    note: str,
    steer: Steer | None,
    launch: LaunchContext | None,
    budget: _policy.Budget,
    measured_round: int,
    round: int,
    loop_severities: frozenset[str] = _policy.DEFAULT_LOOP_SEVERITIES,
) -> tuple[str, list[ResolvedTask], list[BaseException]]:
    """Run one recovery handler -- a task's, a step's, the node's, or the
    node's conflict handler -- and report its own verdict. Retrying what it
    recovered is the caller's, because only the caller knows the scope.

    The handler is told what the orchestrator already knows (`note`, and the
    findings the failing measurement left) so it does not rediscover it
    (Kraft-s7c04.26). A person's own steer leads and keeps its own template;
    Kraft's context is appended to it, never substituted for it.
    """
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            "node_recovery_started",
            {
                "node_id": node.id,
                "scope": scope,
                "failed_tasks": [t.path for t in failed],
                "tasks": [t.path for step in handler for t in step.tasks],
            },
        )
    )
    found, _reported = collect_findings(db, work_item_id, node, measured_round)
    seeded = prompts.seeded_findings_note(found) if found else None
    note = f"{note}\n{prompts.SUGGEST_ACTION}"
    context = f"{note}\n\n{seeded}" if seeded else note
    if steer is not None and steer and not steer.targeted:
        # A steer addressed to tasks by path is theirs, never a repair's.
        # `.take()` because the incoming note is folded into the one replacing
        # it -- leaving it undelivered here would deliver it twice
        # (Kraft-s7c04.58 covers the two-hook case this single Steer cannot
        # serve).
        repair_steer = Steer(f"{steer.take()}\n\n{context}", source=steer.source)
    else:
        repair_steer = Steer(context, source="seeded")
    verdict, h_failed, h_excs = await measure_node(
        db,
        run_dirs,
        work_item_id,
        node,
        row,
        worktree,
        steps=handler,
        round=round,
        steer=repair_steer,
        launch=launch,
        budget=budget,
        loop_severities=loop_severities,
    )
    if verdict == "ok" and scope != "conflict":
        doubted = [
            t for step in handler for t in step.tasks if repair_doubts(db, work_item_id, node, [t])
        ]
        if doubted:
            return REPAIR_DOUBTED, doubted, []
    return verdict, h_failed, h_excs


def repair_doubts(
    db, work_item_id: str, node: ResolvedNode, tasks: list[ResolvedTask]
) -> list[str]:
    """`task: concerns` for each of `tasks` whose latest session ended
    `done_with_concerns` -- what `REPAIR_DOUBTED` stops on."""
    doubts = []
    for task in tasks:
        session = _latest_session(db, work_item_id, node, task)
        if session is not None and session["status"] == "done_with_concerns":
            concerns = _subprocess.read_concerns(Path(session["result_path"]))
            doubts.append(f"{task.task.id}: {concerns or '(no concerns given)'}")
    return doubts


def measured_tasks(
    node: ResolvedNode, steps: tuple[ResolvedStep, ...] | None = None
) -> dict[str, ResolvedTask]:
    """The tasks one measuring pass ran, by canonical path.

    The same `steps` defaulting `measure_node` uses, so a readback of a pass
    over a recovery plan or a fix loop looks at that group's own tasks and not
    the node's.
    """
    return {t.path: t for step in (node.steps if steps is None else steps) for t in step.tasks}


def collect_findings(
    db,
    work_item_id: str,
    node: ResolvedNode,
    round: int,
    *,
    steps: tuple[ResolvedStep, ...] | None = None,
):
    """(findings, task paths that reported at least one) for one cycle.

    Only the node's own measuring tasks: the fix task is dispatched with
    `round=count` and the next measuring pass runs at that same round, so an
    unfiltered query folds the fix agent's result file into the cycle. Only the
    most recent row per hook point, because a re-entry can measure at a round a
    previous pass already used -- `round` seeds from the persisted fix-loop
    counter, which a gate rejection or a wait re-entry does not clear, and a
    `/retry` deletes the counter row so the next pass restarts at 1 instead.
    Either way stale rows sit at the same number.

    A task that failed without writing a findings file at all -- the
    changed-test-scope builtin is the common case, a subprocess with no findings
    schema to write to -- gets a synthesized `Finding` via `_findings.from_blind_failure` instead
    of vanishing (traced live on a work item that spun for 7 cycles on an
    identical, invisible test failure). `reported` is not extended for it:
    that set means "wrote a real, parseable result file", which a synthesized
    finding does not change -- `walk.py`'s `blind_failures` still computes the
    same way and still forces the loop open, now redundantly with `eligible`,
    which is harmless.

    One task path can carry more than one row in a single round -- the
    changed-test-scope builtin mints one session per matching scope, all under
    this same path (the identity problem, batch-c1 spec's "The identity
    problem underneath all three"). A bare "last wins" read would let a later
    scope's pass silently overwrite an earlier scope's real failure once C2
    stops the loop from short-circuiting on it. Every row for the *latest*
    `head_sha` this hook_point dispatched at is read instead of just the last
    one -- every scope from one dispatch shares that head_sha (stamped once,
    before the loop, in `dispatch_node`'s subprocess branch), so this still
    collapses to the single most recent row for a hook that only ever mints
    one (a re-entry at a *different* head still keeps last-wins there).

    That multi-row read is scoped to builtin tasks -- the only ones a scope
    loop mints more than one session for -- and not to an agent reviewer. A
    reviewer that exits `needs_context` stops before
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
    tasks = measured_tasks(node, steps)
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node.id, round))
    by_hook: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        if row["hook_point"] in tasks:
            by_hook.setdefault(row["hook_point"], []).append(row)  # ordered by created_at
    found: list[_findings.Finding] = []
    reported: set[str] = set()
    for hook, hook_rows in by_hook.items():
        is_scope_loop = isinstance(tasks[hook].task, BuiltinTask)
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
                task = tasks[hook].task
                # An agent's recorded command is its whole argv, prompt and all:
                # nothing to re-run, and a prompt that carries last round's
                # findings (`inputs: [carried_findings]`) would fold into this
                # finding and grow it every round.
                command = (
                    None
                    if isinstance(task, AgentTask)
                    else row["command"]
                    or (task.command if isinstance(task, SubprocessTask) else None)
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
    db, work_item_id: str, node: ResolvedNode, round: int, *, first_iteration: bool = False
) -> str | None:
    """The question from a `needs_context` row in this round, or None.

    Same latest-row-per-hook-point read as `collect_findings` (a resume or
    `/retry` re-enters with stale rows still sitting there, so a first-match
    scan could re-stop the item on a historical row forever) but deliberately
    NOT its "only the measured tasks" filter: the fix loop's own tasks are
    dispatched with this same round, and including them is exactly how a fix
    task's own `needs_context` is meant to surface, one iteration later.

    `first_iteration` is the exception to that, and only that: on the entry's
    very first pass there is by definition no fix from *this* entry yet, so
    any fix row at this round belongs to a bygone one. It matters because
    `walk_node` now seeds `round` from the persisted counter, and it is also
    re-entered on paths that are not resumes -- a gate rejection walking back
    to a fix_loop node, and the wait scheduler -- where the `retry_counters`
    row survives (only `retry_after_cap` deletes it). Without this, the last
    pass's fix question would stop the new pass before it ran a single cycle:
    the same stranding `ESCALATION_HOOK` below closes, through a third door.
    Defaults off so every other caller keeps today's behaviour.

    The fix loop's judge is the one exception. The judge is a brake bolted onto the
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
    skip = {ESCALATION_HOOK} | ({node.judge.path} if node.judge is not None else set())
    if first_iteration:
        skip |= {t.path for step in node.fix_loop for t in step.tasks}
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node.id, round))
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["hook_point"] in skip:
            continue
        latest[row["hook_point"]] = row  # ordered by created_at, so last wins
    for row in latest.values():
        if row["status"] == "needs_context":
            return _subprocess.read_question(Path(row["result_path"])) or "(no question given)"
    return None


def fix_task_paths(node: ResolvedNode) -> list[str]:
    """Every canonical path this node's fix loop dispatches under.

    A fix loop is an ordered shape like any other, so it can hold several
    tasks; the readbacks below want "any of this node's fix work", which is the
    whole set rather than one name.
    """
    return [t.path for step in node.fix_loop for t in step.tasks]


def previous_fix_session(db, work_item_id: str, node: ResolvedNode) -> sqlite3.Row | None:
    """The most recently dispatched fix task for this node, or None if none
    has run yet.

    Deliberately NOT scoped to a `round` passed in by the caller: `round` is
    `walk_node`'s own local counter, and on a fresh entry into that function it
    seeds from the persisted `retry_counters` row -- so it lands back on a
    number a *previous* pass over this node already used (a gate rejection
    walking back here, or the wait scheduler, neither of which clears the
    counter), or on 0 after a `/retry` that deleted the row so the next
    `bump_counter` restarts at 1. Either way the `worker_sessions` rows from
    before that re-entry are still in the table. A lookup keyed on the caller's
    local round can therefore collide with an abandoned attempt that happens to
    land on the same round number (worse than nothing: it hands over a
    plausible-looking file from a cycle that was already exhausted), or, after
    a retry reset, miss every previous attempt outright. Ordering by
    `created_at` and taking the last row sidesteps both: whichever fix task
    actually ran most recently for this node is always the right one to hand
    forward, regardless of what round it or the caller's local counter think
    they're at.
    """
    paths = fix_task_paths(node)
    if not paths:
        return None
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            f"AND hook_point IN ({','.join('?' * len(paths))}) ORDER BY created_at",
            (work_item_id, node.id, *paths),
        ).fetchall()
    )
    return rows[-1] if rows else None


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
    used to get, and the messages and severities are what `walk._carry_severity`
    floors a repeat's rating against (Kraft-s7c04.3) -- and what
    `prompts.carried_findings_note` hands the next reviewer that declares
    `inputs: [carried_findings]` (Kraft-s7c04.1).
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

    Read from the event log rather than carried in a local: crash resume and
    `/resume` re-enter the node through `kraft.executor.walk.run_once` with the
    counter intact, and a loop holding its
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
    # A cycle that started and was refunded (`fix_cycle_refunded`, Kraft-jdkoq)
    # never repaired anything, so it is not a fix having run.
    refunded: set[int] = set()
    for e in reversed(evts):
        if e["payload"].get("node_id") != node_id:
            continue
        if e["type"] == "fix_cycle_refunded":
            refunded.add(e["payload"].get("cycle"))
        elif e["type"] == "fix_cycle_started" and e["payload"].get("cycle") not in refunded:
            fix_seen = True
        elif e["type"] == "findings_measured":
            return (
                [_findings.from_payload(f) for f in e["payload"].get("findings", [])],
                fix_seen,
                e["payload"].get("head_sha"),
            )
    return None, False, None


#: The escalation hook's own `hook_point`. Not a node task and not dispatched
#: from a `tasks` list -- `escalate.dispatch` writes it directly -- so, like
#: the node's judge, its session rows sit in the same `(node, round)` scan that
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
    fix_paths: Sequence[str] = (),
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
    # (wait scheduler, crash resume, /retry), and that counter persists across
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
    #: `fix_paths` is the node's own fix-loop task paths (`fix_task_paths`).
    #: A caller that only wants the per-round findings -- `/retry`'s seeded
    #: steer, which has a node id and no resolved node -- passes none and gets
    #: no fix pointers rather than a second chain read it has no use for.
    fix_rows = (
        db.read(
            lambda c: c.execute(
                "SELECT round, result_path FROM worker_sessions WHERE work_item_id = ? "
                f"AND node_id = ? AND hook_point IN ({','.join('?' * len(fix_paths))}) "
                "ORDER BY round",
                (work_item_id, node_id, *fix_paths),
            ).fetchall()
        )
        if fix_paths
        else []
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
    node: ResolvedNode,
    row,
    worktree,
    *,
    round: int,
    key: str,
    cap: _policy.Cap,
    policy: _policy.Policy,
    launch: LaunchContext | None,
    budget: _policy.Budget,
) -> tuple[str, str]:
    """Ask the fix loop's judge whether the cycle about to be dispatched is
    still worth it (2026-09-12-verify-fix-loop-judge-design). Returns
    `(verdict, reasoning)`; `verdict` is `"paused"` (propagate, no event) or
    one of `"continue"`/`"stop_needs_human"`/`"stop_downgrade"`, never
    anything else -- `_judge_result` is the only place that decides which.

    A fix loop with no judge at all (`fix-loop-judge-is-optional`) fails open
    the same as any other untrusted outcome, rather than dispatching a task
    that does not exist.

    `cap` is the caller's already-resolved cap, not re-resolved here: the judge
    is shown the same budget the loop will actually enforce, and one resolution
    per cycle cannot disagree with itself.
    """
    judge = node.judge
    if judge is None:
        return "continue", ""
    counter = db.read(lambda c: store.read_counter(c, work_item_id, key))
    attempts_used = counter["count"] if counter else 0
    started_at = counter["started_at"] if counter else _now()
    elapsed = (datetime.fromisoformat(_now()) - datetime.fromisoformat(started_at)).total_seconds()
    history = judge_history(
        db, work_item_id, node.id, policy.loop_severities, fix_paths=fix_task_paths(node)
    )
    instruction = prompts.JUDGE_PROMPT.format(
        node_id=node.id,
        history=prompts.format_judge_history(history),
        attempts_used=attempts_used,
        cap_attempts=cap.attempts,
        elapsed_s=int(elapsed),
        cap_wall_clock_s=cap.wall_clock_s,
    )
    status = await dispatch_node(
        db,
        run_dirs,
        judge,
        node,
        row,
        worktree,
        instruction_override=instruction,
        round=round,
        launch=launch,
        budget=budget,
    )
    if status == "paused":
        return "paused", ""
    session = None
    for s in db.read(lambda c: store.sessions_for_round(c, work_item_id, node.id, round)):
        if s["hook_point"] == judge.path:
            session = s  # ordered by created_at -- last one wins
    if session is None:
        return "continue", ""
    result_path = Path(session["result_path"])
    verdict = _judge_result(status, _subprocess.read_verdict(result_path))
    reasoning = _subprocess.read_concerns(result_path) or ""
    return verdict, reasoning
