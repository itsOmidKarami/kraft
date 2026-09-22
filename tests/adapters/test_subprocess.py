"""`kraft.adapters.subprocess`: reading a session's result and exit files, and
`run_task` -- one real child process per session, its row, log, env,
sandbox and process-group teardown."""

import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest
from support.harness import fails_once, fake_docker_bin

from kraft import events, logs, store
from kraft.adapters import subprocess as sp

_FAKE_CLAUDE = Path(__file__).resolve().parents[2] / "fixtures" / "fake-claude.sh"
DOCKER = {"kind": "docker", "image": "kraft-worker:py"}


def _writes_result(payload: dict) -> list[str]:
    """A command that writes `payload` to $KRAFT_RESULT_PATH and exits 0."""
    return ["sh", "-c", f'printf %s {shlex.quote(json.dumps(payload))} > "$KRAFT_RESULT_PATH"']


# --- the result and exit files -------------------------------------------------------


def test_result_path_for_matches_the_convention_run_task_uses(run_dirs):
    assert sp.result_path_for(run_dirs, "abc123") == run_dirs.results / "abc123.json"


@pytest.mark.parametrize(
    "content, rc, expected",
    [
        (None, 0, "done"),
        (None, 3, "failed"),
        # The result file wins over the exit code, either way round.
        ('{"status": "failed"}', 0, "failed"),
        ('{"status": "done"}', 1, "done"),
        ('{"status": "done_with_concerns", "concerns": "untested path"}', 0, "done_with_concerns"),
        ('{"status": "needs_context", "question": "which branch?"}', 0, "needs_context"),
        # Empty is absent: fall through to the exit code.
        ("", 0, "done"),
        # Chunk C: a non-empty file that is not a dict with a known status is
        # failed, whatever the exit code.
        ("not json", 0, "failed"),
        ('{"status": "made_up_status"}', 0, "failed"),
        ("42", 0, "failed"),
        ("[]", 0, "failed"),
    ],
    ids=[
        "absent-rc0",
        "absent-rc3",
        "failed-over-rc0",
        "done-over-rc1",
        "done-with-concerns",
        "needs-context",
        "empty-is-absent",
        "not-json",
        "unknown-status",
        "a-number",
        "a-list",
    ],
)
def test_resolve_reads_the_result_file_over_the_exit_code(tmp_path, content, rc, expected):
    path = tmp_path / "r.json"
    if content is not None:
        path.write_text(content)
    assert sp._resolve(path, rc) == expected


def test_resolve_result_file_survives_non_utf8_bytes(tmp_path):
    """A plugin that died mid-write leaves bytes that are not valid UTF-8.
    UnicodeDecodeError is a ValueError, so `except OSError` alone misses it and
    the session's own status read takes the process down."""
    p = tmp_path / "r.json"
    p.write_bytes(b"\xff\xfe\x00binary")
    assert sp._resolve_result_file(p) == "failed"


def test_read_result_fields_is_the_one_place_the_field_list_lives(tmp_path):
    """`run_task` and `reattach._exit_from_file` are two independent readers of
    the same result file; a field spelled out in only one of them would reach
    the DB on one exit path and silently drop on the other after a restart
    (Kraft-k3d). Both call this instead of enumerating the fields."""
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "status": "done_with_concerns",
                "session_summary_ref": ".engineering/sessions/s1.md",
                "concerns": "the retry path is untested",
                "question": "which branch?",
            }
        )
    )
    assert sp.read_result_fields(path) == {
        "summary_ref": ".engineering/sessions/s1.md",
        "concerns": "the retry path is untested",
        "question": "which branch?",
    }
    assert sp.read_result_fields(tmp_path / "missing.json") == dict.fromkeys(
        ("summary_ref", "concerns", "question")
    )


@pytest.mark.parametrize(
    "payload, concerns, question",
    [
        ({"status": "done_with_concerns", "concerns": "untested"}, "untested", None),
        ({"status": "needs_context", "question": "which branch?"}, None, "which branch?"),
    ],
    ids=["concerns", "question"],
)
def test_read_concerns_and_read_question_read_their_own_field(
    tmp_path, payload, concerns, question
):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(payload))
    assert (sp.read_concerns(path), sp.read_question(path)) == (concerns, question)


