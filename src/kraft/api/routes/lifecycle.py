from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Annotated, Any, Literal

import psutil
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from kraft import builtins as builtins_mod
from kraft import escalate, events, executor, node_runs, start_queue, storage, store
from kraft import progress as progress_mod
from kraft.adapters import forge as forge_mod
from kraft.adapters.forge.git import PUSHED_REFS
from kraft.api import api_router, deps
from kraft.api.routes import board, search
from kraft.api.routes.search import OpenDocument
from kraft.config import git_read
from kraft.executor import gates, stops, walk
from kraft.policy import NO_CAP, PolicyError
from kraft.templates.forks import ChainPath, PathError, override_record
from kraft.templates.models import AgentTask, GateNode
from kraft.templates.retry import RetryOverrideError, validate_retry_override
from kraft.vocab import (
    ENDED,
    HOLDS_SLOT,
    RUNNING,
    ChainEvent,
    EscalationEvent,
    ForgeEvent,
    LimitEvent,
    StopKind,
    Verb,
    WorkItemEvent,
    WorkItemStatus,
    admitting,
)
from kraft.worker import backends

logger = logging.getLogger(__name__)


class Retry(BaseModel):
    steer: str | None = None
    #: What to rerun, by canonical path: `node`, `node.step` or
    #: `node.step.task`. Absent, the node the item stopped on.
    path: str | None = None
    #: Rerun the whole chain from its first node instead
    #: (`work-item-restart-reruns-the-complete-chain`).
    restart: bool = False
    #: Task fields to change for the retried task, and the retried scope's
    #: policy (`retry-overrides-are-policy-bounded`), validated against the
    #: chain and its policy bounds before anything forks.
    task_config: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None


class Skip(BaseModel):
    note: str | None = None
    #: What to skip, by canonical path: the node the item stands on, or a
    #: `node.step` or `node.step.task` inside it. Absent, the current node or
    #: pending gate.
    path: str | None = None


class Progress(BaseModel):
    #: the N of the plan's `## Task N` heading the agent is starting
    task: int


class MrLabels(BaseModel):
    labels: list[str]


class Steer(BaseModel):
    text: str


class Unblock(BaseModel):
    #: Drop only this dependency. Absent, every one the item still waits on.
    dependency: str | None = None


class Resume(BaseModel):
    #: Reaches every paused agent task (`steer-defaults-to-all-paused-agent-tasks`).
    steer: str | None = None
    #: Individual steers for paused agent tasks, by canonical task path; each
    #: wins over `steer` for the task it names.
    steers: dict[str, str] = {}


class EndWorkItem(BaseModel):
    #: Required (`manual-*-is-an-explicit-work-item-terminal-action`): recorded
    #: on the audit event.
    reason: str


class CompleteWorkItem(EndWorkItem):
    #: Close the item's beads as a walked completion would. Off by default.
    close_beads: bool = False


class CancelWorkItem(EndWorkItem):
    #: Close the item's open merge request on the forge once cancel has ended
    #: it (B4). Off by default -- cancel never touches the forge on its own.
    #: `/complete` does not take this: a completed item's MR is the one the
    #: walk's own `merge` node already landed or left open on purpose.
    close_mr: bool = False


class Escalate(BaseModel):
    message: str
    new_thread: bool = False


def _terminate(pid: int | None) -> None:
    """SIGINT the session's whole process group.

    Sessions are launched with `start_new_session=True`, so the child is its own
    group leader — signalling the group reaches an agent CLI's own children too,
    which a bare kill(pid) would orphan.

    SIGINT, not SIGTERM (Kraft-s7c04.18). An agent CLI treats SIGINT as "stop
    this turn" and flushes its result envelope on the way out; SIGTERM kills it
    without a word. That envelope carries `total_cost_usd`, and it is the only
    cost figure Kraft will ever have for a session a human interrupted -- with
    SIGTERM, 71 of 71 paused sessions recorded NULL cost. Measured against
    claude 2.1.273: SIGTERM mid-turn produced no further output at all, SIGINT
    produced `[Request interrupted by user]` and a complete result line.

    Still only the first rung. `adapters.subprocess.run_task` waits for the
    flush and then `_kill_group` runs the existing SIGTERM -> SIGKILL ladder, so
    a hook that ignores SIGINT dies exactly as promptly as it did before.
    """
    if pid is None:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        pass  # already gone, or not ours — the row still moves to paused


def _kill_orphans_under(worktree: Path) -> list[int]:
    """SIGTERM any process whose cwd sits inside the worktree, before it is deleted.

    Abandon only stops sessions Kraft itself launched (`_terminate`, via
    `pause`'s own path). A server or other long-lived process an agent started
    by hand from inside the worktree — `just dev`, most often — is invisible to
    that path and outlives the directory removal, still listening on its port
    with an interpreter whose `.venv` no longer exists (Kraft-ugm6). Matched by
    cwd rather than command line: cheap, exact, and does not require guessing
    at argv shapes.

    Best-effort, like the removal beside it: a `psutil` per-process call can
    race the process exiting mid-scan, and a process that ignores SIGTERM
    (Kraft-9oab) is a separate problem this does not try to solve.
    """
    worktree = worktree.resolve()
    killed = []
    for proc in psutil.process_iter(["pid"]):
        try:
            cwd = proc.cwd()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if not cwd:
            continue
        try:
            under = Path(cwd).resolve().is_relative_to(worktree)
        except OSError:
            continue
        if not under:
            continue
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        killed.append(proc.pid)
    return killed


async def _stop_live_sessions(st, wid: str, *, pause_item: bool = True) -> list[str]:
    """Pause and SIGTERM every session running on the item's current node.

    The one thing that can be live on an `awaiting_gate`/`needs_human` item a
    human is about to act on: a gate's own `auto_escalate` review
    (`approve_gate`/`reject_gate`, below) or a `needs_human` stop's own
    `auto_escalate_stuck` escalation turn (`retry_work_item`'s
    non-self-retry branch) (Kraft-vyk8). Same ordering as `pause_work_item`:
    the sessions are marked `paused` in the DB *before* they are signalled,
    so the dying subprocess's exit handler reads the already-updated row
    instead of resolving as a crash. A no-op, returning `[]`, when nothing is
    running on this node -- the common case at both call sites, since most
    gates and stops have nothing auto-dispatched onto them at all.
    """
    sessions = st.db.read(lambda c: store.running_sessions_for_node(c, wid))
    ids = [s["id"] for s in sessions]
    if ids:
        if pause_item:
            await st.db.write(lambda c: store.pause_work_item(c, wid, ids))
        else:
            # `retry` stops the turn without moving the item: it goes on to
            # claim the item out of `needs_human` itself, and a status left
            # at `paused` fails that claim ("work item is not stopped").
            # `stop_escalation_session` is exactly that, per-session half of
            # `pause_work_item` minus the work_items UPDATE.
            for sid in ids:
                await st.db.write(lambda c, sid=sid: store.stop_escalation_session(c, wid, sid))
        for s in sessions:
            _terminate(s["pid"])
    return ids


def _forget_sandbox(run_dirs, worktree: Path, wid: str) -> None:
    """Drop what a sandboxed item kept beside its worktree (its code store,
    and the home its agent CLIs wrote caches and chats into), before the
    worktree goes."""
    for backend in backends.every():
        backend.release(run_dirs, worktree, wid)


def _unpushed_commits(repo: Path, branch: str) -> int | None:
    """How many of `branch`'s commits only `branch` holds in `repo`: on no
    other local branch (the base it merged into, say), no remote-tracking
    branch, no tag, and not in what Kraft last pushed of any branch
    (`forge.git.push`'s record, which still has a squash-merged branch the
    forge has since deleted). 0 for a branch `repo` does not have; None when
    git cannot say, which the caller treats as unpushed."""
    ref = f"refs/heads/{branch}"
    if not git_read(repo, "rev-parse", "--verify", "--quiet", ref, expected_failure=True):
        return 0
    count = git_read(
        repo,
        "rev-list",
        "--count",
        ref,
        "--not",
        f"--exclude={branch}",
        "--branches",
        "--remotes",
        "--tags",
        f"--glob={PUSHED_REFS}/*",
    )
    return int(count) if count and count.isdigit() else None


def _branches_to_keep(repo: Path, branch: str, members: list[Path]) -> dict[str, int]:
    """Archive's guard: each repository (`repo`, then each of `members`) whose
    copy of `branch` holds commits nothing else does, with how many. Archive
    leaves the branch there instead of deleting it with the worktree: an item
    cancelled before it pushed has its work on that branch alone, and the
    auto-archive poller reaches it with nobody watching. A count git could
    not read keeps the branch too, counted as one."""
    kept = {}
    for where in [repo, *(m for m in members if m.is_dir())]:
        count = _unpushed_commits(where, branch)
        if count != 0:
            kept[str(where)] = count or 1
    return kept


