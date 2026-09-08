import asyncio
import json
import os
import shlex
import signal

from kraft import db, events, store
from kraft.adapters import subprocess as sp
from kraft.paths import RunDirs


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


def test_run_task_missing_binary_is_failed(tmp_path):
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
            assert status == "failed"
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s-mb'").fetchone()
            )
            assert row["status"] == "failed"
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
            assert status == "failed"
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
