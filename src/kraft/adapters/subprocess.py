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
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from kraft import caps, events, logs, store
from kraft import harness as _harness
from kraft import usage as _usage
from kraft.paths import private
from kraft.worker import backends as _backends
from kraft.worker import ca as _ca
from kraft.worker import channel as _channel
from kraft.worker import inject as _inject
from kraft.worker import sandbox as _sandbox
from kraft.worker.egress import PhaseLists
from kraft.worker.env import worker_env
from kraft.worker.inject import InjectRule

if TYPE_CHECKING:
    from kraft.config import RepoEntry

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


def result_path_for(run_dirs, name: str) -> Path:
    """Where a session's $KRAFT_RESULT_PATH lives -- the one formula every
    caller that needs to predict it ahead of dispatch must use, rather than
    reimplementing it. `name` is the session id, or an escalation thread's
    `escalate.thread_files` name."""
    return run_dirs.results / f"{name}.json"


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


async def _sync_refs(
    db, backend: _backends.SandboxBackend, refs, work_item_id: str, session_id: str
) -> None:
    """Publish the item branch out of a sandboxed session, and record why not
    when it could not: the branch then stays where it was, and a person
    decides."""
    await _record_unsynced(
        db,
        refs,
        work_item_id,
        session_id,
        await asyncio.to_thread(backend.code_out, refs, session_id),
    )


async def _record_unsynced(
    db, refs, work_item_id: str, session_id: str, problem: str | None
) -> None:
    if problem:
        logger.warning("session %s: %s", session_id, problem)
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                "sandbox_branch_not_synced",
                {"session_id": session_id, "branch": refs.branch, "reason": problem},
            )
        )


#: What a sandboxed session its memory limit killed is recorded as.
SANDBOX_OOM_KILLED = "sandbox_oom_killed"
#: How the log line `record_oom_kill` appends starts: the stop's cause.
OOM_LINE = "kraft: a process in the sandbox was killed"


def oom_cause(oom: _backends.OomKill) -> str:
    """How a stop names the kill, honest about one the runtime never confirmed."""
    if oom.confirmed:
        return f"by its memory limit ({oom.memory})"
    return f"under its {oom.memory} memory limit (the runtime did not confirm it was the limit)"


async def record_oom_kill(
    db,
    work_item_id: str,
    session_id: str,
    log_path: Path,
    oom: _backends.OomKill,
    status: str | None,
) -> str | None:
    """The status a session its sandbox's memory limit killed ends with, and
    the record of it: `sandbox_oom_killed` and a `kraft:` line naming the
    limit, the stop's cause. A session that failed (or left no word at all)
    ends `config_error`, not `failed`: the same limit kills a retry the same
    way, so no fix loop runs and the item stops for a person. One that still
    reported a result of its own (something it ran was killed, and it got
    past it) keeps it; the event is recorded all the same.

    An unconfirmed kill (exit 137 under the limit, no word from the runtime)
    counts only when Kraft did not stop the session itself. Every stop of
    Kraft's own that can reach here marks the row `paused` before it signals
    (a pause, an escalation turn a retry stops); a time cap and a cancel
    leave their callers before any of them asks. Such a session keeps
    `status`, and nothing is recorded."""
    if not oom.confirmed and await db.write(
        lambda c: store.session_status(c, session_id) == "paused"
    ):
        return status
    with open(log_path, "a") as fh:
        fh.write(
            f"\n{OOM_LINE} {oom_cause(oom)}: raise the sandbox's resources.memory, or make "
            "the task need less\n"
        )
    payload = {"session_id": session_id, "memory": oom.memory, "confirmed": oom.confirmed}
    await db.write(lambda c: events.append(c, work_item_id, SANDBOX_OOM_KILLED, payload))
    return "config_error" if status in (None, "failed", "unknown") else status


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


def _rate_limit_rejection(log_path: Path, *, reader: str | None) -> _usage.RateLimitInfo | None:
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