def _rescue_detached_head(repo: Path, worktree: Path, wid: str) -> dict:
    """Archive's guard for the commits no branch has: the worktree's HEAD,
    when it is detached and holds commits on no branch, remote-tracking
    branch or tag and not in what Kraft pushed. Those are named
    `kraft/rescued/<wid>` in `repo` before the worktree goes, and returned
    as `rescued_branch` and `rescued_commits`; nothing to rescue is {}.

    A rescue git refuses (a sandboxed item's repository may not have the
    commits at all) returns `worktree_kept` with the reason instead: the
    worktree is then the only copy, and archive must leave it.

    A repository moved or deleted since has no commits to name here, and no
    directory to run git in: nothing to rescue."""
    if not worktree.is_dir() or not repo.is_dir():
        return {}
    if git_read(worktree, "symbolic-ref", "-q", "HEAD", expected_failure=True):
        return {}
    head = git_read(worktree, "rev-parse", "--verify", "--quiet", "HEAD", expected_failure=True)
    if not head:
        return {}
    count = git_read(
        repo,
        "rev-list",
        "--count",
        head,
        "--not",
        "--branches",
        "--remotes",
        "--tags",
        f"--glob={PUSHED_REFS}/*",
    )
    if count == "0":
        return {}
    name = f"kraft/rescued/{wid}"
    done = subprocess.run(
        ["git", "update-ref", f"refs/heads/{name}", head],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        reason = done.stderr.strip() or f"git update-ref exited {done.returncode}"
        logger.warning("archive %s: could not keep detached HEAD %s: %s", wid, head, reason)
        return {"worktree_kept": f"rescue failed: {reason}"}
    return {
        "rescued_branch": name,
        "rescued_commits": int(count) if count and count.isdigit() else 1,
    }


async def _remove_worktree(
    repo: Path,
    worktree: Path,
    branch: str,
    wid: str,
    members: list[Path] | None = None,
    *,
    keep_branch_in: frozenset[str] = frozenset(),
) -> dict:
    """Reclaim the worktree and its branch, and the same in each of `members`
    -- the connected repositories its workspace members were checked out
    from (`builtins.member_repositories`, Kraft-ju36l).

    `keep_branch_in` names repositories (by path, `_branches_to_keep`'s keys)
    whose branch, and Kraft's record of pushing it, stay: only their
    worktree goes.

    Takes the branch rather than the work item id: the name is stored on the
    row now, and rebuilding it here would be a second derivation that can
    disagree with the one git actually created (Kraft-nhps).

    Best-effort: the row is already abandoned by the time this runs, and a git
    failure here must not leave the item in a state the board cannot show. The
    prune is between the two because a directory removed out from under git
    leaves an administrative entry that makes the branch delete fail --
    removing the root removes each member's directory with it, so a member
    repository is pruned the same way. Only the root's outcome is returned,
    as `worktree_removed`.

    A repository moved or deleted since leaves git nowhere to run: the
    worktree directory is removed directly instead, since its `.git` link
    points into the missing repository and nothing else could ever reclaim
    it, and the answer names the path as `repo_missing`. Its branch, if the
    repository was only moved, stays there.
    """
    # Lockfiles a server killed mid-rebase left set aside, under the run dir.
    await asyncio.to_thread(shutil.rmtree, builtins_mod.set_aside_dir(worktree), ignore_errors=True)
    if not repo.is_dir():
        logger.warning(
            "abandon %s: repository %s is gone; removing the worktree directory itself",
            branch,
            repo,
        )
        existed = worktree.exists()
        await asyncio.to_thread(shutil.rmtree, worktree, ignore_errors=True)
        await _remove_members(members, branch, keep_branch_in)
        return {
            "worktree_removed": existed and not worktree.exists(),
            "repo_missing": str(repo),
        }
    ok = True
    steps = [
        ["git", "worktree", "remove", "--force", str(worktree)],
        ["git", "worktree", "prune"],
    ]
    if str(repo) not in keep_branch_in:
        steps += [
            ["git", "branch", "-D", branch],
            # What Kraft last pushed of it (`forge.git.push`); absent is fine.
            ["git", "update-ref", "-d", f"{PUSHED_REFS}/{branch}"],
        ]
    for args in steps:
        try:
            done = await asyncio.to_thread(
                subprocess.run, args, cwd=repo, capture_output=True, text=True
            )
        except OSError as exc:
            logger.warning("abandon %s: %s failed: %s", branch, args[1], exc)
            ok = False
            continue
        if done.returncode != 0:
            logger.warning("abandon %s: %s failed: %s", branch, args[1], done.stderr.strip())
            ok = False
    await _remove_members(members, branch, keep_branch_in)
    # The review flow's per-attempt refs (node_runs.pin_ref) die with the branch.
    await asyncio.to_thread(node_runs.drop_refs, repo, wid)
    return {"worktree_removed": ok}


async def _remove_members(
    members: list[Path] | None, branch: str, keep_branch_in: frozenset[str]
) -> None:
    for member in members or []:
        # Best-effort per member, like the rest: a member repository moved or
        # deleted since must not keep the refs and attachments below alive.
        if not member.is_dir():
            logger.warning("abandon %s: member repository %s is gone", branch, member)
            continue
        try:
            await _remove_member_branch(member, branch, keep=str(member) in keep_branch_in)
        except OSError as exc:
            logger.warning("abandon %s in %s: %s", branch, member, exc)


async def _remove_member_branch(member: Path, branch: str, *, keep: bool = False) -> None:
    """Prune `member`'s stale worktree entry and delete `branch` there, and
    Kraft's record of pushing it -- unless `keep`, when only the prune runs."""
    await asyncio.to_thread(
        subprocess.run, ["git", "worktree", "prune"], cwd=member, capture_output=True
    )
    if keep:
        return
    ref = f"refs/heads/{branch}"
    if git_read(member, "rev-parse", "--verify", "--quiet", ref, expected_failure=True):
        done = await asyncio.to_thread(
            subprocess.run,
            ["git", "branch", "-D", branch],
            cwd=member,
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            logger.warning("abandon %s in %s: %s", branch, member, done.stderr.strip())
    await asyncio.to_thread(
        subprocess.run,
        ["git", "update-ref", "-d", f"{PUSHED_REFS}/{branch}"],
        cwd=member,
        capture_output=True,
    )


def _connected_members(st, row) -> list[Path]:
    """`row`'s members' connected repositories, read before its worktree goes."""
    connected = builtins_mod.member_repositories(row, deps.launch(st, row["repo"]).repositories)
    return [m for m in connected.values() if m is not None]


# Kraft-x85; reaping what else ran in the worktree: Kraft-ugm6.
@api_router.post("/work-items/{wid}/abandon")
async def abandon_work_item(wid: str, request: Request):
    """Terminal state plus worktree and branch reclaim, a cancelled item's
    included.

    Refuses while the item is active rather than killing its sessions itself:
    `pause` already owns stopping an attempt, and doing both here would leave
    two places that know how to terminate an agent. That only covers sessions
    Kraft itself launched, though, so anything else started from inside the
    worktree is reaped separately, right before the worktree goes.
    """
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._work_item_row(st, wid)
    if row["status"] not in admitting(Verb.ABANDON):
        raise HTTPException(409, "work item is active; pause it before abandoning")
    # An already-abandoned item is a cancelled one (cancel stores `abandoned`
    # and keeps everything) or a second call: either way it is reclaimed
    # again, without a second `work_item_abandoned`. A second call finds
    # nothing left and answers `worktree_removed: false`.
    if row["status"] != WorkItemStatus.ABANDONED:
        await st.db.write(lambda c: store.abandon_work_item(c, wid))
    worktree = st.run_dirs.worktrees / wid
    killed = await asyncio.to_thread(_kill_orphans_under, worktree)
    if killed:
        logger.warning("abandon %s: killed orphaned process(es) %s under worktree", wid, killed)
    await asyncio.to_thread(_forget_sandbox, st.run_dirs, worktree, wid)
    removed = await _remove_worktree(
        Path(row["repo"]), worktree, store.branch_for(row), wid, _connected_members(st, row)
    )
    if not worktree.exists():
        storage.forget(st, wid)
    # Best-effort, like the worktree removal beside it: the row is already
    # abandoned, and a failure to delete a directory must not leave the item in
    # a state the board cannot show.
    shutil.rmtree(st.run_dirs.attachments / wid, ignore_errors=True)
    return {"id": wid, "status": "abandoned", **removed}


def _archive_lock(st) -> asyncio.Lock:
    """One archive at a time. Made on first use: the stand-in apps the poller
    tests build have no lifespan to make it in."""
    lock = getattr(st, "_archive_lock", None)
    if lock is None:
        lock = st._archive_lock = asyncio.Lock()
    return lock


async def _archive_one(app, row, by: str, *, reason: str | None = None) -> dict:
    """Shared by the archive route, `archive.poller` and `storage.tick`: reclaim the
    worktree the way `abandon_work_item` does, then flip the row. Returns
    `worktree_removed` (a completed item usually still has one; an
    already-abandoned item does not, and `_remove_worktree`'s git calls fail
    best-effort in that case, same as a second `abandon` call today), plus
    `kept_branch` and `unpushed_commits` when the branch stayed.

    Unlike abandon, archive never deletes commits: a branch with commits
    nothing else holds stays (`_branches_to_keep`), a detached HEAD's get a
    branch of their own (`_rescue_detached_head`), and the attachment copies
    stay too, which "Duplicate as new item" re-snapshots from. Uncommitted
    changes in the worktree go with it.
    """
    st = app.state
    wid = row["id"]
    # The age poller, the storage poller and the route can all hold one row.
    async with _archive_lock(st):
        current = st.db.read(
            lambda c: c.execute(
                "SELECT archived_at, archived_by FROM work_items WHERE id = ?", (wid,)
            ).fetchone()
        )
        if current is None or current["archived_at"]:
            # `archived_by` so the route answers who did archive it, not "you".
            return {"worktree_removed": False, "archived_by": current and current["archived_by"]}
        repo, branch = Path(row["repo"]), store.branch_for(row)
        members = _connected_members(st, row)
        worktree = st.run_dirs.worktrees / wid
        kept = await asyncio.to_thread(_branches_to_keep, repo, branch, members)
        extra = {"kept_branch": branch, "unpushed_commits": sum(kept.values())} if kept else {}
        extra |= await asyncio.to_thread(_rescue_detached_head, repo, worktree, wid)
        await st.db.write(lambda c: store.archive_work_item(c, wid, by, reason=reason, **extra))
        for where, count in kept.items():
            logger.info(
                "archive %s kept branch %s in %s: %d unpushed commit(s)", wid, branch, where, count
            )
        if "worktree_kept" in extra:
            # The rescue failed, so the worktree (and a sandbox's ref store beside
            # it) holds the only copy of those commits: archived, but left whole.
            return {"worktree_removed": False, **extra}
        killed = await asyncio.to_thread(_kill_orphans_under, worktree)
        if killed:
            logger.warning("archive %s: killed orphaned process(es) %s under worktree", wid, killed)
        await asyncio.to_thread(_forget_sandbox, st.run_dirs, worktree, wid)
        removed = await _remove_worktree(
            repo, worktree, branch, wid, members, keep_branch_in=frozenset(kept)
        )
        # The directory, not `worktree_removed`: that flag also goes False when the
        # worktree went but deleting its branch or a ref failed (`_remove_worktree`).
        if not worktree.exists():
            storage.forget(st, wid)
        return {**removed, **extra}


# UI v2 · 03.
@api_router.post("/work-items/{wid}/archive")
async def archive_work_item(wid: str, request: Request):
    """Archive a completed/abandoned item: "Ended as" keeps
    reading completed/abandoned -- only `archived_at`/`archived_by` change.
    The answer carries `kept_branch` and `unpushed_commits` when the branch
    stayed because it holds commits nothing else does."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    if row["status"] not in admitting(Verb.ARCHIVE):
        raise HTTPException(409, "only a completed or abandoned item can be archived")
    if row["archived_at"]:
        return {"id": wid, "archived_by": row["archived_by"], "worktree_removed": False}
    result = await _archive_one(request.app, row, "you")
    return {"id": wid, "archived_by": "you", **result}


# UI v2 · 03.
@api_router.post("/work-items/{wid}/restore")
async def restore_work_item(wid: str, request: Request):
    """Put an archived item back under Done."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    if not row["archived_at"]:
        raise HTTPException(409, "work item is not archived")
    await st.db.write(lambda c: store.restore_work_item(c, wid))
    return {"id": wid, "status": row["status"]}


class Bulk(BaseModel):
    action: Literal["pause", "cancel", "archive", "restore"]
    ids: Annotated[list[str], Field(min_length=1, max_length=200)]
    #: Required when `action` is `cancel` (`EndWorkItem.reason`'s own rule);
    #: unused by the other three.
    reason: str | None = None


# B9.
@api_router.post("/work-items/bulk")
async def bulk_work_items(body: Bulk, request: Request):
    """The single-item route each action already has, called once per id,
    in `ids` order, each in its own write -- a `/retry`-style all-or-nothing
    claim would need a new one of those per action, and nothing here asks for
    one. An id's `HTTPException` becomes a result rather than aborting the
    rest, same posture as a CI matrix: one leg's failure does not cancel its
    siblings. `cancel`'s reason is checked once, up front, so a request that
    cannot possibly succeed touches nothing (`EndWorkItem.reason` would
    otherwise answer the same 422 for the first id and silently skip it for
    the rest)."""
    if body.action == "cancel" and not (body.reason or "").strip():
        raise HTTPException(422, "reason: cancel needs a non-blank reason")
    results = [await _bulk_one(request, wid, body.action, body.reason) for wid in body.ids]
    return {"results": results}


async def _bulk_one(request: Request, wid: str, action: str, reason: str | None) -> dict:
    st = request.app.state
    try:
        if action == "pause":
            await pause_work_item(wid, request)
        elif action == "cancel":
            await cancel_work_item(wid, CancelWorkItem(reason=reason or ""), request)
        elif action == "archive":
            await archive_work_item(wid, request)
        else:
            await restore_work_item(wid, request)
    except HTTPException as exc:
        error = "not found" if exc.status_code == 404 else str(exc.detail)
        row = st.db.read(
            lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
        return {"id": wid, "ok": False, "error": error} | (
            {"status": row["status"]} if row is not None else {}
        )
    row = st.db.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    return {"id": wid, "ok": True, "status": row["status"] if row is not None else None}


#: What `pause` answers while the start queue is starting the item.
_STARTING = "work item is being started from the queue: pause it again in a moment"


# 02 §10.2.
@api_router.post("/work-items/{wid}/pause")
async def pause_work_item(wid: str, request: Request):
    """Stop the current node's running sessions.

    There is no stdin channel into a one-shot agent CLI, so pause and steer are
    one mechanism: kill this attempt, carry new context into the next one.
    """
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._work_item_row(st, wid)
    # 'waiting' as well as 'active': a node parked on a pipeline is exactly the
    # thing a human most wants to stop, and it used to 409 (Kraft-tnak). There
    # is no session to signal in that state -- the wait is a row now -- so the
    # write below is the whole operation. 'rate_limited' too (R10b-01's
    # follow-up): the board offers Pause there, and nothing else stops the
    # poller relaunching it -- `store.pause_work_item` clears its `retry_at`.
    if row["status"] not in admitting(Verb.PAUSE):
        if wid in start_queue.starting(request.app) and row["status"] in (
            WorkItemStatus.PAUSED,
            WorkItemStatus.NEEDS_HUMAN,
        ):
            # Taken by the start queue and not yet claimed: the row reads paused
            # or needs_human, but it is about to run.
            raise HTTPException(409, _STARTING)
        raise HTTPException(409, f"work item is {row['status']}, not running")
    if row["status"] in (WorkItemStatus.QUEUED, WorkItemStatus.BLOCKED):
        # Nothing of it runs: pausing withdraws the start it asked for.
        if not await st.db.write(lambda c: store.dequeue_work_item(c, wid, why="paused")):
            # Something took it first: the scheduler, which is starting it, a
            # blocked item's abandoned dependency, or a write that superseded
            # the hold (`store._common._end_overtaken_holds`).
            raise HTTPException(
                409,
                _STARTING
                if wid in start_queue.starting(request.app)
                else "work item status changed; try again",
            )
        return {"id": wid, "paused_sessions": []}
    sessions = st.db.read(lambda c: store.running_sessions_for_node(c, wid))
    ids = [s["id"] for s in sessions]
    # mark first, then signal: the adapter checks the row when its child dies, and
    # a SIGTERM that lands before the row is paused would resolve as 'failed'
    await st.db.write(lambda c: store.pause_work_item(c, wid, ids))
    for s in sessions:
        _terminate(s["pid"])
    # Bounded (Kraft-c5ui): pause spawns nothing, so it would rather return
    # late than block the whole request on a git/forge call the old task
    # happens to be parked in -- the sessions above are already SIGTERM'd
    # either way.
    await deps.cancel(request.app, wid, timeout=deps.CANCEL_TIMEOUT)
    return {"id": wid, "paused_sessions": ids}


@api_router.post("/work-items/{wid}/unblock")
async def unblock_work_item(wid: str, request: Request, body: Unblock | None = None):
    """Stop the item coming after what it still waits on, or after the one
    dependency named. It starts nothing: a blocked item with nothing left to
    wait for is released by the scheduler's next pass (`kraft.start_queue`)."""
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._live_work_item_row(st, wid)
    if row["status"] not in admitting(Verb.UNBLOCK):
        raise HTTPException(
            409,
            f"work item is {row['status']}: only a blocked or paused item's "
            "dependencies can be dropped",
        )
    only = body.dependency if body else None
    dropped = await st.db.write(lambda c: store.drop_dependencies(c, wid, only))
    if only and not dropped:
        raise HTTPException(409, f"{only} is not one of this work item's dependencies")
    return {
        "id": wid,
        "dropped": dropped,
        "waiting_on": st.db.read(lambda c: store.unmet_dependencies(c, wid)),
    }


@api_router.post("/work-items/{wid}/progress")
async def report_progress(wid: str, body: Progress, request: Request):
    """The implementer saying which plan task it is starting. Recorded as a
    `plan_progress` event carrying everything a client needs to show it."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    node_id = progress_mod.active_implementation_node(row)
    if node_id is None:
        if progress_mod.chain_implementation_node(row) is None:
            raise HTTPException(
                409,
                "no implementing node was found in this chain (a node whose own steps "
                "hold an agent task with no `skill:`), so it has no plan progress",
            )
        raise HTTPException(409, "work item is not running its implementation node")
    if progress_mod.rework_run(st.db, row):
        # Kraft-hj2q9: no event, so no client rebuilds a task bar from one.
        raise HTTPException(
            409, "this run is rework after a gate rejection: it follows the note, not the plan"
        )
    worktree = st.run_dirs.worktrees / wid
    tasks = progress_mod.tasks_for(row, worktree)
    if not tasks:
        raise HTTPException(400, "this work item's plan has no '## Task N' headings")
    if not 1 <= body.task <= len(tasks):
        raise HTTPException(400, f"task must be between 1 and {len(tasks)}")
    payload = {
        "node_id": node_id,
        "task": body.task,
        "total": len(tasks),
        "title": tasks[body.task - 1][0],
    }
    await st.db.write(lambda c: events.append(c, wid, WorkItemEvent.PLAN_PROGRESS, payload))
    p = progress_mod.for_item(st.db, row, worktree)
    return {"id": wid, "progress": p.model_dump() if p else None}


def steer_reachable(row, node_id: str | None = None) -> bool:
    """Whether a Steer note given at `node_id` -- this item's current node by
    default -- could reach any agent task from there to the end of its chain.

    One entry point, so the `GET /work-items/{id}` field the UI hides a
    control on and the 409 the steer route raises cannot disagree. Answers off
    the item's frozen snapshot. Fails open (`True`) when the current node is
    not in its own chain, or when there is no current node at all -- an
    unmapped edge is not a reason to hide a control that may still work. A row
    with no snapshot was filed by the legacy loader and no V1 walk can run it,
    so nothing on it can ever read a note.
    """
    start_id = node_id if node_id is not None else row["current_node_id"]
    if start_id is None:
        return True
    v1 = store.materialized_chain_of(row)
    if v1 is None:
        return False
    reached = False
    for node in v1.chain.nodes:
        reached = reached or node.id == start_id
        if reached and any(isinstance(t.task, AgentTask) for t in node.tasks()):
            return True
    # Only "not reachable" when the node was actually found: an id that is
    # in no node of this chain takes the fail-open path.
    return not reached


def review_reachable(row) -> bool:
    """Whether review threads given now could be read by anything ahead: an
    agent task (working agents address them, reviewers judge against them)
    or a gate (it shows them, blocks on a must-fix, bounces or launches the
    reply agent). `steer_reachable` widened by gates; fails open the same way."""
    start_id = row["current_node_id"]
    if start_id is None:
        return True
    if store.materialized_chain_of(row) is None:
        # A legacy row: no V1 walk can run it, so nothing can read a thread --
        # the same answer `steer_reachable` gives. `executor.chain_of` would
        # raise LookupError here, and the route would 500 instead of 409.
        return False
    nodes = store.effective_nodes(executor.chain_of(row), store.node_overrides_of(row))
    reached = False
    for n in nodes:
        reached = reached or n.id == start_id
        if reached and (
            isinstance(n.node, GateNode)
            or any(isinstance(t.task, AgentTask) for s in n.steps for t in s.tasks)
        ):
            return True
    return not reached


def queued_refusal(row) -> str:
    """The 409 every door but pause, cancel and abandon gives a queued item."""
    return (
        "work item is queued for a free slot and starts on its own: "
        f"pause it to take it out of the queue (kraft item pause {row['id']})"
    )


async def slots_answer(st, wid: str) -> dict:
    """The `slots` a queued answer carries. With `storage`, and the item's
    `work_item_storage_held`, when the storage limit and not a busy slot is
    what holds it (`storage.holds`)."""
    limit = st.policy.max_concurrent if st.policy else 1
    slots = {"busy": st.db.read(store.active_count), "limit": limit}
    if storage.holds(st, wid):
        slots["storage"] = storage.figures(st)
        await st.db.write(
            lambda c: events.append(c, wid, WorkItemEvent.STORAGE_HELD, slots["storage"])
        )
    return slots


async def queued_answer(request: Request, wid: str, verb: str, body, from_statuses) -> dict:
    """Hold a start request that passed every check but capacity, and answer
    for it. `kraft.start_queue` makes the same request again when a slot
    frees, so `body` is saved as the caller sent it."""
    st = request.app.state
    queued = await st.db.write(
        lambda c: store.queue_work_item(
            c,
            wid,
            verb=verb,
            body=body.model_dump(mode="json", exclude_defaults=True),
            headers=deps.caller_headers(request),
            from_statuses=from_statuses,
        )
    )
    if not queued:
        raise HTTPException(
            409, "work item is not stopped" if verb == "retry" else "work item is not paused"
        )
    return {"id": wid, "status": WorkItemStatus.QUEUED, "slots": await slots_answer(st, wid)}


def blocked_refusal(row) -> str:
    """The 409 every door but pause, cancel, abandon and unblock gives a
    blocked item."""
    return (
        "work item is blocked until the items it comes after complete, and starts "
        f"on its own: pause it to hold it (kraft item pause {row['id']}), or drop "
        f"them (kraft item unblock {row['id']})"
    )


async def blocked_answer(request: Request, wid: str, verb: str, body, from_statuses) -> dict | None:
    """Hold a start whose item comes after something unfinished, and answer for
    it. None when nothing is unmet: the door goes on to its capacity check.

    An abandoned dependency will never be met, so that start is refused: Kraft
    does not guess whether the work still makes sense without it."""
    st = request.app.state
    unmet = st.db.read(lambda c: store.unmet_dependencies(c, wid))
    if not unmet:
        return None
    dead = [d for d in unmet if d["status"] == WorkItemStatus.ABANDONED]
    if dead:
        names = ", ".join(f"{d['id']} ({d['title']})" for d in dead)
        raise HTTPException(
            409,
            f"work item comes after {names}, which was abandoned and will never complete: "
            f"drop it (kraft item unblock {wid}) or abandon this item",
        )
    blocked = await st.db.write(
        lambda c: store.block_work_item(
            c,
            wid,
            verb=verb,
            body=body.model_dump(mode="json", exclude_defaults=True),
            headers=deps.caller_headers(request),
            from_statuses=from_statuses,
        )
    )
    if not blocked:
        raise HTTPException(
            409, "work item is not stopped" if verb == "retry" else "work item is not paused"
        )
    return {"id": wid, "status": WorkItemStatus.BLOCKED, "waiting_on": unmet}


def held_refusal(row) -> str | None:
    """What a queued or blocked item answers a door that does not take it, or
    None in any other status."""
    if row["status"] == WorkItemStatus.QUEUED:
        return queued_refusal(row)
    if row["status"] == WorkItemStatus.BLOCKED:
        return blocked_refusal(row)
    return None


def _not_stopped(row) -> str:
    """The 409 a retry on an item that is not stopped gets. A paused one is
    told its way on, since Resume, not Retry, is what picks it up (R10b-01)."""
    if held := held_refusal(row):
        return held
    if row["status"] == WorkItemStatus.PAUSED:
        return "work item is paused, not stopped: resume it instead, or skip what it would run"
    return "work item is not stopped"


def not_paused(row, gate: str | None = None) -> str:
    """The 409 a steer or resume on an item that is not paused gets. A running
    one is told what to do (Ruling 183: a steer never reaches a running item),
    and so is one waiting for a person: at a `gate`, approve or reject it;
    stopped anywhere else, retry it, which takes a steer too (R11a)."""
    if held := held_refusal(row):
        return held
    if row["status"] in RUNNING:
        return f"work item is {row['status']}: it is running, so pause it first"
    if row["status"] == WorkItemStatus.NEEDS_HUMAN and gate is not None:
        return (
            f"work item is waiting at its {gate} gate, not paused: approve or reject it "
            f"(kraft item approve {row['id']}, or kraft item reject {row['id']} --note ...)"
        )
    if row["status"] == WorkItemStatus.NEEDS_HUMAN:
        return (
            "work item is needs_human, not paused: it stopped, so retry it instead "
            f"(kraft item retry {row['id']}, which takes --steer too)"
        )
    return f"work item is {row['status']}, not paused"


def paused_steer_refusal(db, row) -> str | None:
    """Why a steer given to this paused item would reach no agentic work, or
    None when an agent task of its node is paused to take it (Ruling 183,
    Kraft-5d3sy). A pause that stopped a subprocess or a CI wait has no agent
    task to resume with the text, and handing it to whichever agent runs next
    is not steering the work the operator stopped. An item filed paused and
    never started stopped nothing: its steer is a note to its first agent, as
    `steer_reachable` already fails open on no current node."""
    if row["current_node_id"] is None or executor.resume_steer(db, row, None, {}):
        return None
    return (
        f"no agent task is paused at node {row['current_node_id']!r}, and a steer reaches "
        "only an agent task a pause stopped; resume without one"
    )


def steerable(st, row) -> bool:
    """`GET /work-items/{id}`'s `steerable`: what the door this item's status
    offers would accept -- a paused item's own agent task, else (a retry or a
    needs_context answer) an agent task downstream (`steer_reachable`)."""
    if row["status"] == WorkItemStatus.PAUSED:
        return paused_steer_refusal(st.db, row) is None
    return steer_reachable(row)


@api_router.post("/work-items/{wid}/steer")
async def steer_work_item(wid: str, body: Steer, request: Request):
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    if row["status"] not in admitting(Verb.STEER) or (
        row["status"] == WorkItemStatus.NEEDS_HUMAN and not board._needs_context_stop(st, wid)
    ):
        raise HTTPException(409, not_paused(row, board._pending_gate(st, wid)))
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "steer text is required")
    if row["status"] == WorkItemStatus.PAUSED and (why := paused_steer_refusal(st.db, row)):
        raise HTTPException(409, why)
    await st.db.write(lambda c: store.set_steer(c, wid, text))
    return {"id": wid, "steer": text}


@api_router.post("/work-items/{wid}/resume")
async def resume_work_item(wid: str, body: Resume, request: Request):
    """Relaunch the paused node, carrying the steer into the next agent launch."""
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._live_work_item_row(st, wid)
    from_statuses = [
        s
        for s in admitting(Verb.RESUME)
        if s != WorkItemStatus.NEEDS_HUMAN
        or (row["status"] == WorkItemStatus.NEEDS_HUMAN and board._needs_context_stop(st, wid))
    ]
    if row["status"] not in from_statuses:
        # Same reason as retry: claim_for_run below still decides, but an item
        # that was never paused should hear that, not a complaint about its
        # steer text or a busy slot.
        raise HTTPException(409, not_paused(row, board._pending_gate(st, wid)))
    running = escalate.escalation_running(st.db, wid)
    if running is not None:
        raise HTTPException(409, f"an escalation turn ({running}) is already running")
    # The one door that starts new work. Deliberately not on `approve` or
    # `retry`: those continue an item that is already underway, and refusing
    # them would strand a human mid-chain with no way to finish (Kraft-n2d).
    # `st.policy` can be None when policy.yaml is invalid (`st.invalid_policy`
    # non-empty) — fall back to the same "1" resume already used rather than
    # crash a path that used to degrade gracefully.
    limit = st.policy.max_concurrent if st.policy else 1
    # A pending gateless rewind (review threads anywhere §1) outranks the
    # paused node: the walk is about to jump to its target, not resume where
    # it stopped, so the typed steer's checks run against the target and
    # nothing paused is being steered.
    rewind = st.db.read(lambda c: store.pending_rewind(c, wid))
    steer_text = None
    if body.steer and body.steer.strip():
        steer_text = body.steer.strip()
        if rewind is not None:
            if not steer_reachable(row, rewind["target"]):
                raise HTTPException(
                    409,
                    f"node {rewind['target']!r} has no agent task downstream to steer; "
                    "this text would be dropped",
                )
        else:
            if row["status"] == WorkItemStatus.PAUSED and (why := paused_steer_refusal(st.db, row)):
                raise HTTPException(409, why)
            found = row["current_node_id"] in store.chain_node_ids(row)
            if found and not steer_reachable(row):
                raise HTTPException(
                    409,
                    f"node {row['current_node_id']!r} has no agent task downstream to steer; "
                    "this text would be dropped",
                )

    individual = {p: t.strip() for p, t in body.steers.items() if t.strip()}
    if rewind is not None:
        # The rewind's target may not be the paused node at all; nothing there
        # is paused to address individually, and the joined note below is the
        # whole steer.
        steer_to = None
    else:
        try:
            steer_to = executor.resume_steer(st.db, row, steer_text, individual)
        except executor.SteerError as exc:
            raise HTTPException(422, str(exc)) from None

    if deps.task_is_live(request.app, wid):
        # Checked before the claim and the rebase below: a paused/needs_human
        # item can still have a live walk task behind it (the brief window
        # while that walk's own request_gate/mark_needs_human is unwinding),
        # and catching spawn's own refusal only after those writes would
        # strand the item claimed 'active' with no walk behind it.
        raise HTTPException(409, "a walk is already running for this work item")
    # Bracketed from *before* the claim to the hand-off (the claim-then-return
    # class). A claim makes this item read `active`, which means "a walk is
    # behind this"; any exit from here that neither spawns one nor leaves a
    # status a selector re-picks would leave it claimed and unowned.
    # `stops.claimed_or_stopped` performs that stop once, for every exit --
    # including the ones no static sweep can enumerate, and including the
    # failed-claim 409s below. Those are *nearly* inert, not inert: a claim fails
    # either because no slot was free -- and then the status is still one
    # `from_statuses` names, which the bracket ignores -- or because the status is
    # not one it claims from, and that one *can* be `active`, for an item a restart
    # left claimed with no walk behind it. In that case the bracket writes
    # `needs_human` on the way out of the 409, which is the right answer for an
    # orphan (the `task_is_live` refusal above already ran, so no walk owns it) but
    # is a refusal path that now moves the status. The comment this replaces said it
    # could not.
    if (held := await blocked_answer(request, wid, "resume", body, from_statuses)) is not None:
        return held
    if storage.holds(st, wid):
        return await queued_answer(request, wid, "resume", body, from_statuses)
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        row["current_node_id"],
        reason="resume claimed this item but could not start a walk",
        handed_off=lambda: deps.task_is_live(request.app, wid),
    ):
        claimed = await st.db.write(
            lambda c: store.claim_for_run(c, wid, from_statuses=from_statuses, limit=limit)
        )
        if not claimed:
            if st.db.read(store.active_count) >= limit:
                return await queued_answer(request, wid, "resume", body, from_statuses)
            raise HTTPException(409, "work item is not paused")
        # The claim moves before this awaited rebase deliberately (Kraft-11e0):
        # the up-to-60s network call now happens on an item already marked
        # `active`, and no second caller can pass the claim while it runs.
        if steer_text is not None:
            await st.db.write(lambda c: store.set_steer(c, wid, steer_text))
        steer = await st.db.write(lambda c: store.take_steer(c, wid))
        start_index = None
        if rewind is not None:
            steer = "\n\n".join(filter(None, [rewind["note"], steer]))
            target_nodes = store.effective_nodes(
                executor.chain_of(row), store.node_overrides_of(row)
            )
            start_index = next(
                (i for i, n in enumerate(target_nodes) if n.id == rewind["target"]), None
            )
        worktree = st.run_dirs.worktrees / wid
        conflict = None
        try:
            # The refresh runs host git in the worktree and its members (Kraft-ju36l).
            stops.refuse_planted_repos(row, deps.launch(st, row["repo"]), worktree)
            # Left to a starting node that rebases itself, which then restarts
            # its span on the move (`walk.rebases_itself`).
            start = rewind["target"] if rewind is not None else row["current_node_id"]
            new_base = (
                None
                if walk.rebases_itself(row, start)
                else await builtins_mod.refresh_worktree_base(
                    worktree,
                    Path(row["repo"]),
                    store.branch_for(row),
                    base=await builtins_mod.base_branch(st.db, wid, Path(row["repo"])),
                )
            )
        except builtins_mod.RebaseConflict as exc:
            # Handed to the walk, which gives it to the node's `on_conflict`
            # handler as it would a task's, or stops for a human (Kraft-e7anb).
            new_base, conflict = None, str(exc)
        except RuntimeError as exc:
            # The claim already flipped this item to 'active'; a failed rebase
            # must not leave it stranded there with no walk behind it.
            reason = str(exc)
            cause = forge_mod.failure_cause(reason) or "git"
            await st.db.write(
                lambda c: store.mark_needs_human(
                    c,
                    wid,
                    row["current_node_id"],
                    reason,
                    kind=StopKind.INFRA,
                    facts={"cause": cause},
                )
            )
            # Not escalated: a git failure is not in the stuck set (Ruling 176).
            return deps.work_item_answer(st, wid)
        # `refresh_worktree_base` now reports the upstream head even when the
        # branch already contained it (Kraft-jypzx), which is the common case
        # on an ordinary resume -- compare against what was already recorded
        # so that case doesn't log a redundant event and DB write every time.
        if new_base and new_base != row["base_ref"]:
            worktree_head = git_read(worktree, "rev-parse", "HEAD", expected_failure=True)
            await st.db.write(
                lambda c: events.append(
                    c,
                    wid,
                    ChainEvent.WORKTREE_REBASE_VERIFIED,
                    {"reported_head": new_base, "worktree_head": worktree_head},
                    node_id=row["current_node_id"],
                )
            )
            await st.db.write(lambda c: store.set_base_ref(c, wid, new_base))
        await st.db.write(lambda c: store.resume_work_item(c, wid, steer))
        # Every paused agent task gets the steer, or its own; a steer left
        # through `/steer` stands in for the one this request did not type.
        targets = {p: t or steer for p, t in (steer_to or {}).items()}
        targets = {p: t for p, t in targets.items() if t} or None

        try:
            deps.spawn(
                request.app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    executor.run(
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        bd_cwd=deps.bd_cwd(),
                        policy=st.policy,
                        # Addressed to the paused agent tasks when there are
                        # any; otherwise the note the next agent launch takes.
                        steer=None if targets else steer,
                        steer_to=targets,
                        launch=deps.launch(st, row["repo"]),
                        on_approve=deps._on_approve(st),
                        conflict=conflict,
                        # No position: the walk resumes at the item's own
                        # cursor, so work that completed before the pause is
                        # not rerun (Kraft-c3dab). An item that never started
                        # stands at the start of its chain. Except when a
                        # gateless rewind is pending: it names its own target.
                        **({"start_index": start_index} if start_index is not None else {}),
                    ),
                ),
            )
        except deps.AlreadyRunning:
            raise HTTPException(409, "a walk is already running for this work item") from None
        return {
            "id": wid,
            "node_id": row["current_node_id"],
            "steer": steer,
            "steered": sorted(targets or {}),
        }


# Design 4b, handoff spec §8.
@api_router.post("/work-items/{wid}/open-worktree")
async def open_worktree(wid: str, body: OpenDocument, request: Request):
    """Open the item's worktree in an editor.

    Local-only by nature: the path means nothing to a browser on another
    machine, which is why the UI only offers this when the server can act on it.
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)
    path = st.run_dirs.worktrees / wid
    if not path.is_dir():
        raise deps.worktree_missing(row)
    return search._launch_editor(request, body.editor, path)


# Design 4b. No route back without it: Kraft-bzwi. reopen-mr: B8.
@api_router.post("/work-items/{wid}/retry")
async def retry_work_item(wid: str, body: Retry, request: Request):
    """Re-run the stopped node, steer text in hand, clearing a breached
    loop cap if there was one.

    This is the only door back onto an item stopped by a task failure. It used
    to refuse a node with no fix loop, on the grounds that only a capped node
    can be capped — true, and beside the point: resume wants `paused`, pause
    wants `running`, and approve/reject want a pending gate, so refusing here
    stranded the item with no route at all. A missing fix loop now
    just means there is no counter to clear.

    An escalated agent calling this on itself (its own `X-Kraft-Session-Id`
    matches the escalation session still live for this item) is deferred
    rather than run inline -- see the `work_item_self_retry_requested`
    branch below.

    The body of this route is `_retry`, so `POST /work-items/{id}/reopen-mr`
    can retry the same stopped node through the exact same function
    after it reopens the merge request, instead of a second copy of this
    logic that could drift from it.
    """
    return await _retry(wid, body, request)


async def _retry(wid: str, body: Retry, request: Request):
    from kraft.api.routes.gates import _decided_by  # local: it imports this module

    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._live_work_item_row(st, wid)
    asked = body  # as the caller sent it: a queued retry is made again from this
    if held := held_refusal(row):
        # Ahead of the node check: an item held before it ever started has none.
        raise HTTPException(409, held)
    if row["current_node_id"] not in store.chain_node_ids(row):
        raise HTTPException(409, "work item has no current node to retry")
    if row["status"] not in admitting(Verb.RETRY):
        # Cheap precondition, ahead of the steer and slot checks: claim_for_run
        # below is still the authoritative gate, but reaching it only after
        # those meant an item that was never stopped got told its steer text
        # was unreachable instead of that it is not stopped.
        raise HTTPException(409, _not_stopped(row))
    if body.path is None and not body.restart:
        rewind = st.db.read(lambda c: store.pending_rewind(c, wid))
        if rewind is not None:
            body = body.model_copy(
                update={
                    "path": rewind["target"],
                    "steer": "\n\n".join(filter(None, [rewind["note"], body.steer])),
                }
            )
    chain = walk.chain_of(row)
    target = _retry_target(chain, row, body)
    override = _retry_override(chain, target, body)
    # The node the rerun starts at: what a steer has to reach from, whose
    # findings seed one, and which counters the retry clears (Kraft-bzwi: a
    # node with no fix loop just has none to clear).
    node = target.node if target is not None else chain.chain.nodes[0]
    node_id = node.id
    is_gate = isinstance(node.node, GateNode)
    key = None if is_gate or node.node.fix_loop is None else walk._loop_key(node)
    gate_key = gates.reject_loop_key(node.id) if is_gate else None
    caller_session_id = request.headers.get("x-kraft-session-id")
    running = escalate.escalation_running(st.db, wid)
    if running is not None and running != caller_session_id:
        # Not the escalation calling on itself -- a stranger's retry (a human,
        # via the UI/API) outranks a still-running escalation turn the same
        # way a human's gate decision outranks a live auto_escalate review
        # (`_stop_live_sessions`, gates.py): kill it and let this retry
        # proceed, instead of refusing the human outright (Kraft-vyk8).
        #
        # The steer check below still refuses this same retry further down --
        # run it *before* the kill, not after: `_stop_live_sessions` SIGTERMs
        # the escalation turn and moves the item needs_human -> paused, and
        # that doesn't undo itself just because the request goes on to 409. A
        # human who gets an error must find the item exactly as it was
        # (code-review finding). The atomic claim below is still the only
        # place that authoritatively decides capacity (Kraft-m43g,
        # Kraft-nxht), but a cheap advisory check here, ahead of the kill,
        # keeps a full board from paying for a live turn's death (SIGTERM
        # plus a cancelled walk task): a full board queues the retry ahead of
        # the kill, which belongs to the start (code-review finding).
        precheck_steer = (body.steer or "").strip() or None
        if precheck_steer is not None and not steer_reachable(row, node_id):
            raise HTTPException(
                409,
                f"node {node_id!r} has no agent task downstream to steer; "
                "this text would be dropped",
            )
        if (
            held := await blocked_answer(request, wid, "retry", asked, admitting(Verb.RETRY))
        ) is not None:
            return held
        limit = st.policy.max_concurrent if st.policy else 1
        if st.db.read(store.active_count) >= limit or storage.holds(st, wid):
            # Queued with the turn left running: the kill below belongs to the
            # start, which makes this same request again.
            return await queued_answer(request, wid, "retry", asked, admitting(Verb.RETRY))
        # `running_sessions_for_node` matches an escalation session by
        # hook_point, not node, so the session `escalation_running` just
        # found above is always among the ones killed here -- including a
        # stale row whose node_id drifted from current_node_id (code review
        # finding). Clearing `running` unconditionally is sound because of
        # that, not in spite of it.
        await _stop_live_sessions(st, wid, pause_item=False)
        # The escalation turn also holds this item's task slot, and the
        # `task_is_live` 409 further down would otherwise refuse this very
        # retry *after* the kill above already changed the item. Unbounded,
        # for the reason `skip` gives at its own `deps.cancel`: this request
        # spawns the replacement walk itself, so the old task has to be gone
        # rather than merely cancelled-and-unwinding, or `spawn` refuses it.
        await deps.cancel(request.app, wid)
        running = None
    if running is not None:
        # The escalation agent calling `kraft item retry` on itself: its own
        # session is still `running`/`pending` in the DB, mid-tool-call, and
        # will not exit until this very response returns -- so the rebase and
        # spawn below cannot run yet without racing a live agent in the same
        # worktree, the exact collision `escalation_running` exists to
        # prevent everywhere else. Record the request instead and let
        # `gates.auto_escalate_stuck` perform it once its `await` on this
        # session's run actually returns (Kraft code-review finding).
        steer = (body.steer or "").strip() or None
        if steer is not None and not steer_reachable(row, node_id):
            raise HTTPException(
                409,
                f"node {node_id!r} has no agent task downstream to steer; "
                "this text would be dropped",
            )
        seeded = False
        if steer is None:
            steer = executor.unresolved_findings_steer(st.db, wid, node_id, st.policy)
            if steer is not None:
                seeded = True
            else:
                last = st.db.read(lambda c: store.last_rejection(c, wid))
                steer = (last or {}).get("note") or None
        await st.db.write(
            lambda c: events.append(
                c,
                wid,
                EscalationEvent.WORK_ITEM_SELF_RETRY_REQUESTED,
                {
                    "session_id": caller_session_id,
                    "node_id": node_id,
                    "path": target.path if target is not None else None,
                    "restart": body.restart,
                    # Validated above, carried as validated (Kraft-vvj32).
                    "override": override_record(override) if override is not None else None,
                    "key": key,
                    "gate_key": gate_key,
                    "steer": steer,
                    "seeded": seeded,
                },
            )
        )
        return {"id": wid, "node_id": node_id, "loop": key, "steer": steer}

    limit = st.policy.max_concurrent if st.policy else 1

    steer = (body.steer or "").strip() or None
    if steer is not None and not steer_reachable(row, node_id):
        # Explicit only: the seeded-findings and last-rejection fallbacks
        # below are Kraft's own carry-forward, not something the caller just
        # typed and needs told.
        raise HTTPException(
            409,
            f"node {node_id!r} has no agent task downstream to steer; this text would be dropped",
        )
    seeded = False
    if steer is None:
        # The last review's unresolved findings (Kraft-7sec, second half) --
        # ahead of last_rejection, which is a human's own note and covers a
        # rejection only; a fix-loop cap breach has none.
        steer = executor.unresolved_findings_steer(st.db, wid, node_id, st.policy)
        if steer is not None:
            seeded = True
        else:
            # The reason the human already typed at the gate. Without this a
            # rejection that exhausted its cap makes them type it twice for it
            # to reach an agent at all (Kraft-ko7j).
            last = st.db.read(lambda c: store.last_rejection(c, wid))
            steer = (last or {}).get("note") or None

    if deps.task_is_live(request.app, wid):
        # Checked before the claim, the rebase, and retry_after_cap below: a
        # needs_human item can still have a live walk task behind it (a
        # pending gate under auto_escalate review, or the brief window while
        # the walk that just called request_gate/mark_needs_human is still
        # unwinding), and catching spawn's own refusal only after those
        # writes would strand the item claimed 'active' with the cap already
        # cleared and no walk behind it.
        raise HTTPException(409, "a walk is already running for this work item")
    # Bracketed from *before* the claim to the hand-off (the claim-then-return
    # class). A claim makes this item read `active`, which means "a walk is
    # behind this"; any exit from here that neither spawns one nor leaves a
    # status a selector re-picks would leave it claimed and unowned.
    # `stops.claimed_or_stopped` performs that stop once, for every exit --
    # including the ones no static sweep can enumerate, and including the
    # failed-claim 409s below. Those are *nearly* inert, not inert: a claim fails
    # either because no slot was free -- and then the status is still one
    # `from_statuses` names, which the bracket ignores -- or because the status is
    # not one it claims from, and that one *can* be `active`, for an item a restart
    # left claimed with no walk behind it. In that case the bracket writes
    # `needs_human` on the way out of the 409, which is the right answer for an
    # orphan (the `task_is_live` refusal above already ran, so no walk owns it) but
    # is a refusal path that now moves the status. The comment this replaces said it
    # could not.
    if (
        held := await blocked_answer(request, wid, "retry", asked, admitting(Verb.RETRY))
    ) is not None:
        return held
    if storage.holds(st, wid):
        return await queued_answer(request, wid, "retry", asked, admitting(Verb.RETRY))
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        node_id,
        reason="retry claimed this item but could not start a walk",
        handed_off=lambda: deps.task_is_live(request.app, wid),
    ):
        claimed = await st.db.write(
            lambda c: store.claim_for_run(c, wid, from_statuses=admitting(Verb.RETRY), limit=limit)
        )
        if not claimed:
            if st.db.read(store.active_count) >= limit:
                return await queued_answer(request, wid, "retry", asked, admitting(Verb.RETRY))
            raise HTTPException(409, "work item is not stopped")
        worktree = st.run_dirs.worktrees / wid
        conflict = None
        try:
            # The refresh runs host git in the worktree and its members (Kraft-ju36l).
            stops.refuse_planted_repos(row, deps.launch(st, row["repo"]), worktree)
            # Left to a node that rebases itself (`walk.rebases_itself`).
            new_base = (
                None
                if walk.rebases_itself(row, node_id)
                else await builtins_mod.refresh_worktree_base(
                    worktree,
                    Path(row["repo"]),
                    store.branch_for(row),
                    base=await builtins_mod.base_branch(st.db, wid, Path(row["repo"])),
                )
            )
        except builtins_mod.RebaseConflict as exc:
            # The retry still forks; the walk hands the conflict to the
            # starting node's `on_conflict` handler, or stops for a human
            # (Kraft-e7anb).
            new_base, conflict = None, str(exc)
        except RuntimeError as exc:
            reason = str(exc)
            cause = forge_mod.failure_cause(reason) or "git"
            await st.db.write(
                lambda c: store.mark_needs_human(
                    c, wid, node_id, reason, kind=StopKind.INFRA, facts={"cause": cause}
                )
            )
            # Not escalated: a git failure is not in the stuck set (Ruling 176).
            return deps.work_item_answer(st, wid)
        # See the matching comment in `resume_work_item` (Kraft-jypzx): an
        # ordinary retry finds the branch already containing the upstream
        # head, so gate the write on this actually changing base_ref.
        if new_base and new_base != row["base_ref"]:
            worktree_head = git_read(worktree, "rev-parse", "HEAD", expected_failure=True)
            await st.db.write(
                lambda c: events.append(
                    c,
                    wid,
                    ChainEvent.WORKTREE_REBASE_VERIFIED,
                    {"reported_head": new_base, "worktree_head": worktree_head},
                    node_id=node_id,
                )
            )
            await st.db.write(lambda c: store.set_base_ref(c, wid, new_base))

        # Computed ahead of `spawn`, which only schedules the walk task: a
        # session for it may or may not exist by the time this request
        # returns, so reading the count after would be a race either way --
        # read it now, while it is still a clean prediction.
        attempt = _retry_attempt(st.db, wid, node, target)
        try:
            deps.spawn(
                request.app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    executor.retry(
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        target=target,
                        # Only a person's retry resets a cap counter
                        # (Kraft-s7c04.22): not a worker's, not an MCP
                        # assistant's.
                        by_person=_decided_by(request) == "human",
                        override=override,
                        bd_cwd=deps.bd_cwd(),
                        policy=st.policy,
                        steer=steer,
                        seeded=seeded,
                        launch=deps.launch(st, row["repo"]),
                        on_approve=deps._on_approve(st),
                        conflict=conflict,
                    ),
                ),
            )
        except deps.AlreadyRunning:
            raise HTTPException(409, "a walk is already running for this work item") from None
        return {
            "id": wid,
            "node_id": node_id,
            "path": target.path if target is not None else None,
            "loop": key,
            "steer": steer,
            "attempt": attempt,
        }


