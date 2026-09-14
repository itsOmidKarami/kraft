import asyncio
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft.paths import RunDirs
from kraft.templates import Registry, Template

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
        # Kraft-lpdd: this suite is about the fix loop's own cap, not the
        # unrelated auto-escalate trigger a `needs_human` cap breach would
        # otherwise also fire.
        "auto_escalate_stuck: false\n"
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
            # attempts=1, not 2: on.test.run's blind failure is now a stable
            # synthesized Finding (Kraft: findings.from_blind_failure), so
            # with a noop fix agent the identical fingerprint recurring at
            # round 1 now correctly trips the stuck-detector before a
            # 2-attempt cap would ever be reached -- that's the loop working
            # as intended, not a regression. attempts=1 breaches the cap on
            # the second bump, strictly before the stuck-check runs, so this
            # test still exercises the cap-breach path specifically rather
            # than the stuck path (both are `needs_human`, but for different
            # reasons, and this test is about the cap).
            pol = _make_policy(tmp_path, attempts=1)
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
            assert types.count("fix_cycle_started") == 1
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
            assert row["count"] == 2  # attempts + 1, the breaching bump
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fix_loop_per_item_attempts_override_breaches_before_policy_cap(tmp_path, monkeypatch):
    """A per-node `node_overrides` attempts value must be what `resolve_cap`
    actually applies, not just what's stored -- the policy default alone
    would let this item run 5 cycles; the override caps it at 1."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
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
                title="capped tighter than policy by this item's own override",
                repo=str(repo),
                template=_fixloop_template(),
                bd_cwd=str(tracker),
                node_overrides={"verify": {"attempts": 1}},
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
            assert types.count("fix_cycle_started") == 1
            row = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row["count"] == 2  # override attempts (1) + 1, the breaching bump
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
                lambda c: store.claim_for_run(c, wid, from_statuses=["needs_human"])
            )
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


def _repair_fixloop_template() -> Template:
    """A node with both `fix_loop` and `on_failure` (Kraft-cbr's Task 6/7
    shape): `n1` measures via a subprocess script whose behaviour a marker
    file in the worktree controls, so a test can script "repair fixes it",
    "repair doesn't", and "the error text changes" without a real forge or
    agent."""
    return Template(
        id="repair-fixloop",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "n1",
                "tasks": ["on.check"],
                "gate_after": None,
                "fix_loop": "n1_fix",
                "on_failure": ["on.repair"],
            },
        ],
    )


_CHECK_SCRIPT = (
    "import json, os, sys\n"
    "marker = 'marker.txt'\n"
    "count_path = 'count.txt'\n"
    "count = int(open(count_path).read()) if os.path.exists(count_path) else 0\n"
    "count += 1\n"
    "open(count_path, 'w').write(str(count))\n"
    "result_path = os.environ.get('KRAFT_RESULT_PATH')\n"
    "if os.path.exists(marker):\n"
    "    if result_path:\n"
    "        open(result_path, 'w').write(json.dumps({'status': 'done'}))\n"
    "    sys.exit(0)\n"
    "message = 'job test: failed\\n  AssertionError: expected 3, got 4'\n"
    "if count > 1:\n"
    "    message = 'job test: failed\\n  AssertionError: expected 3, got 5'\n"
    "if result_path:\n"
    "    open(result_path, 'w').write(json.dumps({'status': 'failed', 'findings': ["
    "{'severity': 'important', 'message': message, 'source_plugin': 'on.ci.poll'}]}))\n"
    "sys.exit(1)\n"
)

_REPAIR_SCRIPT_FIXES = "open('marker.txt', 'w').write('x')\n"
_REPAIR_SCRIPT_NOOP = "pass\n"


def _repair_fixloop_registry(repair_script: str) -> Registry:
    fake = f"{sys.executable} {_FAKE_AGENT}"
    return Registry(
        hooks={
            "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
            "on.check": {"kind": "subprocess", "command": [sys.executable, "-c", _CHECK_SCRIPT]},
            "on.repair": {"kind": "subprocess", "command": [sys.executable, "-c", repair_script]},
            # The fallthrough's ordinary paid fix cycle dispatches this --
            # "noop" so it never actually fixes anything, matching this
            # test's low attempts cap and letting the fix loop cap out.
            "on.implementation.start": {"kind": "agent", "command": fake},
        }
    )


def _run_repair_fixloop(tmp_path, repair_script: str, *, attempts=3, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _repair_fixloop_registry(repair_script)
            pol_path = tmp_path / "policy.yaml"
            pol_path.write_text(
                f"loops:\n  n1_fix: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
                f"default: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n"
                # Kraft-lpdd: this suite is about the fix loop's own cap, not
                # the unrelated auto-escalate trigger a `needs_human` cap
                # breach would otherwise also fire.
                "auto_escalate_stuck: false\n"
            )
            pol = policy.load_policy(pol_path)
            wid = await executor.intake(
                database,
                rd,
                title="repair before paid cycle",
                repo=str(repo),
                template=_repair_fixloop_template(),
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
            return result, database.read(lambda c: events.read_after(c, 0, wid))
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_ci_fix_loop_runs_on_failure_repair_before_the_first_paid_fix_cycle(tmp_path):
    """A node with both fix_loop and on_failure: the first failure gets one
    repair attempt, and if that repair's re-measure goes clean the node
    completes with zero fix_cycle_started events -- no agent turn spent."""
    result, evts = _run_repair_fixloop(tmp_path, _REPAIR_SCRIPT_FIXES)
    types = [e["type"] for e in evts]

    assert result == "completed"
    assert types.count("fix_cycle_started") == 0
    assert types.count("node_recovery_started") == 1


def test_ci_fix_loop_falls_through_to_a_paid_cycle_when_repair_does_not_take(tmp_path, monkeypatch):
    """Repair runs, re-measure still fails -- the ordinary fix cycle takes
    over from there. The paid fix cycle here is a no-op fake agent (nothing
    about who ultimately resolves it matters for this assertion), so the
    node caps out after its one allowed cycle."""
    result, evts = _run_repair_fixloop(
        tmp_path, _REPAIR_SCRIPT_NOOP, attempts=1, monkeypatch=monkeypatch
    )
    types = [e["type"] for e in evts]

    assert result == "needs_human"
    assert types.count("node_recovery_started") == 1
    # The repair's own re-measure ran (round=_REPAIR_ROUND) but is not counted
    # by store.bump_counter -- only the fallthrough's ordinary cap check is.
    assert len([e for e in evts if e["type"] == "work_item_needs_human"]) == 1


def test_ci_fix_loop_reads_the_repair_s_re_measure_not_the_stale_pre_repair_findings(
    tmp_path, monkeypatch
):
    """The regression this task exists to prevent: measuring task fails with
    finding A, on_failure's repair does not resolve it but the re-measure
    reports a *different* finding B (the repair partially edited something,
    or here just runs a second time and the script's own state moved on) --
    the `findings_measured` event recorded right after the fall-through must
    carry finding B, not the stale finding A from before the repair ran."""
    result, evts = _run_repair_fixloop(
        tmp_path, _REPAIR_SCRIPT_NOOP, attempts=1, monkeypatch=monkeypatch
    )

    measured = [e for e in evts if e["type"] == "findings_measured"]
    assert measured, "no findings_measured event was recorded"
    last = measured[-1]
    messages = [f["message"] for f in last["payload"]["findings"]]
    assert any("expected 3, got 5" in m for m in messages), messages
    assert not any("expected 3, got 4" in m for m in messages), messages
