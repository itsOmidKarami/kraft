from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from kraft import store
from kraft import usage as _usage
from kraft.adapters.subprocess import _resolve_result_file, read_result_fields

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


async def _adopt(db, session_id: str, pid: int, poll_s: float = 0.1) -> None:
    while _pid_alive(pid):
        await asyncio.sleep(poll_s)
    row = db.read(
        lambda c: c.execute(
            "SELECT log_path, result_path FROM worker_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    )
    result_path = Path(row["result_path"])
    await _exit_from_file(
        db,
        session_id,
        Path(row["log_path"]),
        result_path,
        _resolve_file(result_path) or "failed",
    )


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
            adopted_tasks[sid] = asyncio.create_task(_adopt(db, sid, pid))
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
