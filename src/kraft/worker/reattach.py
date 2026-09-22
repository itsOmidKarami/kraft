from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from kraft import caps, events, store
from kraft import policy as _policy
from kraft import usage as _usage
from kraft.adapters.subprocess import (
    _kill_group,
    _progress_usage,
    _resolve_exit_file,
    _resolve_result_file,
    fail_abandoned_jobs,
    read_result_fields,
)
from kraft.executor import gates
from kraft.executor.context import LaunchContext, OnApprove
from kraft.executor.dispatch import ESCALATION_HOOK
from kraft.store._common import _now, _span_ms
from kraft.templates.models import AgentTask
from kraft.worker import sandbox as _sandbox

logger = logging.getLogger(__name__)

#: Only a session started this recently gets the grace retry below --
#: Kraft-s7c04.51. The live incident this fixes was 13.6s old
#: (worker_session_started -> session_unknown) when it was declared
#: unconfirmed; this threshold needs comfortable margin above that, not just
#: above zero, or the fix does not even cover the case that motivated it.
#: Not a policy knob: no evidence yet that anyone would want to tune it, and
#: the campaign's own standing lesson is not to build configurability ahead
#: of a need for it.
_REATTACH_GRACE_AGE_MS = 30_000
_REATTACH_GRACE_RETRY_DELAY_S = 2.0


@dataclass
class ReattachSummary:
    scanned: int = 0
    adopted: list[str] = field(default_factory=list)
    resolved_from_file: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    resumed_work_items: list[str] = field(default_factory=list)


def _pid_alive(pid: int) -> bool:
    """True only if pid names a live process (a zombie/defunct child is NOT alive)."""
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return False


def _identity_ok(pid: int, pid_start_time) -> bool:
    if pid_start_time is None or not _pid_alive(pid):
        return False
    try:
        return abs(psutil.Process(pid).create_time() - pid_start_time) < 1e-6
    except psutil.Error:
        return False


def _is_young(started_at) -> bool:
    """Within `_REATTACH_GRACE_AGE_MS` of now -- young enough that
    `_identity_ok`'s answer may not have settled (Kraft-s7c04.51). A row with
    no usable `started_at` is not young: without an age there is no evidence
    the identity check was premature, and guessing "young" would hand a grace
    retry to every such row.
    """
    age_ms = _span_ms(started_at, _now())
    return age_ms is not None and age_ms < _REATTACH_GRACE_AGE_MS


def _identity_mismatch_reason(pid: int | None, pid_start_time) -> str:
    """Which specific check `_identity_ok` failed, for the `session_unknown`
    event a caller writes right after -- Kraft-s7c04.51: a session reattach
    called unconfirmed 13 seconds after it started, and answering "was it
    dead, or a mismatch, or never recorded a start time at all" took a live
    agent minutes of reverse-engineering from a restart timestamp. Recording
    it here means the next occurrence doesn't need that."""
    if pid is None:
        # Before any other check: `psutil.Process(None)` is not an error, it
        # is *this* process -- so falling through would report Kraft's own
        # create_time as the session's `observed=`, inside the one diagnostic
        # this exists to make trustworthy.
        return "no pid recorded"
    if pid_start_time is None:
        return "no pid_start_time recorded"
    if not _pid_alive(pid):
        return f"pid {pid} is not alive"
    try:
        observed = psutil.Process(pid).create_time()
    except psutil.Error:
        return f"pid {pid} could not be inspected"
    return f"pid {pid} create_time mismatch: stored={pid_start_time!r} observed={observed!r}"


async def _exit_from_file(
    db, session_id: str, log_path: Path, result_path: Path, status: str
) -> None:
    """Record a session exit from what it left on disk, carrying every field
    `adapters.subprocess.run_task` carries — `concerns` and `question` reach the
    gate and the needs_context stop only through this event, so a restart that
    dropped them would lose what the worker reported. Usage is the same story
    (Kraft-7co4): a session adopted across a restart used to write NULL tokens
    and NULL cost, dropping the node out of every rollup that sums them.

    `read_result_fields` is the one place that field list is spelled out
    (Kraft-k3d) — this and `adapters.subprocess.run_task` both call it rather
    than each enumerating the fields by hand."""
    fields = read_result_fields(result_path)
    # `worker_sessions` does not record which harness ran a session
    # (Kraft-cvnx1, filed and blocked on this work), so an adopted session has
    # no way to name its own reader here. `claude-stream-json` is every
    # existing install's only harness today; a non-claude session adopted
    # across a restart still gets the result file `usage.read` always tries
    # first, and this reader simply finds nothing in a log it cannot parse.
    seen = _usage.read(log_path, result_path, "claude-stream-json")
    # The same reader assumption, holding an adopted turn to `run_task`'s rule.
    status = await fail_abandoned_jobs(db, session_id, log_path, status, "claude-stream-json")
    await db.write(
        lambda c: store.session_exited(
            c,
            session_id,
            status,
            fields["summary_ref"],
            seen,
            concerns=fields["concerns"],
            question=fields["question"],
        )
    )


