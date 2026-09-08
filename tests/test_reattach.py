from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from kraft import db, events, reattach, store
from kraft.paths import RunDirs
from kraft.templates import Registry

_CHAIN = json.dumps(
    {
        "template_id": "quick-task",
        "nodes": [
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
            {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
        ],
    }
)
_REG = Registry(hooks={})


async def _seed_item(database, wid="w1"):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id="B-1",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition=_CHAIN,
        )
    )
    await database.write(lambda c: store.load_chain(c, wid, "implementation"))
    await database.write(lambda c: store.enter_node(c, wid, "implementation"))


def _types(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def test_pending_session_becomes_unknown_and_needs_human(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.results / "s1.json"),
                )
            )  # stays 'pending', pid NULL
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert adopted == {}
            assert summary.unknown == ["s1"]
            assert "w1" not in summary.resumed_work_items
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "needs_human"
            assert "session_unknown" in _types(database, "w1")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_running_dead_pid_resolves_from_result_file(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            (rd.results / "s1.json").write_text(
                '{"status": "done", "session_summary_ref": ".engineering/sessions/s1.md"}'
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.results / "s1.json"),
                )
            )
            # mark it running against a definitely-dead pid with a start time
            await database.write(lambda c: store.session_running(c, "s1", 2_000_000_000, 123.0))
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert adopted == {}
            assert summary.resolved_from_file == ["s1"]
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "done"
            ref = database.read(
                lambda c: c.execute(
                    "SELECT session_summary_ref FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert ref["session_summary_ref"] == ".engineering/sessions/s1.md"
            t = _types(database, "w1")
            assert "session_reattached" in t and "worker_session_exited" in t
            assert summary.resumed_work_items == ["w1"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_running_dead_pid_no_file_is_unknown(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.results / "s1.json"),
                )
            )
            await database.write(lambda c: store.session_running(c, "s1", 2_000_000_000, 123.0))
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert summary.unknown == ["s1"]
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_live_pid_matching_identity_is_adopted(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            start_new_session=True,
        )
        try:
            import psutil

            pst = psutil.Process(proc.pid).create_time()
            await _seed_item(database)
            (rd.results / "s1.json").write_text('{"status": "done"}')
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.results / "s1.json"),
                )
            )
            await database.write(lambda c: store.session_running(c, "s1", proc.pid, pst))
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert summary.adopted == ["s1"]
            assert set(adopted) == {"s1"}
            assert "session_reattached" in _types(database, "w1")
            # session still running until the child exits
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "running"
            # let the adopt task finish, then reap the child promptly
            await adopted["s1"]
            proc.wait()
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "done"
        finally:
            proc.wait()
            await database.close()

    asyncio.run(scenario())


def test_a_restart_mid_ci_poll_has_a_row_to_reattach(tmp_path):
    """Kraft-7xt: `forge.run_task` now creates its session row before the poll
    (Kraft-41b), so a restart mid-wait finds it -- it has no pid (the work
    happens in-process, not in a child), so it stays 'pending' the whole
    time. Before that fix there was no row at all and the node silently
    re-ran from scratch; now the restart surfaces it as needs_human, the same
    as any other unconfirmed session, rather than losing track of it."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            await database.write(
                lambda c: store.enter_node(c, "w1", "verify")
            )  # the forge nodes live past "implementation" in a real chain
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="verify",
                    hook_point="on.ci.poll",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.results / "s1.json"),
                )
            )  # never left 'pending': no pid, no child to launch
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert adopted == {}
            assert summary.unknown == ["s1"]
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resolved_from_file_carries_concerns_and_question(tmp_path):
    """A session resolved from its result file after a restart must reach
    `worker_session_exited` with the same payload `adapters.subprocess.run_task`
    would have stamped — the concerns roll-up at the gate and the
    needs_context question both read that event, so dropping the fields here
    silently loses what the worker reported."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            (rd.results / "s1.json").write_text(
                '{"status": "done_with_concerns", "concerns": "the migration is untested",'
                ' "question": "which db?"}'
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(rd.logs / "s1.log"),
                    result_path=str(rd.results / "s1.json"),
                )
            )
            await database.write(lambda c: store.session_running(c, "s1", 2_000_000_000, 123.0))
            summary, _ = await reattach.reattach(database, rd, _REG)
            assert summary.resolved_from_file == ["s1"]
            exited = [
                e
                for e in database.read(lambda c: events.read_after(c, 0, "w1"))
                if e["type"] == "worker_session_exited"
            ]
            assert exited[-1]["payload"]["concerns"] == "the migration is untested"
            assert exited[-1]["payload"]["question"] == "which db?"
        finally:
            await database.close()

    asyncio.run(scenario())
