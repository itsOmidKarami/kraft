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

import pytest
from support.harness import fake_docker_bin

from kraft import db, events, logs, store
from kraft.adapters import subprocess as sp
from kraft.paths import RunDirs

_FAKE_CLAUDE = Path(__file__).resolve().parents[1] / "fixtures" / "fake-claude.sh"


def _resolve_cases(tmp_path):
    from kraft.adapters.subprocess import _resolve

    missing = tmp_path / "nope.json"
    assert _resolve(missing, 0) == "done"
    assert _resolve(missing, 3) == "failed"
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"status": "failed"}))
    assert _resolve(good, 0) == "failed"
    good.write_text(json.dumps({"status": "done"}))
    assert _resolve(good, 1) == "done"
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert _resolve(bad, 0) == "failed"
    empty = tmp_path / "empty.json"
    empty.write_text("")
    assert _resolve(empty, 0) == "done"  # empty == absent -> fall through to rc


def test_result_path_for_matches_the_convention_run_task_uses(tmp_path):
    rd = RunDirs(tmp_path / "run").ensure()
    assert sp.result_path_for(rd, "abc123") == rd.results / "abc123.json"


def test_resolve_result_file_over_exit_code(tmp_path):
    _resolve_cases(tmp_path)


def test_resolve_non_empty_non_conforming_file_is_failed(tmp_path):
    """Chunk C contract: a non-empty result file that is valid JSON but not a
    dict with status in ('done','failed') resolves to 'failed', regardless of rc."""
    from kraft.adapters.subprocess import _resolve

    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"status": "weird"}))
    assert _resolve(weird, 0) == "failed"
    nondict = tmp_path / "nondict.json"
    nondict.write_text("42")
    assert _resolve(nondict, 0) == "failed"
    nondict.write_text("[]")
    assert _resolve(nondict, 0) == "failed"


def test_result_file_can_report_done_with_concerns(tmp_path):
    from kraft.adapters.subprocess import _resolve

    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "done_with_concerns", "concerns": "untested path"}))
    assert _resolve(path, 0) == "done_with_concerns"


def test_result_file_can_report_needs_context(tmp_path):
    from kraft.adapters.subprocess import _resolve

    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "needs_context", "question": "which branch?"}))
    assert _resolve(path, 0) == "needs_context"


def test_unknown_status_still_resolves_to_failed(tmp_path):
    """Regression guard — passes before this task too."""
    from kraft.adapters.subprocess import _resolve

    path = tmp_path / "result.json"
    path.write_text(json.dumps({"status": "made_up_status"}))
    assert _resolve(path, 0) == "failed"


def test_read_concerns_and_read_question(tmp_path):
    concerns_path = tmp_path / "concerns.json"
    concerns_path.write_text(
        json.dumps({"status": "done_with_concerns", "concerns": "the retry path is untested"})
    )
    assert sp.read_concerns(concerns_path) == "the retry path is untested"
    assert sp.read_question(concerns_path) is None

    question_path = tmp_path / "question.json"
    question_path.write_text(
        json.dumps({"status": "needs_context", "question": "which branch should the MR target?"})
    )
    assert sp.read_question(question_path) == "which branch should the MR target?"
    assert sp.read_concerns(question_path) is None


def test_read_verdict(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"status": "done", "verdict": "approve"}))
    assert sp.read_verdict(p) == "approve"

    p.write_text(json.dumps({"status": "done"}))
    assert sp.read_verdict(p) is None

    p.write_text("not json at all")
    assert sp.read_verdict(p) is None

    assert sp.read_verdict(tmp_path / "missing.json") is None


def test_read_result_fields_is_the_one_place_the_field_list_lives(tmp_path):
    """`run_task` and `reattach._exit_from_file` are two independent readers of
    the same result file; a field spelled out in only one of them would reach
    the DB on one exit path and silently drop on the other after a restart
    (Kraft-k3d). Both now call this instead of enumerating the fields
    themselves."""
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