async def _resume_adopted_escalation(
    db,
    run_dirs,
    *,
    work_item_id: str,
    session_id: str,
    policy: _policy.Policy | None,
    launch_factory: Callable[[str], LaunchContext] | None,
    bd_cwd: str | None,
    on_approve: OnApprove | None,
) -> None:
    """`_adopt` just recorded this escalation session's exit, but a deferred
    self-retry request it may have left on the timeline
    (`work_item_self_retry_requested`, lifecycle.py) still needs
    `gates.resume_after_escalation` to consume it (Kraft-atdbw) -- the frame
    that would have called it live (`auto_escalate_stuck` or the manual
    `/escalate` route, both awaiting inside `escalate.dispatch`) is gone: the
    restart that orphaned this session also dropped it.

    Reconstructs the same "seq right before dispatch" cursor those live
    callers compute themselves, from this session's own `escalation_message`
    event -- `session_id` there is this session's own `worker_sessions.id`
    (`escalate.dispatch` mints one `uuid.uuid4().hex` and uses it as both).
    `events.read_after` is exclusive of its own `after_seq`, so using this
    event's own `seq` as the cursor correctly excludes it while including
    everything the turn produced after it -- the same events a live caller's
    pre-dispatch cursor would also have excluded.

    Called from `_adopt`'s tail (the live-pid branch of `reattach()`,
    already running inside a backgrounded `asyncio.Task`) and, via
    `_guarded_resume_adopted_escalation`, from the resolved-from-file branch
    too: a session whose pid was already dead or absent at reattach time
    discovers the same shape of exit, but the `auto_escalate_delay` poller
    fires before any human runs `kraft item retry` -- it dispatches a new
    paid turn on the already-fixed stop, whose cursor lands past the stale
    request and never consumes it (Kraft-atdbw). `resume_after_escalation`
    awaits `walk.run(...)` when there's a request to consume, which can run
    for as long as a full agent turn, so both call sites run it as a
    backgrounded task rather than blocking `reattach()` -- and so the whole
    server's startup -- for that long.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    sent = next(
        (
            e
            for e in evts
            if e["type"] == "escalation_message" and e["payload"].get("session_id") == session_id
        ),
        None,
    )
    if sent is None:
        return  # not an escalation turn this item's timeline knows about
    row = db.read(
        lambda c: c.execute("SELECT repo FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    launch = launch_factory(row["repo"]) if launch_factory and row else None
    await gates.resume_after_escalation(
        db,
        run_dirs,
        work_item_id=work_item_id,
        cursor=sent["seq"],
        policy=policy,
        launch=launch,
        bd_cwd=bd_cwd,
        on_approve=on_approve,
    )


async def _guarded_resume_adopted_escalation(
    db,
    run_dirs,
    *,
    work_item_id: str,
    node_id: str,
    session_id: str,
    policy: _policy.Policy | None,
    launch_factory: Callable[[str], LaunchContext] | None,
    bd_cwd: str | None,
    on_approve: OnApprove | None,
) -> None:
    """`_resume_adopted_escalation`, guarded like `_guarded_adopt` guards
    `_adopt`: this runs as a backgrounded task off `reattach`'s own loop
    (Kraft-atdbw), so an uncaught exception here would otherwise vanish into
    asyncio's default handler instead of reaching anything that watches the
    work item."""
    try:
        await _resume_adopted_escalation(
            db,
            run_dirs,
            work_item_id=work_item_id,
            session_id=session_id,
            policy=policy,
            launch_factory=launch_factory,
            bd_cwd=bd_cwd,
            on_approve=on_approve,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "resume_after_escalation crashed for resolved-from-file session %s", session_id
        )
        reason = f"resume_after_escalation crashed: {exc!r}"
        try:
            await db.write(lambda c: store.mark_needs_human(c, work_item_id, node_id, reason))
        except Exception:  # noqa: BLE001
            logger.exception("could not mark %s needs_human after resume crash", work_item_id)


def _is_agent_hook(db, row) -> bool:
    """Whether this session is an agent's, defaulting to yes when unknown.

    A V1 item answers off its own frozen chain: `hook_point` is the task's
    canonical path, which no legacy registry hook matches -- reading the
    registry made every V1 subprocess and builtin session an "agent" and
    recorded a green adopted run as failed (Kraft-hwrks, b5afe84c). A row with
    no snapshot was filed by the legacy loader, which no V1 walk can run; it
    takes the conservative answer below.

    Only `_adopted_status` asks, and its unknown-hook direction has to be the
    conservative one: calling an agent a subprocess would let its exit code
    override the `require_result_file` contract.
    """
    item = db.read(
        lambda c: c.execute(
            "SELECT * FROM work_items WHERE id = ?", (row["work_item_id"],)
        ).fetchone()
    )
    snapshot = store.materialized_chain_of(item) if item is not None else None
    if snapshot is not None:
        task = next(
            (t for n in snapshot.chain.nodes for t in n.tasks() if t.path == row["hook_point"]),
            None,
        )
        return task is None or isinstance(task.task, AgentTask)
    return True


def _adopted_status(result_path, *, is_agent: bool) -> str:
    """Result file, else the task's own recorded exit code, else `unknown`.

    Never `failed` on absent evidence: an adopted process cannot be reaped, so
    `_resolve_result_file(...) or "failed"` recorded every green subprocess run as a
    failure and fed the fix loop a defect that was not there (b5afe84c: two
    green 14-minute `just ci-test` runs, 28 minutes re-fixing passing tests).

    The exit file is NOT consulted for an agent hook. `run_task` applies
    `require_result_file` for those (`adapters/agent.py`): an agent that exits
    clean without writing its result file broke its contract and is `failed`.
    Letting a 0 exit code speak for it would record a real failure as `done`,
    which is the one direction that must never happen -- so a session whose
    task cannot be found in its item's chain is treated as an agent too.
    """
    status = _evidenced_status(result_path, is_agent=is_agent)
    if status is not None:
        return status
    return "failed" if is_agent else "unknown"


def _evidenced_status(result_path, *, is_agent: bool) -> str | None:
    """What a session left on disk says it ended as, or None if nothing does:
    its result file, else -- never for an agent, see `_adopted_status` -- its
    own recorded exit code."""
    status = _resolve_result_file(Path(result_path))
    if status is None and not is_agent:
        status = _resolve_exit_file(Path(result_path).with_suffix(".exit"))
    return status


async def _adopt(
    db,
    session_id: str,
    pid: int,
    poll_s: float = 0.1,
    progress_s: float = 5.0,
    *,
    run_dirs=None,
    policy: _policy.Policy | None = None,
    launch_factory: Callable[[str], LaunchContext] | None = None,
    bd_cwd: str | None = None,
    on_approve: OnApprove | None = None,
) -> None:
    row = db.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = ?", (session_id,)).fetchone()
    )
    log_path = Path(row["log_path"])
    result_path = Path(row["result_path"])
    # The child survived the restart that orphaned this task (run_task starts it
    # start_new_session=True precisely so it can) and keeps writing its own log
    # regardless. Without polling that log here too, tokens_in/out freeze at
    # whatever run_task's own loop last wrote before the restart, for the rest
    # of the session's life (Kraft-jgs6).
    seen_usage: dict[str, _usage.Usage] = {}
    log_offset = 0
    next_progress = time.monotonic() + progress_s
    # The cap it was launched under still binds it (Kraft-kx2fs): its scope's
    # time left, from the timeline, with its own run counted from its start.
    item = db.read(
        lambda c: c.execute(
            "SELECT * FROM work_items WHERE id = ?", (row["work_item_id"],)
        ).fetchone()
    )
    hit = db.read(lambda c: caps.for_session(c, item, row)) if item is not None else None
    deadline = caps.monotonic() + hit.remaining_s if hit is not None else None
    while _pid_alive(pid):
        if deadline is not None and caps.monotonic() >= deadline:
            await _stop_at_cap(db, row, pid, hit)
            return
        await asyncio.sleep(poll_s)
        if time.monotonic() < next_progress:
            continue
        next_progress = time.monotonic() + progress_s
        try:
            # Same reasoning as `_exit_from_file`'s reader choice above: the
            # adopted session's harness is unrecorded, so this assumes the
            # only harness in production today.
            log_offset, live = _progress_usage(
                log_path, log_offset, seen_usage, "claude-stream-json"
            )
            if live is not None:
                await db.write(lambda c, u=live: store.session_progress(c, session_id, u))
        except Exception:
            logger.exception("usage progress tick failed for adopted session %s", session_id)
    await _exit_from_file(
        db,
        session_id,
        log_path,
        result_path,
        _adopted_status(result_path, is_agent=_is_agent_hook(db, row)),
    )
    if row["hook_point"] == ESCALATION_HOOK and run_dirs is not None:
        await _resume_adopted_escalation(
            db,
            run_dirs,
            work_item_id=row["work_item_id"],
            session_id=session_id,
            policy=policy,
            launch_factory=launch_factory,
            bd_cwd=bd_cwd,
            on_approve=on_approve,
        )


#: How long a capped adopted session's group gets after SIGTERM before
#: SIGKILL, as `run_task`'s own `group_kill_grace`.
_KILL_GRACE_S = 10.0


async def _stop_at_cap(db, row, pid: int, hit) -> None:
    """Kill an adopted session whose time cap ran out -- its process group,
    as `run_task` does; `_guarded_adopt`'s `finally` tears down a sandbox's
    container -- and stop its item for a human under the cap's reason."""
    await _kill_group(pid, _KILL_GRACE_S)
    session_id = row["id"]

    def _capped(c):
        store.session_exited(c, session_id, "capped_out")
        events.append(
            c,
            row["work_item_id"],
            caps.REACHED,
            hit.payload(node_id=row["node_id"], task=row["hook_point"], session_id=session_id),
        )
        store.mark_needs_human(c, row["work_item_id"], row["node_id"], hit.reason)

    await db.write(_capped)


