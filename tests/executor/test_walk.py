import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import (
    _git,
    isolated_bd,
    make_repo,
    seed_v1_library,
    v1_chain,
    v1_item,
    v1_named_chain,
    v1_resolved,
    v1_walk,
)

from kraft import db, events, executor, store
from kraft.adapters import beads
from kraft.adapters import forge as _forge
from kraft.api import deps
from kraft.config import ConfigError, git_read
from kraft.paths import RunDirs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_AGENT = Path(__file__).resolve().parents[1] / "support" / "fake_agent.py"

#: A repo that deliberately needs no preparation. Most tests here are about
#: chain walking, not environments.
NO_SETUP = {"setup_command": ""}
#: The same, on a forge: a V1 forge task runs on `backend: auto`, which reads
#: the forge off the repo entry. Without one the task fails in-process on the
#: missing forge before the patched `resolve` is ever asked for a backend.
FORGE_REPO = {**NO_SETUP, "forge": "github"}


_FAKE = f"{sys.executable} {_FAKE_AGENT}"


def _quick_task(tmp_path):
    """The shipped gateless `quick-task`, its agent task on the fake agent."""
    return v1_named_chain(tmp_path / "templates", agent_command=_FAKE)


def _chain(tmp_path, nodes, *, chain_id="t"):
    """`nodes` (authored V1 node mappings) as a chain to file, with the `fake`
    harness an `_agent` task names overlaid onto this test's `KRAFT_HOME`."""
    seed_v1_library(tmp_path / "templates", agent_command=_FAKE)
    return v1_resolved(nodes, chain_id=chain_id)


def _agent(task_id="implement", **fields):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _sub(task_id, argv):
    return {"id": task_id, "kind": "subprocess", "command": shlex.join(argv)}


def _forge_task(task_id, target):
    return {"id": task_id, "kind": "forge", "target": target}


def _exec(node_id, *tasks, **fields):
    return {"id": node_id, "kind": "exec", "tasks": list(tasks), **fields}