def test_read_result_fields_on_a_missing_file_is_all_none(tmp_path):
    assert sp.read_result_fields(tmp_path / "missing.json") == {
        "summary_ref": None,
        "concerns": None,
        "question": None,
    }


def test_the_field_readers_tolerate_a_broken_file(tmp_path):
    """Missing file, non-JSON, non-mapping, absent key, and non-UTF-8 bytes.

    UnicodeDecodeError is a ValueError, so `except OSError` does not catch it.
    That exact clause has been wrong three times in this codebase already.
    """
    missing = tmp_path / "missing.json"
    non_json = tmp_path / "bad.json"
    non_json.write_text("not json")
    non_mapping = tmp_path / "list.json"
    non_mapping.write_text("[1, 2, 3]")
    absent_key = tmp_path / "absent.json"
    absent_key.write_text(json.dumps({"status": "done"}))
    non_utf8 = tmp_path / "binary.json"
    non_utf8.write_bytes(b"\xff\xfe\x00\x01")

    for reader in (sp.read_summary_ref, sp.read_concerns, sp.read_question):
        assert reader(missing) is None
        assert reader(non_json) is None
        assert reader(non_mapping) is None
        assert reader(absent_key) is None
        assert reader(non_utf8) is None


async def _seed(database, wid="w1", node="verify"):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )


def test_run_task_result_file_wins(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["sh", "-c", 'printf \'{"status":"failed"}\' > "$KRAFT_RESULT_PATH"; exit 0'],
                cwd=tmp_path,
            )
            assert status == "failed"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, pid, pid_start_time, exited_at FROM worker_sessions "
                    "WHERE id='s1'"
                ).fetchone()
            )
            assert row["status"] == "failed"
            assert row["pid"] is not None
            assert row["exited_at"] is not None
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
            assert types[-2:] == ["worker_session_started", "worker_session_exited"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_records_the_command_it_ran(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
            )
            row = database.read(
                lambda c: c.execute("SELECT command FROM worker_sessions WHERE id='s1'").fetchone()
            )
            return status, row["command"]
        finally:
            await database.close()

    status, command = asyncio.run(scenario())
    assert status == "done"
    assert command == "true"


def test_run_task_stamps_concerns_and_question_onto_the_exit_event(tmp_path):
    """`run_task` reads `read_concerns`/`read_question` off the result file the
    same way it already reads `read_summary_ref`, and carries them on
    `worker_session_exited` — the only channel this text has, since the
    migration adds no `concerns` column."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-concerns",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=[
                    "sh",
                    "-c",
                    'printf \'{"status":"done_with_concerns","concerns":"untested retry path"}\''
                    ' > "$KRAFT_RESULT_PATH"; exit 0',
                ],
                cwd=tmp_path,
            )
            assert status == "done_with_concerns"
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "worker_session_exited"
            assert ev["payload"]["concerns"] == "untested retry path"
            assert "question" not in ev["payload"]

            status2 = await sp.run_task(
                database,
                rd,
                session_id="s-question",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=[
                    "sh",
                    "-c",
                    'printf \'{"status":"needs_context","question":"which branch?"}\''
                    ' > "$KRAFT_RESULT_PATH"; exit 0',
                ],
                cwd=tmp_path,
            )
            assert status2 == "needs_context"
            ev2 = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev2["payload"]["question"] == "which branch?"
            assert "concerns" not in ev2["payload"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_wraps_in_docker_when_sandbox_is_set(tmp_path, monkeypatch):
    async def scenario():
        bin_dir = fake_docker_bin(tmp_path)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        called = tmp_path / "docker-was-called"
        monkeypatch.setenv("FAKE_DOCKER_CALLED", str(called))
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-sandbox",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["sh", "-c", 'printf \'{"status":"done"}\' > "$KRAFT_RESULT_PATH"; exit 0'],
                cwd=tmp_path,
                sandbox={"kind": "docker", "image": "kraft-worker:py"},
            )
            assert status == "done"
            # Not just "the command ran" (it would have, unwrapped, too) --
            # that it ran *through docker*.
            assert called.exists()
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_tears_down_the_container_by_name_when_sandboxed(tmp_path, monkeypatch):
    """Kraft-rki: the pid Kraft tracks and signals is `docker run`'s own
    client, not the container -- it survives a SIGKILLed client because it's
    parented by the docker daemon, not Kraft's process group. `run_task` has
    to reach it a second way, by the name `docker_argv` gave it.
    """

    async def scenario():
        bin_dir = fake_docker_bin(tmp_path)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        rm_log = tmp_path / "docker-rm-log"
        monkeypatch.setenv("FAKE_DOCKER_RM_LOG", str(rm_log))
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-sandbox-teardown",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["sh", "-c", 'printf \'{"status":"done"}\' > "$KRAFT_RESULT_PATH"; exit 0'],
                cwd=tmp_path,
                sandbox={"kind": "docker", "image": "kraft-worker:py"},
            )
            assert status == "done"
            assert rm_log.read_text().splitlines() == ["kraft-s-sandbox-teardown"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_tears_down_the_container_on_cancel(tmp_path, monkeypatch):
    """Kraft-rki: a pause or skip cancels `run_task` from inside the poll
    loop (deps.cancel -> CancelledError), which used to propagate straight
    past the container teardown that only ever sat after the `try/finally`.
    A container is parented by the docker daemon, not Kraft's process
    group, so that teardown is its only kill switch -- it has to run on the
    cancellation path too, not just the clean-exit one.
    """

    async def scenario():
        bin_dir = fake_docker_bin(tmp_path)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        rm_log = tmp_path / "docker-rm-log"
        monkeypatch.setenv("FAKE_DOCKER_RM_LOG", str(rm_log))
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            task = asyncio.create_task(
                sp.run_task(
                    database,
                    rd,
                    session_id="s-sandbox-cancel",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    cmd=["sleep", "5"],
                    cwd=tmp_path,
                    sandbox={"kind": "docker", "image": "kraft-worker:py"},
                )
            )
            for _ in range(200):
                row = database.read(
                    lambda c: c.execute(
                        "SELECT status FROM worker_sessions WHERE id='s-sandbox-cancel'"
                    ).fetchone()
                )
                if row and row["status"] == "running":
                    break
                await asyncio.sleep(0.02)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert rm_log.read_text().splitlines() == ["kraft-s-sandbox-cancel"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_passes_env_through_to_docker_argv(tmp_path, monkeypatch):
    """Kraft-rki: `env=` is otherwise silently dropped once `sandbox` is set
    -- only `FORWARDED_ENV` crosses into the container bare. Concretely,
    `dispatch.py` passes `PYTHONDONTWRITEBYTECODE=1` for `on.test.run` so a
    fix-loop re-measure cannot import a stale `.pyc`. `docker_argv` itself
    (`test_sandbox.py`) covers turning `env=` into literal `-e NAME=VALUE`
    flags; this only checks `run_task` actually forwards its own `env=` arg
    into that call rather than dropping it on the sandboxed path.
    """
    seen = {}
    real_docker_argv = sp._sandbox.docker_argv

    def fake_docker_argv(cmd, cwd, sandbox, results_dir, **kw):
        seen["env"] = kw.get("env")
        return real_docker_argv(cmd, cwd, sandbox, results_dir, **kw)

    monkeypatch.setattr(sp._sandbox, "docker_argv", fake_docker_argv)

    async def scenario():
        bin_dir = fake_docker_bin(tmp_path)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-sandbox-env",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["sh", "-c", 'printf \'{"status":"done"}\' > "$KRAFT_RESULT_PATH"; exit 0'],
                cwd=tmp_path,
                sandbox={"kind": "docker", "image": "kraft-worker:py"},
                env={"PYTHONDONTWRITEBYTECODE": "1"},
            )
            assert status == "done"
        finally:
            await database.close()

    asyncio.run(scenario())
    assert seen["env"] == {"PYTHONDONTWRITEBYTECODE": "1"}


def test_run_task_exit_code_fallback(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            ok = await sp.run_task(
                database,
                rd,
                session_id="s-ok",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
            )
            bad = await sp.run_task(
                database,
                rd,
                session_id="s-bad",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["false"],
                cwd=tmp_path,
            )
            assert (ok, bad) == ("done", "failed")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_missing_binary_is_a_config_error(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-mb",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["kraft-nonexistent-binary-xyz"],
                cwd=tmp_path,
            )
            # Not "failed": a task that never launched is a configuration
            # problem, and reporting it as a task failure opened a fix cycle no
            # agent could win by editing source (Kraft-579).
            assert status == "config_error"
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s-mb'").fetchone()
            )
            assert row["status"] == "config_error"
            # The status alone is what this branch used to leave behind: a 5ms
            # "failed" and a zero-byte log, which cost an operator the whole
            # diagnosis. The log has to name what was missing.
            log = (rd.logs / "s-mb.log").read_text()
            assert "kraft-nonexistent-binary-xyz" in log
            assert "command" in log
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_missing_cwd_says_so_rather_than_blaming_the_command(tmp_path):
    """Popen raises FileNotFoundError for a missing cwd too, and the two are
    fixed in different places — the log must not send you hunting for a binary
    that is sitting right there."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-mc",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["echo", "hi"],  # exists; the cwd is what does not
                cwd=tmp_path / "no-such-worktree",
            )
            assert status == "config_error"
            log = (rd.logs / "s-mc.log").read_text()
            assert "working directory" in log
            assert "no-such-worktree" in log
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_child_is_detached(tmp_path):
    """start_new_session=True -> child sits in its own process group."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        child_pid = None
        try:
            await _seed(database)
            task = asyncio.create_task(
                sp.run_task(
                    database,
                    rd,
                    session_id="s-d",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    cmd=["sleep", "5"],
                    cwd=tmp_path,
                )
            )
            pid_start_time = "unset"
            for _ in range(200):
                row = database.read(
                    lambda c: c.execute(
                        "SELECT pid, pid_start_time, status FROM worker_sessions WHERE id='s-d'"
                    ).fetchone()
                )
                if row and row["status"] == "running" and row["pid"]:
                    child_pid = row["pid"]
                    pid_start_time = row["pid_start_time"]
                    break
                await asyncio.sleep(0.02)
            assert child_pid is not None
            # psutil create_time() must actually be captured for a live child,
            # not silently None — Chunk C's PID-reuse guard depends on it.
            assert pid_start_time is not None
            assert os.getpgid(child_pid) != os.getpgid(0)  # own session/pgroup
            task.cancel()
        finally:
            if child_pid:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await database.close()

    asyncio.run(scenario())


def test_post_resolve_can_downgrade(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-pr",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
                post_resolve=lambda base, log, rc: "failed",
            )
            assert status == "failed"
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s-pr'").fetchone()
            )
            assert row["status"] == "failed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_require_result_file_downgrades_a_silent_success(tmp_path):
    """Kraft-avpe: an agent that exits 0 with no result file at all is not
    'done' -- it never reached the end of the contract every agent hook is told
    to follow. cmd writes nothing to $KRAFT_RESULT_PATH and exits 0."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-rrf",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                cmd=["true"],
                cwd=tmp_path,
                require_result_file=True,
            )
            assert status == "failed"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions WHERE id='s-rrf'"
                ).fetchone()
            )
            assert row["status"] == "failed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_require_result_file_does_not_affect_a_normal_subprocess_hook(tmp_path):
    """The default is False and only run_agent_task sets it -- on.test.run and
    every other subprocess-kind binding has no result-file contract and must
    resolve to 'done' on exit 0 exactly as it does today."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-norrf",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
            )
            assert status == "done"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_require_result_file_does_not_override_a_rate_limited_launch(tmp_path):
    """A rejected launch produces no result file by construction (existing
    comment in run_task) -- it must still resolve to rate_limited, not be
    caught by the new check as a contract violation.

    Nothing in this file already drives run_task end-to-end through a
    rejected-rate-limit log (the existing rate_limit_* tests below call
    sp._rate_limit_rejection directly on a hand-written log file) -- this
    builds that path itself, writing the same event shape those tests parse
    (test_rate_limit_rejection_reads_a_rejected_event, this file)."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            # The child writes a rejected rate_limit_event to stdout (which
            # run_task redirects to the session's log file) and exits 0 with
            # no result file -- the exact shape a real rejected launch leaves
            # behind.
            event = (
                '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
                '"resetsAt":1788968400,"rateLimitType":"five_hour"}}'
            )
            status = await sp.run_task(
                database,
                rd,
                session_id="s-rl",
                work_item_id="w1",
                node_id="implementation",
                hook_point="on.implementation.start",
                cmd=["sh", "-c", f"echo '{event}'"],
                cwd=tmp_path,
                require_result_file=True,
            )
            assert status == "rate_limited"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_persists_session_summary_ref(tmp_path):
    """03 §3: session_summary_ref from the result file lands on the session row."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            result = json.dumps(
                {"status": "done", "session_summary_ref": ".engineering/sessions/s1.md"}
            )
            await sp.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["sh", "-c", f'printf %s {shlex.quote(result)} > "$KRAFT_RESULT_PATH"'],
                cwd=tmp_path,
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT session_summary_ref FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert row["session_summary_ref"] == ".engineering/sessions/s1.md"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_without_summary_ref_leaves_column_null(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            await sp.run_task(
                database,
                rd,
                session_id="s2",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT session_summary_ref FROM worker_sessions WHERE id='s2'"
                ).fetchone()
            )
            assert row["session_summary_ref"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resolve_result_file_survives_non_utf8_bytes(tmp_path):
    """A plugin that died mid-write leaves bytes that are not valid UTF-8.

    UnicodeDecodeError is a ValueError, so `except OSError` alone misses it and
    the session's own status read takes the process down. Same clause has been
    wrong four times in this codebase.
    """
    from kraft.adapters.subprocess import _resolve_result_file

    p = tmp_path / "r.json"
    p.write_bytes(b"\xff\xfe\x00binary")
    assert _resolve_result_file(p) == "failed"


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
    """A line without its newline is not a line yet.

    The sidecar is keyed by line number, so stamping a number before the line
    is complete gives that timestamp to whatever text lands next -- and
    `logs.jsonl` would then read a different line under the same number.
    """
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


def test_child_stdin_is_devnull(tmp_path, monkeypatch):
    """In stream-json mode the CLI waits on stdin before it starts, and the
    child inherits the server's. Measured: `< /dev/null` removes the 3s
    `no stdin data received` wait. No hook should read the server's stdin."""
    seen = {}
    real_popen = subprocess.Popen

    def spy(cmd, **kw):
        seen.update(kw)
        return real_popen(cmd, **kw)

    monkeypatch.setattr(sp.subprocess, "Popen", spy)

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            await sp.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["sh", "-c", "exit 0"],
                cwd=tmp_path,
            )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert seen["stdin"] == subprocess.DEVNULL


