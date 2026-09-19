import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import _git, fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, store
from kraft.adapters import beads
from kraft.adapters import forge as _forge
from kraft.api import deps
from kraft.config import ConfigError, git_read
from kraft.paths import RunDirs
from kraft.templates import Registry, Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"

#: A repo that deliberately needs no preparation. Most tests here are about
#: chain walking, not environments.
NO_SETUP = {"setup_command": ""}


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def _bd_status(repo, bead_id):
    out = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)[0]["status"]


def _events(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def _argv_lines(path: Path) -> list[list[str]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def test_run_happy_path_completes_and_closes_bead(tmp_path, monkeypatch):
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
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"

            worktree = rd.worktrees / wid
            assert "a + b" in (worktree / "calc.py").read_text()

            verify = subprocess.run(
                ["python", "-m", "pytest", "-q"], cwd=worktree, capture_output=True, text=True
            )
            assert verify.returncode == 0

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "completed"
            assert _bd_status(tracker, row["bead_id"]) == "closed"

            types = _events(database, wid)
            assert types[0] == "work_item_created"
            assert types[1] == "chain_loaded"
            assert types[-1] == "work_item_completed"
            assert types.count("node_started") == 3
            assert types.count("node_completed") == 3
        finally:
            await database.close()

    asyncio.run(scenario())


def test_done_with_concerns_advances_the_chain(tmp_path, monkeypatch):
    """An agent reporting `done_with_concerns` is not a failure: the chain keeps
    walking exactly as it would for `done`."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_CONCERNS", "tests pass but the API contract feels off")
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
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "completed"

            impl_session = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions "
                    "WHERE work_item_id = ? AND node_id = 'implementation'",
                    (wid,),
                ).fetchone()
            )
            assert impl_session["status"] == "done_with_concerns"

            types = _events(database, wid)
            assert types[-1] == "work_item_completed"
            assert types.count("node_completed") == 3
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_verify_failure_stops_at_verify(tmp_path, monkeypatch):
    """C1 (Kraft-s7c04.8) had `implementation` run this same `on.test.run`
    gate directly after its own agent task, so this assertion moved to stop
    at `implementation` instead. C1 met neither of its own bead's acceptance
    criteria -- the gate ran after the session exited, so the agent never
    saw a failure, and `implementation` has no `fix_loop` to route one into
    -- so it is reverted (Kraft-s7c04.8 decision, 2026-09-16). This test
    stops at `verify` again."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # leave the bug in place
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
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id, bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["current_node_id"] == "verify"

            types = _events(database, wid)
            assert "work_item_needs_human" in types
            assert "work_item_completed" not in types
            # verify started but never completed
            assert types.count("node_started") == 3
            assert types.count("node_completed") == 2

            assert _bd_status(tracker, row["bead_id"]) in ("open", "in_progress")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_rate_limit_stops_the_chain_without_a_fix_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "rate_limit")
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
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "rate_limited"

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id, retry_at FROM work_items WHERE id = ?",
                    (wid,),
                ).fetchone()
            )
            assert row["status"] == "rate_limited"
            assert row["current_node_id"] == "implementation"
            assert row["retry_at"] == "2026-09-09T15:40:00+00:00"

            types = _events(database, wid)
            assert "rate_limit_hit" in types
            assert "work_item_rate_limited" in types
            # No fix loop, no repair task, no human page for this stop.
            assert "work_item_needs_human" not in types
            assert "node_recovery_started" not in types
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_waiting_task_marks_the_row_and_ends_the_run(tmp_path, monkeypatch):
    """The whole point: the executor task ends rather than blocking, and the row
    carries the wait (Kraft-ru98)."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    fake = _forge.FakeForge(ci_states=["pending"])
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": "fake"},
                }
            )
            tmpl = Template(
                id="ci-wait",
                nodes=[
                    {
                        "id": "env_setup",
                        "tasks": ["on.env.prepare"],
                        "gate_after": None,
                        "fix_loop": None,
                    },
                    {
                        "id": "mr_checks",
                        "tasks": ["on.ci.poll"],
                        "gate_after": None,
                        "fix_loop": None,
                    },
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "waiting"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "waiting"
            assert row["retry_at"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_needs_human_reason_names_a_failed_forge_task_s_kind(tmp_path, monkeypatch):
    """Kraft-5m7t: `on.ci.poll` is forge-kind, not an agent session -- a
    `retry --steer` against a node whose only failed task is this one has
    nowhere for the steer text to land. Naming the kind in the stop reason
    is the cheapest way a human (or `retry`'s own caller) can tell that
    before burning a retry on it."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    fake = _forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(_forge.FailedJob("test", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={"on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": "fake"}}
            )
            tmpl = Template(
                id="mr-checks-only",
                nodes=[
                    {
                        "id": "mr_checks",
                        "tasks": ["on.ci.poll"],
                        "gate_after": None,
                        "fix_loop": None,
                    }
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"
            stopped = next(
                e["payload"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "work_item_needs_human"
            )
            return stopped["reason"]
        finally:
            await database.close()

    reason = asyncio.run(scenario())
    assert "on.ci.poll [forge]" in reason


class _RaisingForge:
    """A forge whose `open_mr` fails with no findings to report -- the shape
    `ForgeError` takes in `run.py`: `findings = None`, so `finish_session`
    writes no result file at all (Kraft-s7c04.24's blind in-process
    failure). Every other method a test below might touch on is left
    unimplemented; nothing here reaches them."""

    async def find_mr(self, **kwargs):
        return None

    async def open_mr(self, **kwargs):
        raise _forge.ForgeError("boom: no capacity")


def _in_process_policy(tmp_path):
    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text("default: { attempts: 9, wall_clock_s: 3600 }\n")
    return policy.load_policy(pol_path)


def test_an_in_process_blind_failure_stops_without_opening_a_fix_cycle(tmp_path, monkeypatch):
    """.24: `on.mr.open` (`kind: forge`) fails with nothing a worker commit
    could ever change -- the handler runs in the orchestrator process, not
    the worktree. The node must stop for a human without spending a fix
    cycle: no `retry_counters` row for its loop key, no `fix_cycle_started`
    event, and a reason naming the in-process cause."""
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingForge())
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    pol = _in_process_policy(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": "fake"},
                    "on.implementation.start": {
                        "kind": "agent",
                        "command": f"{sys.executable} {_FAKE_AGENT}",
                    },
                }
            )
            tmpl = Template(
                id="mr-open-only",
                nodes=[
                    {
                        "id": "open_mr",
                        "tasks": ["on.mr.open"],
                        "gate_after": None,
                        "fix_loop": "open_mr_fix_loop",
                    }
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            reason = next(
                e["payload"]["reason"] for e in evts if e["type"] == "work_item_needs_human"
            )
            assert "fix_cycle_started" not in [e["type"] for e in evts]
            counter = database.read(lambda c: store.read_counter(c, wid, "open_mr_fix_loop"))
            return reason, counter
        finally:
            await database.close()

    reason, counter = asyncio.run(scenario())
    assert "on.mr.open [forge]" in reason
    assert "Reinstall" in reason
    assert counter is None, "the unwinnable check must not have bumped the loop counter"


def test_a_red_pipeline_with_failed_jobs_opens_its_fix_cycle_as_before(tmp_path, monkeypatch):
    """The healthy control -- the point of this task. A `kind: forge` task
    that fails *with* findings (a real red pipeline) is worker-fixable and
    must open its fix cycle exactly as before this change. Without this
    test the change is indistinguishable from the bead's wrong version,
    which would have refused every red-pipeline fix loop too."""
    fake = _forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(_forge.FailedJob("test", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    # attempts=1 and a FakeForge that reports red forever: the loop must
    # cap out after exactly one fix cycle, so the counter this test reads
    # is unambiguous rather than however many cycles a longer run buys.
    pol_path.write_text("default: { attempts: 1, wall_clock_s: 3600 }\n")
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": "fake"},
                    "on.implementation.start": {
                        "kind": "agent",
                        "command": f"{sys.executable} {_FAKE_AGENT}",
                    },
                }
            )
            tmpl = Template(
                id="mr-checks-only",
                nodes=[
                    {
                        "id": "mr_checks",
                        "tasks": ["on.ci.poll"],
                        "gate_after": None,
                        "fix_loop": "ci_fix_loop",
                    }
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            counter = database.read(lambda c: store.read_counter(c, wid, "ci_fix_loop"))
            return result, [e["type"] for e in evts], counter
        finally:
            await database.close()

    result, types, counter = asyncio.run(scenario())
    assert "fix_cycle_started" in types, "a real red pipeline must still open a fix cycle"
    # attempts=1: one fix cycle runs, the re-measure is still red (FakeForge
    # repeats its last state forever), and the next bump (count=2) breaches --
    # the counter existing and having bumped past 1 is what proves the loop
    # ran through the ordinary bump_counter path rather than being refused.
    assert counter is not None and counter["count"] == 2
    assert result == "needs_human"  # capped after its one allotted cycle -- not refused outright


def test_a_co_failing_subprocess_task_keeps_the_loop_open(tmp_path, monkeypatch):
    """A blind in-process failure alongside a co-failing `kind: subprocess`
    task (worker-fixable) must not be waved off as unwinnable -- the
    subprocess task alone justifies the cycle."""
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingForge())
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    pol = _in_process_policy(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": "fake"},
                    "on.test.run": {
                        "kind": "subprocess",
                        "command": [sys.executable, "-c", "import sys; sys.exit(1)"],
                    },
                    "on.implementation.start": {
                        "kind": "agent",
                        "command": f"{sys.executable} {_FAKE_AGENT}",
                    },
                }
            )
            tmpl = Template(
                id="co-failure",
                nodes=[
                    {
                        "id": "checks",
                        "tasks": ["on.mr.open", "on.test.run"],
                        "gate_after": None,
                        "fix_loop": "checks_fix_loop",
                    }
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            return [e["type"] for e in evts]
        finally:
            await database.close()

    types = asyncio.run(scenario())
    assert "fix_cycle_started" in types, "a co-failing worker-fixable task must keep the loop open"


def test_a_co_tasks_findings_keep_the_loop_open(tmp_path, monkeypatch):
    """A blind in-process failure alongside a co-task that reported real
    findings (a review, or the same node's own red pipeline) must not
    discard those findings by refusing the cycle."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_FINDING", "a real defect a worker can fix")
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingForge())
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    pol = _in_process_policy(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": "fake"},
                    "on.review.local.run": {
                        "kind": "agent",
                        "command": f"{sys.executable} {_FAKE_AGENT}",
                    },
                    "on.implementation.start": {
                        "kind": "agent",
                        "command": f"{sys.executable} {_FAKE_AGENT}",
                    },
                }
            )
            tmpl = Template(
                id="co-finding",
                nodes=[
                    {
                        "id": "checks",
                        "tasks": ["on.mr.open", "on.review.local.run"],
                        "gate_after": None,
                        "fix_loop": "checks_fix_loop",
                    }
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            return [e["type"] for e in evts]
        finally:
            await database.close()

    types = asyncio.run(scenario())
    assert "fix_cycle_started" in types, "a co-task's real findings must keep the loop open"


def test_a_pending_needs_context_question_outranks_the_unwinnable_stop(tmp_path, monkeypatch):
    """Ordering control (review finding): a question a fix agent has already
    asked must reach the human, even on a round where the *re-measure*
    looks unwinnable on its own. Placed after `needs_context_question` in
    `walk_node` for exactly this -- moved back above it, this test fails."""
    from kraft.executor import dispatch

    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingForge())
    monkeypatch.setattr(
        dispatch, "needs_context_question", lambda *a, **k: "should I use library X or Y?"
    )
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    pol = _in_process_policy(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={"on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": "fake"}}
            )
            tmpl = Template(
                id="mr-open-only",
                nodes=[
                    {
                        "id": "open_mr",
                        "tasks": ["on.mr.open"],
                        "gate_after": None,
                        "fix_loop": "open_mr_fix_loop",
                    }
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            reason = next(
                e["payload"]["reason"] for e in evts if e["type"] == "work_item_needs_human"
            )
            return reason
        finally:
            await database.close()

    reason = asyncio.run(scenario())
    assert "needs_context: should I use library X or Y?" in reason
    assert "Reinstall" not in reason


def test_a_steer_with_no_agent_dispatch_to_land_in_is_reported_undelivered(tmp_path, monkeypatch):
    """Kraft-s7c04.50: `Steer` is good for one agent launch, whichever
    dispatch gets there first -- but if this run_once call's only task is
    `kind: forge` (no prompt for a steer to land in at all), it ends
    `needs_human` having never had anywhere to deliver it. That must be
    visible as an event, not just quietly lost (the same shape as the live
    incident on acf59aafa6bb4512bd68049705414fc7, where a steer given at a
    subprocess-only round vanished with the restart that followed)."""
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    fake = _forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(_forge.FailedJob("test", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={"on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": "fake"}}
            )
            tmpl = Template(
                id="mr-checks-only",
                nodes=[
                    {
                        "id": "mr_checks",
                        "tasks": ["on.ci.poll"],
                        "gate_after": None,
                        "fix_loop": None,
                    }
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                steer="please look at the flaky pipeline",
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"
            return [
                e["payload"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "steer_undelivered"
            ]
        finally:
            await database.close()

    fired = asyncio.run(scenario())
    assert len(fired) == 1
    assert fired[0]["steer"] == "please look at the flaky pipeline"


def test_a_steer_taken_by_a_real_agent_is_not_reported_undelivered(tmp_path, monkeypatch):
    """Control for the test above: a steer that reaches an actual agent
    dispatch must not also fire `steer_undelivered` -- the delivery path
    itself works (verified live); this only guards the surfacing check
    against false positives on the common, healthy case."""
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
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                steer="a note for whichever agent runs first",
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
            return _events(database, wid)
        finally:
            await database.close()

    types = asyncio.run(scenario())
    assert "steer_undelivered" not in types


def test_dispatch_routes_on_mr_rebase_to_the_mr_rebase_builtin(tmp_path, monkeypatch):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    from kraft.templates import Registry, Template

    registry = Registry(
        hooks={
            "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
            "on.mr.rebase": {"kind": "builtin", "handler": "mr_rebase"},
        }
    )
    tmpl = Template(
        id="rebase_only",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
            {"id": "pre_mr_rebase", "tasks": ["on.mr.rebase"], "gate_after": None},
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
            session = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions WHERE hook_point = 'on.mr.rebase'"
                ).fetchone()
            )
            assert session["status"] == "done"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_rebase_drift_note_names_commits_and_files(tmp_path):
    from kraft.executor.prompts import rebase_drift_note

    repo = make_repo(tmp_path)
    old_base = git_read(repo, "rev-parse", "HEAD")
    (repo / "moved.txt").write_text("moved on\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "moved on upstream")
    new_base = git_read(repo, "rev-parse", "HEAD")

    note = rebase_drift_note(repo, "kraft/some-branch", old_base, new_base)
    assert "kraft/some-branch" in note
    assert "moved on upstream" in note
    assert "moved.txt" in note


def test_rebase_drift_note_truncates_a_long_diff(tmp_path):
    from kraft.executor.prompts import _REBASE_NOTE_MAX, rebase_drift_note

    repo = make_repo(tmp_path)
    old_base = git_read(repo, "rev-parse", "HEAD")
    for i in range(200):
        (repo / f"file_{i}.txt").write_text(f"content {i}\n" * 20)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "a very large upstream change")
    new_base = git_read(repo, "rev-parse", "HEAD")

    note = rebase_drift_note(repo, "kraft/some-branch", old_base, new_base)
    assert len(note) < _REBASE_NOTE_MAX + 500  # template text plus the capped body
    assert "(truncated)" in note


def test_run_unknown_hook_in_registry_is_needs_human(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(
                hooks={
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                }
            )
            tmpl = Template(
                id="bogus",
                nodes=[
                    {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
                    {"id": "work", "tasks": ["on.bogus"], "gate_after": None},
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "needs_human"

            evts = database.read(lambda c: events.read_after(c, 0, wid))
            types = [e["type"] for e in evts]
            assert "work_item_needs_human" in types
            assert "work_item_completed" not in types
            # the raised exception's repr is preserved in the needs_human reason
            reason = next(
                e["payload"]["reason"] for e in evts if e["type"] == "work_item_needs_human"
            )
            assert "on.bogus" in reason and "KeyError" in reason
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fix_cycle_dispatch_gets_the_same_launch_context(tmp_path, monkeypatch):
    """The fix cycle's `dispatch_node` (executor's fix-cycle call site) is
    separate from the measuring `dispatch_node` inside `measure_node` --
    missing it means the fix agent silently runs on a different model than
    the one that measured."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    from kraft import policy
    from kraft.templates import Registry

    registry_base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={
            **registry_base.hooks,
            # verify's own task fails every cycle without ever calling the fake
            # agent, so the only agent launch in this run is the fix cycle's.
            "on.test.run": {"kind": "subprocess", "command": [sys.executable, "-c", "exit(1)"]},
        }
    )
    tmpl = Template(
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
    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  verify_fix_loop: { attempts: 1, wall_clock_s: 3600 }\n"
        "default: { attempts: 1, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="fix cycle model",
                repo=str(repo),
                template=tmpl,
                bd_cwd=str(tracker),
            )
            launch = executor.LaunchContext(
                repo_entry={"default_model": "haiku", "setup_command": ""}, steering_dir=None
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    # The fix-loop judge shares the same fake agent (fake_registry() binds
    # both) and is asked once between the free first cycle and the cap
    # catching it on the next -- filtered out here since this test is about
    # the *fix* cycle's launch context, not the judge's.
    argvs = [
        a for a in _argv_lines(argv_log) if "is about to spend another cycle" not in " ".join(a)
    ]
    assert len(argvs) == 1  # only the fix cycle ever launches the fake agent
    assert argvs[0][argvs[0].index("--model") + 1] == "haiku"


def test_run_once_threads_local_files_from_the_launch_context(tmp_path, monkeypatch):
    """Kraft-gxcmy's production wiring: `run_once` (`walk.py:1184`) passes
    `launch.repo_entry["local_files"]` into `ensure_worktree`. Every other
    `local_files` test drives `ensure_worktree` directly -- not the path that
    actually runs in production -- so deleting that one line left the whole
    suite green. This test fails if it is."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")

    tmpl = Template(
        id="env-only",
        nodes=[{"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None}],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=tmpl, bd_cwd=str(tracker)
            )
            launch = executor.LaunchContext(
                repo_entry={"local_files": [".python-version"], "setup_command": ""},
                steering_dir=None,
            )
            result = await executor.run_once(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=launch,
            )
            assert result == "completed"
            assert (rd.worktrees / wid / ".python-version").read_text() == "3.11\n"
        finally:
            await database.close()

    asyncio.run(scenario())


# --- rebase-and-drift-review before open_mr (Kraft-4bgg) --------------------


def _rebase_chain_registry(tmp_path):
    argv_log = tmp_path / "argv.jsonl"
    base = fake_registry(sys.executable, _FAKE_AGENT)
    registry = Registry(
        hooks={
            **base.hooks,
            "on.test.run": {"kind": "subprocess", "command": [sys.executable, "-c", "exit(0)"]},
            "on.review.local.run": {"kind": "agent", "command": f"{sys.executable} {_FAKE_AGENT}"},
        }
    )
    return registry, argv_log


def _rebase_chain_template(*, verify_fix_loop=False, extra_node=None):
    """`extra_node`, when given, sits between `verify` and `pre_mr_rebase` --
    an intervening node (e.g. `mr_checks`) that also declares
    `rebase_bounce_to: verify`, so a bounce from `pre_mr_rebase` must clear
    its counter too, not just `verify`'s (Kraft-s7c04.25 spec §2)."""
    nodes = [
        {
            "id": "verify",
            "tasks": ["on.test.run", "on.review.local.run"],
            "gate_after": None,
            "fix_loop": "verify_fix_loop" if verify_fix_loop else None,
        },
    ]
    if extra_node is not None:
        nodes.append(extra_node)
    nodes.append(
        {
            "id": "pre_mr_rebase",
            "tasks": ["on.mr.rebase"],
            "gate_after": None,
            "rebase_bounce_to": "verify",
        }
    )
    nodes.append({"id": "open_mr", "tasks": ["on.mr.open"], "gate_after": None})
    return Template(id="rebase_drift", nodes=nodes)


def _gitignore_engineering(repo):
    """`refresh_worktree_base`'s dirty check (unmodified by this plan) is a
    plain `git status --porcelain`, unlike `_assert_clean`'s pathspec-excluded
    one -- it has no way to know `.engineering/sessions/*.md` is Kraft's own
    bookkeeping rather than the agent's work. A connected repo that has never
    heard of Kraft is exactly as likely to already ignore it (many do) as not;
    fixture repos need to say so explicitly to exercise the moved-branch path
    rather than always hitting the "worktree has uncommitted changes" skip.
    """
    (repo / ".gitignore").write_text(".engineering/\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "gitignore .engineering"], cwd=repo, check=True)


def test_a_moved_base_bounces_back_to_verify_with_a_drift_note(tmp_path, monkeypatch):
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)
    registry, argv_log = _rebase_chain_registry(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 2, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="bounce me",
                repo=str(repo),
                template=_rebase_chain_template(),
                bd_cwd=str(tracker),
            )
            from kraft import builtins as kraft_builtins

            await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
            )

            (repo / "moved.txt").write_text("moved on\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "moved on upstream"], cwd=repo, check=True)
            new_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"

            row = database.read(
                lambda c: c.execute(
                    "SELECT base_ref FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["base_ref"] == new_head

            starts = [
                e["payload"]["node_id"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "node_started"
            ]
            # This task's template (`_rebase_chain_template`) has no `env_setup`
            # node -- `ensure_worktree` is called directly above, not through one.
            assert starts == [
                "verify",
                "pre_mr_rebase",
                "verify",
                "pre_mr_rebase",
                "open_mr",
            ]
        finally:
            await database.close()

    asyncio.run(scenario())

    argvs = _argv_lines(argv_log)
    assert len(argvs) == 2  # on.review.local.run runs once per verify entry
    first_instruction = argvs[0][argvs[0].index("-p") + 1]
    second_instruction = argvs[1][argvs[1].index("-p") + 1]
    assert "moved on upstream" not in first_instruction
    assert "moved on upstream" in second_instruction
    assert "moved.txt" in second_instruction


def test_a_bounce_clears_the_targets_fix_loop_counter(tmp_path, monkeypatch):
    """.25: a bounce back to `verify` must not leave `verify`'s own
    `verify_fix_loop` counter pointing at a clock from before the bounce --
    43717ee6 priced a judge stop at "3858s of a 3600s wall-clock cap" 3
    seconds after a bounce that had spent none of it. Asserted on the
    counter row's absence, not a recomputed elapsed time, which would pass
    trivially on a fast test machine whether or not the row was cleared."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)
    registry, argv_log = _rebase_chain_registry(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 2, wall_clock_s: 3600 }\n"
        "  verify_fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="bounce clears counters",
                repo=str(repo),
                template=_rebase_chain_template(verify_fix_loop=True),
                bd_cwd=str(tracker),
            )
            from kraft import builtins as kraft_builtins

            await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
            )

            # A stale counter left over from an earlier, unrelated pass over
            # `verify` -- what a bounce must not let the re-entered loop inherit.
            cap = policy.Cap(attempts=3, wall_clock_s=3600)
            await database.write(lambda c: store.bump_counter(c, wid, "verify_fix_loop", cap))
            assert (
                database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop")) is not None
            )

            (repo / "moved.txt").write_text("moved on\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "moved on upstream"], cwd=repo, check=True)

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
            assert database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop")) is None, (
                "verify_fix_loop counter survived the bounce"
            )
            reset = next(
                e
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "loop_counters_reset"
            )
            assert "verify" in reset["payload"]["nodes"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_bounce_clears_an_intervening_nodes_fix_loop_too(tmp_path, monkeypatch):
    """The span, not just the target (human's call at the design gate): a
    bounce from `pre_mr_rebase` back to `verify` re-runs every node between
    them too, and an intervening node's own fix-loop counter (e.g.
    `mr_checks`' `ci_fix_loop`) is exactly as stale as `verify`'s."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)
    registry, argv_log = _rebase_chain_registry(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 2, wall_clock_s: 3600 }\n"
        "  verify_fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "  ci_fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="bounce clears the span",
                repo=str(repo),
                template=_rebase_chain_template(
                    verify_fix_loop=True,
                    extra_node={
                        "id": "mr_checks",
                        "tasks": ["on.ci.poll"],
                        "gate_after": None,
                        "fix_loop": "ci_fix_loop",
                    },
                ),
                bd_cwd=str(tracker),
            )
            from kraft import builtins as kraft_builtins

            await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
            )

            cap = policy.Cap(attempts=3, wall_clock_s=3600)
            await database.write(lambda c: store.bump_counter(c, wid, "verify_fix_loop", cap))
            await database.write(lambda c: store.bump_counter(c, wid, "ci_fix_loop", cap))

            (repo / "moved.txt").write_text("moved on\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "moved on upstream"], cwd=repo, check=True)

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
            assert database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop")) is None
            assert database.read(lambda c: store.read_counter(c, wid, "ci_fix_loop")) is None, (
                "the intervening node's fix-loop counter survived the bounce"
            )
            reset = next(
                e
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "loop_counters_reset"
            )
            assert set(reset["payload"]["nodes"]) >= {"verify", "mr_checks"}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_pre_mr_rebase_conflict_dispatches_resolver_and_bounces_to_verify(tmp_path, monkeypatch):
    """.23's node door: `on.mr.rebase` raising `RebaseConflict` inside
    `walk_node`'s no-fix_loop branch must dispatch the resolver rather than
    stopping outright -- and on a resolving verdict, `run_once`'s existing
    `rebase_bounce_to` machinery carries the walk back to `verify` with no
    special casing (design §4.8's whole point for this door)."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "rebase")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)
    registry, argv_log = _rebase_chain_registry(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 2, wall_clock_s: 3600 }\n"
        "  rebase_conflict: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            from kraft import builtins as kraft_builtins

            wid = await executor.intake(
                database,
                rd,
                title="conflict at pre_mr_rebase",
                repo=str(repo),
                template=_rebase_chain_template(),
                bd_cwd=str(tracker),
            )
            worktree = await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
            )

            # A real conflict: the worktree and the (now-upstream) repo both
            # touch the same line, mirroring test_builtins.py's fixture.
            (worktree / "calc.py").write_text(
                "def add(a, b):\n    return a - b - 1  # bug: should be +\n"
            )
            _git(worktree, "add", "-A")
            _git(worktree, "commit", "-m", "worktree edit")
            (repo / "calc.py").write_text(
                "def add(a, b):\n    return a - b - 2  # bug: should be +\n"
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "conflicting edit upstream")

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            starts = [e["payload"]["node_id"] for e in evts if e["type"] == "node_started"]
            return result, starts
        finally:
            await database.close()

    result, starts = asyncio.run(scenario())
    assert result == "completed"
    assert starts.count("verify") == 2, "did not bounce back to verify after the resolver"
    assert starts[-1] in ("pre_mr_rebase", "open_mr")


def test_no_movement_skips_the_bounce(tmp_path, monkeypatch):
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)
    registry, argv_log = _rebase_chain_registry(tmp_path)
    monkeypatch.setenv("KRAFT_FAKE_AGENT_ARGV_LOG", str(argv_log))

    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 2, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="no movement",
                repo=str(repo),
                template=_rebase_chain_template(),
                bd_cwd=str(tracker),
            )
            from kraft import builtins as kraft_builtins

            await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
            )

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"

            starts = [
                e["payload"]["node_id"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "node_started"
            ]
            assert starts == ["verify", "pre_mr_rebase", "open_mr"]
        finally:
            await database.close()

    asyncio.run(scenario())
    assert len(_argv_lines(argv_log)) == 1  # verify ran exactly once


def test_rebase_bounce_cap_escalates_to_needs_human(tmp_path, monkeypatch):
    """Forces repeated bounces deterministically: verify's own on.test.run task
    also advances `repo`, so pre_mr_rebase finds something to rebase every
    single time it re-checks -- no timing, no async interleaving needed."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)
    monkeypatch.setenv("KRAFT_UPSTREAM_REPO", str(repo))

    base = fake_registry(sys.executable, _FAKE_AGENT)
    advance_upstream = (
        "import os, subprocess; "
        "r = os.environ['KRAFT_UPSTREAM_REPO']; "
        "subprocess.run(['git', 'commit', '--allow-empty', '-m', 'moved again'], "
        "cwd=r, check=True)"
    )
    registry = Registry(
        hooks={
            **base.hooks,
            "on.test.run": {
                "kind": "subprocess",
                "command": [sys.executable, "-c", advance_upstream],
            },
        }
    )

    from kraft import policy

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 1, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
        # Kraft-lpdd: this test is about the rebase-bounce cap escalating to
        # needs_human, not the unrelated auto-escalate trigger that stop
        # would otherwise also fire.
        "auto_escalate_stuck: false\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="thrash",
                repo=str(repo),
                template=_rebase_chain_template(),
                bd_cwd=str(tracker),
            )
            from kraft import builtins as kraft_builtins

            await kraft_builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
            )

            # one commit before the run starts, so the first pre_mr_rebase
            # entry already has something to rebase
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "moved once"], cwd=repo, check=True
            )

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_closes_an_auto_intaken_bead_in_its_own_workspace(tmp_path, monkeypatch):
    """Auto-intake adopts a bead that already lives in its repo's own `.beads`
    workspace, not the instance-wide tracker `bd_cwd` points at. Closing it in
    `bd_cwd` fails: the id does not exist there (Kraft-8mu.5.2)."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    other = isolated_bd(tmp_path, name="other")
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            bead_id = await beads.intake("make the failing test pass", cwd=str(other))
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
                bead_id=bead_id,
                bead_cwd=str(other),
            )
            assert (
                await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=registry,
                    bd_cwd=str(tracker),
                    launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
                )
                == "completed"
            )
            assert _bd_status(other, bead_id) == "closed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_the_first_node_runs_before_env_setup_and_still_has_a_worktree(tmp_path):
    """default.yaml puts `spec` first and `env_setup` fourth, so the executor —
    not the env_setup node — is what guarantees the first task has a checkout
    to run in (Kraft-bmp)."""
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = Registry(hooks={"on.spec.requested": {"kind": "builtin", "handler": "noop"}})
            chain = {
                "template_id": "t",
                "nodes": [
                    {
                        "id": "spec",
                        "tasks": ["on.spec.requested"],
                        "gate_after": None,
                        "fix_loop": None,
                    }
                ],
            }
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id="B",
                    title="t",
                    repo=str(repo),
                    chain_template="t",
                    chain_definition=json.dumps(chain),
                )
            )
            await executor.run(
                database,
                rd,
                work_item_id="w1",
                registry=registry,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert (rd.worktrees / "w1").is_dir()
        finally:
            await database.close()

    asyncio.run(scenario())


def test_needs_human_names_the_session_that_failed(tmp_path, monkeypatch):
    """Kraft-eh6p. `work_item_needs_human` is the event a human lands on, and
    its reason names the hook ('task failed in node open_mr: on.mr.open'), not
    the failure — which lives in the failed session's log. Without the session
    id on this event the timeline has nothing to hang a 'view log' button on,
    and the only route to the reason is noticing the preceding
    worker_session_exited row."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "error")
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
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            assert (
                await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=fake_registry(sys.executable, _FAKE_AGENT),
                    bd_cwd=str(tracker),
                    launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
                )
                == "needs_human"
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            stopped = next(e for e in evts if e["type"] == "work_item_needs_human")
            failed = [
                e["payload"]["session_id"]
                for e in evts
                if e["type"] == "worker_session_exited" and e["payload"]["status"] == "failed"
            ]
            return stopped["payload"], failed
        finally:
            await database.close()

    payload, failed = asyncio.run(scenario())

    assert failed, "the scenario did not produce a failed session"
    assert payload.get("session_id") == failed[-1], (
        "needs_human does not name the session whose log holds the reason"
    )


def _gate_check(flag: Path) -> list[str]:
    """A command that fails until `flag` exists — a stand-in for `on.ci.poll`
    against a pipeline that is red for a reason outside the code."""
    return [
        sys.executable,
        "-c",
        f"import pathlib, sys; sys.exit(0 if pathlib.Path({str(flag)!r}).exists() else 1)",
    ]


def _touch(flag: Path) -> list[str]:
    return [sys.executable, "-c", f"import pathlib; pathlib.Path({str(flag)!r}).touch()"]


def _run_one_node(tmp_path, template: Template, registry: Registry) -> tuple[str, list[dict]]:
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="a pipeline that is red for a reason outside the code",
                repo=str(repo),
                template=template,
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            return result, database.read(lambda c: events.read_after(c, 0, wid))
        finally:
            await database.close()

    return asyncio.run(scenario())


def _recovering(measure: list[str], repair: list[str]) -> tuple[Template, Registry]:
    """A one-node chain whose node measures itself, and repairs on failure."""
    return (
        Template(
            id="recovering",
            nodes=[{"id": "checks", "tasks": ["on.ci.poll"], "on_failure": ["on.mr.sync"]}],
        ),
        Registry(
            hooks={
                "on.ci.poll": {"kind": "subprocess", "command": measure},
                "on.mr.sync": {"kind": "subprocess", "command": repair},
            }
        ),
    )


def test_a_failed_node_repairs_itself_and_measures_again(tmp_path):
    """Kraft-rv6i. A node's tasks run concurrently, so nothing in the node can
    react to what another task in it found, and there was no step at all
    between a task failing and the item dropping to needs_human. With
    `on_failure` the node gets one repair pass — believed only because the
    node's own task passes on the re-measure."""
    flag = tmp_path / "labelled"
    result, evts = _run_one_node(tmp_path, *_recovering(_gate_check(flag), _touch(flag)))

    assert result == "completed", "a repaired node did not carry the chain to the end"
    assert [e["type"] for e in evts].count("node_recovery_started") == 1
    recovery = next(e for e in evts if e["type"] == "node_recovery_started")
    assert recovery["payload"] == {
        "node_id": "checks",
        "failed_tasks": ["on.ci.poll"],
        "tasks": ["on.mr.sync"],
    }
    assert not [e for e in evts if e["type"] == "work_item_needs_human"]


def test_a_repair_that_did_not_take_still_stops_for_a_human(tmp_path):
    """The re-measure is the point: a repair task exiting 0 is not evidence
    that the thing it was repairing is fixed."""
    flag = tmp_path / "never-written"
    result, evts = _run_one_node(
        tmp_path, *_recovering(_gate_check(flag), [sys.executable, "-c", "pass"])
    )

    assert result == "needs_human"
    assert [e["type"] for e in evts].count("node_recovery_started") == 1, (
        "the repair pass ran more than once for one entry into the node"
    )
    stopped = next(e for e in evts if e["type"] == "work_item_needs_human")
    assert "after on_failure" in stopped["payload"]["reason"], (
        "the stop does not say a repair was already tried"
    )


def test_a_node_without_on_failure_stops_exactly_as_before(tmp_path):
    """The repair pass is opt-in per node: a chain that declares no
    `on_failure` must not gain a second measurement or a recovery event."""
    flag = tmp_path / "never-written"
    result, evts = _run_one_node(
        tmp_path,
        Template(id="plain", nodes=[{"id": "checks", "tasks": ["on.ci.poll"]}]),
        Registry(hooks={"on.ci.poll": {"kind": "subprocess", "command": _gate_check(flag)}}),
    )

    assert result == "needs_human"
    assert not [e for e in evts if e["type"] == "node_recovery_started"]
    stopped = next(e for e in evts if e["type"] == "work_item_needs_human")
    assert "after on_failure" not in stopped["payload"]["reason"]


def test_a_node_dict_lacking_on_failure_never_triggers_recovery(tmp_path):
    """Kraft-o33x: `chain_definition` is a JSON snapshot taken once at intake
    (`templates.materialize`), stored on the work item row and never
    re-read from the current `templates/default.yaml` on disk. An item
    created before `on_failure: [on.mr_checks.repair]` was added to
    `mr_checks` has a stored node dict with no `on_failure` key at all --
    `node.get("on_failure")` is None for it, by construction, for the
    lifetime of that item, and `walk_node`'s `if node.get("on_failure"):`
    guard is exactly the same check whether the key is missing or
    explicitly `null`. This spec's own `ci_fix_loop` addition (Task 6) has
    the identical exposure: an item whose `chain_definition` predates it
    keeps running the old `mr_checks` node (no `fix_loop`, no
    `rebase_bounce_to` either) until a `set-chain-template` or a chain_review
    gate splice backfills it."""
    flag = tmp_path / "never-written"
    result, evts = _run_one_node(
        tmp_path,
        # No "on_failure" key at all -- not even None -- the exact shape a
        # pre-Kraft-o33x item's stored chain_definition carries for mr_checks.
        Template(id="stale-chain", nodes=[{"id": "mr_checks", "tasks": ["on.ci.poll"]}]),
        Registry(hooks={"on.ci.poll": {"kind": "subprocess", "command": _gate_check(flag)}}),
    )

    assert result == "needs_human"
    assert not [e for e in evts if e["type"] == "node_recovery_started"], (
        "recover_node ran even though the stored node dict has no on_failure"
    )


async def _call_recover_node(tmp_path, monkeypatch, *, measured_round, steer, collect_at):
    """Drives the real `recover_node` with `dispatch.measure_node` and
    `dispatch.collect_findings` stubbed out, so the test pins exactly what
    `recover_node` builds and hands to the repair hook's `Steer` -- without
    paying for a real agent invocation. `collect_at` maps round -> findings
    list, modelling `dispatch.collect_findings`'s real signature.

    Returns the `steer` kwarg the repair hook's `measure_node` call received.
    """
    from kraft import policy
    from kraft.executor import dispatch, walk

    calls: list = []

    async def fake_measure(*args, steer=None, **kwargs):
        calls.append(steer)
        return "ok", [], []

    def fake_collect(db, wid, node, round, registry):
        return collect_at.get(round, []), set()

    monkeypatch.setattr(dispatch, "measure_node", fake_measure)
    monkeypatch.setattr(dispatch, "collect_findings", fake_collect)

    node = {"id": "checks", "tasks": ["on.ci.poll"], "on_failure": ["on.mr.sync"]}
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    try:
        await database.write(
            lambda c: store.create_work_item(
                c,
                id="wi",
                bead_id="B",
                title="t",
                repo=str(tmp_path),
                chain_template="t",
                chain_definition=json.dumps({"template_id": "t", "nodes": [node]}),
            )
        )
        row = database.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = 'wi'").fetchone()
        )
        await walk.recover_node(
            database,
            rd,
            "wi",
            node,
            row,
            Registry(hooks={}),
            tmp_path,
            failed=["on.ci.poll"],
            steer=steer,
            launch=None,
            budget=policy.NO_BUDGET,
            measured_round=measured_round,
        )
    finally:
        await database.close()
    return calls[0]


def test_recover_node_seeds_the_repair_with_the_measured_rounds_findings(tmp_path, monkeypatch):
    """.26: the repair agent used to be handed nothing but 'this failed' --
    the diagnosis lived only in the failed session's own log, with no path
    given to it (6c712ea8: 4 of its 16 tool calls were spent hunting for
    one). `recover_node` must read `measured_round`'s findings via
    `dispatch.collect_findings` and seed them into the repair hook's prompt."""
    from kraft import findings as _findings

    finding = _findings.Finding(
        severity="critical",
        message="on.ci.poll failed; no output captured.\n\nReproduce with: pytest -k x",
        file=None,
        line=None,
        source_plugin="on.ci.poll",
    )

    repair_steer = asyncio.run(
        _call_recover_node(
            tmp_path, monkeypatch, measured_round=3, steer=None, collect_at={3: [finding]}
        )
    )

    assert repair_steer is not None and repair_steer, "no seeded note reached the repair hook"
    text = repair_steer.take()
    assert "Reproduce with: pytest -k x" in text
    assert finding.message.split("\n")[0] in text


def test_recover_node_reads_findings_from_the_round_the_failure_measured_at(tmp_path, monkeypatch):
    """The round `recover_node` must read is the round the *failing*
    measurement ran at, not `recover_node`'s own dispatch round -- passing
    the wrong one reads a stale or empty round and seeds nothing (or seeds
    a previous round's stale findings). Pinned by giving round 2 findings
    and round 5 none, and asking `recover_node` for round 5: an
    implementation that read the wrong round would still find round 2's
    findings and pass this test's sibling above for the wrong reason."""
    from kraft import findings as _findings

    stale = _findings.Finding(
        severity="critical",
        message="stale round 2 finding",
        file=None,
        line=None,
        source_plugin="on.ci.poll",
    )

    repair_steer = asyncio.run(
        _call_recover_node(
            tmp_path, monkeypatch, measured_round=5, steer=None, collect_at={2: [stale]}
        )
    )

    assert "stale round 2 finding" not in repair_steer.take(), (
        "recover_node seeded a finding from the wrong round"
    )


def test_recover_node_merges_an_incoming_human_steer_ahead_of_the_seeded_note(
    tmp_path, monkeypatch
):
    """A person's own instruction leads and keeps its own `source`, so a
    fix-loop judge exemption and the human prompt template both survive the
    merge -- the seeded half is self-labelling text, not a relabelling of
    the human's."""
    from kraft import findings as _findings
    from kraft.executor.context import Steer

    finding = _findings.Finding(
        severity="critical",
        message="the ci failure",
        file=None,
        line=None,
        source_plugin="on.ci.poll",
    )
    incoming = Steer("focus on the auth module", source="human")

    repair_steer = asyncio.run(
        _call_recover_node(
            tmp_path, monkeypatch, measured_round=1, steer=incoming, collect_at={1: [finding]}
        )
    )

    assert repair_steer.source == "human"
    text = repair_steer.take()
    assert "focus on the auth module" in text
    assert "the ci failure" in text


def test_recover_node_with_no_findings_for_the_round_passes_the_steer_through(
    tmp_path, monkeypatch
):
    """A node with no findings for this round still gets the orchestrator's
    failure note (Task 4) -- no seeded *findings* note invented out of
    nothing, but the note naming what failed is always present."""
    repair_steer = asyncio.run(
        _call_recover_node(tmp_path, monkeypatch, measured_round=0, steer=None, collect_at={})
    )

    assert repair_steer
    text = repair_steer.take()
    assert "on.ci.poll" in text
    assert "re-measured" in text


def test_node_level_repair_is_told_which_tasks_failed_and_that_it_is_re_measured(
    tmp_path, monkeypatch
):
    from kraft import policy
    from kraft.executor import dispatch, walk

    seen = {}

    async def fake_measure_node(db_, run_dirs_, item, node, row_, reg, wt, **kw):
        if node["tasks"] == ["on.fix"]:
            seen["steer"] = kw.get("steer")
        return "ok", [], []

    def fake_collect(db_, wid, node, round, registry):
        return [], set()

    monkeypatch.setattr(dispatch, "measure_node", fake_measure_node)
    monkeypatch.setattr(dispatch, "collect_findings", fake_collect)

    node = {"id": "n", "tasks": ["on.a", "on.b"], "on_failure": ["on.fix"]}

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="wi",
                    bead_id="B",
                    title="t",
                    repo=str(tmp_path),
                    chain_template="t",
                    chain_definition=json.dumps({"template_id": "t", "nodes": [node]}),
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'wi'").fetchone()
            )
            await walk.recover_node(
                database,
                rd,
                "wi",
                node,
                row,
                Registry(hooks={}),
                tmp_path,
                failed=["on.a"],
                steer=None,
                launch=None,
                budget=policy.NO_BUDGET,
                measured_round=0,
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    text = seen["steer"].take()
    assert "on.a" in text, "names the task that failed"
    assert "on.b" not in text, "does not name a task that passed"
    assert "re-measured" in text, "says its own report is not the verdict"


async def _resolver_scenario(tmp_path, tracker, agent_env, call_resolver):
    """Sets up a real conflicted worktree, runs `call_resolver(database, rd,
    wid, row, worktree, conflict)`, and returns whatever it returns. Shared
    by the resolver's status-mapping tests below -- only the fake agent's
    env and the assertions differ between them.
    """
    from kraft import builtins as kraft_builtins

    repo = make_repo(tmp_path)
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    try:
        wid = await executor.intake(
            database,
            rd,
            title="t",
            repo=str(repo),
            template=_quick_task(),
            bd_cwd=str(tracker),
        )
        worktree = await kraft_builtins.ensure_worktree(
            database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
        )
        row = database.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
        branch = store.branch_for(row)

        (worktree / "calc.py").write_text(
            "def add(a, b):\n    return a - b - 1  # bug: should be +\n"
        )
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-m", "worktree edit")

        (repo / "calc.py").write_text("def add(a, b):\n    return a - b - 2  # bug: should be +\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "conflicting edit")

        try:
            await kraft_builtins.refresh_worktree_base(worktree, repo, branch)
            raise AssertionError("expected a RebaseConflict from the fixture")
        except kraft_builtins.RebaseConflict as exc:
            conflict = str(exc)

        for k, v in agent_env.items():
            os.environ[k] = v
        try:
            return await call_resolver(database, rd, wid, row, worktree, conflict)
        finally:
            for k in agent_env:
                os.environ.pop(k, None)
    finally:
        await database.close()


def test_resolve_rebase_conflict_resolves_and_moves_base_ref(tmp_path, monkeypatch):
    """.23's success path, end to end: the fake agent's `rebase` mode
    actually re-runs and completes the rebase (the only way to test this
    honestly -- a mocked dispatch could report `done` without moving
    anything), and `resolve_rebase_conflict` must persist the new base and
    report `"ok"`."""
    from kraft.executor import walk

    tracker = isolated_bd(tmp_path)

    async def call(database, rd, wid, row, worktree, conflict):
        registry = fake_registry(sys.executable, _FAKE_AGENT)
        node = {"id": "pre_mr_rebase", "tasks": ["on.mr.rebase"]}
        status, steer_text, new_base = await walk.resolve_rebase_conflict(
            database,
            rd,
            work_item_id=wid,
            node=node,
            row=row,
            registry=registry,
            worktree=worktree,
            policy=None,
            launch=None,
            conflict=conflict,
        )
        stored = database.read(
            lambda c: c.execute("SELECT base_ref FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
        return status, steer_text, new_base, stored["base_ref"]

    status, steer_text, new_base, stored_base_ref = asyncio.run(
        _resolver_scenario(tmp_path, tracker, {"KRAFT_FAKE_AGENT": "rebase"}, call)
    )
    assert status == "ok"
    assert steer_text is None
    assert new_base is not None and new_base == stored_base_ref


def test_resolve_rebase_conflict_downgrades_a_done_that_never_moved(tmp_path, monkeypatch):
    """An agent that reports `done` without completing the rebase (the
    default `fix` mode does no git work at all) is downgraded to the
    failure path -- believing the report would leave the branch still
    conflicted while everything downstream assumes it is resolved."""
    from kraft.executor import walk

    tracker = isolated_bd(tmp_path)

    async def call(database, rd, wid, row, worktree, conflict):
        registry = fake_registry(sys.executable, _FAKE_AGENT)
        node = {"id": "pre_mr_rebase", "tasks": ["on.mr.rebase"]}
        return await walk.resolve_rebase_conflict(
            database,
            rd,
            work_item_id=wid,
            node=node,
            row=row,
            registry=registry,
            worktree=worktree,
            policy=None,
            launch=None,
            conflict=conflict,
        )

    status, steer_text, new_base = asyncio.run(
        _resolver_scenario(tmp_path, tracker, {}, call)  # default KRAFT_FAKE_AGENT="fix"
    )
    assert status == "needs_human"
    assert steer_text is None and new_base is None


def test_resolve_rebase_conflict_done_with_concerns_carries_them_forward(tmp_path, monkeypatch):
    """`done_with_concerns` resolves the rebase, same as `done`, but the
    resolver's own relevance judgement -- the point of §4.1 -- must reach
    the caller so it can seed the next reviewer with it."""
    from kraft.executor import walk

    tracker = isolated_bd(tmp_path)

    async def call(database, rd, wid, row, worktree, conflict):
        registry = fake_registry(sys.executable, _FAKE_AGENT)
        node = {"id": "pre_mr_rebase", "tasks": ["on.mr.rebase"]}
        return await walk.resolve_rebase_conflict(
            database,
            rd,
            work_item_id=wid,
            node=node,
            row=row,
            registry=registry,
            worktree=worktree,
            policy=None,
            launch=None,
            conflict=conflict,
        )

    status, steer_text, new_base = asyncio.run(
        _resolver_scenario(
            tmp_path,
            tracker,
            {
                "KRAFT_FAKE_AGENT": "rebase",
                "KRAFT_FAKE_AGENT_STATUS": "done_with_concerns",
                "KRAFT_FAKE_AGENT_CONCERNS": "upstream renamed the function this calls",
            },
            call,
        )
    )
    assert status == "ok"
    assert steer_text == "upstream renamed the function this calls"
    assert new_base is not None


def test_resolve_rebase_conflict_needs_context_stops_with_the_question(tmp_path, monkeypatch):
    """The load-bearing verdict (design §4.5): the upstream change invalidates
    the agreed spec or plan, and Kraft cannot re-plan itself mid-chain."""
    from kraft.executor import walk

    tracker = isolated_bd(tmp_path)

    async def call(database, rd, wid, row, worktree, conflict):
        registry = fake_registry(sys.executable, _FAKE_AGENT)
        node = {"id": "pre_mr_rebase", "tasks": ["on.mr.rebase"]}
        status, steer_text, new_base = await walk.resolve_rebase_conflict(
            database,
            rd,
            work_item_id=wid,
            node=node,
            row=row,
            registry=registry,
            worktree=worktree,
            policy=None,
            launch=None,
            conflict=conflict,
        )
        reason = next(
            e["payload"]["reason"]
            for e in database.read(lambda c: events.read_after(c, 0, wid))
            if e["type"] == "work_item_needs_human"
        )
        return status, steer_text, new_base, reason

    status, steer_text, new_base, reason = asyncio.run(
        _resolver_scenario(
            tmp_path,
            tracker,
            {
                "KRAFT_FAKE_AGENT_STATUS": "needs_context",
                "KRAFT_FAKE_AGENT_QUESTION": "does the renamed API change the plan?",
            },
            call,
        )
    )
    assert status == "needs_human"
    assert steer_text is None and new_base is None
    assert "needs_context: does the renamed API change the plan?" in reason


def test_resolve_rebase_conflict_failed_stops_with_the_conflict(tmp_path, monkeypatch):
    from kraft.executor import walk

    tracker = isolated_bd(tmp_path)

    async def call(database, rd, wid, row, worktree, conflict):
        registry = fake_registry(sys.executable, _FAKE_AGENT)
        node = {"id": "pre_mr_rebase", "tasks": ["on.mr.rebase"]}
        status, steer_text, new_base = await walk.resolve_rebase_conflict(
            database,
            rd,
            work_item_id=wid,
            node=node,
            row=row,
            registry=registry,
            worktree=worktree,
            policy=None,
            launch=None,
            conflict=conflict,
        )
        reason = next(
            e["payload"]["reason"]
            for e in database.read(lambda c: events.read_after(c, 0, wid))
            if e["type"] == "work_item_needs_human"
        )
        return status, reason

    status, reason = asyncio.run(
        _resolver_scenario(tmp_path, tracker, {"KRAFT_FAKE_AGENT_STATUS": "failed"}, call)
    )
    assert status == "needs_human"
    assert "could not resolve" in reason


def test_resolve_rebase_conflict_cap_breach_stops_without_dispatching(tmp_path, monkeypatch):
    """A third conflict on one item must not dispatch a fourth resolver."""
    from kraft import policy
    from kraft.executor import walk

    tracker = isolated_bd(tmp_path)
    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_conflict: { attempts: 1, wall_clock_s: 3600 }\n"
        "default: { attempts: 9, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def call(database, rd, wid, row, worktree, conflict):
        registry = fake_registry(sys.executable, _FAKE_AGENT)
        node = {"id": "pre_mr_rebase", "tasks": ["on.mr.rebase"]}
        first = await walk.resolve_rebase_conflict(
            database,
            rd,
            work_item_id=wid,
            node=node,
            row=row,
            registry=registry,
            worktree=worktree,
            policy=pol,
            launch=None,
            conflict=conflict,
        )
        second = await walk.resolve_rebase_conflict(
            database,
            rd,
            work_item_id=wid,
            node=node,
            row=row,
            registry=registry,
            worktree=worktree,
            policy=pol,
            launch=None,
            conflict=conflict,
        )
        return first, second

    (first_status, _, _), (second_status, _, _) = asyncio.run(
        _resolver_scenario(tmp_path, tracker, {"KRAFT_FAKE_AGENT_STATUS": "failed"}, call)
    )
    assert first_status == "needs_human"  # the agent's own "failed"
    assert second_status == "needs_human"  # the cap, not a second dispatch


def test_pausing_between_nodes_stops_the_walk_before_the_next_one_starts(tmp_path, monkeypatch):
    """Kraft-e7pm. The original bug: pause landed between env_setup's session
    exit and the next node, so there was no session to signal and the walk
    kept going -- resume then started a second one. This pins the fix at the
    layer that actually stops it: the loop's own per-node status read."""
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
            from kraft.executor import walk as walk_mod

            calls = []
            original_walk_node = walk_mod.walk_node

            async def spy(db_, run_dirs_, wid_, node, row_, registry_, worktree_, **kw):
                calls.append(node["id"])
                result = await original_walk_node(
                    db_, run_dirs_, wid_, node, row_, registry_, worktree_, **kw
                )
                if node["id"] == "env_setup":
                    # the exact original bug: pause lands between two nodes,
                    # with no running session for `pause_work_item` to signal
                    await database.write(lambda c: store.pause_work_item(c, wid, []))
                return result

            monkeypatch.setattr(walk_mod, "walk_node", spy)
            result = await executor.run_once(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "paused"
            assert calls == ["env_setup"], "implementation must never have been dispatched"
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "paused"

            # resume: exactly one walk runs the rest of the chain to completion
            monkeypatch.setattr(walk_mod, "walk_node", original_walk_node)
            claimed = await database.write(
                lambda c: store.claim_for_run(c, wid, from_statuses=["paused"])
            )
            assert claimed
            await database.write(lambda c: store.resume_work_item(c, wid, None))
            result2 = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                start_index=1,  # implementation: the node right after env_setup,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result2 == "completed"
            types = _events(database, wid)
            assert types.count("work_item_completed") == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_blocked_bead_pauses_the_walk_before_any_worktree_is_made(tmp_path, monkeypatch):
    """Kraft-tsfpk: a work item whose bead is `blocked_by` something must
    never create a worktree or start a session."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    blocker_id = asyncio.run(beads.intake("the blocker", cwd=str(tracker)))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="do the blocked thing",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            bead_id = row["bead_id"]
            assert bead_id
            subprocess.run(
                ["bd", "dep", "add", bead_id, blocker_id, "--type", "blocks"],
                cwd=tracker,
                capture_output=True,
                text=True,
                check=True,
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "paused"
            assert not (rd.worktrees / wid).exists()
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT COUNT(*) AS n FROM worker_sessions WHERE work_item_id = ?", (wid,)
                ).fetchone()
            )
            assert sessions["n"] == 0
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "paused"
            types = _events(database, wid)
            assert "work_item_blocked_by_dependency" in types
            # events.read_after already parses `payload` into a dict -- no
            # json.loads needed on top of it (unlike analytics.py's raw-SQL
            # event reads in Tasks 8-9, which get the column back as text).
            payload = next(
                e["payload"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "work_item_blocked_by_dependency"
            )
            assert payload["blocked_by"] == [blocker_id]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resuming_a_still_blocked_item_re_pauses_cheaply(tmp_path, monkeypatch):
    """The 'cheap refusal' the bead asks for: a resume of a still-blocked item
    costs one `bd blocked` call and re-pauses -- no worker_sessions row."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    blocker_id = asyncio.run(beads.intake("the blocker", cwd=str(tracker)))

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="do the blocked thing",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            subprocess.run(
                ["bd", "dep", "add", row["bead_id"], blocker_id, "--type", "blocks"],
                cwd=tracker,
                capture_output=True,
                text=True,
                check=True,
            )
            first = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert first == "paused"
            # A resume re-enters through run_once at the same start_index (0).
            second = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert second == "paused"
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT COUNT(*) AS n FROM worker_sessions WHERE work_item_id = ?", (wid,)
                ).fetchone()
            )
            assert sessions["n"] == 0
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_blocked_sub_bead_the_item_states_pauses_the_walk(tmp_path, monkeypatch):
    """The motivating case plan-review finding 1 named: a manually created
    item's own tracking bead is always edge-free (fresh from `entry.intake`),
    so only a check against `implements_beads` -- the sub-beads the
    item states -- ever catches a real dependency for this path."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    # `Kraft-` prefix -- the same
    # setup `tests/test_bead_bookkeeping.py`'s own sub-bead test uses, since
    # `isolated_bd`'s shared template is prefixed `TEST` and would never match.
    tracker = make_repo(tmp_path, name="tracker")
    subprocess.run(
        ["bd", "init", "--prefix", "Kraft"], cwd=tracker, check=True, capture_output=True
    )
    repo = make_repo(tmp_path)

    async def scenario():
        sub = await beads.intake("the sub task", cwd=str(tracker))
        blocker = await beads.intake("the blocker", cwd=str(tracker))
        subprocess.run(
            ["bd", "dep", "add", sub, blocker, "--type", "blocks"],
            cwd=tracker,
            capture_output=True,
            text=True,
            check=True,
        )
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="implements a blocked sub-bead",
                implements_beads=[sub],
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT bead_id, implements_beads FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            # The tracking bead itself has no edges -- confirms the case this
            # test is pinning actually needs implements_beads to catch it.
            assert await beads.blocked_by([row["bead_id"]], cwd=str(tracker)) == []
            assert json.loads(row["implements_beads"]) == [sub]
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "paused"
            assert not (rd.worktrees / wid).exists()
            payload = next(
                e["payload"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "work_item_blocked_by_dependency"
            )
            assert payload["blocked_by"] == [blocker]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_a_bead_blocked_only_by_its_own_bundlemate_dispatches(tmp_path, monkeypatch):
    """A work item bundling two beads with a `blocks` edge between them (the
    Kraft-5fx.2..5fx.12 shape) must not read as blocked by a bead it is
    itself implementing -- the blocker here is in the item's own bead set,
    not an outside dependency."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = make_repo(tmp_path, name="tracker")
    subprocess.run(
        ["bd", "init", "--prefix", "Kraft"], cwd=tracker, check=True, capture_output=True
    )
    repo = make_repo(tmp_path)

    async def scenario():
        sub = await beads.intake("the sub task", cwd=str(tracker))
        bundlemate = await beads.intake("bundled dependency", cwd=str(tracker))
        subprocess.run(
            ["bd", "dep", "add", sub, bundlemate, "--type", "blocks"],
            cwd=tracker,
            capture_output=True,
            text=True,
            check=True,
        )
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="implements two bundled beads",
                implements_beads=[sub, bundlemate],
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT implements_beads FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert set(json.loads(row["implements_beads"])) == {sub, bundlemate}
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_an_unblocked_bead_dispatches_exactly_as_before(tmp_path, monkeypatch):
    """No bead at all (bd was down at intake), or a bead with no open
    blocker, must not regress the ordinary happy path."""
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
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())


_LOOP_NODE = {
    "id": "verify",
    "tasks": ["on.test.run"],
    "gate_after": None,
    "fix_loop": "verify_fix_loop",
}


class _StopMeasuring(Exception):
    """Sentinel: unwinds `walk_node` the instant it dispatches its first measure."""


def _first_measured_round(tmp_path, monkeypatch, *, seeded_count, node=None, on_measure=None):
    """The `round` `walk_node` stamps its first `dispatch.measure_node` with.

    Drives the real `walk_node` with the counter pre-seeded to `seeded_count`
    (None = no `retry_counters` row at all, i.e. a first-ever entry), and stops
    at the first measure rather than running a whole paid loop -- the weaker of
    the two shapes the plan offers, chosen because it pins the defect directly:
    the bug *is* the value of that kwarg.
    """
    from kraft import policy
    from kraft.executor import dispatch

    node = node or _LOOP_NODE
    seen: list[int] = []
    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  verify_fix_loop: { attempts: 9, wall_clock_s: 3600 }\n"
        "default: { attempts: 9, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def fake_measure(*args, round=0, **kwargs):
        seen.append(round)
        if on_measure is not None:
            return on_measure
        raise _StopMeasuring

    monkeypatch.setattr(dispatch, "measure_node", fake_measure)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="wi",
                    bead_id="B",
                    title="t",
                    repo=str(tmp_path),
                    chain_template="quick-task",
                    chain_definition=json.dumps({"template_id": "quick-task", "nodes": [node]}),
                )
            )
            if seeded_count is not None:
                cap = policy.Cap(attempts=9, wall_clock_s=3600)
                for _ in range(seeded_count):
                    await database.write(
                        lambda c: store.bump_counter(c, "wi", "verify_fix_loop", cap)
                    )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'wi'").fetchone()
            )
            try:
                await executor.walk.walk_node(
                    database,
                    rd,
                    "wi",
                    node,
                    row,
                    Registry(hooks={}),
                    tmp_path,
                    policy=pol,
                )
            except _StopMeasuring:
                pass
        finally:
            await database.close()

    asyncio.run(scenario())
    return seen