@pytest.mark.parametrize(
    "raw",
    [None, b"not json", b"[1, 2, 3]", b'{"status": "done"}', b"\xff\xfe\x00\x01"],
    ids=["missing", "not-json", "not-a-mapping", "key-absent", "not-utf8"],
)
@pytest.mark.parametrize(
    "reader", [sp.read_summary_ref, sp.read_concerns, sp.read_question, sp.read_verdict]
)
def test_the_field_readers_tolerate_a_broken_file(tmp_path, reader, raw):
    """UnicodeDecodeError is a ValueError, so `except OSError` does not catch
    it. That exact clause has been wrong three times in this codebase."""
    path = tmp_path / "r.json"
    if raw is not None:
        path.write_bytes(raw)
    assert reader(path) is None


def test_read_verdict(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"status": "done", "verdict": "approve"}))
    assert sp.read_verdict(p) == "approve"


_REJECTED = (
    '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
    '"resetsAt":1788968400,"rateLimitType":"five_hour"}}\n'
)


@pytest.mark.parametrize(
    "log, reader, expected",
    [
        (
            '{"type":"assistant","message":{}}\n'
            + _REJECTED
            + '{"type":"result","is_error":true}\n',
            "claude-stream-json",
            {
                "rate_limit_type": "five_hour",
                "resets_at": 1788968400,
                "resets_at_iso": "2026-09-09T15:40:00+00:00",
            },
        ),
        # `overageStatus` can read "rejected" while the turn itself was
        # allowed: only a top-level `status: "rejected"` refused the launch.
        (
            '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
            '"overageStatus":"rejected","resetsAt":1788986400,"rateLimitType":"five_hour"}}\n',
            "claude-stream-json",
            None,
        ),
        ('{"type":"result","is_error":true}\n', "claude-stream-json", None),
        (None, "claude-stream-json", None),
        # A harness that never declared rate_limit_signal must not have its log
        # parsed against claude's schema -- a check that SKIPS reads as one
        # that PASSED.
        (_REJECTED, None, None),
    ],
    ids=[
        "rejected",
        "allowed-with-overage-rejected",
        "no-event",
        "unreadable-log",
        "no-capability",
    ],
)
def test_rate_limit_rejection(tmp_path, log, reader, expected):
    path = tmp_path / "s.log"
    if log is not None:
        path.write_text(log)
    assert sp._rate_limit_rejection(path, reader=reader) == expected


def test_exit_wrapper_preserves_argv_exactly(tmp_path):
    """The wrapper must replay argv byte-for-byte, spaces and quotes included."""
    out = tmp_path / "argv.txt"
    weird = ["a b", "c'd", 'e"f', "g*h", "--flag=i j"]
    cmd = [
        sys.executable,
        "-c",
        "import sys,pathlib; pathlib.Path(sys.argv[1]).write_text(repr(sys.argv[2:]))",
        str(out),
        *weird,
    ]
    subprocess.run(sp._wrap_with_exit_file(cmd, tmp_path / "s.exit"), check=True)
    assert eval(out.read_text()) == weird


@pytest.mark.parametrize("code, expected", [(0, "done"), (7, "failed")])
def test_exit_wrapper_records_the_code_and_propagates_it(tmp_path, code, expected):
    exit_path = tmp_path / "s.exit"
    wrapped = sp._wrap_with_exit_file(
        [sys.executable, "-c", f"raise SystemExit({code})"], exit_path
    )
    assert subprocess.run(wrapped).returncode == code, "the wrapper must not swallow the code"
    assert sp._resolve_exit_file(exit_path) == expected


@pytest.mark.parametrize("content", [None, "not a number"], ids=["missing", "garbage"])
def test_resolve_exit_file_missing_or_garbage_is_none(tmp_path, content):
    path = tmp_path / "s.exit"
    if content is not None:
        path.write_text(content)
    assert sp._resolve_exit_file(path) is None


# --- run_task ----------------------------------------------------------------------------


@pytest.fixture
async def run(database, run_dirs, tmp_path):
    """`await run(cmd, sid, **run_task_kwargs)` -> `(status, session row)`: one
    real `run_task` for work item `w1`, in `tmp_path`."""
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )

    async def go(cmd, sid="s1", **kw):
        kw = {"node_id": "verify", "hook_point": "on.test.run", "cwd": tmp_path, **kw}
        status = await sp.run_task(
            database, run_dirs, session_id=sid, work_item_id="w1", cmd=cmd, **kw
        )
        return status, _row(database, sid)

    return go