def test_log_is_readable_while_the_child_is_still_running(tmp_path):
    """Kraft-77z: the log file was 0 bytes for the whole node, so `kraft view
    logs -f` streamed nothing and the modal stayed empty.

    Driven through `fixtures/fake-claude.sh` rather than a shell one-liner on
    purpose: a shell writes its first line immediately whatever the adapter
    does, so a shell child would make this test green against the very bug it
    is here to pin. The fake stands in for the CLI, and before this task it
    prints nothing until it exits.
    """

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        early = []
        try:
            await _seed(database)
            task = asyncio.create_task(
                sp.run_task(
                    database,
                    rd,
                    session_id="s1",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    cmd=[str(_FAKE_CLAUDE), "-p", "do it", "--append-system-prompt", "ctx"],
                    cwd=tmp_path,
                    env={"KRAFT_FAKE_CLAUDE": "noop", "KRAFT_FAKE_CLAUDE_STREAM_DELAY": "3"},
                )
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            while not task.done() and loop.time() < deadline:
                rows = list(logs.jsonl(rd.logs / "s1.log"))
                if rows:
                    early = rows
                    break
                await asyncio.sleep(0.05)
            assert await task == "done"
        finally:
            await database.close()
        return early

    early = asyncio.run(scenario())
    assert early, "the log was empty while the child was still running"
    assert "init" in early[0]["text"]


def test_running_session_row_carries_tokens_before_exit(tmp_path):
    """Kraft-54dk / Kraft-2r8s: usage was harvested once, at exit, so a live
    node's row held NULL tokens and NULL model for its whole run -- and
    `usage_rollup` reports 0 for exactly the node a human is watching."""
    script = (
        'printf \'{"type":"system","subtype":"init","model":"claude-opus-5"}\\n\'; '
        'printf \'{"type":"assistant","request_id":"req_1","message":'
        '{"model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":200}}}\\n\'; '
        "sleep 3; "
        'printf \'{"type":"result","is_error":false,'
        '"usage":{"input_tokens":1000,"output_tokens":200}}\\n\''
    )

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        live = None
        try:
            await _seed(database)
            task = asyncio.create_task(
                sp.run_task(
                    database,
                    rd,
                    session_id="s1",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    cmd=["sh", "-c", script],
                    cwd=tmp_path,
                    progress_s=0.2,
                )
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            while not task.done() and loop.time() < deadline:
                row = database.read(
                    lambda c: c.execute(
                        "SELECT status, model, tokens_in, tokens_out FROM worker_sessions "
                        "WHERE id = 's1'"
                    ).fetchone()
                )
                if row is not None and row["status"] == "running" and row["tokens_in"]:
                    live = dict(row)
                    break
                await asyncio.sleep(0.05)
            assert await task == "done"
        finally:
            await database.close()
        return live

    live = asyncio.run(scenario())
    assert live == {
        "status": "running",
        "model": "claude-opus-5",
        "tokens_in": 1000,
        "tokens_out": 200,
    }


def test_rate_limit_rejection_reads_a_rejected_event(tmp_path):
    log = tmp_path / "s.log"
    log.write_text(
        '{"type":"assistant","message":{}}\n'
        '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
        '"resetsAt":1788968400,"rateLimitType":"five_hour"}}\n'
        '{"type":"result","is_error":true}\n'
    )
    got = sp._rate_limit_rejection(log)
    assert got == {
        "rate_limit_type": "five_hour",
        "resets_at": 1788968400,
        "resets_at_iso": "2026-09-09T15:40:00+00:00",
    }


def test_rate_limit_rejection_ignores_allowed_events(tmp_path):
    """`overageStatus` can read "rejected" while the turn itself was allowed --
    only a top-level `status: "rejected"` means the launch was refused."""
    log = tmp_path / "s.log"
    log.write_text(
        '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
        '"overageStatus":"rejected","resetsAt":1788986400,"rateLimitType":"five_hour"}}\n'
        '{"type":"result","is_error":false}\n'
    )
    assert sp._rate_limit_rejection(log) is None


def test_rate_limit_rejection_none_without_the_event(tmp_path):
    log = tmp_path / "s.log"
    log.write_text('{"type":"result","is_error":true}\n')
    assert sp._rate_limit_rejection(log) is None


def test_rate_limit_rejection_best_effort_on_unreadable_log(tmp_path):
    assert sp._rate_limit_rejection(tmp_path / "missing.log") is None


def test_run_task_aborts_before_popen_if_the_row_was_stopped_first(tmp_path, monkeypatch):
    """Kraft-qx1q: a session row marked paused/stopped between `create_session`
    and Popen must never actually launch -- nothing SIGTERMs a session with no
    pid yet."""
    original_create_session = store.create_session

    def create_then_stop(conn, **kwargs):
        original_create_session(conn, **kwargs)
        conn.execute("UPDATE worker_sessions SET status = 'paused' WHERE id = ?", (kwargs["id"],))

    monkeypatch.setattr(store, "create_session", create_then_stop)

    def _boom(*a, **kw):
        raise AssertionError("Popen must not run once the row is no longer pending")

    monkeypatch.setattr(sp.subprocess, "Popen", _boom)
    rd = RunDirs(tmp_path).ensure()

    async def scenario():
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            return await sp.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
            )
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "paused"
    assert not (rd.logs / "s1.log").exists()  # aborted before the log was even opened


def test_run_task_kills_a_backgrounded_grandchild_when_the_session_ends(tmp_path):
    """A worker that backgrounds a child (a fixture server, `just dev`) must
    not outlive the session (Kraft-1nye)."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            pidfile = tmp_path / "grandchild.pid"
            status = await sp.run_task(
                database,
                rd,
                session_id="s-grp",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["bash", "-c", f"sleep 30 & echo $! > {pidfile}"],
                cwd=tmp_path,
            )
            assert status == "done"
            grandchild = int(pidfile.read_text().strip())
            alive = True
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                alive = False
            assert not alive
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_escalates_to_sigkill_when_the_group_ignores_sigterm(tmp_path):
    """SIGTERM first, SIGKILL only for what ignores it — the two-stage kill
    `serve.py:_terminate` already models."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            pidfile = tmp_path / "grandchild.pid"
            status = await sp.run_task(
                database,
                rd,
                session_id="s-ign",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                # SIG_IGN on TERM is inherited across fork+exec, so the
                # backgrounded `sleep` ignores it too.
                cmd=["bash", "-c", f"trap '' TERM; sleep 30 & echo $! > {pidfile}"],
                cwd=tmp_path,
                group_kill_grace=0.3,
            )
            assert status == "done"
            grandchild = int(pidfile.read_text().strip())
            # `_kill_group` returns the instant it sends SIGKILL, not once the
            # kernel has finished reaping — a loaded CI box can still see the
            # grandchild for a few ms after that. Poll instead of one
            # synchronous check, which is racy against signal delivery, not a
            # sign the kill logic under test is wrong.
            deadline = time.monotonic() + 2.0
            alive = True
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild, 0)
                except ProcessLookupError:
                    alive = False
                    break
                await asyncio.sleep(0.02)
            assert not alive
        finally:
            await database.close()

    asyncio.run(scenario())