def test_resumed_fix_loop_reuses_the_round_it_was_interrupted_on(tmp_path, monkeypatch):
    """A resume mid-loop must not re-buy a measurement of an unchanged tree.

    7ced80e6: the counter read 3, the resumed entry measured at round 0, the
    round-3 session could not match on `round`, and $5.85 / 823s bought a
    byte-identical answer -- charged to the wall cap the judge then stopped on.

    Asserts on the `round` kwarg of the first `measure_node` rather than on a
    reused session row: `reusable_session`'s `round = ?` filter is what the
    kwarg feeds, and the kwarg is the defect itself.
    """
    assert _first_measured_round(tmp_path, monkeypatch, seeded_count=3) == [3]


def test_first_ever_entry_still_measures_at_round_zero(tmp_path, monkeypatch):
    """No `retry_counters` row yet -- nothing to continue from, so round 0."""
    assert _first_measured_round(tmp_path, monkeypatch, seeded_count=None) == [0]


def test_on_failure_repair_still_fires_on_a_resumed_entry(tmp_path, monkeypatch):
    """`round == 0` used to mean "first iteration of this entry".

    Once `round` seeds from the counter the two part company, and a resumed
    entry -- exactly the case where a loop is already in trouble -- would
    silently stop running its `on_failure` repair.
    """
    from kraft.executor import walk

    repaired = []

    async def fake_recover(*args, **kwargs):
        repaired.append(kwargs.get("round"))
        raise _StopMeasuring

    monkeypatch.setattr(walk, "recover_node", fake_recover)
    node = {**_LOOP_NODE, "on_failure": ["on.repair.start"]}
    rounds = _first_measured_round(
        tmp_path,
        monkeypatch,
        seeded_count=4,
        node=node,
        on_measure=("failed", ["on.test.run"], []),
    )
    assert rounds == [4]
    assert repaired == [walk._REPAIR_ROUND], "on_failure repair did not run on the resumed entry"


