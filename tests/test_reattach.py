from __future__ import annotations

import asyncio
import json
import os
import signal
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

    async def _boom(db, session_id, pid, poll_s=0.1, progress_s=5.0, **kw):
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


def test_reattach_resumes_a_deferred_self_retry_left_by_an_escalation_turn(tmp_path, monkeypatch):
    """Kraft-atdbw: `lifecycle.retry_work_item` defers a self-retry from a
    live escalation session by writing `work_item_self_retry_requested`
    rather than running it inline, because the caller's own session is still
    live. The only consumers are `gates.resume_after_escalation`, called
    from inside `escalate.dispatch`'s awaiting frame -- gone if the server
    restarts mid-turn. `_adopt`'s tail must pick the deferred request back up
    for an adopted `hook_point='escalation'` session, or it sits unconsumed
    forever and the item re-escalates onto an already-fixed stop.

    Exercises the *live-pid* branch (a real orphaned subprocess, not a
    dead-pid/result-file row). The resolved-from-file branch has the same
    consumer wired in too (see
    `test_reattach_resumes_a_deferred_self_retry_from_a_resolved_from_file_session`),
    but as a backgrounded task rather than inline: `resume_after_escalation`
    awaits `walk.run(...)`, which can run as long as a full agent turn, and
    running it synchronously inside `reattach()`'s own loop would block
    `reattach()` -- and so the whole server's startup -- for that long.
    """
    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    async def fake_refresh(worktree, repo, branch):
        return None

    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)
    monkeypatch.setattr("kraft.builtins.refresh_worktree_base", fake_refresh)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(0.3)"],
            start_new_session=True,
        )
        try:
            import psutil

            pst = psutil.Process(proc.pid).create_time()
            await _seed_item(database)
            await database.write(
                lambda c: store.mark_needs_human(c, "w1", "implementation", "budget exhausted")
            )
            # The escalation turn: a worker_sessions row with
            # hook_point='escalation', its escalation_message event, and --
            # as if the agent called `kraft item retry` on itself mid-turn --
            # a deferred self-retry request, exactly what lifecycle.py's
            # retry_work_item writes.
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="esc1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "esc1.log"),
                    result_path=str(rd.results / "esc1.json"),
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    "w1",
                    "escalation_message",
                    {"session_id": "esc1", "message": "please fix it", "auto": False},
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    "w1",
                    "work_item_self_retry_requested",
                    {
                        "session_id": "esc1",
                        "node_id": "implementation",
                        "key": None,
                        "gate_key": None,
                        "steer": "fixed it",
                        "seeded": False,
                    },
                )
            )
            (rd.results / "esc1.json").write_text('{"status": "done"}')
            await database.write(lambda c: store.session_running(c, "esc1", proc.pid, pst))

            summary, adopted = await reattach.reattach(
                database, rd, _REG, launch_factory=lambda repo: None
            )
            assert summary.adopted == ["esc1"]
            await adopted["esc1"]
            proc.wait()

            evs = database.read(lambda c: events.read_after(c, 0, "w1"))
            return [e["type"] for e in evs]
        finally:
            proc.wait()
            await database.close()

    types = asyncio.run(scenario())
    assert len(walk_calls) == 1
    assert walk_calls[0]["steer"] == "fixed it"
    # Retried and re-entered -- not sitting needs_human on the original
    # (already "fixed") stop, and not re-escalated.
    assert "work_item_retried" in types
    assert types.index("work_item_self_retry_requested") < types.index("work_item_retried")


def test_reattach_resumes_a_deferred_self_retry_from_a_resolved_from_file_session(
    tmp_path, monkeypatch
):
    """Kraft-atdbw's exact failure mode: an escalation session calls
    `kraft item retry` on itself, then exits while the server is down --
    so its pid is gone and it resolves from its result file instead of
    through the live-pid `_adopt` path. Before this fix that branch only
    ran `_exit_from_file` and never `resume_after_escalation`, leaving the
    self-retry request unconsumed: the item sat `needs_human`, and the
    `auto_escalate_delay` poller (which fires before any human runs
    `kraft item retry`) would dispatch a new paid turn on a stop the agent
    had already fixed, its cursor past the stale request.
    """
    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    async def fake_refresh(worktree, repo, branch):
        return None

    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)
    monkeypatch.setattr("kraft.builtins.refresh_worktree_base", fake_refresh)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            await database.write(
                lambda c: store.mark_needs_human(c, "w1", "implementation", "budget exhausted")
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="esc1",
                    work_item_id="w1",
                    node_id="implementation",
                    hook_point="escalation",
                    log_path=str(rd.logs / "esc1.log"),
                    result_path=str(rd.results / "esc1.json"),
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    "w1",
                    "escalation_message",
                    {"session_id": "esc1", "message": "please fix it", "auto": False},
                )
            )
            await database.write(
                lambda c: events.append(
                    c,
                    "w1",
                    "work_item_self_retry_requested",
                    {
                        "session_id": "esc1",
                        "node_id": "implementation",
                        "key": None,
                        "gate_key": None,
                        "steer": "fixed it",
                        "seeded": False,
                    },
                )
            )
            (rd.results / "esc1.json").write_text('{"status": "done"}')
            # A definitely-dead pid -- the session exited while the server
            # was down, so it resolves from the result file, not _adopt.
            await database.write(lambda c: store.session_running(c, "esc1", 2_000_000_000, 123.0))

            summary, adopted = await reattach.reattach(
                database, rd, _REG, launch_factory=lambda repo: None
            )
            assert summary.resolved_from_file == ["esc1"]
            await adopted["esc1"]

            evs = database.read(lambda c: events.read_after(c, 0, "w1"))
            return [e["type"] for e in evs]
        finally:
            await database.close()

    types = asyncio.run(scenario())
    assert len(walk_calls) == 1
    assert walk_calls[0]["steer"] == "fixed it"
    assert "work_item_retried" in types
    assert types.index("work_item_self_retry_requested") < types.index("work_item_retried")


def test_adopting_a_session_leaves_its_backgrounded_child_alone(tmp_path):
    """The one exception in §5: a session Kraft adopted after a restart is
    meant to outlive the process that launched it. Killing its group on
    adoption would undo the reattach feature outright."""

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        pidfile = tmp_path / "grandchild.pid"
        proc = subprocess.Popen(
            ["bash", "-c", f"sleep 30 & echo $! > {pidfile}; sleep 2"],
            start_new_session=True,
        )
        grandchild = None
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
            for _ in range(50):
                if pidfile.exists():
                    break
                await asyncio.sleep(0.1)
            grandchild = int(pidfile.read_text().strip())
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert summary.adopted == ["s1"]
            await adopted["s1"]
            proc.wait()
            os.kill(grandchild, 0)  # still alive: no ProcessLookupError
        finally:
            proc.wait()
            if grandchild:
                try:
                    os.kill(grandchild, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await database.close()

    asyncio.run(scenario())