def _retry_attempt(db, wid: str, node, target: ChainPath | None) -> int:
    """B2: the attempt number the task this retry launches will run as.

    A `path` naming a task retries just that one; a `path` naming a step
    retries every task in it; no `path` (and a `restart`, which also hands in
    `target=None`) retries the whole node, so every one of its own tasks is a
    candidate -- the highest next-attempt among them is the one that will
    actually be reported once a session exists (`walk` dispatches the first
    one that is not already `done`, and `next_attempt` is monotonic in
    whichever that turns out to be). The dedicated escalation task is never a
    candidate: its sessions are never written under its own path (they use
    the literal hook_point `"escalation"`), so counting it would only ever
    read a stale, always-empty 1.
    """
    if target is not None and target.task is not None:
        paths = [target.task.path]
    elif target is not None and target.step is not None:
        paths = [t.path for t in target.step.tasks]
    else:
        paths = [t.path for t in node.tasks() if t is not node.escalation]
    if not paths:
        return 1
    return max(db.read(lambda c, p=p: store.next_attempt(c, wid, node.id, p)) for p in paths)


def _retry_target(chain, row, body: Retry) -> ChainPath | None:
    """What a retry reruns: `body.path`, the whole chain on `restart`, or the
    node the item stopped on. A retry reruns its target and everything after
    it, so a target past where the item stands would skip the work between --
    that is `skip`'s job, and refused here."""
    if body.restart:
        if body.path is not None:
            raise HTTPException(422, "path: a restart reruns the whole chain; give one, not both")
        return None
    try:
        target = ChainPath.parse(chain, body.path or row["current_node_id"])
    except PathError as exc:
        raise HTTPException(422, f"path: {exc}") from None
    here = store.node_index(row, row["current_node_id"])
    if target.node_index > here:
        raise HTTPException(
            409,
            f"path: {target.path!r} is after the node the item stands on "
            f"({row['current_node_id']!r}); a retry reruns work, it does not skip ahead",
        )
    return target


