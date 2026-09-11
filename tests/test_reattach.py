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

#: What a Claude Code worker leaves as its log's last line. Cache reads are
#: input tokens that were billed, so `usage.read` folds them into tokens_in:
#: 100 + 300 = 400.
_ENVELOPE = json.dumps(
    {
        "type": "result",
        "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 300},
        "modelUsage": {"claude-opus-5": {}},
        "total_cost_usd": 1.25,
    }
)


def _usage_row(database, sid="s1"):
    return database.read(
        lambda c: c.execute(
            "SELECT model, tokens_in, tokens_out, cost_usd FROM worker_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    )


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


def test_a_crashed_adopt_marks_the_work_item_needs_human(monkeypatch, tmp_path):
    """Kraft-mjwz: `reattach()` hands its tasks straight to the caller rather
    than through `api._spawn`'s `_guard`, so a crash anywhere in `_adopt`
    outside its own per-tick try (its opening DB read, or `_exit_from_file` at
    the end) used to vanish -- no log, no needs_human, task leaked forever.
    `_guarded_adopt` is the fix; this pins its catch, not the log-polling loop
    (that's Kraft-jgs6, covered elsewhere)."""

    async def _boom(db, session_id, pid, poll_s=0.1, progress_s=5.0):
        raise ValueError("boom")

    monkeypatch.setattr(reattach, "_adopt", _boom)

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
            # A pid that is certainly alive and passes the identity check (this
            # test process itself) — the crash under test is in _adopt, not in
            # the identity check that decides whether it even runs.
            import os

            import psutil

            pst = psutil.Process(os.getpid()).create_time()
            await database.write(lambda c: store.session_running(c, "s1", os.getpid(), pst))
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert summary.adopted == ["s1"]
            await asyncio.gather(*adopted.values(), return_exceptions=True)

            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "needs_human"
            events_seen = database.read(lambda c: events.read_after(c, 0, "w1"))
            crash = next(e for e in events_seen if e["type"] == "work_item_needs_human")
            assert "reattach crashed" in crash["payload"]["reason"]
            assert "boom" in crash["payload"]["reason"]
        finally:
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


def test_a_session_resolved_from_file_records_its_usage(tmp_path):
    """A worker that outlived a Kraft restart spent real money. Recording the
    exit without its usage leaves all four columns NULL forever, and the run
    reads as free in every place Kraft reports money (Kraft-7co4)."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            # the result file carries no usage block, so the log envelope is
            # the only source — which is where an agent's numbers actually are
            (rd.results / "s1.json").write_text('{"status": "done"}')
            (rd.logs / "s1.log").write_text(_ENVELOPE + "\n")
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
            assert summary.resolved_from_file == ["s1"]

            row = _usage_row(database)
            assert row["model"] == "claude-opus-5"
            assert (row["tokens_in"], row["tokens_out"]) == (400, 20)
            assert row["cost_usd"] == 1.25

            exited = next(
                e
                for e in database.read(lambda c: events.read_after(c, 0, "w1"))
                if e["type"] == "worker_session_exited"
            )
            assert exited["payload"]["tokens_in"] == 400
            assert exited["payload"]["tokens_out"] == 20
            assert exited["payload"]["cost_usd"] == 1.25
            assert exited["payload"]["model"] == "claude-opus-5"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_an_adopted_session_carries_live_tokens_before_it_exits(tmp_path):
    """Kraft-jgs6: the child survives the restart that orphaned this task
    (`run_task` starts it `start_new_session=True` precisely so it can) and
    keeps writing its own log regardless. Before `_adopt` had its own progress
    loop, tokens_in/out sat frozen at whatever `run_task`'s loop last wrote
    before the restart, for the rest of the session's life -- looking exactly
    like Kraft-41f7 (the poller dying) recurring, but it was a different loop
    entirely, one that never polled the log at all."""

    script = (
        'printf \'{"type":"assistant","request_id":"req_1","message":'
        '{"model":"claude-opus-5","usage":{"input_tokens":1000,"output_tokens":200}}}\\n\'; '
        "sleep 3"
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        log_path = rd.logs / "s1.log"
        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            ["sh", "-c", script],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_file.close()  # the child holds its own dup'd fd
        live = None
        try:
            import psutil

            pst = psutil.Process(proc.pid).create_time()
            await _seed_item(database)
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(log_path),
                    result_path=str(rd.results / "s1.json"),
                )
            )
            await database.write(lambda c: store.session_running(c, "s1", proc.pid, pst))
            # Calls `_adopt` directly (rather than through `reattach()`) so the
            # test can shorten `progress_s` -- the default 5s would make this
            # test as slow as the bug it is pinning.
            task = asyncio.create_task(
                reattach._adopt(database, "s1", proc.pid, poll_s=0.05, progress_s=0.2)
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            while not task.done() and loop.time() < deadline:
                row = _usage_row(database)
                if row is not None and row["tokens_in"]:
                    live = dict(row)
                    break
                await asyncio.sleep(0.05)
            (rd.results / "s1.json").write_text('{"status": "done"}')
            await task
            proc.wait()
        finally:
            proc.wait()
            await database.close()
        return live

    live = asyncio.run(scenario())
    assert live is not None, "tokens_in never appeared on the row before the child exited"
    assert live["model"] == "claude-opus-5"
    assert (live["tokens_in"], live["tokens_out"]) == (1000, 200)


def test_an_adopted_session_records_its_usage(tmp_path):
    """The `_adopt` path, whose own SELECT has to grow a log_path column — the
    file-resolved test above passes without that and would not catch it."""

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
            (rd.logs / "s1.log").write_text(_ENVELOPE + "\n")
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
            await adopted["s1"]
            proc.wait()

            row = _usage_row(database)
            assert row["model"] == "claude-opus-5"
            assert (row["tokens_in"], row["tokens_out"]) == (400, 20)
            assert row["cost_usd"] == 1.25
        finally:
            proc.wait()
            await database.close()

    asyncio.run(scenario())
