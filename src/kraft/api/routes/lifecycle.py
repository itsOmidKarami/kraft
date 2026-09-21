from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

import psutil
from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import builtins as builtins_mod
from kraft import escalate, events, executor, store
from kraft import progress as progress_mod
from kraft.adapters import forge as forge_mod
from kraft.api import api_router, deps
from kraft.api.routes import board, search
from kraft.api.routes.search import OpenDocument
from kraft.config import git_read
from kraft.executor import gates, stops, walk
from kraft.templates.forks import ChainPath, PathError, override_record
from kraft.templates.models import AgentTask, GateNode
from kraft.templates.retry import RetryOverrideError, validate_retry_override

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
    except ProcessLookupError, PermissionError:
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
        except psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess:
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
        except psutil.NoSuchProcess, psutil.AccessDenied:
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


async def _remove_worktree(repo: Path, worktree: Path, branch: str) -> bool:
    """Reclaim the worktree and its branch.

    Takes the branch rather than the work item id: the name is stored on the
    row now, and rebuilding it here would be a second derivation that can
    disagree with the one git actually created (Kraft-nhps).

    Best-effort: the row is already abandoned by the time this runs, and a git
    failure here must not leave the item in a state the board cannot show. The
    prune is between the two because a directory removed out from under git
    leaves an administrative entry that makes the branch delete fail.
    """
    ok = True
    for args in (
        ["git", "worktree", "remove", "--force", str(worktree)],
        ["git", "worktree", "prune"],
        ["git", "branch", "-D", branch],
    ):
        done = await asyncio.to_thread(
            subprocess.run, args, cwd=repo, capture_output=True, text=True
        )
        if done.returncode != 0:
            logger.warning("abandon %s: %s failed: %s", branch, args[1], done.stderr.strip())
            ok = False
    return ok


@api_router.post("/work-items/{wid}/abandon")
async def abandon_work_item(wid: str, request: Request):
    """Terminal state plus worktree and branch reclaim (Kraft-x85).

    Refuses while the item is active rather than killing its sessions itself:
    `pause` already owns stopping an attempt, and doing both here would leave
    two places that know how to terminate an agent. That only covers sessions
    Kraft itself launched, though, so anything else started from inside the
    worktree is reaped separately, right before the worktree goes (Kraft-ugm6).
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)
    if row["status"] == "active":
        raise HTTPException(409, "work item is active; pause it before abandoning")
    if row["status"] == "abandoned":
        return {"id": wid, "status": "abandoned", "worktree_removed": False}
    await st.db.write(lambda c: store.abandon_work_item(c, wid))
    worktree = st.run_dirs.worktrees / wid
    killed = await asyncio.to_thread(_kill_orphans_under, worktree)
    if killed:
        logger.warning("abandon %s: killed orphaned process(es) %s under worktree", wid, killed)
    removed = await _remove_worktree(Path(row["repo"]), worktree, store.branch_for(row))
    # Best-effort, like the worktree removal beside it: the row is already
    # abandoned, and a failure to delete a directory must not leave the item in
    # a state the board cannot show.
    shutil.rmtree(st.run_dirs.attachments / wid, ignore_errors=True)
    return {"id": wid, "status": "abandoned", "worktree_removed": removed}


async def _archive_one(app, row, by: str) -> bool:
    """Shared by the archive route and `archive.poller`: reclaim the
    worktree the same way `abandon_work_item` does (UI v2 · 03: "Archive
    reclaims the worktree the same way abandon tears it down today"), then
    flip the row. Returns whether the worktree was actually removed (a
    completed item usually still has one; an already-abandoned item does
    not, and `_remove_worktree`'s git calls fail best-effort in that case,
    same as a second `abandon` call today).
    """
    st = app.state
    wid = row["id"]
    await st.db.write(lambda c: store.archive_work_item(c, wid, by))
    worktree = st.run_dirs.worktrees / wid
    killed = await asyncio.to_thread(_kill_orphans_under, worktree)
    if killed:
        logger.warning("archive %s: killed orphaned process(es) %s under worktree", wid, killed)
    removed = await _remove_worktree(Path(row["repo"]), worktree, store.branch_for(row))
    shutil.rmtree(st.run_dirs.attachments / wid, ignore_errors=True)
    return removed


@api_router.post("/work-items/{wid}/archive")
async def archive_work_item(wid: str, request: Request):
    """Archive a completed/abandoned item (UI v2 · 03): "Ended as" keeps
    reading completed/abandoned -- only `archived_at`/`archived_by` change."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    if row["status"] not in ("completed", "abandoned"):
        raise HTTPException(409, "only a completed or abandoned item can be archived")
    if row["archived_at"]:
        return {"id": wid, "archived_by": row["archived_by"], "worktree_removed": False}
    removed = await _archive_one(request.app, row, "you")
    return {"id": wid, "archived_by": "you", "worktree_removed": removed}


@api_router.post("/work-items/{wid}/restore")
async def restore_work_item(wid: str, request: Request):
    """Put an archived item back under Done (UI v2 · 03)."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    if not row["archived_at"]:
        raise HTTPException(409, "work item is not archived")
    await st.db.write(lambda c: store.restore_work_item(c, wid))
    return {"id": wid, "status": row["status"]}


@api_router.post("/work-items/{wid}/pause")
async def pause_work_item(wid: str, request: Request):
    """Stop the current node's running sessions (02 §10.2).

    There is no stdin channel into a one-shot agent CLI, so pause and steer are
    one mechanism: kill this attempt, carry new context into the next one.
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)
    # 'waiting' as well as 'active': a node parked on a pipeline is exactly the
    # thing a human most wants to stop, and it used to 409 (Kraft-tnak). There
    # is no session to signal in that state -- the wait is a row now -- so the
    # write below is the whole operation.
    if row["status"] not in ("active", "waiting"):
        raise HTTPException(409, f"work item is {row['status']}, not running")
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
    await st.db.write(lambda c: events.append(c, wid, "plan_progress", payload))
    return {"id": wid, "progress": progress_mod.for_item(st.db, row, worktree)}


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