def _retry_override(chain, target: ChainPath | None, body: Retry):
    """The validated override, or a 422 naming the field it was refused for
    (`retry-overrides-are-policy-bounded`). Refused before the claim, so a
    refusal forks nothing."""
    if not body.task_config and not body.policy:
        return None
    if target is None:
        field = "task_config" if body.task_config else "policy"
        raise HTTPException(422, f"{field}: a work-item restart carries no override")
    try:
        return validate_retry_override(
            chain, target.path, task_config=body.task_config, policy=body.policy
        )
    except RetryOverrideError as exc:
        raise HTTPException(422, str(exc)) from None


class RaiseBudget(BaseModel):
    #: The new cap; `None` means "no cap" (UI v2 · 04 point 5, Prototype
    #: `raiseBudget`). Omitting the field entirely is a 422 -- there is no
    #: sensible "raise by nothing".
    budget_usd: float | None


#: `set-policy` replaces the item's whole policy override, so a hint that
#: sends someone there has to say so, or the fields it already set are lost.
_SET_POLICY_KEEPS_NOTHING = (
    "set-policy replaces the item's whole policy override, so pass every field it "
    "already sets too (policy_override in `kraft view show ID --json`)"
)

#: Why raising here would not help, per breach scope (`kraft.caps.Breach`),
#: and what does (docs: caps-and-budgets).
_NOT_ITEM_CAP = {
    "usd": (
        "a budget_usd on a node, step or task stopped this item: raise it with "
        "`kraft item set-policy ID --policy budget_usd=N` (item-wide, up to "
        "maxima.work_item; a cap the chain set on that node still binds), then "
        f"retry -- {_SET_POLICY_KEEPS_NOTHING}; policy.yaml only applies to items "
        "filed after it changes"
    ),
    "tokens": (
        "a token_budget stopped this item, not a dollar cap: raise it with "
        "`kraft item set-policy ID --policy token_budget=N` (item-wide, up to "
        f"maxima.work_item), then retry -- {_SET_POLICY_KEEPS_NOTHING}; policy.yaml "
        "only applies to items filed after it changes"
    ),
    "daily": (
        "the daily cap stopped this item, not its own cap: raise budget.daily_usd "
        "in policy.yaml, or wait for local midnight, then retry"
    ),
}
#: A `budget_usd` stop on unknown spend, which no higher cap passes
#: (Kraft-tugdf.12).
_UNKNOWN_SPEND = (
    "a budget_usd stopped this item on spend a harness never reported, which no "
    "higher cap passes: clear it item-wide with `kraft item raise-budget ID --usd none` "
    "(refused under a maxima.work_item.budget_usd; a cap the chain set on a node, "
    "step or task stays), or skip the node"
)