async def open_egress(
    db,
    backend,
    session_id: str,
    work_item_id: str,
    sandbox: dict,
    lists: PhaseLists,
    credentials: tuple[InjectRule, ...] = (),
    repo: str | None = None,
) -> dict:
    """Open a session's egress under `sandbox['network']`: its channel, with
    the `credentials` its proxy injects, the lists (and those credentials,
    never their values) recorded on its row for a reattach (no `db` for a
    setup command, which has no row and is never reattached), and the
    backend's route to the channel. The proxy environment the launch adds;
    `SandboxNotReady` whenever any of it is missing -- never a launch with
    open egress."""
    channels = _channel.current()
    if channels is None:
        raise _sandbox.SandboxNotReady(
            "a sandbox with `network:` reaches out only through the Kraft server's egress "
            "channel, and this process has none"
        )
    transport = await backend.egress_transport()
    sock_path = await channels.open(
        session_id, work_item_id, lists, transport=transport, credentials=credentials
    )
    if db is not None:
        # Which transport, for a reattach to re-register it the same way.
        egress = {**lists.to_json(), "transport": transport}
        if credentials:
            egress["credentials"] = [r.to_json() for r in credentials]
            # Whose `worker_env` the values came from, for a reattach to
            # read them again; absent, the daemon's own (no repo entry).
            if repo is not None:
                egress["repo"] = repo
        await db.write(lambda c: store.set_session_egress(c, session_id, egress))
    proxy_env = await backend.open_session(session_id, sandbox, sock_path)
    if not proxy_env:
        raise _sandbox.SandboxNotReady(
            f"the {backend.kind} sandbox gave its session no egress route for `network:`"
        )
    return proxy_env


async def close_egress(backend, session_id: str) -> None:
    """Undo `open_egress`: the backend's route, then the channel."""
    try:
        await backend.close_session(session_id)
    finally:
        if (channels := _channel.current()) is not None:
            await channels.close(session_id)