@api_router.post("/work-items/{wid}/steer")
async def steer_work_item(wid: str, body: Steer, request: Request):
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    if row["status"] != "paused" and not (
        row["status"] == "needs_human" and board._needs_context_stop(st, wid)
    ):
        raise HTTPException(409, "work item is not paused")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "steer text is required")
    await st.db.write(lambda c: store.set_steer(c, wid, text))
    return {"id": wid, "steer": text}


@api_router.post("/work-items/{wid}/resume")
async def resume_work_item(wid: str, body: Resume, request: Request):
    """Relaunch the paused node, carrying the steer into the next agent launch."""
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    from_statuses = ["paused"]
    if row["status"] == "needs_human" and board._needs_context_stop(st, wid):
        from_statuses.append("needs_human")
    if row["status"] not in from_statuses:
        # Same reason as retry: claim_for_run below still decides, but an item
        # that was never paused should hear that, not a complaint about its
        # steer text or a busy slot.
        raise HTTPException(409, "work item is not paused")
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
    steer_text = None
    if body.steer and body.steer.strip():
        steer_text = body.steer.strip()
        found = row["current_node_id"] in store.chain_node_ids(row)
        if found and not steer_reachable(row):
            raise HTTPException(
                409,
                f"node {row['current_node_id']!r} has no agent task downstream to steer; "
                "this text would be dropped",
            )

    individual = {p: t.strip() for p, t in body.steers.items() if t.strip()}
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
                raise HTTPException(
                    409, f"all {limit} slots are busy; pause something or raise max_concurrent"
                )
            raise HTTPException(409, "work item is not paused")
        # The claim moves before this awaited rebase deliberately (Kraft-11e0):
        # the up-to-60s network call now happens on an item already marked
        # `active`, and no second caller can pass the claim while it runs.
        if steer_text is not None:
            await st.db.write(lambda c: store.set_steer(c, wid, steer_text))
        steer = await st.db.write(lambda c: store.take_steer(c, wid))
        worktree = st.run_dirs.worktrees / wid
        conflict = None
        try:
            new_base = await builtins_mod.refresh_worktree_base(
                worktree, Path(row["repo"]), store.branch_for(row)
            )
        except builtins_mod.RebaseConflict as exc:
            # Handed to the walk, which gives it to the node's `on_conflict`
            # handler as it would a task's, or stops for a human (Kraft-e7anb).
            new_base, conflict = None, str(exc)
        except RuntimeError as exc:
            # The claim already flipped this item to 'active'; a failed rebase
            # must not leave it stranded there with no walk behind it.
            reason = str(exc)
            await st.db.write(
                lambda c: store.mark_needs_human(c, wid, row["current_node_id"], reason)
            )
            # Not escalated: a git failure is not in the stuck set (Ruling 176).
            return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}
        if new_base:
            worktree_head = git_read(worktree, "rev-parse", "HEAD", expected_failure=True)
            await st.db.write(
                lambda c: events.append(
                    c,
                    wid,
                    "worktree_rebase_verified",
                    {"reported_head": new_base, "worktree_head": worktree_head},
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
                        # No position: the walk resumes at the item's own
                        # cursor, so work that completed before the pause is
                        # not rerun (Kraft-c3dab). An item that never started
                        # stands at the start of its chain.
                        policy=st.policy,
                        # Addressed to the paused agent tasks when there are
                        # any; otherwise the note the next agent launch takes.
                        steer=None if targets else steer,
                        steer_to=targets,
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
            "node_id": row["current_node_id"],
            "steer": steer,
            "steered": sorted(targets or {}),
        }


@api_router.post("/work-items/{wid}/open-worktree")
async def open_worktree(wid: str, body: OpenDocument, request: Request):
    """Open the item's worktree in an editor (design 4b, handoff spec §8).

    Local-only by nature: the path means nothing to a browser on another
    machine, which is why the UI only offers this when the server can act on it.
    """
    st = request.app.state
    deps._work_item_row(st, wid)
    path = st.run_dirs.worktrees / wid
    if not path.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")
    return search._launch_editor(request, body.editor, path)


@api_router.post("/work-items/{wid}/retry")
async def retry_work_item(wid: str, body: Retry, request: Request):
    """Re-run the stopped node, steer text in hand (4b), clearing a breached
    loop cap if there was one.

    This is the only door back onto an item stopped by a task failure. It used
    to refuse a node with no fix loop, on the grounds that only a capped node
    can be capped — true, and beside the point: resume wants `paused`, pause
    wants `running`, and approve/reject want a pending gate, so refusing here
    stranded the item with no route at all (Kraft-bzwi). A missing fix loop now
    just means there is no counter to clear.

    An escalated agent calling this on itself (its own `X-Kraft-Session-Id`
    matches the escalation session still live for this item) is deferred
    rather than run inline -- see the `work_item_self_retry_requested`
    branch below.
    """
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    if row["current_node_id"] not in store.chain_node_ids(row):
        raise HTTPException(409, "work item has no current node to retry")
    if row["status"] != "needs_human":
        # Cheap precondition, ahead of the steer and slot checks: claim_for_run
        # below is still the authoritative gate, but reaching it only after
        # those meant an item that was never stopped got told its steer text
        # was unreachable instead of that it is not stopped.
        raise HTTPException(409, "work item is not stopped")
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
        # plus a cancelled walk task) only to 409 on the claim afterwards
        # anyway (code-review finding).
        limit = st.policy.max_concurrent if st.policy else 1
        if st.db.read(store.active_count) >= limit:
            raise HTTPException(
                409, f"all {limit} slots are busy; pause something or raise max_concurrent"
            )
        precheck_steer = (body.steer or "").strip() or None
        if precheck_steer is not None and not steer_reachable(row, node_id):
            raise HTTPException(
                409,
                f"node {node_id!r} has no agent task downstream to steer; "
                "this text would be dropped",
            )
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
                "work_item_self_retry_requested",
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
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        node_id,
        reason="retry claimed this item but could not start a walk",
        handed_off=lambda: deps.task_is_live(request.app, wid),
    ):
        claimed = await st.db.write(
            lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"], limit=limit)
        )
        if not claimed:
            if st.db.read(store.active_count) >= limit:
                raise HTTPException(
                    409, f"all {limit} slots are busy; pause something or raise max_concurrent"
                )
            raise HTTPException(409, "work item is not stopped")
        worktree = st.run_dirs.worktrees / wid
        conflict = None
        try:
            new_base = await builtins_mod.refresh_worktree_base(
                worktree, Path(row["repo"]), store.branch_for(row)
            )
        except builtins_mod.RebaseConflict as exc:
            # The retry still forks; the walk hands the conflict to the
            # starting node's `on_conflict` handler, or stops for a human
            # (Kraft-e7anb).
            new_base, conflict = None, str(exc)
        except RuntimeError as exc:
            reason = str(exc)
            await st.db.write(lambda c: store.mark_needs_human(c, wid, node_id, reason))
            # Not escalated: a git failure is not in the stuck set (Ruling 176).
            return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}
        if new_base:
            worktree_head = git_read(worktree, "rev-parse", "HEAD", expected_failure=True)
            await st.db.write(
                lambda c: events.append(
                    c,
                    wid,
                    "worktree_rebase_verified",
                    {"reported_head": new_base, "worktree_head": worktree_head},
                )
            )
            await st.db.write(lambda c: store.set_base_ref(c, wid, new_base))

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
        }


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