async def _guarded_adopt(
    db,
    session_id: str,
    work_item_id: str,
    node_id: str,
    pid: int,
    *,
    run_dirs=None,
    policy: _policy.Policy | None = None,
    launch_factory: Callable[[str], LaunchContext] | None = None,
    bd_cwd: str | None = None,
    on_approve: OnApprove | None = None,
) -> None:
    """`_adopt`, but a crash marks the work item needs_human instead of
    vanishing. `reattach` hands its tasks to the caller directly rather than
    through `api.deps.spawn`, which is what every other executor task goes
    through for exactly this reason -- so an adopted session skipped it, and
    a crash here (in `_adopt`'s own DB read, or in `_exit_from_file` at the
    end -- the per-tick progress loop already guards itself) had nothing
    watching it: no log, no needs_human, and the task itself leaks in
    `app.state.tasks` forever, since nothing keyed by session_id ever pops it
    (Kraft-mjwz)."""
    try:
        await _adopt(
            db,
            session_id,
            pid,
            run_dirs=run_dirs,
            policy=policy,
            launch_factory=launch_factory,
            bd_cwd=bd_cwd,
            on_approve=on_approve,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("adopted session %s crashed", session_id)
        reason = f"reattach crashed: {exc!r}"
        try:
            await db.write(lambda c: store.mark_needs_human(c, work_item_id, node_id, reason))
        except Exception:  # noqa: BLE001
            logger.exception("could not mark %s needs_human after adopt crash", work_item_id)
    finally:
        # However this adopted session ends -- its client pid exiting, a
        # crash, or a shutdown cancelling this task -- its container is
        # parented by the docker daemon and outlives all three unless
        # something says so (Kraft-rki). `teardown` is a no-op for a session
        # that was never sandboxed, which is why this does not first work out
        # whether this one was: deciding that per call path is how the third
        # path got missed twice.
        await asyncio.shield(_sandbox.teardown(session_id))


async def reattach(
    db,
    run_dirs,
    *,
    policy: _policy.Policy | None = None,
    launch_factory: Callable[[str], LaunchContext] | None = None,
    bd_cwd: str | None = None,
    on_approve: OnApprove | None = None,
    #: Overridable so a test exercising the retry *logic* doesn't also pay
    #: the real delay -- every dead-pid test seeds its session moments
    #: before calling this, so it is always "young" by construction and
    #: would otherwise eat this sleep for behavior it isn't testing.
    grace_retry_delay_s: float = _REATTACH_GRACE_RETRY_DELAY_S,
) -> tuple[ReattachSummary, dict[str, asyncio.Task]]:
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE status IN ('pending', 'running')"
        ).fetchall()
    )
    summary = ReattachSummary(scanned=len(rows))
    adopted_tasks: dict[str, asyncio.Task] = {}
    pending_escalation_resumes: list[dict] = []

    # Only an adopted session still has something running to tear down later
    # (`_guarded_adopt`'s `finally`). For every other row the session is over
    # as far as Kraft is concerned, while its container -- daemon-parented, so
    # it survived the restart that orphaned this row exactly as the client pid
    # could have -- is not (Kraft-rki).
    #
    # Resolved for every row up front, once each, so the grace retry below can
    # be a single sleep for the whole scan.
    adopt = {
        r["id"]: (
            r["status"] != "pending"
            and r["pid"] is not None
            and _identity_ok(r["pid"], r["pid_start_time"])
        )
        for r in rows
    }
    # Kraft-s7c04.51: a session started 13 seconds before this exact scan was
    # declared unconfirmed on the very first look -- "cannot have lost its PID
    # identity" that young. Those get one retry, in case `_identity_ok`'s
    # answer had not settled yet.
    #
    # ONE sleep for the whole scan, not one per row. `rows` is every
    # `pending`/`running` session, and a restart moments after several started
    # -- `kraft admin stop` immediately followed by a start, the common case
    # here -- puts all of them inside the age window at once. Sleeping per row
    # would serialise that into `grace_retry_delay_s` x N of added startup, in
    # the one scenario where those sessions are most likely genuinely dead.
    # Waiting once and re-checking the whole set costs a single delay whatever
    # N is, and every row still gets at least the settling time it would have
    # had.
    #
    # Resolved before the loop so it lands before that row's
    # `_sandbox.teardown`: flipping a row back to adopted after tearing its
    # sandbox down would tear down a container for a session about to be
    # treated as still-live.
    grace = [
        r
        for r in rows
        if not adopt[r["id"]]
        and r["status"] != "pending"
        and r["pid"] is not None
        and _is_young(r["started_at"])
    ]
    if grace:
        await asyncio.sleep(grace_retry_delay_s)
        for r in grace:
            adopt[r["id"]] = _identity_ok(r["pid"], r["pid_start_time"])

    for r in rows:
        sid = r["id"]
        adopting = adopt[sid]
        if not adopting:
            await _sandbox.teardown(sid)
        if r["status"] == "pending":
            await db.write(lambda c, sid=sid: store.session_unknown(c, sid))
            await db.write(
                lambda c, r=r: store.mark_needs_human(
                    c,
                    r["work_item_id"],
                    r["node_id"],
                    "reattach: session pending, spawn unconfirmed",
                )
            )
            summary.unknown.append(sid)
            continue

        pid = r["pid"]
        if adopting:
            await db.write(lambda c, sid=sid: store.session_reattached(c, sid))
            adopted_tasks[sid] = asyncio.create_task(
                _guarded_adopt(
                    db,
                    sid,
                    r["work_item_id"],
                    r["node_id"],
                    pid,
                    run_dirs=run_dirs,
                    policy=policy,
                    launch_factory=launch_factory,
                    bd_cwd=bd_cwd,
                    on_approve=on_approve,
                )
            )
            summary.adopted.append(sid)
            continue

        # A dead process whose identity cannot be confirmed still left its
        # evidence: the exit sidecar, for a subprocess, as well as the result
        # file -- so a green run Kraft merely cannot confirm pages nobody
        # (Kraft-s7c04.38).
        status = _evidenced_status(r["result_path"], is_agent=_is_agent_hook(db, r))
        if status is not None:
            await db.write(lambda c, sid=sid: store.session_reattached(c, sid))
            await _exit_from_file(db, sid, Path(r["log_path"]), Path(r["result_path"]), status)
            summary.resolved_from_file.append(sid)
            if r["hook_point"] == ESCALATION_HOOK and run_dirs is not None:
                # Deferred: starting this task now would let its own
                # claim_for_run race the closing active-items scan below,
                # so it lands in summary.resumed_work_items and startup.py
                # spawns a second, concurrent walk for the same item.
                pending_escalation_resumes.append(
                    {"work_item_id": r["work_item_id"], "node_id": r["node_id"], "session_id": sid}
                )
        else:
            reason = _identity_mismatch_reason(pid, r["pid_start_time"])
            await db.write(
                lambda c, sid=sid, reason=reason: store.session_unknown(c, sid, reason=reason)
            )
            await db.write(
                lambda c, r=r, reason=reason: store.mark_needs_human(
                    c,
                    r["work_item_id"],
                    r["node_id"],
                    f"reattach: running session, PID identity unconfirmed, no result ({reason})",
                )
            )
            summary.unknown.append(sid)

    active = db.read(
        lambda c: c.execute("SELECT id FROM work_items WHERE status = 'active'").fetchall()
    )
    summary.resumed_work_items = [row["id"] for row in active]

    for pending in pending_escalation_resumes:
        sid = pending["session_id"]
        adopted_tasks[sid] = asyncio.create_task(
            _guarded_resume_adopted_escalation(
                db,
                run_dirs,
                work_item_id=pending["work_item_id"],
                node_id=pending["node_id"],
                session_id=sid,
                policy=policy,
                launch_factory=launch_factory,
                bd_cwd=bd_cwd,
                on_approve=on_approve,
            )
        )

    return summary, adopted_tasks