# Point 5. The escalation refusal: Kraft-9efnk.29; another cap's stop: Kraft-9efnk.28.
@api_router.post("/work-items/{wid}/budget/raise")
async def raise_budget(wid: str, body: RaiseBudget, request: Request):
    """Raise a work item's spend cap and continue it from wherever its budget
    stopped it -- the composed action the "Raise budget" button in a `budget`
    `needs_human` card takes. Sets the item's own cap
    (`store.raise_budget`, its own `budget_raised` event so the timeline
    reads "raised the cap", not a generic PATCH) and retries the stopped node
    the same way `POST .../retry` does.

    Refuses a worker's own item before the write: the retry below would
    refuse it anyway, but only after the cap had already moved. An
    escalation turn is refused too: a spending cap is a person's call,
    like a gate.

    A stop on the item-wide `budget_usd` of the item's policy raises that
    one instead: `budget_usd` is merged into the item's stored policy
    override, every other field it sets kept, and checked as a `PATCH` of
    it would be (a 422 under `maxima.work_item`). Spend a harness never
    reported is passed only by no cap at all, so that stop takes only
    `null`. This is the composed action the interface's Raise cap takes in
    two calls.

    Refuses, before the write, any other stop: a node, step or task's
    `budget_usd`, a `token_budget` or `budget.daily_usd` would stop the
    item again right after. And refuses, before the write too, a retry that
    could not start (a walk still running), so a 409
    never leaves the cap raised and the item stopped. With every slot busy the
    cap is raised and the retry is queued. The retry's own claim
    is still what decides: if it refuses after all, the answer says the cap
    was raised and the retry was not.
    """
    st = request.app.state
    deps.forbid_self_action(st, request, wid, escalation_may=False)
    row = deps._live_work_item_row(st, wid)
    if row["status"] not in admitting(Verb.RAISE_BUDGET):
        raise HTTPException(409, held_refusal(row) or "work item is not stopped")
    stop = (board._current_stop(st, wid) or {}).get("budget") or {}
    scope = stop.get("scope")
    item_wide = scope == "usd" and stop.get("path") == ""
    if item_wide and stop.get("unknown_launches") and body.budget_usd is not None:
        raise HTTPException(409, _UNKNOWN_SPEND)
    if scope != "work_item" and not item_wide:
        raise HTTPException(
            409,
            _UNKNOWN_SPEND
            if stop.get("unknown_launches")
            else _NOT_ITEM_CAP.get(scope, "work item was not stopped by a spend cap"),
        )
    spent = stop.get("spent_usd")
    if body.budget_usd is not None and spent is not None and body.budget_usd <= spent:
        # It would stop again at once, as the cap it replaces did (R12E-06).
        raise HTTPException(
            422,
            f"the item has already spent {_usd(spent)}; a cap of {_usd(body.budget_usd)} "
            "would stop it again at once: raise it above that, or to none",
        )
    write = (
        _raise_policy_budget(wid, body.budget_usd)
        if item_wide
        else (lambda c: store.raise_budget(c, wid, body.budget_usd))
    )
    _refuse_a_retry_that_cannot_start(request, wid)
    await st.db.write(write)
    try:
        return await retry_work_item(wid, Retry(steer=None), request)
    except HTTPException as exc:
        raise HTTPException(
            exc.status_code,
            f"the cap was raised to {_usd(body.budget_usd)}, but the retry was refused: "
            f"{exc.detail}. Retry it with `kraft item retry` once that is resolved",
        ) from exc


