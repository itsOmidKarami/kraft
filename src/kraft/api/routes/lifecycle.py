from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path

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
from kraft.templates import Registry

logger = logging.getLogger(__name__)


class Retry(BaseModel):
    steer: str | None = None


class Skip(BaseModel):
    note: str | None = None


class Progress(BaseModel):
    #: the N of the plan's `## Task N` heading the agent is starting
    task: int


class MrLabels(BaseModel):
    labels: list[str]


class Steer(BaseModel):
    text: str


class Resume(BaseModel):
    steer: str | None = None


class Escalate(BaseModel):
    message: str


def _terminate(pid: int | None) -> None:
    """SIGTERM the session's whole process group.

    Sessions are launched with `start_new_session=True`, so the child is its own
    group leader — signalling the group reaches an agent CLI's own children too,
    which a bare kill(pid) would orphan.
    """
    if pid is None:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
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
    `task_progress` event carrying everything a client needs to show it."""
    st = request.app.state
    row = deps._work_item_row(st, wid)
    node_id = progress_mod.active_implementation_node(row)
    if node_id is None:
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
        "title": tasks[body.task - 1],
    }
    await st.db.write(lambda c: events.append(c, wid, "task_progress", payload))
    return {"id": wid, "progress": progress_mod.for_item(st.db, row, worktree)}


def _node_has_agent_task(node: dict, registry: Registry) -> bool:
    """Whether this node's own dispatch could ever consume a Steer note.

    `Steer.take()` (executor.py) is read only from the agent-kind branch of
    `_dispatch`. A node whose own tasks are all subprocess/forge/builtin, and
    which has no `fix_loop` (a fix cycle always falls back to the agent-kind
    `on.implementation.start`), has nothing on it that will ever read one.
    """
    if node.get("fix_loop"):
        return True
    hooks = [*node.get("tasks", []), *(node.get("on_failure") or [])]
    return any(registry.hooks.get(h, {}).get("kind") == "agent" for h in hooks)


def _steer_reachable(nodes: list[dict], start_id: str, registry: Registry) -> bool:
    """Whether a Steer note given at `start_id` could reach *any* agent task
    from there to the end of the chain.

    `run()` threads one `Steer` object through every node from `start_index`
    on (`carried`, executor.py `run`) -- it is consumed by whichever agent-kind
    dispatch runs first, not necessarily the one it was given on. `open_mr`
    (forge-kind, no fix_loop) has nothing of its own, but `human_review` right
    after it does; a note given while stopped at `open_mr` still reaches that
    agent if `open_mr` and `mr_checks` succeed on retry. Checking only the
    current node (Kraft-bz9b's first pass) refused that as dead on arrival.

    Not a guarantee of delivery -- if `start_id` fails again, the walk never
    reaches the later node and the note is dropped same as before -- only
    that it is not *structurally* impossible, which is what the API can 409
    on and the UI can hide a control for.
    """
    reached = False
    for n in nodes:
        if n["id"] == start_id:
            reached = True
        if reached and _node_has_agent_task(n, registry):
            return True
    return False


@api_router.post("/work-items/{wid}/steer")
async def steer_work_item(wid: str, body: Steer, request: Request):
    st = request.app.state
    row = deps._work_item_row(st, wid)
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
    row = deps._work_item_row(st, wid)
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
    if st.db.read(store.active_count) >= limit:
        raise HTTPException(
            409, f"all {limit} slots are busy; pause something or raise max_concurrent"
        )
    chain = json.loads(row["chain_definition"])
    steer_text = None
    if body.steer and body.steer.strip():
        steer_text = body.steer.strip()
        found = any(n["id"] == row["current_node_id"] for n in chain["nodes"])
        if found and not _steer_reachable(chain["nodes"], row["current_node_id"], st.registry):
            raise HTTPException(
                409,
                f"node {row['current_node_id']!r} has no agent task downstream to steer; "
                "this text would be dropped",
            )

    if deps.task_is_live(request.app, wid):
        # Checked before the claim and the rebase below: a paused/needs_human
        # item can still have a live walk task behind it (the brief window
        # while that walk's own request_gate/mark_needs_human is unwinding),
        # and catching spawn's own refusal only after those writes would
        # strand the item claimed 'active' with no walk behind it.
        raise HTTPException(409, "a walk is already running for this work item")
    claimed = await st.db.write(lambda c: store.claim_for_run(c, wid, from_statuses=from_statuses))
    if not claimed:
        raise HTTPException(409, "work item is not paused")

    # The claim moves before this awaited rebase deliberately (Kraft-11e0):
    # the up-to-60s network call now happens on an item already marked
    # `active`, and no second caller can pass the claim while it runs.
    if steer_text is not None:
        await st.db.write(lambda c: store.set_steer(c, wid, steer_text))
    steer = await st.db.write(lambda c: store.take_steer(c, wid))
    worktree = st.run_dirs.worktrees / wid
    try:
        new_base = await builtins_mod.refresh_worktree_base(
            worktree, Path(row["repo"]), store.branch_for(row)
        )
    except RuntimeError as exc:
        # The claim already flipped this item to 'active'; a failed rebase
        # must not leave it stranded there with no walk behind it.
        reason = str(exc)
        await st.db.write(lambda c: store.mark_needs_human(c, wid, row["current_node_id"], reason))
        return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}
    if new_base:
        await st.db.write(lambda c: store.set_base_ref(c, wid, new_base))
    await st.db.write(lambda c: store.resume_work_item(c, wid, steer))

    start = next((i for i, n in enumerate(chain["nodes"]) if n["id"] == row["current_node_id"]), 0)
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
                    registry=st.registry,
                    bd_cwd=deps.bd_cwd(),
                    start_index=start,
                    policy=st.policy,
                    steer=steer,
                    launch=deps.launch(st, row["repo"]),
                    on_approve=deps._on_approve(st),
                ),
            ),
        )
    except deps.AlreadyRunning:
        raise HTTPException(409, "a walk is already running for this work item") from None
    return {"id": wid, "node_id": row["current_node_id"], "steer": steer}


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
    row = deps._work_item_row(st, wid)
    chain = json.loads(row["chain_definition"])
    node_id = row["current_node_id"]
    node = next((n for n in chain["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise HTTPException(409, "work item has no current node to retry")
    if row["status"] != "needs_human":
        # Cheap precondition, ahead of the steer and slot checks: claim_for_run
        # below is still the authoritative gate, but reaching it only after
        # those meant an item that was never stopped got told its steer text
        # was unreachable instead of that it is not stopped.
        raise HTTPException(409, "work item is not stopped")
    key = node.get("fix_loop") or None
    gate = node.get("gate_after")
    gate_key = f"{gate}_reject_loop" if gate else None
    if row["status"] != "needs_human":
        raise HTTPException(409, "work item is not stopped")
    caller_session_id = request.headers.get("x-kraft-session-id")
    running = escalate.escalation_running(st.db, wid)
    if running is not None and running != caller_session_id:
        raise HTTPException(409, f"an escalation turn ({running}) is already running")
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
        if steer is not None and not _steer_reachable(chain["nodes"], node_id, st.registry):
            raise HTTPException(
                409,
                f"node {node_id!r} has no agent task downstream to steer; "
                "this text would be dropped",
            )
        if steer is None:
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
                    "key": key,
                    "gate_key": gate_key,
                    "steer": steer,
                },
            )
        )
        return {"id": wid, "node_id": node_id, "loop": key, "steer": steer}

    limit = st.policy.max_concurrent if st.policy else 1
    if st.db.read(store.active_count) >= limit:
        raise HTTPException(
            409, f"all {limit} slots are busy; pause something or raise max_concurrent"
        )

    steer = (body.steer or "").strip() or None
    if steer is not None and not _steer_reachable(chain["nodes"], node_id, st.registry):
        # Explicit only: the last-rejection fallback below is Kraft's own
        # carry-forward, not something the caller just typed and needs told.
        raise HTTPException(
            409,
            f"node {node_id!r} has no agent task downstream to steer; this text would be dropped",
        )
    if steer is None:
        # The reason the human already typed at the gate. Without this a
        # rejection that exhausted its cap makes them type it twice for it to
        # reach an agent at all (Kraft-ko7j).
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
    claimed = await st.db.write(
        lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"])
    )
    if not claimed:
        raise HTTPException(409, "work item is not stopped")

    worktree = st.run_dirs.worktrees / wid
    try:
        new_base = await builtins_mod.refresh_worktree_base(
            worktree, Path(row["repo"]), store.branch_for(row)
        )
    except RuntimeError as exc:
        reason = str(exc)
        await st.db.write(lambda c: store.mark_needs_human(c, wid, node_id, reason))
        return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}
    if new_base:
        await st.db.write(lambda c: store.set_base_ref(c, wid, new_base))

    await st.db.write(
        lambda c: store.retry_after_cap(c, wid, node_id, key, steer, gate_key=gate_key)
    )
    start = next(i for i, n in enumerate(chain["nodes"]) if n["id"] == node_id)
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
                    registry=st.registry,
                    bd_cwd=deps.bd_cwd(),
                    start_index=start,
                    policy=st.policy,
                    steer=steer,
                    launch=deps.launch(st, row["repo"]),
                    on_approve=deps._on_approve(st),
                ),
            ),
        )
    except deps.AlreadyRunning:
        raise HTTPException(409, "a walk is already running for this work item") from None
    return {"id": wid, "node_id": node_id, "loop": key, "steer": steer}


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
    row = deps._work_item_row(st, wid)
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
        row = deps._work_item_row(st, wid)
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

        chain = json.loads(row["chain_definition"])
        gate = board._pending_gate(st, wid)
        if gate is not None:
            node_index = board._gate_node_index(chain, gate)
        else:
            node_index = next(
                (i for i, n in enumerate(chain["nodes"]) if n["id"] == row["current_node_id"]),
                None,
            )
            if node_index is None:
                raise HTTPException(409, "work item has no current node to skip")
        node_id = chain["nodes"][node_index]["id"]

        sessions = st.db.read(lambda c: store.running_sessions_for_node(c, wid))

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
                        registry=st.registry,
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


@api_router.post("/work-items/{wid}/escalate")
async def escalate_work_item(wid: str, body: Escalate, request: Request):
    """Send a message into this item's escalation thread, starting one if
    none exists yet. Only door onto a `needs_human` stop meant for
    back-and-forth with an agent rather than a one-shot retry (spec:
    docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)
    if row["status"] != "needs_human":
        raise HTTPException(409, "work item is not needs_human")
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "message is required")
    running = escalate.escalation_running(st.db, wid)
    if running is not None:
        raise HTTPException(409, f"an escalation turn ({running}) is already running")

    async def _run_escalation() -> None:
        cursor_evts = st.db.read(lambda c: events.read_after(c, 0, wid))
        cursor = cursor_evts[-1]["seq"] if cursor_evts else 0
        await escalate.dispatch(
            st.db,
            st.run_dirs,
            work_item_id=wid,
            message=message,
            launch=deps.launch(st, row["repo"]),
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
            registry=st.registry,
            policy=st.policy,
            launch=deps.launch(st, row["repo"]),
            bd_cwd=deps.bd_cwd(),
            on_approve=deps._on_approve(st),
        )

    try:
        deps.spawn(
            request.app,
            f"{wid}:escalate",
            deps.guard(st.db, wid, _run_escalation()),
        )
    except deps.AlreadyRunning:
        raise HTTPException(409, "an escalation turn is already running") from None
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
    await st.db.write(lambda c: events.append(c, wid, "mr_labels_set", {"labels": list(labels)}))
    return {"work_item_id": wid, "labels": list(labels)}