def _row(database, sid) -> dict | None:
    row = database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = ?", (sid,)).fetchone()
    )
    return dict(row) if row else None


async def _running(database, sid) -> dict:
    """The session row, once its child is running with a pid."""
    for _ in range(250):
        row = _row(database, sid)
        if row and row["status"] == "running" and row["pid"]:
            return row
        await asyncio.sleep(0.02)
    raise AssertionError(f"session {sid} never started")


async def _cancelled(task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_run_task_result_file_wins(run, database):
    status, row = await run(_writes_result({"status": "failed"}))

    assert (status, row["status"]) == ("failed", "failed")
    assert row["pid"] is not None and row["exited_at"] is not None
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
    assert types[-2:] == ["worker_session_started", "worker_session_exited"]


@pytest.mark.parametrize(
    "cmd, kw, expected",
    [
        (["true"], {}, "done"),
        (["false"], {}, "failed"),
        # Kraft-avpe: an agent that exits 0 with no result file never reached
        # the end of the contract every agent hook is told to follow. The
        # default is False: on.test.run and every other subprocess hook has no
        # result-file contract (the `true` row above).
        (["true"], {"require_result_file": True}, "failed"),
        (["true"], {"post_resolve": lambda base, log, rc: "failed"}, "failed"),
    ],
    ids=["exit-0", "exit-1", "required-result-file-missing", "post-resolve-downgrades"],
)
async def test_run_task_status_without_a_result_file(run, cmd, kw, expected):
    status, row = await run(cmd, **kw)
    assert (status, row["status"]) == (expected, expected)


async def test_run_task_records_the_command_it_ran(run):
    assert (await run(["true"]))[1]["command"] == "true"


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"status": "done_with_concerns", "concerns": "untested retry path"}, "concerns"),
        ({"status": "needs_context", "question": "which branch?"}, "question"),
    ],
    ids=["concerns", "question"],
)
async def test_run_task_stamps_concerns_and_question_onto_the_exit_event(
    run, database, payload, field
):
    """Read off the result file like `read_summary_ref`, and carried on
    `worker_session_exited` -- the only channel this text has, since there is
    no `concerns` column."""
    status, _ = await run(_writes_result(payload))

    assert status == payload["status"]
    ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
    assert ev["type"] == "worker_session_exited"
    assert {k: ev["payload"].get(k) for k in ("concerns", "question")} == {
        "concerns": None,
        "question": None,
        field: payload[field],
    }
    assert ({"concerns", "question"} - {field}).isdisjoint(ev["payload"])


@pytest.mark.parametrize(
    "result, ref",
    [
        (
            {"status": "done", "session_summary_ref": ".engineering/sessions/s1.md"},
            ".engineering/sessions/s1.md",
        ),
        (None, None),
    ],
    ids=["persisted", "absent-leaves-null"],
)
async def test_run_task_persists_session_summary_ref(run, result, ref):
    """03 §3: session_summary_ref from the result file lands on the session row."""
    _, row = await run(_writes_result(result) if result else ["true"])
    assert row["session_summary_ref"] == ref


@pytest.mark.parametrize(
    "cmd, cwd, phrases",
    [
        # Not "failed": a task that never launched is a configuration problem,
        # and a task failure opened a fix cycle no agent could win (Kraft-579).
        # The log has to name what was missing: a 5ms "failed" and a zero-byte
        # log cost an operator the whole diagnosis.
        (["kraft-nonexistent-binary-xyz"], None, ["kraft-nonexistent-binary-xyz", "command"]),
        # Popen raises FileNotFoundError for a missing cwd too, and the two are
        # fixed in different places: do not send anyone hunting for a binary.
        (["echo", "hi"], "no-such-worktree", ["working directory", "no-such-worktree"]),
    ],
    ids=["missing-binary", "missing-cwd"],
)
async def test_run_task_that_cannot_launch_is_a_config_error(
    run, run_dirs, tmp_path, cmd, cwd, phrases
):
    status, row = await run(cmd, "s-mb", **({"cwd": tmp_path / cwd} if cwd else {}))

    assert (status, row["status"]) == ("config_error", "config_error")
    log = (run_dirs.logs / "s-mb.log").read_text()
    for phrase in phrases:
        assert phrase in log