def _usd(value: float | None) -> str:
    return "no cap" if value is None else f"${value:g}"


def _raise_policy_budget(wid: str, budget_usd: float | None):
    """The write that sets the item-wide `budget_usd` in the item's own
    policy override, keeping every other field it sets: `set-policy` and a
    `PATCH` replace the whole override, so the caller would otherwise have to
    resend it. 422 when the merged override is refused (a maxima bound, say).

    The override is read inside the write, not from the row the route read
    first: a `PATCH` of `policy` queued in between would otherwise land and
    then be overwritten by a merge of the older override."""

    def write(c):
        row = c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        stored = store.policy_override_of(row)
        merged = stored.model_dump(exclude_none=True, exclude_defaults=True) if stored else {}
        merged["budget_usd"] = NO_CAP if budget_usd is None else budget_usd
        chain = store.materialized_chain_of(row)
        if chain is None:
            raise HTTPException(
                409, "this work item has no chain snapshot to hold a policy override"
            )
        try:
            override = chain.with_item_policy(merged).item_policy
        except PolicyError as exc:
            raise HTTPException(422, str(exc)) from exc
        store.set_policy_override(c, wid, override)
        events.append(
            c, wid, LimitEvent.BUDGET_RAISED, {"budget_usd": budget_usd, "key": "policy.budget_usd"}
        )

    return write


