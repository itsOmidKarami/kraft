from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import psutil

from kraft import logs, store
from kraft import usage as _usage


def _resolve_result_file(path: Path) -> str | None:
    """Status from a result file alone, or None if it's missing/empty."""
    if not path.exists():
        return None
    try:
        raw = path.read_text().strip()
    except OSError:
        return "failed"
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "failed"
    status = data.get("status") if isinstance(data, dict) else None
    return status if status in ("done", "failed") else "failed"


def read_summary_ref(path: Path) -> str | None:
    """`session_summary_ref` from a result file, or None (03 §3)."""
    try:
        data = json.loads(path.read_text())
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ref = data.get("session_summary_ref")
    return ref if isinstance(ref, str) and ref else None


def _watch_log(
    log_path: Path, times_path: Path, stop: threading.Event, poll_s: float = 0.2
) -> None:
    """Record when each new line appeared in `log_path`, into a JSONL sidecar.

    Counts lines exactly the way `logs.jsonl` reads them back — one
    `str.splitlines()` over the whole file — so a line ending in `\r` can never
    shift the numbering between writer and reader. Best-effort throughout: an I/O
    error here must never take the session down with it.
    """
    seen = 0
    try:
        with open(times_path, "w") as times:
            while True:
                done = stop.is_set()
                try:
                    total = len(log_path.read_text(errors="replace").splitlines())
                except OSError:
                    total = seen
                if total > seen:
                    now = datetime.now(UTC).isoformat()
                    for n in range(seen, total):
                        times.write(json.dumps({"n": n, "t": now}) + "\n")
                    times.flush()
                    seen = total
                if done:
                    return
                stop.wait(poll_s)
    except OSError:
        pass


def _resolve(result_path: Path, returncode: int) -> str:
    file_status = _resolve_result_file(result_path)
    if file_status is not None:
        return file_status
    return "done" if returncode == 0 else "failed"


async def run_task(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    cmd: list[str],
    cwd: str | Path,
    env: dict | None = None,
    post_resolve: Callable[[str, Path, int], str] | None = None,
    poll_s: float = 0.05,
    round: int = 0,
    pricing: _usage.Pricing | None = None,
) -> str:
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = run_dirs.results / f"{session_id}.json"

    await db.write(
        lambda c: store.create_session(
            c,
            id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            log_path=str(log_path),
            result_path=str(result_path),
            round=round,
        )
    )

    full_env = {**os.environ, **(env or {}), "KRAFT_RESULT_PATH": str(result_path)}
    log = open(log_path, "w")
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=full_env,
            )
        except FileNotFoundError:
            await db.write(lambda c: store.session_exited(c, session_id, "failed"))
            return "failed"
    finally:
        log.close()  # the child holds its own dup'd fd
    # The child owns the log fd, so it keeps writing across a Kraft restart — which
    # is the whole premise of reattach. A watcher thread therefore *tails the file*
    # rather than draining a pipe: piping through the parent would give the
    # surviving agent a SIGPIPE the moment Kraft exited. It records when each new
    # line appeared into a sidecar, so the log modal has a time column (design 6c)
    # without the plain-text log — read by the copy button and by the envelope
    # parser — gaining a prefix.
    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_log,
        args=(log_path, logs.times_path(log_path), stop),
        daemon=True,
    )
    watcher.start()

    try:
        pid_start_time = psutil.Process(proc.pid).create_time()
    except psutil.Error:
        pid_start_time = None
    await db.write(lambda c: store.session_running(c, session_id, proc.pid, pid_start_time))

    # Poll instead of `await asyncio.to_thread(proc.wait)`: a blocked thread is
    # uncancellable, so on SIGTERM the executor task's cancel() could not
    # interrupt it and uvicorn fell back to a hard exit (same fix as
    # reattach._adopt).
    while proc.poll() is None:
        await asyncio.sleep(poll_s)
    # the child is gone; let the watcher record whatever it wrote on the way out
    stop.set()
    watcher.join(timeout=2)
    returncode = proc.returncode
    status = _resolve(result_path, returncode)
    if post_resolve is not None:
        status = post_resolve(status, log_path, returncode)
    # A human pausing the item SIGTERMs this child, so a non-zero rc here may mean
    # "stopped on purpose" rather than "failed". The row is the authority.
    if await db.write(lambda c: store.session_status(c, session_id)) == "paused":
        return "paused"
    summary_ref = read_summary_ref(result_path)
    seen = _usage.read(log_path, result_path)
    if seen is not None:
        seen = seen.with_cost(pricing or _usage.DEFAULT_PRICING)
    await db.write(lambda c: store.session_exited(c, session_id, status, summary_ref, seen))
    return status