async def test_run_task_builds_the_child_env_instead_of_inheriting_os_environ(
    run, tmp_path, monkeypatch
):
    """Kraft-69atv: `{**os.environ, ...}` gave every worker whatever shell
    started the daemon. The child sees the repo's declared `env` and not a
    repo-scoped leak like VIRTUAL_ENV."""
    monkeypatch.setenv("VIRTUAL_ENV", "/some/other/repo/.venv")
    dumped = tmp_path / "child-env.txt"

    status, _ = await run(
        ["sh", "-c", f'env > "{dumped}"; printf \'{{"status":"done"}}\' > "$KRAFT_RESULT_PATH"'],
        node_id="implementation",
        hook_point="on.implementation.start",
        repo_entry={"env": {"MY_REPO": "1"}},
    )

    assert status == "done"
    child = dict(line.split("=", 1) for line in dumped.read_text().splitlines() if "=" in line)
    assert child["MY_REPO"] == "1"
    assert "VIRTUAL_ENV" not in child
    assert "KRAFT_RESULT_PATH" in child, "the call-site overlay still applies"


async def test_child_stdin_is_devnull(run, monkeypatch):
    """In stream-json mode the CLI waits on stdin before it starts, and the
    child inherits the server's. Measured: `< /dev/null` removes the 3s `no
    stdin data received` wait."""
    seen = {}
    real_popen = subprocess.Popen

    def spy(cmd, **kw):
        seen.update(kw)
        return real_popen(cmd, **kw)

    monkeypatch.setattr(sp.subprocess, "Popen", spy)

    await run(["sh", "-c", "exit 0"])

    assert seen["stdin"] == subprocess.DEVNULL


async def test_require_result_file_does_not_override_a_rate_limited_launch(run):
    """A rejected launch produces no result file by construction, so it must
    still resolve to rate_limited, not be caught as a contract violation. The
    child writes the event a real rejected launch leaves on stdout (which
    run_task redirects to the log) and exits 0."""
    status, _ = await run(
        ["sh", "-c", f"echo '{_REJECTED.strip()}'"],
        node_id="implementation",
        hook_point="on.implementation.start",
        require_result_file=True,
        reader="claude-stream-json",
    )

    assert status == "rate_limited"


async def test_run_task_leaves_the_exit_file_beside_the_result(run, run_dirs):
    """The wiring, not just the helpers: `run_task` passes the wrapped argv to
    `Popen`, which `reattach._adopted_status` depends on."""
    status, _ = await run([sys.executable, "-c", "raise SystemExit(4)"], "s-exit")

    assert status == "failed"
    # And no result file: the Kraft-avpe "no contract" signal survives.
    assert not (run_dirs.results / "s-exit.json").exists()
    assert (run_dirs.results / "s-exit.exit").read_text() == "4"


async def test_run_task_aborts_before_popen_if_the_row_was_stopped_first(
    run, run_dirs, monkeypatch
):
    """Kraft-qx1q: a row marked paused/stopped between `create_session` and
    Popen must never launch -- nothing SIGTERMs a session with no pid yet."""
    original = store.create_session

    def create_then_stop(conn, **kwargs):
        original(conn, **kwargs)
        conn.execute("UPDATE worker_sessions SET status = 'paused' WHERE id = ?", (kwargs["id"],))

    def _boom(*a, **kw):
        raise AssertionError("Popen must not run once the row is no longer pending")

    monkeypatch.setattr(store, "create_session", create_then_stop)
    monkeypatch.setattr(sp.subprocess, "Popen", _boom)

    assert (await run(["true"]))[0] == "paused"
    assert not (run_dirs.logs / "s1.log").exists(), "aborted before the log was even opened"


# --- the log, live -------------------------------------------------------------------


