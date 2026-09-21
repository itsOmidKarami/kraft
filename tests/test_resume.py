from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path

from support.harness import isolated_bd, make_repo, v1_named_chain, v1_seeded_chain

from kraft import builtins, db, events, executor, store
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"
_FAKE = f"{sys.executable} {_FAKE_AGENT}"


def _quick_task(tmp_path):
    """The shipped gateless `quick-task`, its agent task on the fake agent."""
    return v1_named_chain(tmp_path / "templates", agent_command=_FAKE)


def _default_template(tmp_path):
    """The shipped `default` chain, its agent tasks on the fake agent."""
    return v1_named_chain(tmp_path / "templates", "default", agent_command=_FAKE)


async def _prepare(database, rd, repo, wid):
    """The worktree, cut the way V1 does before the first node (no env node)."""
    return await builtins.ensure_worktree(
        database, rd, repo=str(repo), work_item_id=wid, repo_entry=None
    )


def _types(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def _launch(tmp_path) -> executor.LaunchContext:
    # The shipped spec task names a `steering:` profile, which dispatch still
    # resolves to a file (`seed_v1_library` writes it out beside the library).
    return executor.LaunchContext(repo_entry=None, steering_dir=tmp_path / "templates" / "steering")


def test_resume_from_verify_with_env_and_impl_done(tmp_path, monkeypatch):
    """Chain crashed after `implementation` completed; resume runs only `verify`."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            # Simulate a partial run: implementation done, stopped before verify.
            await database.write(lambda c: store.load_chain(c, wid, "implementation"))
            # do the real implementation via run's node walker
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            wt = await _prepare(database, rd, repo, wid)
            impl_node = executor.chain_of(row).chain.nodes[0]
            assert await executor.walk_node(database, rd, wid, impl_node, row, wt) == "ok"
            # current_node_id now points at implementation (last enter_node). Move it to verify
            # the way a crash-recovery would NOT — instead leave it and call resume, which
            # should see implementation's session done and advance.
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                adopted={},
                bd_cwd=str(tracker),
            )
            assert result == "completed"
            assert "a + b" in (wt / "calc.py").read_text()
            t = _types(database, wid)
            assert t[-1] == "work_item_completed"
            # implementation once, then verify exactly once
            assert t.count("node_started") == 2
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_no_session_for_current_node_dispatches_fresh(tmp_path, monkeypatch):
    """Crash between enter_node and create_session -> resume re-dispatches the node fresh.

    V1 has no `env_setup` node; the worktree it used to cut is prepared before
    the first node, so the crash here lands on `implementation`, and its
    already-existing worktree must not break the fresh re-dispatch.
    """
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            wt = await _prepare(database, rd, repo, wid)
            impl_node = executor.chain_of(row).chain.nodes[0]
            await database.write(lambda c: store.load_chain(c, wid, "implementation"))
            await executor.walk_node(database, rd, wid, impl_node, row, wt)
            # Simulate a crash between enter_node(implementation) and its
            # create_session: drop its session rows and point current_node_id
            # back at it. The worktree must not break the fresh re-dispatch.
            await database.write(
                lambda c: c.execute(
                    "DELETE FROM worker_sessions WHERE work_item_id = ? "
                    "AND node_id = 'implementation'",
                    (wid,),
                )
            )
            await database.write(lambda c: store.enter_node(c, wid, "implementation"))
            assert wt.is_dir()  # worktree survived the "crash"
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=_quick_task(tmp_path),
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
                    hook_point="implementation.main.implement",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-impl", "failed"))
            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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
            # The card says which session and how it ended, not only that one
            # did not resolve (Task 6b review round 1).
            reason = [
                e
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "work_item_needs_human"
            ][-1]["payload"]["reason"]
            assert reason.startswith("resume: current-node session did not resolve cleanly")
            assert "implement: failed" in reason, reason
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_still_gives_a_node_the_repair_its_template_declared(tmp_path):
    """Kraft-rv6i. A crash between a node failing and its `on_failure` pass is
    exactly when the repair matters, and the session-count reconciliation would
    read it as "did not resolve cleanly" and stop for a human with the repair
    never tried."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    flag = tmp_path / "labelled"
    measure = shlex.join(
        [
            sys.executable,
            "-c",
            f"import pathlib,sys; sys.exit(0 if pathlib.Path({str(flag)!r}).exists() else 1)",
        ]
    )
    repair = shlex.join(
        [sys.executable, "-c", f"import pathlib; pathlib.Path({str(flag)!r}).touch()"]
    )
    chain = v1_seeded_chain(
        tmp_path / "templates",
        [
            {
                "id": "checks",
                "kind": "exec",
                "tasks": [{"id": "poll", "kind": "subprocess", "command": measure}],
                "on_failure": {"tasks": [{"id": "sync", "kind": "subprocess", "command": repair}]},
            }
        ],
        agent_command=_FAKE,
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="a red pipeline the server restarted under",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
            )
            await database.write(lambda c: store.load_chain(c, wid, "checks"))
            await database.write(lambda c: store.enter_node(c, wid, "checks"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-poll",
                    work_item_id=wid,
                    node_id="checks",
                    hook_point="checks.main.poll",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-poll", "failed"))

            result = await executor.resume(
                database, rd, work_item_id=wid, registry=None, adopted={}, bd_cwd=str(tracker)
            )
            return result, _types(database, wid)
        finally:
            await database.close()

    result, types = asyncio.run(scenario())

    assert "node_recovery_started" in types, "resume skipped the node's declared repair"
    assert result == "completed"


def test_resume_awaits_adopted_task_before_reading_status(tmp_path):
    """resume must await the adopted wait-task before re-reading session status."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=_quick_task(tmp_path),
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
                    hook_point="implementation.main.implement",
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
                registry=None,
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
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_default_template(tmp_path),
                bd_cwd=str(tracker),
            )
            # walk to the spec gate
            r = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=_launch(tmp_path),
            )
            assert r == "awaiting_gate"
            # approve it, then simulate a crash BEFORE the approve endpoint's run() spawns
            await database.write(lambda c: store.approve_gate(c, wid, "spec_approval"))
            wi = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            # A V1 gate is its own node, so the walk stands on it.
            assert wi["status"] == "active" and wi["current_node_id"] == "spec_approval"

            before = _types(database, wid)
            r2 = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                adopted={},
                bd_cwd=str(tracker),
                launch=_launch(tmp_path),
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
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=_default_template(tmp_path),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=_launch(tmp_path),
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
                registry=None,
                adopted={},
                bd_cwd=str(tracker),
                launch=_launch(tmp_path),
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
