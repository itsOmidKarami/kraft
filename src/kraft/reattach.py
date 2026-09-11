from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from kraft import store
from kraft import usage as _usage
from kraft.adapters.subprocess import _progress_usage, _resolve_result_file, read_result_fields

logger = logging.getLogger(__name__)


@dataclass
class ReattachSummary:
    scanned: int = 0
    adopted: list[str] = field(default_factory=list)
    resolved_from_file: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    resumed_work_items: list[str] = field(default_factory=list)


_resolve_file = _resolve_result_file


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
    seen = _usage.read(log_path, result_path)
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


async def _adopt(
    db, session_id: str, pid: int, poll_s: float = 0.1, progress_s: float = 5.0
) -> None:
    row = db.read(
        lambda c: c.execute(
            "SELECT log_path, result_path FROM worker_sessions WHERE id = ?", (session_id,)
        ).fetchone()
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
    while _pid_alive(pid):
        await asyncio.sleep(poll_s)
        if time.monotonic() < next_progress:
            continue
        next_progress = time.monotonic() + progress_s
        try:
            log_offset, live = _progress_usage(log_path, log_offset, seen_usage)
            if live is not None:
                await db.write(lambda c, u=live: store.session_progress(c, session_id, u))
        except Exception:
            logger.exception("usage progress tick failed for adopted session %s", session_id)
    await _exit_from_file(
        db,
        session_id,
        log_path,
        result_path,
        _resolve_file(result_path) or "failed",
    )


async def _guarded_adopt(db, session_id: str, work_item_id: str, node_id: str, pid: int) -> None:
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
        await _adopt(db, session_id, pid)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("adopted session %s crashed", session_id)
        reason = f"reattach crashed: {exc!r}"
        try:
            await db.write(lambda c: store.mark_needs_human(c, work_item_id, node_id, reason))
        except Exception:  # noqa: BLE001
            logger.exception("could not mark %s needs_human after adopt crash", work_item_id)


async def reattach(db, run_dirs, registry) -> tuple[ReattachSummary, dict[str, asyncio.Task]]:
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE status IN ('pending', 'running')"
        ).fetchall()
    )
    summary = ReattachSummary(scanned=len(rows))
    adopted_tasks: dict[str, asyncio.Task] = {}

    for r in rows:
        sid = r["id"]
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
        if pid is not None and _identity_ok(pid, r["pid_start_time"]):
            await db.write(lambda c, sid=sid: store.session_reattached(c, sid))
            adopted_tasks[sid] = asyncio.create_task(
                _guarded_adopt(db, sid, r["work_item_id"], r["node_id"], pid)
            )
            summary.adopted.append(sid)
            continue

        status = _resolve_file(Path(r["result_path"]))
        if status is not None:
            await db.write(lambda c, sid=sid: store.session_reattached(c, sid))
            await _exit_from_file(db, sid, Path(r["log_path"]), Path(r["result_path"]), status)
            summary.resolved_from_file.append(sid)
        else:
            await db.write(lambda c, sid=sid: store.session_unknown(c, sid))
            await db.write(
                lambda c, r=r: store.mark_needs_human(
                    c,
                    r["work_item_id"],
                    r["node_id"],
                    "reattach: running session, PID identity unconfirmed, no result",
                )
            )
            summary.unknown.append(sid)

    active = db.read(
        lambda c: c.execute("SELECT id FROM work_items WHERE status = 'active'").fetchall()
    )
    summary.resumed_work_items = [row["id"] for row in active]
    return summary, adopted_tasks