def _eventually(check, timeout=5.0):
    """True as soon as `check()` is true, False if it never is. The watcher runs
    on its own thread, so a fixed sleep is either flaky or slow."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def test_log_time_sidecar_holds_back_a_partial_line(tmp_path):
    """A line without its newline is not a line yet. The sidecar is keyed by
    line number, so stamping a number before the line is complete gives that
    timestamp to whatever text lands next."""
    log = tmp_path / "s.log"
    log.write_text("")
    times = logs.times_path(log)
    stop = threading.Event()
    watcher = threading.Thread(
        target=sp._watch_log, args=(log, times, stop), kwargs={"poll_s": 0.02}, daemon=True
    )
    watcher.start()
    try:
        with open(log, "a") as fh:
            fh.write("first\nsecond")  # 'second' has no newline yet
            fh.flush()
            assert _eventually(lambda: sorted(logs.read_times(times)) == [0])
            time.sleep(0.1)
            assert sorted(logs.read_times(times)) == [0], "a partial line was stamped"
            fh.write("\n")
            fh.flush()
            assert _eventually(lambda: sorted(logs.read_times(times)) == [0, 1])
    finally:
        stop.set()
        watcher.join(timeout=2)
    assert [row["n"] for row in logs.jsonl(log)] == [0, 1]
    assert all(row["t"] is not None for row in logs.jsonl(log))


async def test_log_is_readable_while_the_child_is_still_running(run, run_dirs):
    """Kraft-77z: the log was 0 bytes for the whole node, so `kraft view logs
    -f` streamed nothing. Driven through `fixtures/fake-claude.sh`, not a
    shell one-liner: a shell writes its first line immediately whatever the
    adapter does, so it would pass against the very bug this pins."""
    task = asyncio.create_task(
        run(
            [str(_FAKE_CLAUDE), "-p", "do it", "--append-system-prompt", "ctx"],
            env={"KRAFT_FAKE_CLAUDE": "noop", "KRAFT_FAKE_CLAUDE_STREAM_DELAY": "3"},
        )
    )
    early = []
    deadline = time.monotonic() + 10
    while not task.done() and time.monotonic() < deadline:
        early = list(logs.jsonl(run_dirs.logs / "s1.log"))
        if early:
            break
        await asyncio.sleep(0.05)

    assert (await task)[0] == "done"
    assert early, "the log was empty while the child was still running"
    assert "init" in early[0]["text"]


@pytest.mark.parametrize("seam", [None, "read", "write"], ids=["clean", "bad-read", "bad-write"])
async def test_running_session_row_carries_tokens_before_exit(
    run, database, monkeypatch, caplog, seam
):
    """Kraft-54dk / Kraft-2r8s: usage was harvested once, at exit, so a live
    node's row held NULL tokens and model for its whole run -- and
    `usage_rollup` reports 0 for exactly the node a human is watching.

    Kraft-0jpb, pinning Kraft-41f7: a tick that raises -- reading the log
    (`_progress_usage`) or writing the row (`store.session_progress`) -- is
    logged with its traceback, and the next tick still lands."""
    if seam == "read":
        monkeypatch.setattr(sp, "_progress_usage", fails_once(sp._progress_usage))
    elif seam == "write":
        monkeypatch.setattr(store, "session_progress", fails_once(store.session_progress))
    script = (
        'printf \'{"type":"system","subtype":"init","model":"claude-opus-5"}\\n\'; '
        'printf \'{"type":"assistant","request_id":"req_1","message":'
        '{"model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":200}}}\\n\'; '
        "sleep 3; "
        'printf \'{"type":"result","is_error":false,'
        '"usage":{"input_tokens":1000,"output_tokens":200}}\\n\''
    )
    task = asyncio.create_task(
        run(["sh", "-c", script], progress_s=0.2, reader="claude-stream-json")
    )
    live = None
    deadline = time.monotonic() + 10
    while not task.done() and time.monotonic() < deadline:
        row = _row(database, "s1")
        if row and row["status"] == "running" and row["tokens_in"]:
            live = {k: row[k] for k in ("status", "model", "tokens_in", "tokens_out")}
            break
        await asyncio.sleep(0.05)

    assert (await task)[0] == "done"
    assert live == {
        "status": "running",
        "model": "claude-opus-5",
        "tokens_in": 1000,
        "tokens_out": 200,
    }
    failed = [r for r in caplog.records if "usage progress tick failed" in r.getMessage()]
    assert [r.exc_info[1].args for r in failed] == ([] if seam is None else [("poison tick",)])


# --- the process group ---------------------------------------------------------------------


async def test_run_task_child_is_detached(run, database):
    """start_new_session=True: the child sits in its own process group."""
    task = asyncio.create_task(run(["sleep", "5"], "s-d"))
    row = await _running(database, "s-d")
    try:
        # psutil's create_time() is captured for a live child, not silently
        # None -- Chunk C's PID-reuse guard depends on it.
        assert row["pid_start_time"] is not None
        assert os.getpgid(row["pid"]) != os.getpgid(0)
    finally:
        await _cancelled(task)
        try:
            os.kill(row["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


def _gone(pid: int, within: float = 2.0) -> bool:
    """`_kill_group` returns the instant it sends SIGKILL, not once the kernel
    has reaped -- a loaded CI box can still see the process for a few ms."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize(
    "script, kw",
    [
        # A worker that backgrounds a child (a fixture server, `just dev`) must
        # not outlive the session (Kraft-1nye).
        ("sleep 30 & echo $! > {pidfile}", {}),
        # SIGTERM first, SIGKILL for what ignores it (serve.py:_terminate's
        # two stages). SIG_IGN on TERM is inherited across fork+exec.
        ("trap '' TERM; sleep 30 & echo $! > {pidfile}", {"group_kill_grace": 0.3}),
    ],
    ids=["sigterm", "sigkill-when-sigterm-is-ignored"],
)
async def test_run_task_kills_a_backgrounded_grandchild_when_the_session_ends(
    run, tmp_path, script, kw
):
    pidfile = tmp_path / "grandchild.pid"

    status, _ = await run(["bash", "-c", script.format(pidfile=pidfile)], **kw)

    assert status == "done"
    assert _gone(int(pidfile.read_text().strip()))