async def _file_one_node(database, tmp_path, node: dict, *, wid="wi"):
    """File a work item whose chain is just `node`, and return its
    `ResolvedNode` and row -- what `walk_node`/`recover_node` take."""
    chain = v1_chain([node], repo=tmp_path)
    await v1_item(database, chain, repo=tmp_path, wid=wid)
    row = database.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    return chain.chain.nodes[0], row


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
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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
            # V1 quick-task is two nodes: `env_setup` is implicit preparation now.
            assert types.count("node_started") == 2
            assert types.count("node_completed") == 2
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
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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
            assert types.count("node_completed") == 2
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
    stops at `verify` again.

    quick-task's shape with a real suite at `verify`: the seeded fixture turns
    `verify_changed_test_scopes` into `true`, which could never fail."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # leave the bug in place
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    chain = _chain(
        tmp_path,
        [
            _exec("implementation", _agent()),
            _exec("verify", _sub("suite", ["python", "-m", "pytest", "-q"])),
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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
            assert types.count("node_started") == 2
            assert types.count("node_completed") == 1

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
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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
            tmpl = _chain(tmp_path, [_exec("mr_checks", _forge_task("ci_poll", "mr.ci"))])
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
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
            tmpl = _chain(tmp_path, [_exec("mr_checks", _forge_task("ci_poll", "mr.ci"))])
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
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
    assert "ci_poll [forge]" in reason


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
            tmpl = _chain(
                tmp_path,
                [
                    _exec(
                        "open_mr",
                        _forge_task("open", "mr.open_draft"),
                        fix_loop={"tasks": [_agent("repair")]},
                    )
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
            )
            assert result == "needs_human"
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            reason = next(
                e["payload"]["reason"] for e in evts if e["type"] == "work_item_needs_human"
            )
            assert "fix_cycle_started" not in [e["type"] for e in evts]
            counter = database.read(lambda c: store.read_counter(c, wid, "open_mr.fix_loop"))
            return reason, counter
        finally:
            await database.close()

    reason, counter = asyncio.run(scenario())
    assert "open [forge]" in reason
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
            tmpl = _chain(
                tmp_path,
                [
                    _exec(
                        "mr_checks",
                        _forge_task("ci_poll", "mr.ci"),
                        fix_loop={"tasks": [_agent("repair")]},
                    )
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            counter = database.read(lambda c: store.read_counter(c, wid, "mr_checks.fix_loop"))
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
            tmpl = _chain(
                tmp_path,
                [
                    _exec(
                        "checks",
                        _forge_task("open", "mr.open_draft"),
                        _sub("suite", [sys.executable, "-c", "import sys; sys.exit(1)"]),
                        fix_loop={"tasks": [_agent("repair")]},
                    )
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
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
            tmpl = _chain(
                tmp_path,
                [
                    _exec(
                        "checks",
                        _forge_task("open", "mr.open_draft"),
                        _agent("review"),
                        fix_loop={"tasks": [_agent("repair")]},
                    )
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
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
            tmpl = _chain(
                tmp_path,
                [
                    _exec(
                        "open_mr",
                        _forge_task("open", "mr.open_draft"),
                        fix_loop={"tasks": [_agent("repair")]},
                    )
                ],
            )
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
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
            tmpl = _chain(tmp_path, [_exec("mr_checks", _forge_task("ci_poll", "mr.ci"))])
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                steer="please look at the flaky pipeline",
                launch=executor.LaunchContext(repo_entry=FORGE_REPO, steering_dir=None),
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
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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

    # verify's own task fails every cycle without ever calling the fake agent,
    # so the only agent launch in this run is the fix cycle's.
    tmpl = _chain(
        tmp_path,
        [
            _exec(
                "verify",
                _sub("suite", [sys.executable, "-c", "exit(1)"]),
                fix_loop={"tasks": [_agent("repair")]},
            )
        ],
    )
    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  verify.fix_loop: { attempts: 1, wall_clock_s: 3600 }\n"
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
                chain=tmpl,
                bd_cwd=str(tracker),
            )
            launch = executor.LaunchContext(
                repo_entry={"default_model": "haiku", "setup_command": ""}, steering_dir=None
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=launch,
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    # The V1 loop declares no judge. The stop that follows the cap hands the
    # item to auto-escalation, whose `claude` the fixture also points at the
    # fake agent -- filtered out here since this test is about the *fix*
    # cycle's launch context, not the escalation's.
    argvs = [
        a
        for a in _argv_lines(argv_log)
        if not a[a.index("-p") + 1].startswith("This is an automatic escalation")
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

    # No env node in V1: the worktree is prepared before the first node runs.
    tmpl = _chain(tmp_path, [_exec("work", _sub("noop", ["true"]))])

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=tmpl, bd_cwd=str(tracker)
            )
            launch = executor.LaunchContext(
                repo_entry={"local_files": [".python-version"], "setup_command": ""},
                steering_dir=None,
            )
            result = await executor.run_once(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=launch,
            )
            assert result == "completed"
            assert (rd.worktrees / wid / ".python-version").read_text() == "3.11\n"
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
            bead_id = await beads.intake("make the failing test pass", cwd=str(other))
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
                bead_id=bead_id,
                bead_cwd=str(other),
            )
            assert (
                await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=None,
                    bd_cwd=str(tracker),
                    launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
                )
                == "completed"
            )
            assert _bd_status(other, bead_id) == "closed"
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
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            assert (
                await executor.run(
                    database,
                    rd,
                    work_item_id=wid,
                    registry=None,
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


def _run_one_node(tmp_path, node: dict) -> tuple[str, list[dict]]:
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    chain = _chain(tmp_path, [node])

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="a pipeline that is red for a reason outside the code",
                repo=str(repo),
                chain=chain,
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            return result, database.read(lambda c: events.read_after(c, 0, wid))
        finally:
            await database.close()

    return asyncio.run(scenario())


def _recovering(measure: list[str], repair: list[str]) -> dict:
    """A one-node chain whose node measures itself, and repairs on failure."""
    return _exec("checks", _sub("poll", measure), on_failure={"tasks": [_sub("sync", repair)]})


def test_a_failed_node_repairs_itself_and_measures_again(tmp_path):
    """Kraft-rv6i. A node's tasks run concurrently, so nothing in the node can
    react to what another task in it found, and there was no step at all
    between a task failing and the item dropping to needs_human. With
    `on_failure` the node gets one repair pass — believed only because the
    node's own task passes on the re-measure."""
    flag = tmp_path / "labelled"
    result, evts = _run_one_node(tmp_path, _recovering(_gate_check(flag), _touch(flag)))

    assert result == "completed", "a repaired node did not carry the chain to the end"
    assert [e["type"] for e in evts].count("node_recovery_started") == 1
    recovery = next(e for e in evts if e["type"] == "node_recovery_started")
    assert recovery["payload"] == {
        "node_id": "checks",
        "failed_tasks": ["checks.main.poll"],
        "tasks": ["checks.on_failure.main.sync"],
    }
    assert not [e for e in evts if e["type"] == "work_item_needs_human"]


def test_a_repair_that_did_not_take_still_stops_for_a_human(tmp_path):
    """The re-measure is the point: a repair task exiting 0 is not evidence
    that the thing it was repairing is fixed."""
    flag = tmp_path / "never-written"
    result, evts = _run_one_node(
        tmp_path, _recovering(_gate_check(flag), [sys.executable, "-c", "pass"])
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
        _exec("checks", _sub("poll", _gate_check(flag))),
    )

    assert result == "needs_human"
    assert not [e for e in evts if e["type"] == "node_recovery_started"]
    stopped = next(e for e in evts if e["type"] == "work_item_needs_human")
    assert "after on_failure" not in stopped["payload"]["reason"]


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

    def fake_collect(db, wid, node, round):
        return collect_at.get(round, []), set()

    monkeypatch.setattr(dispatch, "measure_node", fake_measure)
    monkeypatch.setattr(dispatch, "collect_findings", fake_collect)

    node = _exec("checks", _sub("poll", ["true"]), on_failure={"tasks": [_sub("sync", ["true"])]})
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    try:
        resolved, row = await _file_one_node(database, tmp_path, node)
        await walk.recover_node(
            database,
            rd,
            "wi",
            resolved,
            row,
            tmp_path,
            failed=list(resolved.steps[0].tasks),
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
        message="poll failed; no output captured.\n\nReproduce with: pytest -k x",
        file=None,
        line=None,
        source_plugin="checks.main.poll",
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
        source_plugin="checks.main.poll",
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
        source_plugin="checks.main.poll",
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
    assert "in node checks: poll" in text
    assert "re-measured" in text


def test_node_level_repair_is_told_which_tasks_failed_and_that_it_is_re_measured(
    tmp_path, monkeypatch
):
    from kraft import policy
    from kraft.executor import dispatch, walk

    seen = {}

    async def fake_measure_node(db_, run_dirs_, item, node, row_, wt, **kw):
        if kw.get("steps") is node.on_failure:
            seen["steer"] = kw.get("steer")
        return "ok", [], []

    def fake_collect(db_, wid, node, round):
        return [], set()

    monkeypatch.setattr(dispatch, "measure_node", fake_measure_node)
    monkeypatch.setattr(dispatch, "collect_findings", fake_collect)

    node = _exec(
        "n",
        _sub("alpha", ["true"]),
        _sub("beta", ["true"]),
        on_failure={"tasks": [_sub("fix", ["true"])]},
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            resolved, row = await _file_one_node(database, tmp_path, node)
            await walk.recover_node(
                database,
                rd,
                "wi",
                resolved,
                row,
                tmp_path,
                failed=[resolved.steps[0].tasks[0]],
                steer=None,
                launch=None,
                budget=policy.NO_BUDGET,
                measured_round=0,
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    text = seen["steer"].take()
    assert "alpha" in text, "names the task that failed"
    assert "beta" not in text, "does not name a task that passed"
    assert "re-measured" in text, "says its own report is not the verdict"


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
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            from kraft.executor import walk as walk_mod

            calls = []
            original_walk_node = walk_mod.walk_node

            async def spy(db_, run_dirs_, wid_, node, row_, worktree_, **kw):
                calls.append(node.id)
                result = await original_walk_node(db_, run_dirs_, wid_, node, row_, worktree_, **kw)
                if node.id == "implementation":
                    # the exact original bug: pause lands between two nodes,
                    # with no running session for `pause_work_item` to signal
                    await database.write(lambda c: store.pause_work_item(c, wid, []))
                return result

            monkeypatch.setattr(walk_mod, "walk_node", spy)
            result = await executor.run_once(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "paused"
            assert calls == ["implementation"], "verify must never have been dispatched"
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
                registry=None,
                bd_cwd=str(tracker),
                start_index=1,  # verify: the node right after implementation
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
            wid = await executor.intake(
                database,
                rd,
                title="do the blocked thing",
                repo=str(repo),
                chain=_quick_task(tmp_path),
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
                registry=None,
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
            wid = await executor.intake(
                database,
                rd,
                title="do the blocked thing",
                repo=str(repo),
                chain=_quick_task(tmp_path),
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
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert first == "paused"
            # A resume re-enters through run_once at the same start_index (0).
            second = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
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
            wid = await executor.intake(
                database,
                rd,
                title="implements a blocked sub-bead",
                implements_beads=[sub],
                repo=str(repo),
                chain=_quick_task(tmp_path),
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
                registry=None,
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
            wid = await executor.intake(
                database,
                rd,
                title="implements two bundled beads",
                implements_beads=[sub, bundlemate],
                repo=str(repo),
                chain=_quick_task(tmp_path),
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
                registry=None,
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
            wid = await executor.intake(
                database,
                rd,
                title="make the failing test pass",
                repo=str(repo),
                chain=_quick_task(tmp_path),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            assert result == "completed"
        finally:
            await database.close()

    asyncio.run(scenario())


_LOOP_NODE = _exec(
    "verify", _sub("suite", ["true"]), fix_loop={"tasks": [_sub("repair", ["true"])]}
)


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
        "loops:\n  verify.fix_loop: { attempts: 9, wall_clock_s: 3600 }\n"
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
            resolved, row = await _file_one_node(database, tmp_path, node)
            if seeded_count is not None:
                cap = policy.Cap(attempts=9, wall_clock_s=3600)
                for _ in range(seeded_count):
                    await database.write(
                        lambda c: store.bump_counter(c, "wi", "verify.fix_loop", cap)
                    )
            try:
                await executor.walk.walk_node(
                    database,
                    rd,
                    "wi",
                    resolved,
                    row,
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
    node = {**_LOOP_NODE, "on_failure": {"tasks": [_sub("repair", ["true"])]}}
    rounds = _first_measured_round(
        tmp_path,
        monkeypatch,
        seeded_count=4,
        node=node,
        on_measure=("failed", [], []),
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
            wid = await executor.intake(
                database,
                rd,
                title="poisoned config",
                repo=str(repo),
                chain=_quick_task(tmp_path),
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
                registry=None,
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
            resolved, row = await _file_one_node(database, tmp_path, node)
            return await executor.walk.walk_node(database, rd, "wi", resolved, row, tmp_path)
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_walk_node_completes_on_a_moved_base(tmp_path, monkeypatch):
    """`BASE_MOVED` is not a failure: the node stopped on purpose and completes
    like a clean pass. The restart span a base change re-enters is Task 7's
    (`base-change-restarts-a-declared-chain-span`); the legacy bounce this used
    to hand off to is gone."""
    from kraft.executor import dispatch
    from kraft.executor.context import BASE_MOVED

    node = {
        "id": "open_mr",
        "kind": "exec",
        "steps": [
            {"id": "sync", "tasks": [_forge_task("sync", "mr.sync")]},
            {"id": "open", "tasks": [_forge_task("open", "mr.open_draft")]},
        ],
    }

    async def fake_measure(*a, **kw):
        return BASE_MOVED, [], []

    monkeypatch.setattr(dispatch, "measure_node", fake_measure)
    assert _walk_single_node(tmp_path, node) == "ok"


def test_a_fix_loop_retry_resumes_at_the_failed_group(tmp_path, monkeypatch):
    """verify is [[prep], [test]]: a failing test re-ran the prep step on every
    cycle. The retry re-measures from the step that failed.

    The legacy version also kept a leading rebase step re-running as an
    exemption; that rebase layer is gone in V1 (4a), so there is no exemption
    left to pin."""
    from kraft.executor import dispatch

    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    pol = _in_process_policy(tmp_path)
    dispatched: list[str] = []

    real = dispatch.dispatch_node
    marker = tmp_path / "first-run-done"
    # fails once, then passes: the fix cycle is what turns it green
    fail_once = (
        f"import pathlib,sys; m=pathlib.Path({str(marker)!r}); "
        "sys.exit(0 if m.exists() else (m.write_text('x') or 1))"
    )

    async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
        dispatched.append(task.task.id)
        return await real(db_, run_dirs_, task, node, row_, wt, **kw)

    monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
    chain = _chain(
        tmp_path,
        [
            {
                "id": "verify",
                "kind": "exec",
                "steps": [
                    {"id": "prep", "tasks": [_sub("prep", ["true"])]},
                    {"id": "check", "tasks": [_sub("test", [sys.executable, "-c", fail_once])]},
                ],
                "fix_loop": {"tasks": [_sub("fix", ["true"])]},
            }
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            return await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "completed"
    assert dispatched.count("test") == 2, dispatched
    assert dispatched.count("prep") == 1, dispatched
    assert dispatched.count("fix") == 1, dispatched


def test_a_fix_loop_node_stops_at_a_moved_base_without_spending_a_cycle(tmp_path, monkeypatch):
    from kraft.executor import dispatch
    from kraft.executor.context import BASE_MOVED

    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    pol = _in_process_policy(tmp_path)
    dispatched: list[str] = []

    async def fake_dispatch_node(db_, run_dirs_, task, node, row_, wt, **kw):
        dispatched.append(task.task.id)
        return BASE_MOVED if task.task.id == "sync" else "done"

    monkeypatch.setattr(dispatch, "dispatch_node", fake_dispatch_node)
    chain = _chain(
        tmp_path,
        [
            {
                "id": "verify",
                "kind": "exec",
                "steps": [
                    {"id": "sync", "tasks": [_forge_task("sync", "mr.sync")]},
                    {"id": "check", "tasks": [_sub("test", ["true"])]},
                ],
                "fix_loop": {"tasks": [_sub("fix", ["true"])]},
            }
        ],
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), chain=chain, bd_cwd=str(tracker)
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None),
            )
            return result, database.read(lambda c: store.read_counter(c, wid, "verify.fix_loop"))
        finally:
            await database.close()

    result, counter = asyncio.run(scenario())
    assert result == "completed"
    assert dispatched == ["sync"]
    assert counter is None


# --- Template Schema V1: one execution shape ---------------------------------


def _v1(nodes, repo):
    return v1_chain(nodes, repo=repo)


def test_steps_are_ordered_while_tasks_inside_a_step_are_concurrent(tmp_path):
    """`exec-node-orders-concurrent-task-groups`, end to end: the two tasks in
    one step overlap in time, and the next step does not start until both of
    them have finished."""
    repo = make_repo(tmp_path)
    log = tmp_path / "order.txt"

    def marker(name: str) -> str:
        return f"sh -c 'echo {name}-start >> {log}; sleep 0.4; echo {name}-end >> {log}'"

    chain = _v1(
        [
            {
                "id": "build",
                "kind": "exec",
                "steps": [
                    {
                        "id": "wide",
                        "tasks": [
                            {"id": "a", "kind": "subprocess", "command": marker("a")},
                            {"id": "b", "kind": "subprocess", "command": marker("b")},
                        ],
                    },
                    {
                        "id": "after",
                        "tasks": [{"id": "c", "kind": "subprocess", "command": marker("c")}],
                    },
                ],
            }
        ],
        repo,
    )

    status, _evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "completed"
    lines = log.read_text().split()
    # Concurrent within the step: both started before either finished.
    assert set(lines[:2]) == {"a-start", "b-start"}
    # Ordered between steps: the later step's task started after both ended.
    assert lines.index("c-start") > max(lines.index("a-end"), lines.index("b-end"))
    assert [s["hook_point"] for s in sessions] == [
        "build.wide.a",
        "build.wide.b",
        "build.after.c",
    ] or [s["hook_point"] for s in sessions] == [
        "build.wide.b",
        "build.wide.a",
        "build.after.c",
    ]


def test_a_task_is_identified_by_its_canonical_path_in_sessions_and_events(tmp_path):
    """Ruling 1: the canonical path replaces the hook name in
    `sessions.hook_point` and in every event payload that names a task. A hook
    name reused by two nodes could not tell them apart; a path always can."""
    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {
                "id": "verify",
                "kind": "exec",
                "steps": [
                    {
                        "id": "checks",
                        "tasks": [{"id": "suite", "kind": "subprocess", "command": "false"}],
                    }
                ],
                "on_failure": {
                    "tasks": [{"id": "repair", "kind": "subprocess", "command": "true"}]
                },
            }
        ],
        repo,
    )

    status, evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "needs_human"
    assert "verify.checks.suite" in [s["hook_point"] for s in sessions]
    assert "verify.on_failure.main.repair" in [s["hook_point"] for s in sessions]
    recovery = next(e for e in evts if e["type"] == "node_recovery_started")
    assert recovery["payload"]["failed_tasks"] == ["verify.checks.suite"]
    assert recovery["payload"]["tasks"] == ["verify.on_failure.main.repair"]
    # Operator-facing text names the task, not its address (Ruling 1's second
    # half): the reason already says which node it is in.
    stop = next(e for e in evts if e["type"] == "work_item_needs_human")
    assert "suite [subprocess]" in stop["payload"]["reason"]


def test_the_worktree_and_setup_command_are_prepared_without_an_env_node(tmp_path):
    """Ruling 4: `env_setup` is not a V1 builtin, so worktree creation, the
    repo's setup command and the uncarried-local-files report are implicit
    runtime preparation done before the first node dispatches."""
    repo = make_repo(tmp_path)
    chain = _v1(
        [
            {
                "id": "build",
                "kind": "exec",
                "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
            }
        ],
        repo,
    )

    status, evts, sessions, _row = asyncio.run(
        v1_walk(
            tmp_path,
            chain,
            repo=repo,
            repo_entry={"setup_command": "touch prepared.marker"},
        )
    )

    assert status == "completed"
    assert (tmp_path / "run" / "worktrees" / "w1" / "prepared.marker").is_file()
    # No session stands in for the deleted node, and nothing names its hook.
    assert [s["hook_point"] for s in sessions] == ["build.main.run"]
    prepared = next(e for e in evts if e["type"] == "worktree_prepared")
    assert "touch prepared.marker" in prepared["payload"]["report"]


def test_a_gate_node_halts_the_walk_before_the_node_after_it(tmp_path):
    """`gate-node-opens-and-halts-execution` / `gate-is-an-ordered-node`: the
    gate is its own ordered node, identified by its node id, and the node after
    it does not start."""
    repo = make_repo(tmp_path)
    after = tmp_path / "after.txt"
    chain = _v1(
        [
            {
                "id": "spec",
                "kind": "exec",
                "tasks": [{"id": "write", "kind": "subprocess", "command": "true"}],
            },
            {"id": "spec_approval", "kind": "gate", "message": "Approve the spec."},
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [
                    {"id": "build", "kind": "subprocess", "command": f"touch {after}"},
                ],
            },
        ],
        repo,
    )

    status, evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "awaiting_gate"
    requested = next(e for e in evts if e["type"] == "gate_requested")
    assert requested["payload"] == {"gate": "spec_approval", "node_id": "spec_approval"}
    assert [s["hook_point"] for s in sessions] == ["spec.main.write"]
    assert not after.exists()


# Ruling 2 named four semantics that survive the conversion unchanged. Three of
# them had no test of their own -- only the legacy-red ones Task 5 converts,
# which is the worst place for an invariant to live while an implementation is
# being rewritten under it.


def _two_step_node(marker_a: Path, marker_b: Path, *, first_fails: bool):
    tail = "exit 1" if first_fails else "true"
    return {
        "id": "verify",
        "kind": "exec",
        "steps": [
            {
                "id": "first",
                "tasks": [
                    {
                        "id": "a",
                        "kind": "subprocess",
                        "command": f"sh -c 'touch {marker_a}; {tail}'",
                    }
                ],
            },
            {
                "id": "second",
                "tasks": [{"id": "b", "kind": "subprocess", "command": f"touch {marker_b}"}],
            },
        ],
    }


def test_a_step_that_does_not_pass_stops_the_node_before_the_next_step(tmp_path):
    """Ruling 2's break-on-anything-but-`_ADVANCING` rule: a later step exists
    precisely because it must not run against an unsettled earlier one."""
    repo = make_repo(tmp_path)
    first, second = tmp_path / "a.marker", tmp_path / "b.marker"
    chain = v1_chain([_two_step_node(first, second, first_fails=True)], repo=repo)

    status, _evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "needs_human"
    assert first.exists()
    assert not second.exists()
    assert [s["hook_point"] for s in sessions] == ["verify.first.a"]


def test_a_resumed_node_skips_the_steps_that_already_passed(tmp_path):
    """Ruling 2's `start_step` semantics: a resumed node re-enters at the step
    that stopped it and does not re-buy the ones before it."""
    repo = make_repo(tmp_path)
    first, second = tmp_path / "a.marker", tmp_path / "b.marker"
    chain = v1_chain([_two_step_node(first, second, first_fails=False)], repo=repo)

    status, _evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo, start_step=1))

    assert status == "completed"
    assert not first.exists()
    assert second.exists()
    assert [s["hook_point"] for s in sessions] == ["verify.second.b"]


def test_the_resume_cursor_records_the_step_that_stopped_the_node(tmp_path):
    """Ruling 2's `store.set_current_step` write before each group: recorded
    before the step runs, not after, so a crash mid-step resumes at that step
    rather than past it."""
    repo = make_repo(tmp_path)
    first, second = tmp_path / "a.marker", tmp_path / "b.marker"
    node = _two_step_node(first, second, first_fails=False)
    node["steps"][1]["tasks"][0]["command"] = "false"
    chain = v1_chain([node], repo=repo)

    status, _evts, _sessions, row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "needs_human"
    assert row["current_step"] == 1


def test_a_recovery_pass_does_not_move_the_resume_cursor(tmp_path):
    """The one semantics this task changed on purpose: only the node's *own*
    steps are the node's progress, so a recovery plan's steps neither re-enter
    the node nor overwrite the cursor a later measuring pass reads back. The
    node stopped at its second step; the repair runs two steps of its own and
    the cursor still says 1."""
    repo = make_repo(tmp_path)
    chain = v1_chain(
        [
            {
                "id": "verify",
                "kind": "exec",
                "steps": [
                    {
                        "id": "first",
                        "tasks": [{"id": "a", "kind": "subprocess", "command": "true"}],
                    },
                    {
                        "id": "second",
                        "tasks": [{"id": "b", "kind": "subprocess", "command": "false"}],
                    },
                ],
                # The repair's own first step fails, so `recover_node` returns
                # before its re-measure and nothing else can touch the cursor.
                "on_failure": {
                    "steps": [
                        {
                            "id": "repair",
                            "tasks": [{"id": "r", "kind": "subprocess", "command": "false"}],
                        },
                        {
                            "id": "sync",
                            "tasks": [{"id": "s", "kind": "subprocess", "command": "true"}],
                        },
                    ]
                },
            }
        ],
        repo=repo,
    )

    status, evts, sessions, row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "needs_human"
    assert any(e["type"] == "node_recovery_started" for e in evts)
    assert "verify.on_failure.repair.r" in [s["hook_point"] for s in sessions]
    assert row["current_step"] == 1


def test_a_re_entered_walk_does_not_re_prepare_the_worktree(tmp_path):
    """`env_setup` was a node, so a re-entry at `start_index > 0` skipped it.
    The `ci_wait` poller, `rate_limit_retry`, gate approval and every `/retry`
    re-enter `run_once` that way -- running the repo's `setup_command` per
    dispatch attempt instead of per item would re-`uv sync` a worktree for every
    poll of one pipeline. `ensure_worktree` stays unconditional: a resumed walk
    still needs the worktree to be there."""
    repo = make_repo(tmp_path)
    runs = tmp_path / "setup-runs"
    chain = v1_chain(
        [
            {
                "id": "first",
                "kind": "exec",
                "tasks": [{"id": "a", "kind": "subprocess", "command": "true"}],
            },
            {
                "id": "second",
                "kind": "exec",
                "tasks": [{"id": "b", "kind": "subprocess", "command": "true"}],
            },
        ],
        repo=repo,
    )

    status, evts, sessions, _row = asyncio.run(
        v1_walk(
            tmp_path,
            chain,
            repo=repo,
            repo_entry={"setup_command": f"sh -c 'echo run >> {runs}'"},
            start_index=1,
        )
    )

    assert status == "completed"
    # Cut the worktree (which prepares it once, inside `ensure_worktree`) and
    # walked only the node it was asked to.
    assert (tmp_path / "run" / "worktrees" / "w1" / "calc.py").is_file()
    assert runs.read_text().count("run") == 1
    assert [s["hook_point"] for s in sessions] == ["second.main.b"]
    assert not any(e["type"] == "worktree_prepared" for e in evts)


def test_an_exec_node_that_completes_advances_to_the_next_exec_node(tmp_path):
    """`exec-node-runs-then-advances`. Two *consecutive* execution nodes, which
    no chain in this tree had: every other walk test either has one exec node, or
    a gate between them, so the advancement the requirement is about was pinned
    by nothing (4a's review finding L9).

    The second node's task appends to the same file, so order is observable and
    not just membership.
    """
    repo = make_repo(tmp_path)
    log = tmp_path / "order.txt"
    chain = _v1(
        [
            {
                "id": "first",
                "kind": "exec",
                "tasks": [
                    {"id": "run", "kind": "subprocess", "command": f"sh -c 'echo a >> {log}'"}
                ],
            },
            {
                "id": "second",
                "kind": "exec",
                "tasks": [
                    {"id": "run", "kind": "subprocess", "command": f"sh -c 'echo b >> {log}'"}
                ],
            },
        ],
        repo,
    )

    status, evts, sessions, _row = asyncio.run(v1_walk(tmp_path, chain, repo=repo))

    assert status == "completed"
    assert log.read_text().split() == ["a", "b"]
    assert [s["hook_point"] for s in sessions] == ["first.main.run", "second.main.run"]
    # And the first node is *completed* before the second is entered -- an
    # advance that ran the second node without closing the first would leave the
    # board showing two nodes running at once.
    node_events = [
        (e["type"], e["payload"]["node_id"])
        for e in evts
        if e["type"] in ("node_started", "node_completed")
    ]
    assert node_events == [
        ("node_started", "first"),
        ("node_completed", "first"),
        ("node_started", "second"),
        ("node_completed", "second"),
    ]
