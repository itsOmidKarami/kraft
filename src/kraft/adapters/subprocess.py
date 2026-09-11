from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import psutil

from kraft import events, logs, store
from kraft import usage as _usage

logger = logging.getLogger(__name__)

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


def read_verdict(path: Path) -> str | None:
    """`verdict` from a gate-review result file, or None (Kraft-zr3s).

    Deliberately absent from `read_result_fields`. That function exists because
    a field which must reach the DB has two independent readers -- the
    in-process exit and `reattach._exit_from_file` -- and one of them forgetting
    it is Kraft-k3d. A verdict never reaches the DB: `gate_review` reads it once,
    in-process, and acts on it immediately. The asymmetry is the crash story
    rather than a gap in it -- on the reattach path there is no gate_review frame
    to act on a verdict, so none is read, so the gate stays pending and a human
    decides.
    """
    return _read_str_field(path, "verdict")


def read_result_fields(path: Path) -> dict[str, str | None]:
    """`session_summary_ref`/`concerns`/`question` together.

    The one place the result-file contract's field list is spelled out.
    `run_task` below and `reattach._exit_from_file` are independent readers of
    the same file, on the two paths a session can exit by (in process, or
    adopted after a restart); a field added to one and not the other would
    reach the DB on one path and silently drop on the other (Kraft-k3d).
    """
    return {
        "summary_ref": read_summary_ref(path),
        "concerns": read_concerns(path),
        "question": read_question(path),
    }


def _watch_log(
    log_path: Path, times_path: Path, stop: threading.Event, poll_s: float = 0.2
) -> None:
    """Record when each new line appeared in `log_path`, into a JSONL sidecar.

    Reads forward from one open handle rather than re-reading the file: under
    `--output-format stream-json` the log grows to megabytes while the worker
    works, and re-reading it five times a second would cost more than the
    worker. Only `b"\\n"` is counted, so a line is stamped when it completes and
    a partial trailing line is carried to the next poll -- the numbering
    `logs.split_lines` defines, which `logs.jsonl` reads back by. Best-effort
    throughout: an I/O error here must never take the session down with it.
    """
    seen = 0
    pending = b""
    try:
        with open(times_path, "w") as times, open(log_path, "rb") as log:
            while True:
                done = stop.is_set()
                chunk = log.read()  # b"" at EOF; the next read picks up new bytes
                if chunk:
                    pending += chunk
                    complete = pending.count(b"\n")
                    if complete:
                        now = datetime.now(UTC).isoformat()
                        for n in range(seen, seen + complete):
                            times.write(json.dumps({"n": n, "t": now}) + "\n")
                        times.flush()
                        seen += complete
                        pending = pending[pending.rfind(b"\n") + 1 :]
                if done:
                    return
                stop.wait(poll_s)
    except OSError:
        pass