async def test_run_task_group_kill_does_not_delay_a_session_with_no_survivors(run):
    """The common case (no backgrounded children) must not pay the grace
    period -- `os.killpg` on an already-empty group raises immediately."""
    start = time.monotonic()
    assert (await run(["true"]))[0] == "done"
    assert time.monotonic() - start < 2.0


async def test_cancelling_run_task_reaps_the_leader_instead_of_paying_the_grace(run, database):
    """Kraft-rki: a pause or skip cancels `run_task` mid-poll, so its `finally`
    SIGTERMs a leader that nothing then reaps. Linux counts an unreaped zombie
    as a live group member, so `_kill_group` waited out the whole grace -- 10s
    per cancel in CI -- while macOS passed. Both halves are asserted so this
    fails for the real reason on either platform."""
    task = asyncio.create_task(run(["sleep", "30"], "s-cancel", group_kill_grace=5.0))
    pid = (await _running(database, "s-cancel"))["pid"]

    start = time.monotonic()
    await _cancelled(task)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"cancel paid the group-kill grace ({elapsed:.1f}s)"
    try:
        leftover = psutil.Process(pid).status()
    except psutil.NoSuchProcess:
        leftover = None
    assert leftover is None, f"leader {pid} left unreaped ({leftover})"


async def test_run_task_records_usage_when_a_pause_cancels_it(run, database, run_dirs):
    """Kraft-s7c04.18: `deps.cancel` raises CancelledError out of the poll loop,
    so nothing after the `finally` runs -- `session_exited` is never reached and
    neither is any usage read. This is the only place the paused path passes."""
    task = asyncio.create_task(
        run(["sleep", "5"], "s-paused", flush_grace=0, reader="claude-stream-json")
    )
    await _running(database, "s-paused")
    # What the agent flushed on its way out, written where run_task looks.
    (run_dirs.logs / "s-paused.log").write_text(
        json.dumps(
            {
                "type": "result",
                "total_cost_usd": 0.42,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "modelUsage": {"claude-opus-5": {"inputTokens": 1000, "outputTokens": 20}},
            }
        )
        + "\n"
    )
    await database.write(lambda c: store.pause_work_item(c, "w1", ["s-paused"]))
    await _cancelled(task)

    row = _row(database, "s-paused")
    assert (row["status"], row["tokens_in"]) == ("paused", 1000)
    assert row["cost_usd"] == pytest.approx(0.42)