def test_cancelling_run_task_reaps_the_leader_instead_of_paying_the_grace(tmp_path):
    """Kraft-rki: a pause or skip cancels `run_task` mid-poll, so its
    `finally` SIGTERMs a leader that is still alive and that nothing then
    reaps. Linux counts an unreaped zombie as a live group member, so
    `_kill_group` used to wait out the whole grace before SIGKILL -- 10s per
    cancel in CI, which failed pause/resume, SIGTERM shutdown and the e2e
    pause flow there while macOS (which reports a zombie-only group as gone)
    passed. Both halves are asserted so this fails for the real reason on
    either platform: the timing on Linux, the unreaped leader on macOS."""
    import psutil

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            task = asyncio.create_task(
                sp.run_task(
                    database,
                    rd,
                    session_id="s-cancel",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    cmd=["sleep", "30"],
                    cwd=tmp_path,
                    group_kill_grace=5.0,
                )
            )
            pid = None
            deadline = time.monotonic() + 5.0
            while pid is None and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                row = database.read(
                    lambda c: c.execute(
                        "SELECT pid FROM worker_sessions WHERE id = 's-cancel'"
                    ).fetchone()
                )
                pid = row["pid"] if row else None
            assert pid is not None, "session never started"

            start = time.monotonic()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            elapsed = time.monotonic() - start

            assert elapsed < 2.0, f"cancel paid the group-kill grace ({elapsed:.1f}s)"
            try:
                leftover = psutil.Process(pid).status()
            except psutil.NoSuchProcess:
                leftover = None
            assert leftover is None, f"leader {pid} left unreaped ({leftover})"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_group_kill_does_not_delay_a_session_with_no_survivors(tmp_path):
    """The common case (no backgrounded children) must not pay the grace
    period — `os.killpg` on an already-empty group raises immediately."""

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            start = time.monotonic()
            status = await sp.run_task(
                database,
                rd,
                session_id="s-none",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
            )
            elapsed = time.monotonic() - start
            assert status == "done"
            assert elapsed < 2.0
        finally:
            await database.close()

    asyncio.run(scenario())


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
    wrapped = sp._wrap_with_exit_file(cmd, tmp_path / "s.exit")
    subprocess.run(wrapped, check=True)
    assert eval(out.read_text()) == weird


