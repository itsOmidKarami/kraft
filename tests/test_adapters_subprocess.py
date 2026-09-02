import asyncio
import json
import os
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
