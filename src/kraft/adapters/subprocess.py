from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import psutil

from kraft import caps, events, logs, store
from kraft import usage as _usage
from kraft.worker import sandbox as _sandbox
from kraft.worker.env import worker_env

logger = logging.getLogger(__name__)

_AGENT_STATUSES = ("done", "failed", "done_with_concerns", "needs_context")

#: Test seam. `run_task`'s flush wait is the one sleep a test needs to observe
#: without paying, because the whole point of it is ordering against
#: `_kill_group`, not duration.
_flush_sleep = asyncio.sleep


#: The event naming the background jobs a worker's turn left running.
JOBS_ABANDONED = "background_jobs_abandoned"

#: How much of one job's command a reason quotes.
_JOB_NAME_MAX = 200


async def fail_abandoned_jobs(db, session_id: str, log_path: Path, status: str, reader) -> str:
    """`status`, or `failed` when the agent's turn ended with a background job
    still running (Kraft-xvugd): the session ends with its turn, so nothing
    would ever read that job's result. The prose in `agent._CTX` alone did not
    hold (Kraft-nxqft); this is the check behind it. The jobs are named in the
    log's last line and a `JOBS_ABANDONED` event rather than left to a generic
    missing-result failure.

    A `needs_context` stop keeps its status -- it is a person's stop already,
    and failing it would lose the question -- but the jobs are still named.
    The one door both `run_task` and `reattach` exit an agent session through.
    """
    if reader is None or status not in _AGENT_STATUSES:
        return status
    jobs = _usage.READERS[reader].unfinished_jobs(log_path)
    if not jobs:
        return status
    named = "; ".join(j if len(j) <= _JOB_NAME_MAX else j[: _JOB_NAME_MAX - 1] + "…" for j in jobs)
    reason = (
        f"the turn ended with {len(jobs)} background job(s) still running: {named}. "
        "The session ends with its turn, so nothing reads a job's result: run it in "
        "the foreground."
    )
    with open(log_path, "a") as fh:
        fh.write(f"\nkraft: failed: {reason}\n")

    def _record(c):
        row = c.execute(
            "SELECT work_item_id, node_id FROM worker_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        payload = {"session_id": session_id, "node_id": row["node_id"], "jobs": jobs}
        events.append(c, row["work_item_id"], JOBS_ABANDONED, {**payload, "reason": reason})

    await db.write(_record)
    return status if status == "needs_context" else "failed"


def result_path_for(run_dirs, session_id: str) -> Path:
    """Where a session's $KRAFT_RESULT_PATH lives -- the one formula every
    caller that needs to predict it ahead of dispatch (`escalate.dispatch`'s
    resumed-turn note) must use, rather than reimplementing it."""
    return run_dirs.results / f"{session_id}.json"


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


#: What a stop may suggest a person do next (Kraft-s7c04.27): each is one
#: existing verb -- `kraft item skip`, `kraft item retry`, `kraft item abandon`.
SUGGESTED_ACTIONS = ("skip", "retry", "abandon")


def read_suggested_action(path: Path) -> dict | None:
    """`suggested_action` from a result file, as `{"action", "reason"}`, or
    None when it is missing or malformed. An agent that concluded no repair
    can help -- the node should be skipped, retried later, or the item
    abandoned -- says so here rather than only in prose, so the stop can offer
    it as one command. A shape Kraft cannot act on is dropped, not guessed at.
    """
    try:
        data = json.loads(path.read_text())
    except OSError, ValueError:
        return None
    value = data.get("suggested_action") if isinstance(data, dict) else None
    if not isinstance(value, dict) or value.get("action") not in SUGGESTED_ACTIONS:
        return None
    reason = value.get("reason")
    return {"action": value["action"], "reason": reason if isinstance(reason, str) else ""}


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


def _progress_usage(
    log_path: Path, offset: int, seen: dict[str, _usage.Usage], reader: str | None = None
) -> tuple[int, _usage.Usage | None]:
    """One tick: new lines since `offset` folded into `seen`, the new offset, and
    the running total (or None if nothing has been seen at all).

    Shared by `run_task`'s own poll loop and `reattach._adopt`'s: an adopted
    session's child survives a Kraft restart (`start_new_session=True`) and
    keeps writing its log, so without this loop running there too, tokens_in/out
    freeze at whatever `run_task` last wrote before the restart and never move
    again until the process finally exits -- Kraft-jgs6, the sequel to
    Kraft-41f7 (same frozen-tokens symptom, a different loop missing it).

    `reader=None` means the harness declared no log-based usage schema
    (`source: result_file`) -- there is nothing here to parse, so live
    progress simply never updates for it; the result file still lands at
    session end.
    """
    chunk, offset = _read_new(log_path, offset)
    if reader is None:
        return offset, None
    return offset, _usage.READERS[reader].stream(logs.split_lines(chunk), seen)


def _resolve(result_path: Path, returncode: int) -> str:
    file_status = _resolve_result_file(result_path)
    if file_status is not None:
        return file_status
    return "done" if returncode == 0 else "failed"


def _docker_launch_failed(cidfile: Path) -> bool:
    """True if `docker run` itself never created the container -- the daemon
    is down, or an image could not be pulled -- so the sandboxed command
    never ran: an infra problem no agent edit can fix (Kraft-nc9gm).

    Read off `--cidfile`, not the log (Kraft-6ltwh): the log holds the
    sandboxed command's own output too, so a task that prints docker's
    wording is not docker failing. Nor the exit code: daemon-down is plain 1
    (docker-cli 29.7.2, probed 2026-09-22), the same code a failing command
    returns. docker writes the id once the container exists and removes an
    unwritten cidfile when create fails (probed the same day: daemon
    unreachable exits 1, a refused create exits 125, and neither leaves the
    file; a create that succeeds writes it before start). A container docker
    created but could not start is not caught here; it fails as the task's
    own, as it did before this check existed."""
    try:
        return not cidfile.read_text().strip()
    except OSError:
        return True


def _resolve_exit_file(path: Path) -> str | None:
    """Status from a task's own recorded exit code, or None if absent/garbage.

    `reattach` adopts a process Kraft never forked (only pid + create_time,
    `reattach.py:38-49`), and a process you did not spawn cannot be reaped --
    its exit status is unrecoverable after the fact. So the task records it
    on the way out and adoption reads it here (Kraft-7itv follow-up).

    Deliberately NOT `result_path`: "exited clean with no result file at all"
    is a load-bearing signal (Kraft-avpe) telling a plain subprocess hook,
    which has no result-file contract, apart from an agent hook that broke
    one. Writing a result file for every subprocess task would erase it.
    """
    try:
        raw = path.read_text().strip()
    except OSError, UnicodeDecodeError:
        return None
    if not raw.lstrip("-").isdigit():
        return None
    return "done" if int(raw) == 0 else "failed"


def _missing_executable(cmd0: str, cwd: Path, env: dict[str, str]) -> bool:
    """True when `cmd[0]` names nothing runnable -- the check `Popen` used to do.

    `_wrap_with_exit_file` puts `/bin/sh` at argv[0], which always exists, so
    `Popen` stopped raising `FileNotFoundError` for a missing binary and `sh`
    merely exited 127 -- turning a `config_error` into a `failed` and opening
    a fix cycle no agent can win by editing source (Kraft-579). Resolved here
    instead, the same two ways `execvp` resolves it: a name containing a
    separator is a path relative to the child's cwd, anything else is a PATH
    lookup against the child's own env.
    """
    if os.sep in cmd0:
        candidate = Path(cwd) / cmd0
        return not (candidate.is_file() and os.access(candidate, os.X_OK))
    return shutil.which(cmd0, path=env.get("PATH")) is None


def _wrap_with_exit_file(cmd: list[str], exit_path: Path) -> list[str]:
    """`cmd`, wrapped so it writes its own exit code to `exit_path`.

    `"$@"` replays argv byte-for-byte -- no re-quoting, so an argument with a
    space or a glob survives. `$0` carries the exit path. The child's real
    code is propagated, so every existing caller of `proc.returncode` is
    unaffected.

    Not `exec "$@"`: exec would replace the shell and skip the write on the
    success path, which is the whole point. Keeping `sh` alive costs nothing
    that matters -- `start_new_session=True` makes `sh` the group leader,
    `sh -c` runs with job control off so the real child stays in the same
    pgid, and `_kill_group`'s `os.killpg` still reaches both.
    """
    return [
        "/bin/sh",
        "-c",
        'RC=0; "$@" || RC=$?; printf %s "$RC" > "$0"; exit $RC',
        str(exit_path),
        *cmd,
    ]


def _rate_limit_rejection(log_path: Path, *, reader: str | None) -> dict | None:
    """A rate-limit rejection, if this harness's log can express one.

    `reader is None` means the harness never declared `rate_limit_signal`, so
    there is no schema to match and the honest answer is "not detectable" --
    not "none found". Parsing a foreign log against claude's schema anyway is
    how a skipped check starts reading like a passed one.
    """
    if reader is None:
        return None
    return _usage.READERS[reader].rate_limit(log_path)


async def _kill_group(pgid: int, grace: float, reap: Callable[[], object] | None = None) -> None:
    """SIGTERM `pgid`, then SIGKILL whatever is still there after `grace`
    seconds. `serve.py`'s `_terminate` is the model this copies.

    The session's own child is already gone by the time this runs — this is
    for what it backgrounded into the same group and never waited on itself
    (a fixture server, `just dev`), which `start_new_session=True` put in the
    same process group as the child but this coroutine never touched
    (Kraft-1nye). `os.killpg` raises `ProcessLookupError` the moment the group
    has no members left, so the common case (nothing backgrounded) returns
    almost immediately rather than paying `grace`.

    `reap` is the leader's own `Popen.poll`, called before every liveness
    check. On a pause or skip this runs from `run_task`'s `finally` while the
    leader is still alive, so the SIGTERM above kills it but nothing reaps it
    -- the poll loop that normally would was cancelled out from under it. On
    Linux an unreaped zombie still counts as a group member, so `killpg(pgid,
    0)` kept succeeding and every cancel paid the full `grace` (10s by
    default) before falling through to SIGKILL. macOS raises
    `PermissionError` for a zombie-only group instead, which is why this
    passed locally and failed pause/resume, SIGTERM shutdown and the e2e
    pause flow in CI (Kraft-rki).
    """
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError, PermissionError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        if reap is not None:
            reap()
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError, PermissionError:
            return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        pass


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
    #: How long a lingering group member (something the session backgrounded
    #: and never waited on) gets after SIGTERM before SIGKILL. serve.py's own
    #: `_terminate` uses the same 10s.
    group_kill_grace: float = 10.0,
    #: How long to let an interrupted agent flush its result envelope before
    #: `_kill_group`'s SIGTERM lands on it (Kraft-s7c04.18). Paid only on a
    #: pause. ~1s observed against claude 2.1.273; 2s is the margin.
    flush_grace: float = 2.0,
    round: int = 0,
    head_sha: str | None = None,
    thread: int = 1,
    sandbox: dict | None = None,
    #: Names a log schema in `usage.READERS`, or `None` when the harness
    #: declared no log-based reading (`source: result_file`, no
    #: `rate_limit_signal`). Resolved by the caller from the harness, never
    #: guessed here -- this adapter stays harness-agnostic.
    reader: str | None = None,
    #: Every agent hook is told by `_CTX` to write $KRAFT_RESULT_PATH,
    #: regardless of whether it also declares `artifact:` -- this holds it to
    #: that half of the contract on its own (Kraft-avpe). Only run_agent_task
    #: sets this; a subprocess-kind hook a template binds directly has no such
    #: contract.
    require_result_file: bool = False,
    repo_entry: dict | None = None,
    #: The tightest time cap over this launch (`caps.at_launch`): past its
    #: deadline the process group, and a sandbox's container, is killed and
    #: the session exits `capped_out` with `caps.REACHED` naming the scope.
    time_cap: caps.Deadline | None = None,
) -> str:
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = result_path_for(run_dirs, session_id)
    # The task's own exit code, written by the launch wrapper on the way out.
    # A sidecar, never `result_path` itself -- see `_resolve_exit_file`.
    exit_path = result_path.with_suffix(".exit")
    # docker's own sidecar, written only once it created the container.
    cidfile = result_path.with_suffix(".cid")
    # Captured before any sandbox wrap reassigns `cmd` below (Kraft-s7c04.35) --
    # this must read as what actually ran, never a docker-wrapped invocation.
    command_ran = shlex.join(cmd)

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
            thread=thread,
            command=command_ran,
        )
    )

    full_env = worker_env(repo_entry, {**(env or {}), "KRAFT_RESULT_PATH": str(result_path)})
    if sandbox:
        # The container writes its result here, and `result_path` is mounted
        # read-write into it by name -- docker can only bind-mount a file
        # that already exists, so create it now (empty) rather than letting
        # docker invent a directory at that path.
        result_path.touch(exist_ok=True)
        # docker refuses a cidfile that already exists.
        cidfile.unlink(missing_ok=True)
        cmd = _sandbox.docker_argv(
            cmd,
            cwd,
            sandbox,
            run_dirs.results,
            env={**((repo_entry or {}).get("env") or {}), **(env or {})},
            name=_sandbox.container_name(session_id),
            result_path=result_path,
            cidfile=cidfile,
        )
    # Kraft-qx1q: `create_session` above inserts this row 'pending' with no
    # pid yet. `pause_work_item`, `chain.skip_node`, and
    # `stop_escalation_session` can all mark a row stopped in the window
    # between that insert and here -- SIGTERM has nothing to signal without a
    # pid, so without this check Popen launches an untracked agent into a
    # worktree the caller believes is idle. One read, right before the one
    # place that can actually refuse the launch, covers every caller that
    # marks a row stopped: no log file is opened and no process is started.
    current_status = await db.write(lambda c: store.session_status(c, session_id))
    if current_status != "pending":
        return current_status
    log = open(log_path, "w")
    try:
        try:
            # A missing cwd still raises from `Popen` below and is unaffected;
            # only the missing-binary half has to move up here, because the
            # wrapper puts `/bin/sh` at argv[0] and that always exists.
            # Checked inside the `try` so both halves land in the one handler.
            if Path(cwd).is_dir() and _missing_executable(cmd[0], cwd, full_env):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), cmd[0])
            # stdin is DEVNULL for every task, not just agents: a CLI in
            # stream-json mode waits on stdin before it starts (measured: a 3s
            # "no stdin data received" stall), and no hook has any business
            # reading the server's own stdin.
            proc = subprocess.Popen(
                _wrap_with_exit_file(cmd, exit_path),
                cwd=str(cwd),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=full_env,
                stdin=subprocess.DEVNULL,
            )
            # `start_new_session=True` makes this the group's pgid for life,
            # captured now rather than re-derived after the leader exits.
            pgid = proc.pid
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
        capped = False
        while proc.poll() is None:
            await asyncio.sleep(poll_s)
            if time_cap is not None and caps.monotonic() >= time_cap.at:
                # Left through the `finally` below, which kills the group and
                # tears down a sandbox's container.
                capped = True
                break
            if time.monotonic() < next_progress:
                continue
            next_progress = time.monotonic() + progress_s
            # A bad line or a write hiccup here must not kill this task: the child
            # keeps running and writing its own log fd regardless, so losing this
            # loop silently freezes tokens_in/out for the rest of the session with
            # nothing to show it happened (Kraft-41f7).
            try:
                log_offset, live = _progress_usage(log_path, log_offset, seen_usage, reader)
                if live is not None:
                    await db.write(lambda c, u=live: store.session_progress(c, session_id, u))
            except Exception:
                logger.exception("usage progress tick failed for session %s", session_id)
    finally:
        # A pause or skip cancels this task from inside the poll loop above
        # (deps.cancel -> CancelledError), which used to skip straight past
        # the stop/join pair that only ever sat after the loop -- leaking the
        # watcher thread, and its two open fds, for the rest of the server's
        # life. `finally` runs on that path too, so the watcher stops however
        # this function leaves the loop.
        stop.set()
        watcher.join(timeout=2)
        # A paused row means a human interrupted this session and `_terminate`
        # SIGINT'd it rather than SIGTERM'ing it, so the agent CLI would flush
        # the result envelope that carries its cost (Kraft-s7c04.18). The wait
        # has to be here and not in the pause route: `_kill_group` below
        # SIGTERMs the whole group the moment this loop leaves, which on a
        # cancel is milliseconds after the SIGINT, and a sleep in the route
        # would run concurrently and protect nothing. The group leader is the
        # `/bin/sh` wrapper, not the agent, so `proc.poll()` reaping the
        # wrapper says nothing about whether the agent has finished writing.
        # `db.write` for a SELECT is deliberate, not a slip: the pause is
        # written by the API route concurrently with this coroutine, and
        # `db.write` queues onto the serialized writer so it observes that
        # write. `db.read` goes to the separate `_reader` connection, whose
        # snapshot may predate the pause -- which would skip the flush wait
        # and lose exactly the cost this fix exists to keep.
        paused = await db.write(lambda c: store.session_status(c, session_id)) == "paused"
        if paused and flush_grace:
            await _flush_sleep(flush_grace)
        # Same cancellation as above: without this inside `finally`, a pause
        # or skip propagates CancelledError past the kill calls entirely, and
        # a sandboxed session's container -- parented by the docker daemon,
        # not `pgid`'s process group -- keeps running against the bind-mounted
        # worktree with nothing left to stop it (Kraft-rki). The container
        # kill gets its own nested `finally` so a `_kill_group` failure (a
        # dead pgid can still raise -- PermissionError, not just
        # ProcessLookupError, once its last member is gone) can't shadow it.
        try:
            await _kill_group(pgid, group_kill_grace, reap=proc.poll)
        finally:
            if sandbox:
                await _sandbox.teardown(session_id)
        if paused:
            # The cancelled path never reaches `session_exited`, so Task 6's
            # hook inside it never fires. Idempotent with that hook for the
            # pause that was not cancelled: both write the same numbers and
            # both are guarded on the row still being paused.
            await db.write(
                lambda c: store.record_pause_usage(
                    c, session_id, _usage.read(log_path, result_path, reader)
                )
            )
    if capped:
        hit = time_cap.hit

        def _capped(c):
            store.session_exited(
                c, session_id, "capped_out", None, _usage.read(log_path, result_path, reader)
            )
            events.append(
                c,
                work_item_id,
                caps.REACHED,
                hit.payload(node_id=node_id, task=hook_point, session_id=session_id),
            )

        with open(log_path, "a") as fh:
            fh.write(f"\nkraft: stopped: {hit.reason}\n")
        await db.write(_capped)
        return caps.TIME_CAPPED
    returncode = proc.returncode
    status = _resolve(result_path, returncode)
    # `docker run` itself failing to launch (daemon down, image pull failed)
    # is Kraft's own launcher not reaching the sandboxed command at all --
    # the same "config problem, not a task failure" class as the
    # FileNotFoundError branch above, one step later and inside the sandboxed
    # path only (Kraft-nc9gm). Checked before every other status adjustment
    # below so it can't be shadowed by require_result_file or post_resolve.
    if sandbox and status == "failed" and _docker_launch_failed(cidfile):
        status = "config_error"
    # A session that exits clean with no result file at all never reached the
    # end of its own contract -- `_resolve`'s exit-code fallback cannot tell
    # "no contract" (a plain subprocess hook) from "broke the contract" (an
    # agent hook), so the caller who knows which one this is says so
    # explicitly (Kraft-avpe). Before the rate-limit check below: a rejected
    # launch also has no result file "by construction", and that branch's own
    # unconditional overwrite is what protects it, not an exclusion here.
    if require_result_file and status == "done" and _resolve_result_file(result_path) is None:
        status = "failed"
    if require_result_file:
        status = await fail_abandoned_jobs(db, session_id, log_path, status, reader)
    rate_limit = _rate_limit_rejection(log_path, reader=reader)
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
    seen = _usage.read(log_path, result_path, reader)
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