@api_router.post("/work-items/{wid}/budget/raise")
async def raise_budget(wid: str, body: RaiseBudget, request: Request):
    """Raise a work item's spend cap and continue it from wherever its budget
    stopped it -- the composed action the "Raise budget" button in a `budget`
    `needs_human` card takes (point 5). Sets the item's own cap
    (`store.raise_budget`, its own `budget_raised` event so the timeline
    reads "raised the cap", not a generic PATCH) and retries the stopped node
    the same way `POST .../retry` does.
    """
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    if row["status"] != "needs_human":
        raise HTTPException(409, "work item is not stopped")
    await st.db.write(lambda c: store.raise_budget(c, wid, body.budget_usd))
    return await retry_work_item(wid, Retry(steer=None), request)


@api_router.post("/work-items/{wid}/skip")
async def skip_work_item(wid: str, body: Skip, request: Request):
    """Advance past the current node or pending gate without running or
    approving it (docs/superpowers/specs/2026-09-11-skip-step-design.md).

    Works from any status the other doors cover between them — active/waiting
    (kills the running session first, same ordering as pause), paused, or
    needs_human, gate or no gate — because unlike retry/resume/approve/reject,
    skip does not care what state stopped the item, only what node is current.
    """
    st = request.app.state
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
        row = deps._live_work_item_row(st, wid)
        if row["status"] not in ("active", "waiting", "paused", "needs_human"):
            raise HTTPException(409, f"work item is {row['status']}, cannot skip")
        running = escalate.escalation_running(st.db, wid)
        if running is not None:
            raise HTTPException(409, f"an escalation turn ({running}) is already running")
        if row["status"] in ("paused", "needs_human") and deps.task_is_live(request.app, wid):
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
        if row["status"] in ("active", "waiting"):
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
                lambda c: store.claim_for_run(
                    c, wid, from_statuses=["active", "waiting", "paused", "needs_human"]
                )
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
            return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}


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
    if row["status"] == "active":
        # mark first, then signal: the same race `pause_work_item` guards against.
        await st.db.write(
            lambda c: store.skip_scope(c, wid, target.path, note, session_ids=session_ids)
        )
        for s in sessions:
            _terminate(s["pid"])
        return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        target.node.id,
        reason="skip could not start a walk past the skipped scope",
        handed_off=lambda: deps.task_is_live(request.app, wid),
    ):
        claimed = await st.db.write(
            lambda c: store.claim_for_run(
                c, wid, from_statuses=["waiting", "paused", "needs_human"]
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
        return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}


@api_router.post("/work-items/{wid}/complete")
async def complete_work_item(wid: str, body: CompleteWorkItem, request: Request):
    """Mark the item complete by hand, with a reason. Its beads stay open
    unless `close_beads` says otherwise (Ruling 167): work completed by hand
    may have landed somewhere else, or not at all."""
    row = await _end_work_item(request, wid, "complete", body.reason)
    if body.close_beads:
        await executor.close_beads(
            request.app.state.db, row, deps.bd_cwd(), request.app.state.run_dirs
        )
    return deps._work_item_row(request.app.state, wid)


@api_router.post("/work-items/{wid}/cancel")
async def cancel_work_item(wid: str, body: EndWorkItem, request: Request):
    """Cancel the item, with a reason. Unlike `abandon` the worktree stays:
    archiving reclaims it later, the same as for any ended item."""
    await _end_work_item(request, wid, "cancel", body.reason)
    return deps._work_item_row(request.app.state, wid)


async def _end_work_item(request: Request, wid: str, action: str, reason: str):
    """The terminal actions' one door: a work-item action only, it needs a
    reason, stops whatever runs -- sessions, an escalation turn, the walk --
    and leaves the item where no door leads back onto its chain."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    reason = reason.strip()
    if not reason:
        raise HTTPException(422, "reason: a terminal action needs a reason")
    if row["status"] in store.ENDED:
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


@api_router.post("/work-items/{wid}/escalate")
async def escalate_work_item(wid: str, body: Escalate, request: Request):
    """Send a message into this item's escalation thread, starting one if
    none exists yet. Only door onto a `needs_human` stop meant for
    back-and-forth with an agent rather than a one-shot retry (spec:
    docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).
    """
    st = request.app.state
    row = deps._live_work_item_row(st, wid)
    if row["status"] not in ("needs_human", "paused"):
        raise HTTPException(409, "work item is not needs_human or paused")
    # A `paused` item that has never started (current_node_id is NULL, per
    # /work-items' "it lands paused" default) has no node/context to
    # escalate about -- dispatch reads row["current_node_id"] straight into
    # the session it creates, which is NOT NULL (Kraft-k5ol widened this
    # check to admit `paused`; a never-started item is `paused` too, and
    # was never the case that widening was meant to cover).
    if row["current_node_id"] is None:
        raise HTTPException(409, "work item has not started")
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


@api_router.post("/work-items/{wid}/escalate/stop")
async def stop_escalation(wid: str, request: Request):
    """Kill the running escalation turn (06 'Stop agent'). The item stays
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
    return {"id": wid, "session_id": running, "status": "paused"}


@api_router.post("/work-items/{wid}/mr-labels")
async def set_mr_labels(wid: str, body: MrLabels, request: Request):
    """Label this item's merge request and re-create its pipeline (Kraft-xh0q
    layer 3).

    The mechanism, not the policy: this is the thing an `on_failure` repair
    agent calls once it has read a red `on.ci.poll` and decided which labels
    the trace is asking for. No `_forbid_self_action` here on purpose — that
    guard exists for gates, where a worker deciding for itself would collapse
    the human-gate model (design §6 rule 2). This is the opposite shape: the
    chain fixing metadata on its own merge request is exactly what `open_mr`
    and `ci_poll` already do from inside the same worktree, just triggered by
    an agent's judgement call instead of the executor's own dispatch.
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)
    worktree = st.run_dirs.worktrees / wid
    if not worktree.is_dir():
        raise HTTPException(404, "this work item has no worktree yet")
    labels = tuple(label.strip() for label in body.labels if label.strip())
    if not labels:
        raise HTTPException(422, "no labels given")
    repo_entry = deps.launch(st, row["repo"]).repo_entry
    try:
        backend = forge_mod.backend_for("auto", (repo_entry or {}).get("forge"))
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
        events.append(c, wid, "mr_labels_set", {"labels": list(labels)})

    await st.db.write(_record)
    return {"work_item_id": wid, "labels": list(labels)}