def test_exit_wrapper_records_zero(tmp_path):
    exit_path = tmp_path / "s.exit"
    wrapped = sp._wrap_with_exit_file([sys.executable, "-c", "raise SystemExit(0)"], exit_path)
    rc = subprocess.run(wrapped).returncode
    assert rc == 0
    assert sp._resolve_exit_file(exit_path) == "done"


def test_exit_wrapper_records_nonzero_and_propagates_it(tmp_path):
    exit_path = tmp_path / "s.exit"
    wrapped = sp._wrap_with_exit_file([sys.executable, "-c", "raise SystemExit(7)"], exit_path)
    rc = subprocess.run(wrapped).returncode
    assert rc == 7, "the wrapper must not swallow the child's exit code"
    assert sp._resolve_exit_file(exit_path) == "failed"


def test_resolve_exit_file_missing_or_garbage_is_none(tmp_path):
    assert sp._resolve_exit_file(tmp_path / "nope.exit") is None
    junk = tmp_path / "junk.exit"
    junk.write_text("not a number")
    assert sp._resolve_exit_file(junk) is None


def test_run_task_leaves_the_exit_file_beside_the_result(tmp_path):
    """The wiring, not just the helpers: a real launch must record its code.

    Everything above tests `_wrap_with_exit_file` in isolation; this is the
    only check that `run_task` actually passes the wrapped argv to `Popen`,
    which is what `reattach._adopted_status` depends on existing.
    """

    async def scenario():
        rd = RunDirs(tmp_path).ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-exit",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=[sys.executable, "-c", "raise SystemExit(4)"],
                cwd=tmp_path,
            )
            assert status == "failed"
            # And no result file: the Kraft-avpe "no contract" signal survives.
            assert not (rd.results / "s-exit.json").exists()
            assert (rd.results / "s-exit.exit").read_text() == "4"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_task_records_usage_when_a_pause_cancels_it(tmp_path):
    """Kraft-s7c04.18: `deps.cancel` raises CancelledError out of the poll loop,
    so nothing after the `finally` runs -- `session_exited` is never reached and
    neither is any usage read. This is the only place the cancelled pause path
    passes through."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            task = asyncio.create_task(
                sp.run_task(
                    database,
                    rd,
                    session_id="s-paused",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.test.run",
                    cmd=["sleep", "5"],
                    cwd=tmp_path,
                    flush_grace=0,
                )
            )
            for _ in range(200):
                row = database.read(
                    lambda c: c.execute(
                        "SELECT status FROM worker_sessions WHERE id='s-paused'"
                    ).fetchone()
                )
                if row and row["status"] == "running":
                    break
                await asyncio.sleep(0.02)
            # what the agent flushed on its way out, written where run_task looks
            (rd.logs / "s-paused.log").write_text(
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
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return database.read(
                lambda c: c.execute(
                    "SELECT status, cost_usd, tokens_in FROM worker_sessions WHERE id='s-paused'"
                ).fetchone()
            )
        finally:
            await database.close()

    row = asyncio.run(scenario())
    assert row["status"] == "paused"
    assert row["cost_usd"] == pytest.approx(0.42)
    assert row["tokens_in"] == 1000


def test_run_task_waits_for_the_flush_only_when_the_row_is_paused(tmp_path, monkeypatch):
    """The wait exists because `_kill_group` SIGTERMs the group the instant the
    poll loop leaves, milliseconds after the route's SIGINT. It must not be paid
    on a normal exit, which is every session that was never interrupted."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed(database)
            monkeypatch.setattr(sp, "_flush_sleep", fake_sleep)
            status = await sp.run_task(
                database,
                rd,
                session_id="s-clean",
                work_item_id="w1",
                node_id="verify",
                hook_point="on.test.run",
                cmd=["true"],
                cwd=tmp_path,
                flush_grace=2.0,
            )
            return status
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "done"
    assert slept == []