def test_a_poisoned_repo_entry_is_attributed_to_the_node_not_a_bare_crash(tmp_path):
    """A malformed repos.yaml reaches ensure_worktree through the repo entry.
    It must land as needs_human against the node that was about to dispatch,
    not as guard's bare "executor crashed" (Kraft-cuo3p)."""
    poisoned = deps._PoisonedRepoEntry(ConfigError("repos.yaml: 'local_files' must be a list"))
    with pytest.raises(ConfigError):
        poisoned.get("local_files")


def test_walk_attributes_a_config_error_to_the_first_node(tmp_path):
    """The whole point of Task 1: a broken repos.yaml names the node it broke."""
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
                title="poisoned config",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            launch = executor.LaunchContext(
                repo_entry=deps._PoisonedRepoEntry(ConfigError("repos.yaml: boom")),
                steering_dir=None,
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=registry,
                bd_cwd=str(tracker),
                launch=launch,
            )
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["current_node_id"] is not None

            evts = database.read(lambda c: events.read_after(c, 0, wid))
            reason = next(
                e["payload"]["reason"] for e in evts if e["type"] == "work_item_needs_human"
            )
            assert "boom" in reason
        finally:
            await database.close()

    asyncio.run(scenario())


def _walk_single_node(tmp_path, node):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="wi",
                    bead_id="B",
                    title="t",
                    repo=str(tmp_path),
                    chain_template="default",
                    chain_definition=json.dumps({"template_id": "default", "nodes": [node]}),
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = 'wi'").fetchone()
            )
            return await executor.walk.walk_node(
                database, rd, "wi", node, row, Registry(hooks={}), tmp_path
            )
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_walk_node_completes_on_a_moved_base_so_run_once_can_bounce(tmp_path, monkeypatch):
    """`BASE_MOVED` is not a failure: the node stopped on purpose, and `run_once`
    compares base_ref right after this returns and bounces."""
    from kraft.executor import dispatch
    from kraft.executor.context import BASE_MOVED

    node = {
        "id": "open_mr",
        "steps": [["on.mr.rebase"], ["on.mr.describe"], ["on.mr.open"]],
        "tasks": ["on.mr.rebase", "on.mr.describe", "on.mr.open"],
        "gate_after": None,
        "rebase_bounce_to": "verify",
    }

    async def fake_measure(*a, **kw):
        return BASE_MOVED, [], []

    monkeypatch.setattr(dispatch, "measure_node", fake_measure)
    assert _walk_single_node(tmp_path, node) == "ok"


def test_a_resolved_conflict_re_measures_a_node_that_has_more_steps(tmp_path, monkeypatch):
    """The resolver completing the node is right while the rebase is its only
    work; with the rebase as step 1 of `implementation` it would mark the node
    done with the agent never dispatched."""
    from kraft import builtins as kraft_builtins
    from kraft.executor import dispatch, walk

    node = {
        "id": "implementation",
        "steps": [["on.mr.rebase"], ["on.implementation.start"]],
        "tasks": ["on.mr.rebase", "on.implementation.start"],
        "gate_after": None,
    }
    calls = []

    async def fake_measure(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return "failed", ["on.mr.rebase"], [kraft_builtins.RebaseConflict("both modified x")]
        return "ok", [], []

    async def fake_resolve(*a, **kw):
        return "ok", None, "newsha"

    monkeypatch.setattr(dispatch, "measure_node", fake_measure)
    monkeypatch.setattr(walk, "resolve_rebase_conflict", fake_resolve)
    assert _walk_single_node(tmp_path, node) == "ok"
    assert len(calls) == 2, "the node must be measured again after the resolve"