async def _item_identity(db, work_item_id: str) -> dict[str, str]:
    """The item root's identity (`builtins.item_identity`, J3). The row is read
    here and `git config` runs off the event loop: `db` stays on this thread."""
    row = db.read(
        lambda c: c.execute("SELECT repo FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        return {}
    return await asyncio.to_thread(_sandbox.git_identity, Path(row["repo"]))


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
    repo_entry: RepoEntry | None = None,
    #: The tightest time cap over this launch (`caps.at_launch`): past its
    #: deadline the process group, and a sandbox's container, is killed and
    #: the session exits `capped_out` with `caps.REACHED` naming the scope.
    time_cap: caps.Deadline | None = None,
    #: The name the result file (and its sidecars) goes under, when not the
    #: session's own: an escalation thread's, shared by its turns
    #: (`escalate.thread_files`, Kraft-s7c04.54). The log stays per session.
    files: str | None = None,
    #: `{"harness": <harness id>, "model": <resolved model or None>}`, which a
    #: `rate_limit_hit` carries so a later launch can skip that pair until its
    #: reset (`executor.fallback.known_limited`). Only `run_agent_task` sets it.
    rate_limit_key: dict | None = None,
    #: The `harnesses.yaml` harness this session runs on, recorded on its row
    #: for `worker.reattach` (Kraft-9elw1). Only `run_agent_task` sets it.
    harness: str | None = None,
    #: Host paths a sandboxed launch also mounts read-only at the same path (a
    #: rules file its CLI must read and never rewrite). Ignored unsandboxed.
    ro_paths: tuple[str, ...] = (),
    #: The harness's own hosts (`Harness.network_requires`), allowed on top of
    #: a `network:` sandbox's runtime list. Only `run_agent_task` sets it.
    network_requires: tuple[str, ...] = (),
    #: The credentials this launch's harness declares (`Harness.credentials`),
    #: which fill a sandbox entry naming just `env` (`harness.manage`). Only
    #: `run_agent_task` sets it; the sandbox's own `credentials` are managed
    #: for every launch regardless.
    declared: tuple = (),
) -> str:
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = result_path_for(run_dirs, files or session_id)
    # The task's own exit code, written by the launch wrapper on the way out.
    # A sidecar, never `result_path` itself -- see `_resolve_exit_file`.
    exit_path = result_path.with_suffix(".exit")
    # docker's own sidecar, written only once it created the container.
    cidfile = result_path.with_suffix(".cid")
    # Captured before any sandbox wrap reassigns `cmd` below (Kraft-s7c04.35) --
    # this must read as what actually ran, never a docker-wrapped invocation.
    command_ran = shlex.join(cmd)
    backend = _backends.for_sandbox(sandbox) if sandbox else None
    network = backend is not None and bool(sandbox.get("network"))

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
            harness=harness,
            sandbox=backend.kind if backend is not None else None,
        )
    )

    full_env = worker_env(repo_entry, {**(env or {}), "KRAFT_RESULT_PATH": str(result_path)})
    # A managed credential's value goes to the egress proxy, read from the
    # worker env like any other; the container holds its sentinel, and the
    # docker client does not hold the value either (spec §6). Read off the
    # sandbox here, for every caller: a subprocess task or a test scope runs
    # code the worker wrote, as an agent does.
    credentials = (
        _harness.manage(_harness.sandbox_credentials(sandbox), declared)
        if backend is not None
        else ()
    )
    sentinels = {c.env: c.sentinel for c in credentials}
    rules = _inject.rules(credentials, full_env)
    full_env = {k: v for k, v in full_env.items() if k not in sentinels}
    refs = None
    if backend is not None:
        # Before anything below writes one of them.
        if problem := await backend.owner_refusal(run_dirs, Path(cwd), work_item_id, result_path):
            log_path.write_text(f"kraft: {problem}\n")
            await db.write(lambda c: store.session_exited(c, session_id, "config_error"))
            return "config_error"
        # The container writes its result here, and `result_path` is mounted
        # read-write into it by name -- docker can only bind-mount a file
        # that already exists, so create it now (empty) rather than letting
        # docker invent a directory at that path.
        result_path.touch(exist_ok=True)
        # docker refuses a cidfile that already exists.
        cidfile.unlink(missing_ok=True)
        # `db.write` for the reads: this session's own row, and a co-task's
        # just written, must both be seen.
        others = await db.write(
            lambda c: [s for s in store.live_session_ids(c, work_item_id) if s != session_id]
        )
        branch = await db.write(lambda c: store.branch_of(c, work_item_id))
        try:
            refs = await asyncio.to_thread(
                backend.code_in,
                run_dirs.base,
                Path(cwd),
                branch,
                session_id=session_id,
                live=others,
                work_item_id=work_item_id,
            )
        except (OSError, RuntimeError) as exc:
            log_path.write_text(f"kraft: could not prepare the sandbox's ref store: {exc}\n")
            await db.write(lambda c: store.session_exited(c, session_id, "config_error"))
            return "config_error"
        if problem := await backend.probe(
            sandbox,
            cmd[0],
            # A managed name never crosses, not even into the probe.
            {k: v for k, v in repo_entry.env.items() if k not in sentinels}
            if repo_entry is not None
            else None,
        ):
            log_path.write_text(f"kraft: {problem}\n")
            await db.write(lambda c: store.session_exited(c, session_id, "config_error"))
            return "config_error"
        try:
            # The Kraft CA joins the bundle only when a credential is managed.
            kraft_ca = _ca.ensure_ca(run_dirs)[0] if credentials else None
            ca_bundle = await backend.prepare(sandbox, kraft_ca=kraft_ca)
        except _sandbox.SandboxNotReady as exc:
            log_path.write_text(f"kraft: {exc}\n")
            await db.write(lambda c: store.session_exited(c, session_id, "config_error"))
            return "config_error"
        proxy_env: dict[str, str] = {}
        if network:
            # Before the worker exists: it has no route but this one.
            lists = PhaseLists.of(sandbox["network"], "runtime", network_requires)
            try:
                proxy_env = await open_egress(
                    db,
                    backend,
                    session_id,
                    work_item_id,
                    sandbox,
                    lists,
                    rules,
                    repo_entry.path if repo_entry is not None else None,
                )
            except BaseException as exc:
                # Whatever part of the route did open, closed.
                await close_egress(backend, session_id)
                if not isinstance(exc, _sandbox.SandboxNotReady):
                    raise
                log_path.write_text(f"kraft: {exc}\n")
                await db.write(lambda c: store.session_exited(c, session_id, "config_error"))
                return "config_error"
    # From here until the process is launched, whatever ends this call --
    # an early return, an exception, a cancel -- closes a route it opened;
    # once launched, the poll loop's `finally` below owns that.
    launched = False
    try:
        if backend is not None:
            if refs is not None:
                await _record_unsynced(db, refs, work_item_id, session_id, refs.carried)
            home = backend.home(run_dirs, work_item_id)
            home.mkdir(parents=True, exist_ok=True)
            # A repository's `env:` crosses by name, its value already in
            # `full_env` (`worker_env`), never on the argv `ps` shows. It
            # still outranks the git identity, and `env=` and the relay
            # still outrank it.
            repo_env = repo_entry.env if repo_entry is not None else {}
            identity = await _item_identity(db, work_item_id)
            cmd = backend.wrap(
                cmd,
                cwd,
                sandbox,
                run_dirs.results,
                env={
                    **{k: v for k, v in identity.items() if k not in repo_env},
                    **(env or {}),
                    # Last: the relay is the only route, whatever else says.
                    **proxy_env,
                },
                session_id=session_id,
                result_path=result_path,
                cidfile=cidfile,
                refs=refs,
                home=home,
                passthrough=(
                    *(repo_entry.env_passthrough if repo_entry is not None else ()),
                    *repo_env,
                ),
                ro_paths=ro_paths,
                ca_bundle=ca_bundle,
                sentinels=sentinels,
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
        if backend is None:
            # The root's commit identity, under everything the repository and
            # caller set. A workspace member is a worktree of its connected
            # repository, where Kraft writes no identity (Kraft-ju36l), so a
            # commit there gets the root's from here, as the sandbox does --
            # the item's, never `cwd`'s, which is the member on a fanned-out run.
            identity = await _item_identity(db, work_item_id)
            full_env = {**identity, **full_env}
        # A sandbox's client may run somewhere of the backend's own, not in the
        # worktree: nothing it leaves behind lands where a worker commits.
        client_cwd = backend.client_cwd(session_id) if backend is not None else None
        log = open(log_path, "w", opener=private)
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
                    cwd=str(client_cwd or cwd),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=full_env,
                    stdin=subprocess.DEVNULL,
                )
                # `start_new_session=True` makes this the group's pgid for life,
                # captured now rather than re-derived after the leader exits.
                pgid = proc.pid
                launched = True
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
                if backend is not None:
                    await backend.close(session_id)
                return "config_error"
        finally:
            log.close()  # the child holds its own dup'd fd
    finally:
        if network and not launched:
            await close_egress(backend, session_id)
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
    oom: _backends.OomKill | None = None

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
            # Nested so neither a collect nor a close that raises skips what
            # follows it: the session's commits are published whatever became
            # of its result file.
            try:
                if backend is not None:
                    # Everything below reads the result file: bring it home
                    # before the sandbox holding it goes -- and whether its
                    # memory limit killed the session, which goes with it.
                    try:
                        await backend.collect(session_id, result_path)
                        oom = await backend.oom_killed(session_id)
                    finally:
                        # The worker, then its route out (spec §4).
                        try:
                            await backend.close(session_id)
                        finally:
                            if network:
                                await close_egress(backend, session_id)
            finally:
                if refs is not None:
                    await _sync_refs(db, backend, refs, work_item_id, session_id)
        if paused:
            # The cancelled path never reaches `session_exited`, so Task 6's
            # hook inside it never fires. Idempotent with that hook for the
            # pause that was not cancelled: both write the same numbers and
            # both are guarded on the row still being paused.
            await db.write(
                lambda c: store.record_pause_usage(
                    c, session_id, _usage.read(log_path, result_path, reader), reader
                )
            )
    if capped:
        hit = time_cap.hit

        def _capped(c):
            store.session_exited(
                c,
                session_id,
                "capped_out",
                None,
                _usage.read(log_path, result_path, reader),
                reader=reader,
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
    if backend is not None and status == "failed" and backend.launch_failed(cidfile, returncode):
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
                c,
                work_item_id,
                "rate_limit_hit",
                {**asdict(rl), **(rate_limit_key or {}), "node_id": node_id},
            )
        )
    elif post_resolve is not None:
        status = post_resolve(status, log_path, returncode)
    # A human pausing the item SIGTERMs this child, so a non-zero rc here may mean
    # "stopped on purpose" rather than "failed". The row is the authority.
    if await db.write(lambda c: store.session_status(c, session_id)) == "paused":
        return "paused"
    if oom is not None:
        status = await record_oom_kill(db, work_item_id, session_id, log_path, oom, status)
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
            reader=reader,
        )
    )
    return status
