from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, store
from kraft.paths import RunDirs
from kraft.templates import Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def _default_template() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["default"]


def _types(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def test_resume_from_verify_with_env_and_impl_done(tmp_path, monkeypatch):
    """Chain crashed after `implementation` completed; resume runs only `verify`."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            # Simulate a partial run: env_setup + implementation done, stopped before verify.
            await database.write(lambda c: store.load_chain(c, wid, "env_setup"))
            # do the real env_setup + implementation via run's node walker
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            wt = rd.worktrees / wid
            env_node = {"id": "env_setup", "tasks": ["on.env.prepare"]}
            impl_node = {"id": "implementation", "tasks": ["on.implementation.start"]}
            assert await executor._walk_node(database, rd, wid, env_node, row, registry, wt) == "ok"
            assert (
                await executor._walk_node(database, rd, wid, impl_node, row, registry, wt) == "ok"
            )
            # current_node_id now points at implementation (last enter_node). Move it to verify
            # the way a crash-recovery would NOT — instead leave it and call resume, which
            # should see implementation's session done and advance.
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
            )
            assert result == "completed"
            assert "a + b" in (wt / "calc.py").read_text()
            t = _types(database, wid)
            assert t[-1] == "work_item_completed"
            # verify ran exactly once
            assert t.count("node_started") == 3
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_no_session_for_current_node_dispatches_fresh(tmp_path, monkeypatch):
    """Crash between enter_node and create_session -> resume re-dispatches the node fresh.

    Exercises env_setup (the non-idempotent node) so its worktree-exists guard is covered.
    """
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            wt = rd.worktrees / wid
            env_node = {"id": "env_setup", "tasks": ["on.env.prepare"]}
            impl_node = {"id": "implementation", "tasks": ["on.implementation.start"]}
            await database.write(lambda c: store.load_chain(c, wid, "env_setup"))
            await executor._walk_node(database, rd, wid, env_node, row, registry, wt)
            await executor._walk_node(database, rd, wid, impl_node, row, registry, wt)
            # Simulate a crash between enter_node(env_setup) and its create_session:
            # drop env_setup's session rows and point current_node_id back at it.
            # The worktree it already created must not break the fresh re-dispatch.
            await database.write(
                lambda c: c.execute(
                    "DELETE FROM worker_sessions WHERE work_item_id = ? AND node_id = 'env_setup'",
                    (wid,),
                )
            )
            await database.write(lambda c: store.enter_node(c, wid, "env_setup"))
            assert wt.is_dir()  # worktree survived the "crash"
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
            )
            assert result == "completed"
            verify_sessions = database.read(
                lambda c: c.execute(
                    "SELECT node_id FROM worker_sessions "
                    "WHERE work_item_id = ? AND node_id = 'verify'",
                    (wid,),
                ).fetchall()
            )
            assert len(verify_sessions) == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_current_node_failed_session_is_needs_human(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            from kraft.templates import Registry

            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.implementation.start": {"kind": "agent", "command": "unused"},
                    "on.test.run": {"kind": "subprocess", "command": ["true"]},
                }
            )
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            await database.write(lambda c: store.load_chain(c, wid, "implementation"))
            await database.write(lambda c: store.enter_node(c, wid, "implementation"))
            # a session row already resolved 'failed'
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-impl",
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-impl", "failed"))
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
            )
            assert result == "needs_human"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["current_node_id"] == "implementation"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_awaits_adopted_task_before_reading_status(tmp_path):
    """resume must await the adopted wait-task before re-reading session status."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            from kraft.templates import Registry

            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.implementation.start": {"kind": "agent", "command": "unused"},
                    "on.test.run": {"kind": "subprocess", "command": ["true"]},
                }
            )
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            sid = "s-impl-running"
            (rd.worktrees / wid).mkdir(parents=True, exist_ok=True)
            await database.write(lambda c: store.load_chain(c, wid, "implementation"))
            await database.write(lambda c: store.enter_node(c, wid, "implementation"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id=sid,
                    work_item_id=wid,
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_running(c, sid, 4242, 1.0))

            async def wait_task():
                await asyncio.sleep(0.05)
                await database.write(lambda c: store.session_exited(c, sid, "done"))

            task = asyncio.create_task(wait_task())
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={sid: task},
                bd_cwd=str(tracker),
            )
            assert result == "completed"
            t = _types(database, wid)
            assert "node_completed" in t
            assert t[-1] == "work_item_completed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_after_gate_approval_does_not_re_request_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_default_template(),
                bd_cwd=str(tracker),
            )
            # walk to the spec gate
            r = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
            )
            assert r == "awaiting_gate"
            # approve it, then simulate a crash BEFORE the approve endpoint's run() spawns
            await database.write(lambda c: store.approve_gate(c, wid, "spec_approval"))
            wi = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert wi["status"] == "active" and wi["current_node_id"] == "spec"

            before = _types(database, wid)
            r2 = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
            )
            after = _types(database, wid)
            new_events = after[len(before) :]
            # the spec gate is NOT re-requested; the walk moves on to the plan gate
            assert "gate_requested" in new_events  # for plan_approval
            plan_gate = [
                e
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "gate_requested"
            ][-1]
            assert plan_gate["payload"]["gate"] == "plan_approval"
            # no duplicate node_completed for spec
            spec_completions = [
                e
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "node_completed" and e["payload"]["node_id"] == "spec"
            ]
            assert len(spec_completions) == 1
            assert r2 == "awaiting_gate"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_before_gate_approval_still_re_requests_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_default_template(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
            )
            # NOT approved. Force status back to active as a crash-mid-await would look?
            # No — a genuine await leaves status=needs_human, which reattach does not
            # resume. This case only matters if something set active without approving.
            # Simulate that pathological state:
            await database.write(
                lambda c: c.execute("UPDATE work_items SET status='active' WHERE id=?", (wid,))
            )
            r = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
            )
            assert r == "awaiting_gate"
            last_gate = [
                e
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "gate_requested"
            ][-1]
            assert last_gate["payload"]["gate"] == "spec_approval"
        finally:
            await database.close()

    asyncio.run(scenario())
