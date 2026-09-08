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

_AGENT_STATUSES = ("done", "failed", "done_with_concerns", "needs_context")


def _resolve_result_file(path: Path) -> str | None:
    """Status from a result file alone, or None if it's missing/empty."""
    if not path.exists():
        return None
    try:
        raw = path.read_text().strip()
    except OSError, UnicodeDecodeError:
        # UnicodeDecodeError is a ValueError, not an OSError: a plugin that died
        # mid-write leaves bytes that are not valid UTF-8, and reading them must
        # fail the session rather than escape as a raw traceback.
        return "failed"
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "failed"
    status = data.get("status") if isinstance(data, dict) else None
    # An unrecognized status still resolves to failed: an agent inventing a status
    # is a broken agent, and guessing at its intent is worse than failing the session.
    return status if status in _AGENT_STATUSES else "failed"


def _read_str_field(path: Path, key: str) -> str | None:
    """A string `key` from a result file's JSON, or None if it's missing/unreadable.

    Catches OSError, ValueError rather than json.JSONDecodeError alone: a
    non-UTF-8 result file raises UnicodeDecodeError from read_text(), which is a
    ValueError, not caught by `except OSError`. That exact clause has been wrong
    three times in this codebase already.
    """
    try:
        data = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def read_summary_ref(path: Path) -> str | None:
    """`session_summary_ref` from a result file, or None (03 §3)."""
    return _read_str_field(path, "session_summary_ref")


def read_concerns(path: Path) -> str | None:
    """`concerns` from a `done_with_concerns` result file, or None."""
    return _read_str_field(path, "concerns")


def read_question(path: Path) -> str | None:
    """`question` from a `needs_context` result file, or None."""
    return _read_str_field(path, "question")


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
        except FileNotFoundError as exc:
            # Popen raises this for a missing cwd as well as a missing
            # executable, and the two are fixed in different places. Say which:
            # the alternative is what this branch used to leave behind — a
            # zero-byte log, a 5ms "failed", and no way to tell them apart.
            # Safe to write: in this path the child never took the fd.
            missing = "working directory" if not Path(cwd).is_dir() else f"command {cmd[0]!r}"
            log.write(f"could not start {' '.join(cmd)} in {cwd}: no such {missing} ({exc})\n")
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
    # Read unconditionally, same as summary_ref: each returns None when the
    # result file doesn't carry that field, so a "failed" or "done" exit here
    # costs nothing extra.
    concerns = read_concerns(result_path)
    question = read_question(result_path)
    seen = _usage.read(log_path, result_path)
    await db.write(
        lambda c: store.session_exited(
            c, session_id, status, summary_ref, seen, concerns=concerns, question=question
        )
    )
    return status