def _refuse_a_retry_that_cannot_start(request: Request, wid: str) -> None:
    """`_retry`'s one refusal that does not depend on the cap, asked before
    `raise_budget` writes it: a walk still running. A full board is not one:
    the cap is raised and the retry is queued."""
    if deps.task_is_live(request.app, wid):
        raise HTTPException(
            409, "a walk is already running for this work item; the cap was not changed"
        )


# Spec: docs/superpowers/specs/2026-09-11-skip-step-design.md.
@api_router.post("/work-items/{wid}/skip")
async def skip_work_item(wid: str, body: Skip, request: Request):
    """Advance past the current node or pending gate without running or
    approving it.

    Works from any status the other doors cover between them — active/waiting
    (kills the running session first, same ordering as pause), paused, or
    needs_human, gate or no gate — because unlike retry/resume/approve/reject,
    skip does not care what state stopped the item, only what node is current.
    """
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    # /skip has no live task to gate a race the way spawn does for every other
    # door: paused/needs_human have none yet, and claim_for_run's to_status
    # ("active") is also one of skip's own from_statuses, so two concurrent
    # skips both win that claim and would both write skip_node before either
    # reached spawn. A plain `async with` here would queue the second caller
    # behind the first instead of refusing it -- by the time it woke up the
    # first would already have skipped forward, so the second would just skip
    # the *next* node too (a real operation, but not the 409 two callers
    # racing the same node are supposed to get). Checking `locked()` first
    # makes this a refusal, not a queue: whichever caller's synchronous burst
    # reaches the lock first wins outright.
    if deps.skip_lock(request.app, wid).locked():
        raise HTTPException(409, "a walk is already running for this work item")
    async with deps.skip_lock(request.app, wid):
        row = deps._work_item_row(st, wid)
        if row["status"] not in admitting(Verb.SKIP):
            raise HTTPException(
                409, held_refusal(row) or f"work item is {row['status']}, cannot skip"
            )
        running = escalate.escalation_running(st.db, wid)
        if running is not None:
            raise HTTPException(409, f"an escalation turn ({running}) is already running")
        if row["status"] not in RUNNING and deps.task_is_live(request.app, wid):
            # active/waiting's own live task is the walk this skip is about to
            # cancel below, so it's expected there -- but paused/needs_human
            # are supposed to have none, and occasionally still do (a pending
            # gate under auto_escalate review, or the brief window while the
            # walk that just called request_gate/mark_needs_human is still
            # unwinding). Catching spawn's own refusal only after claim_for_run
            # and skip_node below would leave the item 'active' with the node
            # already recorded skipped and no walk behind it.
            raise HTTPException(409, "a walk is already running for this work item")

        target = _skip_target(row, body.path)
        if target is not None and target.task is None and target.step is None:
            target = None if target.node.id == _skip_node_id(st, wid, row) else target
            if target is not None:
                raise HTTPException(
                    409,
                    f"path: {body.path!r} is not the node the item stands on "
                    f"({row['current_node_id']!r}) or its pending gate",
                )
        if target is not None:
            return await _skip_within_node(st, request, wid, row, target, body.note)
        if row["status"] in RUNNING:
            # Cancel the old walk *first*, before the claim and before
            # skip_node. Cancelling afterwards left a window -- every await
            # between here and there is a chance for the event loop to resume
            # the old walk, which then dispatches the next node and orphans
            # its agent in the worktree. The old walk also has to be gone --
            # not merely cancelled-and-still-unwinding -- before the
            # replacement is spawned, or spawn's own AlreadyRunning check
            # would refuse it. Only for active/waiting: paused/needs_human
            # have no live task of their own to begin with.
            await deps.cancel(request.app, wid)
            # The walk may have moved the item on before it died, so the
            # node this skip is about is whatever is current now, not what
            # the pre-cancel read said.
            row = deps._work_item_row(st, wid)

        node_ids = store.chain_node_ids(row)
        gate = board._pending_gate(st, wid)
        # Both branches over the shared reader, which answers for either chain
        # shape and never raises. This runs after `cancel` above, so a raise
        # here would strand an item whose walk is already gone.
        node_index = (
            store.gate_node_index(row, gate)
            if gate is not None
            else store.node_index(row, row["current_node_id"])
        )
        if node_index is None or node_index >= len(node_ids):
            raise HTTPException(409, "work item has no current node to skip")
        node_id = node_ids[node_index]
        if not ChainPath.parse(walk.chain_of(row), node_id).skippable:
            raise HTTPException(409, f"path: {node_id!r} does not allow skipping")

        sessions = st.db.read(lambda c: store.running_sessions_for_node(c, wid))

        # Bracketed from *before* the claim to the hand-off (the claim-then-return
        # class). A claim makes this item read `active`, which means "a walk is
        # behind this"; any exit from here that neither spawns one nor leaves a
        # status a selector re-picks would leave it claimed and unowned.
        # `stops.claimed_or_stopped` performs that stop once, for every exit --
        # including the ones no static sweep can enumerate, and including the
        # failed-claim 409s below. Those are *nearly* inert, not inert: a claim fails
        # either because no slot was free -- and then the status is still one
        # `from_statuses` names, which the bracket ignores -- or because the status is
        # not one it claims from, and that one *can* be `active`, for an item a restart
        # left claimed with no walk behind it. In that case the bracket writes
        # `needs_human` on the way out of the 409, which is the right answer for an
        # orphan (the `task_is_live` refusal above already ran, so no walk owns it) but
        # is a refusal path that now moves the status. The comment this replaces said it
        # could not.
        async with stops.claimed_or_stopped(
            st.db,
            wid,
            node_id,
            reason="skip could not start a walk for the node after the skipped one",
            handed_off=lambda: deps.task_is_live(request.app, wid),
        ):
            claimed = await st.db.write(
                lambda c: store.claim_for_run(c, wid, from_statuses=admitting(Verb.SKIP))
            )
            if not claimed:
                raise HTTPException(409, "work item status changed; try again")
            note = (body.note or "").strip() or None
            session_ids = [s["id"] for s in sessions]
            # mark first, then signal: same race pause_work_item guards against —
            # a SIGTERM landing before the row says 'paused' resolves as 'failed'.
            await st.db.write(
                lambda c: store.skip_node(c, wid, node_id, gate, note, session_ids=session_ids)
            )
            for s in sessions:
                _terminate(s["pid"])

            try:
                deps.spawn(
                    request.app,
                    wid,
                    deps.guard(
                        st.db,
                        wid,
                        executor.run(
                            st.db,
                            st.run_dirs,
                            work_item_id=wid,
                            bd_cwd=deps.bd_cwd(),
                            start_index=node_index + 1,
                            policy=st.policy,
                            launch=deps.launch(st, row["repo"]),
                            on_approve=deps._on_approve(st),
                        ),
                    ),
                )
            except deps.AlreadyRunning:
                raise HTTPException(409, "a walk is already running for this work item") from None
            return deps.work_item_answer(st, wid)


def _skip_node_id(st, wid: str, row) -> str | None:
    """The node a node skip would skip: the pending gate, or the current node."""
    return board._pending_gate(st, wid) or row["current_node_id"]


def _skip_target(row, path: str | None) -> ChainPath | None:
    """`path` resolved against the item's chain, refused when it names nothing
    there or a component that does not allow skipping
    (`task-step-and-node-are-skippable-by-default`)."""
    if path is None:
        return None
    try:
        target = ChainPath.parse(walk.chain_of(row), path)
    except PathError as exc:
        raise HTTPException(422, f"path: {exc}") from None
    if not target.skippable:
        raise HTTPException(409, f"path: {path!r} does not allow skipping")
    return target


async def _skip_within_node(st, request: Request, wid: str, row, target: ChainPath, note):
    """Skip a task or a step of the node the item stands on
    (`skip-stops-only-the-selected-scope`).

    Only the sessions inside the scope stop; a sibling keeps running. On an
    active item the walk that owns them goes on and counts the scope as done
    (`dispatch.measure_node`). A stopped or paused item is walked again from its
    cursor, where the skipped scope now counts as done.
    """
    if target.node.id != row["current_node_id"]:
        raise HTTPException(
            409,
            f"path: {target.path!r} is not inside the node the item stands on "
            f"({row['current_node_id']!r})",
        )
    note = (note or "").strip() or None
    sessions = st.db.read(lambda c: store.running_sessions_under(c, wid, target.path))
    session_ids = [s["id"] for s in sessions]
    if row["status"] in HOLDS_SLOT:
        # mark first, then signal: the same race `pause_work_item` guards against.
        await st.db.write(
            lambda c: store.skip_scope(c, wid, target.path, note, session_ids=session_ids)
        )
        for s in sessions:
            _terminate(s["pid"])
        return deps.work_item_answer(st, wid)
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        target.node.id,
        reason="skip could not start a walk past the skipped scope",
        handed_off=lambda: deps.task_is_live(request.app, wid),
    ):
        claimed = await st.db.write(
            lambda c: store.claim_for_run(
                c, wid, from_statuses=[s for s in admitting(Verb.SKIP) if s not in HOLDS_SLOT]
            )
        )
        if not claimed:
            raise HTTPException(409, "work item status changed; try again")
        await st.db.write(lambda c: store.skip_scope(c, wid, target.path, note))
        try:
            deps.spawn(
                request.app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    executor.run(
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        bd_cwd=deps.bd_cwd(),
                        policy=st.policy,
                        launch=deps.launch(st, row["repo"]),
                        on_approve=deps._on_approve(st),
                    ),
                ),
            )
        except deps.AlreadyRunning:
            raise HTTPException(409, "a walk is already running for this work item") from None
        return deps.work_item_answer(st, wid)


# Beads stay open: Ruling 167.
@api_router.post("/work-items/{wid}/complete")
async def complete_work_item(wid: str, body: CompleteWorkItem, request: Request):
    """Mark the item complete by hand, with a reason. Its beads stay open
    unless `close_beads` says otherwise: work completed by hand
    may have landed somewhere else, or not at all."""
    row = await _end_work_item(request, wid, "complete", body.reason)
    if body.close_beads:
        await executor.close_beads(
            request.app.state.db, row, deps.bd_cwd(), request.app.state.run_dirs, by_hand=True
        )
    return deps.work_item_answer(request.app.state, wid)


# close_mr: B4.
@api_router.post("/work-items/{wid}/cancel")
async def cancel_work_item(wid: str, body: CancelWorkItem, request: Request):
    """Cancel the item, with a reason. Unlike `abandon` the worktree stays:
    archiving reclaims it later, the same as for any ended item.

    `body.close_mr` closes the item's open merge request once the cancel
    itself has landed -- a forge failure never undoes it, it only shows up in
    the response's `close_mr`.
    """
    row = await _end_work_item(request, wid, "cancel", body.reason)
    result = deps.work_item_answer(request.app.state, wid)
    if body.close_mr:
        result["close_mr"] = await _close_cancelled_mr(request.app.state, wid, row)
    return result