def _read_new(path: Path, offset: int) -> tuple[str, int]:
    """Complete lines written since `offset`, and the offset to resume from.

    A partial trailing line is left where it is rather than decoded half-formed
    -- the log is a stream and the writer is mid-flush. Best-effort: an
    unreadable log is "nothing new", not a failed session.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return "", offset
    cut = data.rfind(b"\n")
    if cut < 0:
        return "", offset
    return data[: cut + 1].decode("utf-8", "replace"), offset + cut + 1


def _resolve(result_path: Path, returncode: int) -> str:
    file_status = _resolve_result_file(result_path)
    if file_status is not None:
        return file_status
    return "done" if returncode == 0 else "failed"


def _rate_limit_rejection(log_path: Path) -> dict | None:
    """The rejected `rate_limit_info` from a stream-json log, or None.

    The CLI emits a `rate_limit_event` line on most turns, nearly all of them
    `status: "allowed"` -- an `overageStatus` of "rejected" on an otherwise
    allowed turn means only that overage spend was refused, not that the turn
    itself was blocked. Only a top-level `status: "rejected"` means the launch
    was refused. Scanned across every line, not just the last: unlike the
    result envelope, this event is not guaranteed to be the final line.
    Best-effort like `agent._envelope_is_error`: a log Kraft cannot read yet is
    "no rejection seen", not a crash.
    """
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "rate_limit_event":
            continue
        info = obj.get("rate_limit_info")
        if not isinstance(info, dict) or info.get("status") != "rejected":
            continue
        resets_at = info.get("resetsAt")
        if not isinstance(resets_at, int | float):
            continue
        return {
            "rate_limit_type": info.get("rateLimitType"),
            "resets_at": resets_at,
            "resets_at_iso": datetime.fromtimestamp(resets_at, UTC).isoformat(),
        }
    return None


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
    #: How often the poll loop stops to fold new log lines into live usage. The
    #: process poll runs 20x a second; parsing the log that often would cost
    #: more than the worker does. A test that cannot wait passes its own.
    progress_s: float = 5.0,
    round: int = 0,
    head_sha: str | None = None,
    #: Every agent hook is told by `_CTX` to write $KRAFT_RESULT_PATH,
    #: regardless of whether it also declares `artifact:` -- this holds it to
    #: that half of the contract on its own (Kraft-avpe). Only run_agent_task
    #: sets this; a subprocess-kind hook a template binds directly has no such
    #: contract.
    require_result_file: bool = False,
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
            head_sha=head_sha,
        )
    )

    full_env = {**os.environ, **(env or {}), "KRAFT_RESULT_PATH": str(result_path)}
    log = open(log_path, "w")
    try:
        try:
            # stdin is DEVNULL for every task, not just agents: a CLI in
            # stream-json mode waits on stdin before it starts (measured: a 3s
            # "no stdin data received" stall), and no hook has any business
            # reading the server's own stdin.
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=full_env,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            # Popen raises this for a missing cwd as well as a missing
            # executable, and the two are fixed in different places. Say which:
            # the alternative is what this branch used to leave behind — a
            # zero-byte log, a 5ms "failed", and no way to tell them apart.
            # Safe to write: in this path the child never took the fd.
            missing = "working directory" if not Path(cwd).is_dir() else f"command {cmd[0]!r}"
            log.write(f"could not start {' '.join(cmd)} in {cwd}: no such {missing} ({exc})\n")
            # Not "failed": a task that never launched is a configuration
            # problem, and reporting it as a task failure opens a fix cycle no
            # agent can win by editing source (Kraft-579). The caller
            # short-circuits this straight to needs_human.
            await db.write(lambda c: store.session_exited(c, session_id, "config_error"))
            return "config_error"
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
    #
    # The same loop, with a second job: read the log forward from our own byte
    # offset every `progress_s` and write the running usage onto the session
    # row. Before this the row held NULL tokens and a NULL model for the whole
    # run, so `usage_rollup` reported 0 for exactly the node a human was
    # watching (Kraft-54dk, Kraft-2r8s).
    seen_usage: dict[str, _usage.Usage] = {}
    log_offset = 0
    next_progress = time.monotonic() + progress_s
    while proc.poll() is None:
        await asyncio.sleep(poll_s)
        if time.monotonic() < next_progress:
            continue
        next_progress = time.monotonic() + progress_s
        # A bad line or a write hiccup here must not kill this task: the child
        # keeps running and writing its own log fd regardless, so losing this
        # loop silently freezes tokens_in/out for the rest of the session with
        # nothing to show it happened (Kraft-41f7).
        try:
            chunk, log_offset = _read_new(log_path, log_offset)
            live = _usage.from_stream(logs.split_lines(chunk), seen_usage)
            if live is not None:
                await db.write(lambda c, u=live: store.session_progress(c, session_id, u))
        except Exception:
            logger.exception("usage progress tick failed for session %s", session_id)
    # the child is gone; let the watcher record whatever it wrote on the way out
    stop.set()
    watcher.join(timeout=2)
    returncode = proc.returncode
    status = _resolve(result_path, returncode)
    # A session that exits clean with no result file at all never reached the
    # end of its own contract -- `_resolve`'s exit-code fallback cannot tell
    # "no contract" (a plain subprocess hook) from "broke the contract" (an
    # agent hook), so the caller who knows which one this is says so
    # explicitly (Kraft-avpe). Before the rate-limit check below: a rejected
    # launch also has no result file "by construction", and that branch's own
    # unconditional overwrite is what protects it, not an exclusion here.
    if require_result_file and status == "done" and _resolve_result_file(result_path) is None:
        status = "failed"
    rate_limit = _rate_limit_rejection(log_path)
    if rate_limit is not None:
        # A rejected launch produced no artifact by construction, so this
        # skips `post_resolve` (agent.py's artifact-presence check) entirely
        # rather than let it downgrade an already-correct status to "failed".
        status = "rate_limited"
        await db.write(
            lambda c, rl=rate_limit: events.append(
                c, work_item_id, "rate_limit_hit", {**rl, "node_id": node_id}
            )
        )
    elif post_resolve is not None:
        status = post_resolve(status, log_path, returncode)
    # A human pausing the item SIGTERMs this child, so a non-zero rc here may mean
    # "stopped on purpose" rather than "failed". The row is the authority.
    if await db.write(lambda c: store.session_status(c, session_id)) == "paused":
        return "paused"
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
    return status