async def test_run_task_waits_for_the_flush_only_when_the_row_is_paused(run, monkeypatch):
    """The wait exists because `_kill_group` SIGTERMs the group the instant the
    poll loop leaves, milliseconds after the route's SIGINT. It must not be
    paid on a normal exit."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(sp, "_flush_sleep", fake_sleep)

    assert (await run(["true"], flush_grace=2.0))[0] == "done"
    assert slept == []


# --- the docker sandbox ------------------------------------------------------------------


@pytest.fixture
def docker(tmp_path, monkeypatch) -> Path:
    """A fake `docker` first on PATH; `docker rm` names land in the returned log."""
    monkeypatch.setenv("PATH", f"{fake_docker_bin(tmp_path)}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_CALLED", str(tmp_path / "docker-was-called"))
    rm_log = tmp_path / "docker-rm-log"
    monkeypatch.setenv("FAKE_DOCKER_RM_LOG", str(rm_log))
    return rm_log


async def test_run_task_runs_sandboxed_through_docker_and_removes_the_container_by_name(
    run, docker, tmp_path
):
    """Not just "the command ran" (it would have, unwrapped, too): it ran
    *through docker*. And Kraft-rki: the pid Kraft tracks is `docker run`'s own
    client, not the container, which survives a SIGKILLed client because the
    docker daemon parents it -- so `run_task` reaches it a second way, by the
    name `docker_argv` gave it."""
    status, _ = await run(_writes_result({"status": "done"}), "s-sandbox", sandbox=DOCKER)

    assert status == "done"
    assert (tmp_path / "docker-was-called").exists()
    assert docker.read_text().splitlines() == ["kraft-s-sandbox"]


async def test_run_task_tears_down_the_container_on_cancel(run, docker, database):
    """Kraft-rki: a pause or skip cancels `run_task` from inside the poll loop,
    which used to propagate straight past the container teardown. A container
    is parented by the docker daemon, not Kraft's process group, so that
    teardown is its only kill switch."""
    task = asyncio.create_task(run(["sleep", "5"], "s-sandbox-cancel", sandbox=DOCKER))
    for _ in range(200):
        row = _row(database, "s-sandbox-cancel")
        if row and row["status"] == "running":
            break
        await asyncio.sleep(0.02)
    await _cancelled(task)

    assert docker.read_text().splitlines() == ["kraft-s-sandbox-cancel"]


async def test_run_task_passes_env_through_to_docker_argv(run, docker, monkeypatch):
    """Kraft-rki: `env=` was silently dropped once `sandbox` was set -- only
    `FORWARDED_ENV` crosses into the container bare. `dispatch.py` passes
    `PYTHONDONTWRITEBYTECODE=1` for `on.test.run` so a fix-loop re-measure
    cannot import a stale `.pyc`. `docker_argv` itself (test_sandbox.py) turns
    `env=` into `-e` flags; this checks `run_task` forwards it at all."""
    seen = {}
    real_docker_argv = sp._sandbox.docker_argv

    def fake_docker_argv(cmd, cwd, sandbox, results_dir, **kw):
        seen["env"] = kw.get("env")
        return real_docker_argv(cmd, cwd, sandbox, results_dir, **kw)

    monkeypatch.setattr(sp._sandbox, "docker_argv", fake_docker_argv)

    status, _ = await run(
        _writes_result({"status": "done"}),
        sandbox=DOCKER,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        repo_entry={"env": {"MY_REPO": "1"}},
    )

    assert status == "done"
    assert seen["env"] == {"MY_REPO": "1", "PYTHONDONTWRITEBYTECODE": "1"}


@pytest.mark.parametrize(
    "message",
    [
        "failed to connect to the docker API; check if the daemon is running",  # current cli
        "Cannot connect to the Docker daemon. Is the docker daemon running?",  # legacy cli
    ],
    ids=["current-wording", "legacy-wording"],
)
async def test_run_task_sandboxed_with_the_daemon_down_is_a_config_error(
    run, run_dirs, tmp_path, monkeypatch, message
):
    """Kraft-nc9gm: `docker run` itself failing to launch (daemon down, or an
    image pull failure) is the same infra-not-agent class as
    `test_run_task_that_cannot_launch_is_a_config_error`, one step later."""
    bin_dir = tmp_path / "fake-docker-daemon-down"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f"#!/usr/bin/env bash\necho {shlex.quote(message)} >&2\nexit 1\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    status, row = await run(["echo", "hi"], "s-daemon-down", sandbox=DOCKER)
    assert (status, row["status"]) == ("config_error", "config_error")
    assert "daemon" in (run_dirs.logs / "s-daemon-down.log").read_text()