async def _close_cancelled_mr(st, wid: str, row) -> dict:
    """B4: close the just-cancelled item's open merge request. `{ok: True}`
    on success; `{ok: False, error}` for no open MR or a forge failure --
    cancel itself already stands by the time this runs, so neither undoes it.
    """
    ref = board._mr_ref(st, wid)
    if ref is None:
        return {"ok": False, "error": "no open merge request"}
    try:
        state = await board._mr_state(st, row)
    except forge_mod.ForgeError as exc:
        return {"ok": False, "error": str(exc)}
    if state != "open":
        return {"ok": False, "error": "no open merge request"}
    worktree = st.run_dirs.worktrees / wid
    mr_ref = forge_mod.MRRef(number=ref["number"], url=ref["url"], state="open")
    repo_entry = deps.launch(st, row["repo"]).repo_entry
    backend = forge_mod.backend_for("auto", repo_entry.forge if repo_entry else None)
    forge = forge_mod.resolve(backend)
    try:
        await forge.close_mr(repo=worktree, mr=mr_ref)
    except forge_mod.ForgeError as exc:
        return {"ok": False, "error": str(exc)}
    await st.db.write(
        lambda c: events.append(
            c,
            wid,
            ForgeEvent.MR_CLOSED,
            {"ref": ref["number"], "url": ref["url"], "by": "cancel"},
            node_id=row["current_node_id"],
        )
    )
    return {"ok": True}


# B8.
@api_router.post("/work-items/{wid}/reopen-mr")
async def reopen_mr(wid: str, request: Request):
    """Undo the MR-closed stop the poller wrote (`mr_poller.py`) by
    reopening the merge request on the forge, then retrying the stopped node
    the way `POST /retry` with no `path` does -- the same `_retry` function,
    not a copy, so the two can never answer a retry differently.

    409 unless the item is actually stopped on a closed merge request; a
    forge failure answers 502 and leaves the item stopped, same posture as
    `_close_cancelled_mr`'s close.
    """
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._live_work_item_row(st, wid)
    if row["stop_kind"] != StopKind.MR_CLOSED:
        raise HTTPException(
            409, held_refusal(row) or "work item is not stopped on a closed merge request"
        )
    ref = board._mr_ref(st, wid)
    if ref is None:
        raise HTTPException(409, "work item has no merge request to reopen")
    worktree = st.run_dirs.worktrees / wid
    mr_ref = forge_mod.MRRef(number=ref["number"], url=ref["url"], state="closed")
    repo_entry = deps.launch(st, row["repo"]).repo_entry
    backend = forge_mod.backend_for("auto", repo_entry.forge if repo_entry else None)
    forge = forge_mod.resolve(backend)
    try:
        await forge.reopen_mr(repo=worktree, mr=mr_ref)
    except forge_mod.ForgeError as exc:
        raise HTTPException(502, str(exc)) from None
    await st.db.write(
        lambda c: events.append(
            c,
            wid,
            ForgeEvent.MR_REOPENED,
            {"ref": ref["number"], "url": ref["url"]},
            node_id=row["current_node_id"],
        )
    )
    return await _retry(wid, Retry(), request)


async def _end_work_item(request: Request, wid: str, action: str, reason: str):
    """The terminal actions' one door: a work-item action only, it needs a
    reason, stops whatever runs -- sessions, an escalation turn, the walk --
    and leaves the item where no door leads back onto its chain."""
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._work_item_row(st, wid)
    reason = reason.strip()
    if not reason:
        raise HTTPException(422, "reason: a terminal action needs a reason")
    if row["status"] in ENDED:
        raise HTTPException(409, f"work item is already {row['status']}")
    sessions = st.db.read(lambda c: store.running_sessions_for_node(c, wid))
    ids = [s["id"] for s in sessions]
    await st.db.write(lambda c: store.end_work_item(c, wid, action, reason, session_ids=ids))
    for s in sessions:
        _terminate(s["pid"])
    # Bounded, as pause's is: the status is already terminal, so a walk that
    # outlives the wait stops at its next node on its own.
    await deps.cancel(request.app, wid, timeout=deps.CANCEL_TIMEOUT)
    return row


# Spec: docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md.
@api_router.post("/work-items/{wid}/escalate")
async def escalate_work_item(wid: str, body: Escalate, request: Request):
    """Send a message into this item's escalation thread, starting one if
    none exists yet. Only door onto a `needs_human` stop meant for
    back-and-forth with an agent rather than a one-shot retry.
    """
    st = request.app.state
    deps.forbid_self_action(st, request, wid)
    row = deps._live_work_item_row(st, wid)
    if row["status"] not in admitting(Verb.ESCALATE):
        raise HTTPException(409, held_refusal(row) or "work item is not needs_human or paused")
    # A `paused` item that has never started (current_node_id is NULL, per
    # /work-items' "it lands paused" default) has no node/context to
    # escalate about -- dispatch reads row["current_node_id"] straight into
    # the session it creates, which is NOT NULL (Kraft-k5ol widened this
    # check to admit `paused`; a never-started item is `paused` too, and
    # was never the case that widening was meant to cover).
    if row["current_node_id"] is None:
        raise HTTPException(409, "work item has not started")
    budget = (board._current_stop(st, wid) or {}).get("budget") or {}
    if row["status"] == WorkItemStatus.NEEDS_HUMAN and budget:
        # The escalation's agent spends against the same cap, which refused
        # its session with nothing on the item to say so (R12E-05). Every
        # budget stop, the daily cap's too (`tests/api/lifecycle_doors.json`).
        how = (
            "raise budget.daily_usd on Settings › Policy or in policy.yaml, or wait for "
            "local midnight"
            if budget.get("scope") == "daily"
            else f"raise it with `kraft item raise-budget {wid} --usd N` where that takes "
            f"it (`kraft view show {wid}` names the cap)"
        )
        raise HTTPException(
            409,
            f"a spend cap stopped this item, and an escalation's agent would hit it too: "
            f"{how}, then escalate",
        )
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "message is required")
    running = escalate.escalation_running(st.db, wid)
    if running is not None:
        raise HTTPException(409, f"an escalation turn ({running}) is already running")
    # Kraft-s7c04.20: `escalation_running` only catches another *escalation*.
    # A gate's own auto-review (`gates.review_gates`) is a live walk task
    # under this same `wid`, holds the worktree, and is invisible to that
    # check -- this is the guard `/retry` (line ~654) and `/resume` (line
    # ~392) already have and this route was missing, which is how 43717ee6
    # got two agents committing to one worktree.
    if deps.task_is_live(request.app, wid):
        raise HTTPException(409, "a walk is already running for this work item")

    async def _run_escalation() -> None:
        cursor_evts = st.db.read(lambda c: events.read_after(c, 0, wid))
        cursor = cursor_evts[-1]["seq"] if cursor_evts else 0
        await escalate.dispatch(
            st.db,
            st.run_dirs,
            work_item_id=wid,
            message=message,
            launch=deps.launch(st, row["repo"]),
            new_thread=body.new_thread,
        )
        # The agent may have called `kraft item retry` on itself mid-turn
        # (lifecycle.py's `work_item_self_retry_requested` deferral above) --
        # consume it the same way `gates.auto_escalate_stuck` does, or a
        # human-escalated agent that fixes the problem and retries silently
        # stays `needs_human` forever (Kraft code-review finding).
        await executor.resume_after_escalation(
            st.db,
            st.run_dirs,
            work_item_id=wid,
            cursor=cursor,
            policy=st.policy,
            launch=deps.launch(st, row["repo"]),
            bd_cwd=deps.bd_cwd(),
            on_approve=deps._on_approve(st),
        )

    try:
        # Registered under `wid`, not a separate `f"{wid}:escalate"` key
        # (Kraft-s7c04.20): sharing the walk's own task-registry slot is what
        # lets `task_is_live` above -- and `/retry`'s existing
        # `deps.cancel(request.app, wid)` preemption -- actually see this
        # turn. `stop_escalation` (below) doesn't use this registry at all,
        # so nothing else depended on the old key.
        deps.spawn(
            request.app,
            wid,
            deps.guard(st.db, wid, _run_escalation()),
        )
    except deps.AlreadyRunning:
        raise HTTPException(409, "a walk is already running for this work item") from None
    return {"id": wid, "status": "escalating"}


# UI v2 · 06, "Stop agent".
@api_router.post("/work-items/{wid}/escalate/stop")
async def stop_escalation(wid: str, request: Request):
    """Kill the running escalation turn. The item stays
    needs_human at whatever it was stopped for -- only the turn ends."""
    st = request.app.state
    deps._work_item_row(st, wid)
    running = escalate.escalation_running(st.db, wid)
    if running is None:
        raise HTTPException(409, "no escalation turn is running")
    row = st.db.read(
        lambda c: c.execute("SELECT pid FROM worker_sessions WHERE id = ?", (running,)).fetchone()
    )
    await st.db.write(lambda c: store.stop_escalation_session(c, wid, running))
    _terminate(row["pid"] if row else None)
    return {"id": wid, "session_id": running, "status": WorkItemStatus.PAUSED}


# Kraft-xh0q layer 3. The human-gate model: design §6 rule 2.
@api_router.post("/work-items/{wid}/mr-labels")
async def set_mr_labels(wid: str, body: MrLabels, request: Request):
    """Label this item's merge request and re-create its pipeline.

    The mechanism, not the policy: this is the thing an `on_failure` repair
    agent calls once it has read a red `on.ci.poll` and decided which labels
    the trace is asking for. No `_forbid_self_action` here on purpose — that
    guard exists for gates, where a worker deciding for itself would collapse
    the human-gate model. This is the opposite shape: the
    chain fixing metadata on its own merge request is exactly what `open_mr`
    and `ci_poll` already do from inside the same worktree, just triggered by
    an agent's judgement call instead of the executor's own dispatch.
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)
    worktree = st.run_dirs.worktrees / wid
    if not worktree.is_dir():
        raise deps.worktree_missing(row)
    labels = tuple(label.strip() for label in body.labels if label.strip())
    if not labels:
        raise HTTPException(422, "no labels given")
    repo_entry = deps.launch(st, row["repo"]).repo_entry
    try:
        backend = forge_mod.backend_for("auto", repo_entry.forge if repo_entry else None)
        forge = forge_mod.resolve(backend)
        await forge.set_labels(repo=worktree, mr=forge_mod.MR(number=0, url=""), labels=labels)
    except forge_mod.ForgeError as exc:
        raise HTTPException(502, str(exc)) from exc

    # `set_labels` re-creates the pipeline, so the pipeline `on.ci.poll`
    # pinned for this same head sha (Kraft-ivh1) is now the stale, red one --
    # and the pin survives a same-sha re-poll by design. Left alone, the next
    # poll re-reads the pipeline that failed for the missing label, reports
    # the identical finding, and the fix loop calls the item stuck having
    # never looked at the pipeline this repair created.
    def _record(c):
        store.set_ci_pipeline_ref(c, wid, "")
        events.append(
            c,
            wid,
            ForgeEvent.MR_LABELS_SET,
            {"labels": list(labels)},
            node_id=row["current_node_id"],
        )

    await st.db.write(_record)
    return {"work_item_id": wid, "labels": list(labels)}
