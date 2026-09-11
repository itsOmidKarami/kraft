import asyncio
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft.paths import RunDirs
from kraft.templates import Template

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _registry():
    return fake_registry(sys.executable, _FAKE_AGENT)


def _fixloop_template() -> Template:
    # env_setup builds the worktree the fix agent + pytest run inside. There is NO
    # implementation node: verify's cycle 0 sees the sample repo's still-failing
    # test, so the fix loop is what does the fixing (via on.implementation.start).
    # Minimal shape that actually exercises the loop.
    return Template(
        id="fixloop",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "verify",
                "tasks": ["on.test.run"],
                "gate_after": None,
                "fix_loop": "verify_fix_loop",
            },
        ],
    )


def _types(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def _make_policy(tmp_path, *, attempts=3, wall_clock_s=3600) -> policy.Policy:
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"loops:\n  verify_fix_loop: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
        f"default: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
    )
    return policy.load_policy(p)


def test_fix_loop_succeeds_first_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path)
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "completed"
            types = _types(database, wid)
            # cycle 0 fails -> 1 fix task fixes calc.py -> re-measure passes
            assert types.count("fix_cycle_started") == 1
            row = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row["count"] == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fix_loop_cap_breach(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path, attempts=2)
            wid = await executor.intake(
                database,
                rd,
                title="never fixed",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "needs_human"
            types = _types(database, wid)
            assert types.count("fix_cycle_started") == 2
            wi = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert wi["status"] == "needs_human"
            assert wi["current_node_id"] == "verify"
            caps = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions WHERE work_item_id=? AND node_id='verify'",
                    (wid,),
                ).fetchall()
            )
            assert any(r["status"] == "capped_out" for r in caps)
            row = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row["count"] == 3  # attempts + 1, the breaching bump
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_mid_fix_loop_reenters_and_continues_budget(tmp_path, monkeypatch):
    """Crash-recovery on a fix_loop node that already ran >=1 cycle must re-enter
    the loop (not escalate), and the surviving counter row continues the budget."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path, attempts=5)
            wid = await executor.intake(
                database,
                rd,
                title="resume mid loop",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id=?", (wid,)).fetchone()
            )
            wt = rd.worktrees / wid
            # env_setup for real so the worktree pytest + the fix agent use exists
            await database.write(lambda c: store.load_chain(c, wid, "env_setup"))
            env_node = {"id": "env_setup", "tasks": ["on.env.prepare"], "fix_loop": None}
            assert await executor.walk_node(database, rd, wid, env_node, row, registry, wt) == "ok"

            # seed `verify` mid-fix-loop: a failed cycle-0 measure + a counter row at 1
            await database.write(lambda c: store.enter_node(c, wid, "verify"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-m0",
                    work_item_id=wid,
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="/l",
                    result_path="/r",
                )
            )
            await database.write(lambda c: store.session_exited(c, "s-m0", "failed"))
            cap = policy.resolve_cap(pol, "verify_fix_loop")
            n0, started0, _ = await database.write(
                lambda c: store.bump_counter(c, wid, "verify_fix_loop", cap)
            )
            assert n0 == 1

            result = await executor.resume(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                adopted={},
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "completed"  # loop re-entered; the fix agent fixed calc.py

            row2 = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row2["count"] == 2  # continued from the seeded 1, not reset
            assert row2["started_at"] == started0  # wall-clock anchor unchanged
            assert "work_item_needs_human" not in _types(database, wid)
            cycles = [
                e["payload"]["cycle"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "fix_cycle_started"
            ]
            assert cycles == [2]  # re-entered at cycle 2, not cycle 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fix_loop_wall_clock_breach(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    # executor.check() reads the current time via kraft.executor.walk's own
    # `_now` (a seam re-exported from store). Pin it far in the future so the
    # very first breach check trips on elapsed wall-clock, regardless of the
    # (large) attempts cap.
    monkeypatch.setattr("kraft.executor.walk._now", lambda: "2099-01-01T00:00:00+00:00")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path, attempts=99, wall_clock_s=1)
            wid = await executor.intake(
                database,
                rd,
                title="slow",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "needs_human"
            types = _types(database, wid)
            assert types.count("fix_cycle_started") == 0  # breached before any fix task
            row = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row["count"] == 1  # one bump, then the breach check
        finally:
            await database.close()

    asyncio.run(scenario())


def test_retry_after_cap_clears_the_budget_and_steers_cycle_one(tmp_path, monkeypatch):
    """4b: a capped loop is stopped for good until a human retries it. The retry
    starts a fresh budget and the human's note leads the first fix cycle."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_PROMPT_LOG", str(prompts))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path, attempts=2)
            wid = await executor.intake(
                database,
                rd,
                title="never fixed",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
            )
            # an agent that fixes nothing burns the whole budget
            monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
            assert (
                await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=registry,
                    bd_cwd=str(tracker),
                    policy=pol,
                )
                == "needs_human"
            )
            assert (
                database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop")) is not None
            )

            # a human retries with a note, and this time the agent fixes the code
            monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
            await database.write(
                lambda c: store.retry_after_cap(
                    c, wid, "verify", "verify_fix_loop", "the sign is flipped"
                )
            )
            assert database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop")) is None
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                start_index=1,
                steer="the sign is flipped",
            )
            assert result == "completed"
            return _types(database, wid)
        finally:
            await database.close()

    types = asyncio.run(scenario())
    assert "work_item_retried" in types
    assert types[-1] == "work_item_completed"

    sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
    steered = [p for p in sent if "the sign is flipped" in p]
    # exactly one: the steer leads cycle 1 of the retry and is not repeated after
    assert len(steered) == 1
    assert steered[0].startswith("A human has steered this run:")
    assert "Fix the code so they pass" in steered[0]
