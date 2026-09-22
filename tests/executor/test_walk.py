"""`executor.walk`: a work item walking its chain -- where it completes, where
it stops, and what the stop says; a node's steps, its `on_failure` repair and
its fix loop; and the work item's bead, which gates and closes the walk."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from support.fake_beads import ON_FAKE_AND_REAL_BD
from support.harness import _git, entry_of, fake_harness_home, isolated_bd, make_repo, v1_resolved

from kraft import events, executor, store
from kraft import findings as _findings
from kraft import policy as _policy
from kraft.adapters import beads
from kraft.adapters import forge as _forge
from kraft.api import deps
from kraft.config import ConfigError
from kraft.executor import dispatch, walk
from kraft.executor.context import Steer

#: A repo that deliberately needs no preparation. Most tests here are about
#: chain walking, not environments.
NO_SETUP = entry_of({"setup_command": ""})
#: The same, on a forge: a V1 forge task runs on `backend: auto`, which reads
#: the forge off the repo entry. Without one the task fails in-process on the
#: missing forge before the patched `resolve` is ever asked for a backend.
FORGE_REPO = entry_of({"setup_command": "", "forge": "github"})


def _agent(task_id="implement", **fields):
    return {"id": task_id, "kind": "agent", "harness": "fake", "prompt": "Do it.", **fields}


def _sub(task_id, argv):
    return {"id": task_id, "kind": "subprocess", "command": shlex.join(argv)}


def _forge_task(task_id, target):
    return {"id": task_id, "kind": "forge", "target": target}


def _exec(node_id, *tasks, **fields):
    return {"id": node_id, "kind": "exec", "tasks": list(tasks), **fields}


def _walk(it, *, repo_entry=NO_SETUP, **kwargs):
    """`run_once` over the item: one walk of its chain from `start_index`."""
    return executor.run_once(
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        launch=executor.LaunchContext(repo_entry=repo_entry),
        **kwargs,
    )


def _loop_policy(tmp_path, attempts=9):
    path = tmp_path / "policy.yaml"
    path.write_text(f"default: {{ attempts: {attempts}, wall_clock_s: 3600 }}\n")
    return _policy.load_policy(path)


def _types(it):
    return [e["type"] for e in it.events()]


def _stop_reason(it):
    return it.events("work_item_needs_human")[-1]["payload"]["reason"]


def _counter(it, key):
    return it.database.read(lambda c: store.read_counter(c, it.id, key))


# -- where a walk completes or stops -------------------------------------------


async def test_done_with_concerns_advances_the_chain(item_on, fake_agent, monkeypatch):
    """An agent reporting `done_with_concerns` is not a failure: the chain keeps
    walking exactly as it would for `done`."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT_STATUS", "done_with_concerns")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_CONCERNS", "tests pass but the API contract feels off")
    it = await item_on(fake_agent.quick_task)

    assert await _walk(it) == "completed"
    assert it.status() == "completed"
    assert [s["status"] for s in it.sessions("implementation")] == ["done_with_concerns"]
    assert _types(it)[-1] == "work_item_completed"
    assert _types(it).count("node_completed") == 2


async def test_rate_limit_stops_the_chain_without_a_fix_loop(item_on, fake_agent, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "rate_limit")
    it = await item_on(fake_agent.quick_task)

    assert await _walk(it) == "rate_limited"
    row = it.row()
    assert (row["status"], row["current_node_id"]) == ("rate_limited", "implementation")
    assert row["retry_at"] == "2026-09-09T15:40:00+00:00"
    types = _types(it)
    assert "rate_limit_hit" in types
    assert "work_item_rate_limited" in types
    # No fix loop, no repair task, no human page for this stop.
    assert "work_item_needs_human" not in types
    assert "node_recovery_started" not in types


async def test_a_waiting_task_marks_the_row_and_ends_the_run(item_on, monkeypatch):
    """The whole point: the executor task ends rather than blocking, and the row
    carries the wait (Kraft-ru98)."""
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _forge.FakeForge(ci_states=["pending"]))
    it = await item_on([_exec("mr_checks", _forge_task("ci_poll", "mr.ci"))])

    assert await _walk(it, repo_entry=FORGE_REPO) == "waiting"
    assert it.status() == "waiting"
    assert it.row()["retry_at"]


async def test_needs_human_names_the_session_that_failed(item_on, fake_agent, monkeypatch):
    """Kraft-eh6p. `work_item_needs_human` is the event a human lands on, and
    its reason names the hook ('task failed in node open_mr: on.mr.open'), not
    the failure — which lives in the failed session's log. Without the session
    id on this event the timeline has nothing to hang a 'view log' button on,
    and the only route to the reason is noticing the preceding
    worker_session_exited row."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "error")
    it = await item_on(fake_agent.quick_task)

    assert await _walk(it) == "needs_human"
    failed = [
        e["payload"]["session_id"]
        for e in it.events("worker_session_exited")
        if e["payload"]["status"] == "failed"
    ]
    assert failed, "the scenario did not produce a failed session"
    assert it.events("work_item_needs_human")[0]["payload"].get("session_id") == failed[-1], (
        "needs_human does not name the session whose log holds the reason"
    )


async def test_walk_attributes_a_config_error_to_the_first_node(item_on, fake_agent):
    """A malformed repos.yaml reaches `ensure_worktree` through the repo entry.
    It must land as needs_human against the node that was about to dispatch,
    not as guard's bare "executor crashed" (Kraft-cuo3p)."""
    it = await item_on(fake_agent.quick_task)

    poisoned = deps._PoisonedRepoEntry(ConfigError("repos.yaml: boom"))
    assert await _walk(it, repo_entry=poisoned) == "needs_human"
    assert it.status() == "needs_human"
    assert it.row()["current_node_id"] is not None
    assert "boom" in _stop_reason(it)


async def test_pausing_between_nodes_stops_the_walk_before_the_next_one_starts(
    item_on, fake_agent, monkeypatch
):
    """Kraft-e7pm. The original bug: pause landed between env_setup's session
    exit and the next node, so there was no session to signal and the walk
    kept going -- resume then started a second one. This pins the fix at the
    layer that actually stops it: the loop's own per-node status read."""
    it = await item_on(fake_agent.quick_task)
    calls = []
    original_walk_node = walk.walk_node

    async def spy(db_, run_dirs_, wid_, node, row_, worktree_, **kw):
        calls.append(node.id)
        result = await original_walk_node(db_, run_dirs_, wid_, node, row_, worktree_, **kw)
        if node.id == "implementation":
            # the exact original bug: pause lands between two nodes, with no
            # running session for `pause_work_item` to signal
            await it.database.write(lambda c: store.pause_work_item(c, it.id, []))
        return result

    monkeypatch.setattr(walk, "walk_node", spy)
    assert await _walk(it) == "paused"
    assert calls == ["implementation"], "verify must never have been dispatched"
    assert it.status() == "paused"

    # resume: exactly one walk runs the rest of the chain to completion
    monkeypatch.setattr(walk, "walk_node", original_walk_node)
    assert await it.database.write(
        lambda c: store.claim_for_run(c, it.id, from_statuses=["paused"])
    )
    await it.database.write(lambda c: store.resume_work_item(c, it.id, None))
    # start_index=1: verify, the node right after implementation
    assert await _walk(it, start_index=1) == "completed"
    assert _types(it).count("work_item_completed") == 1


async def test_run_once_threads_local_files_from_the_launch_context(item_on, repo):
    """Kraft-gxcmy's production wiring: `run_once` passes
    `launch.repo_entry["local_files"]` into `ensure_worktree`. Every other
    `local_files` test drives `ensure_worktree` directly -- not the path that
    actually runs in production -- so deleting that one line left the whole
    suite green. This test fails if it is."""
    (repo / ".gitignore").write_text(".python-version\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ignore the pin")
    (repo / ".python-version").write_text("3.11\n")
    # No env node in V1: the worktree is prepared before the first node runs.
    it = await item_on([_exec("work", _sub("noop", ["true"]))])

    entry = entry_of({"local_files": [".python-version"], "setup_command": ""})
    assert await _walk(it, repo_entry=entry) == "completed"
    assert (it.worktree / ".python-version").read_text() == "3.11\n"


# -- a steer with nowhere to land (Kraft-s7c04.50) -------------------------------


async def test_a_steer_with_no_agent_dispatch_to_land_in_is_reported_undelivered(
    item_on, monkeypatch
):
    """`Steer` is good for one agent launch, whichever dispatch gets there first
    -- but if this run_once call's only task is `kind: forge` (no prompt for a
    steer to land in at all), it ends `needs_human` having never had anywhere to
    deliver it. That must be visible as an event, not just quietly lost (the
    same shape as the live incident on acf59aafa6bb4512bd68049705414fc7, where a
    steer given at a subprocess-only round vanished with the restart that
    followed)."""
    fake = _forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(_forge.FailedJob("test", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)
    it = await item_on([_exec("mr_checks", _forge_task("ci_poll", "mr.ci"))])

    status = await _walk(it, repo_entry=FORGE_REPO, steer="please look at the flaky pipeline")

    assert status == "needs_human"
    assert [e["payload"]["steer"] for e in it.events("steer_undelivered")] == [
        "please look at the flaky pipeline"
    ]


async def test_a_steer_taken_by_a_real_agent_is_not_reported_undelivered(item_on, fake_agent):
    """Control for the test above: a steer that reaches an actual agent
    dispatch must not also fire `steer_undelivered` -- the delivery path
    itself works (verified live); this only guards the surfacing check
    against false positives on the common, healthy case."""
    it = await item_on(fake_agent.quick_task)

    assert await _walk(it, steer="a note for whichever agent runs first") == "completed"
    assert not it.events("steer_undelivered")


# -- the stop reason names its cause -----------------------------------------------


async def test_needs_human_reason_names_a_failed_forge_task_s_kind(item_on, monkeypatch):
    """Kraft-5m7t: `on.ci.poll` is forge-kind, not an agent session -- a
    `retry --steer` against a node whose only failed task is this one has
    nowhere for the steer text to land. Naming the kind in the stop reason
    is the cheapest way a human (or `retry`'s own caller) can tell that
    before burning a retry on it."""
    fake = _forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(_forge.FailedJob("test", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)
    it = await item_on([_exec("mr_checks", _forge_task("ci_poll", "mr.ci"))])

    assert await _walk(it, repo_entry=FORGE_REPO) == "needs_human"
    assert "ci_poll [forge]" in _stop_reason(it)


def _unstartable_agent(task_id):
    return _agent(task_id, prompt="Repair it.", skill="no-such-method")


_FAILS = _sub("check", ["false"])
_FIX = {"tasks": [_sub("fix", ["true"])]}
_OPEN_WITHOUT_A_FORGE = _exec("draft", _forge_task("open", "mr.open_draft"))


@pytest.mark.parametrize(
    ("node", "repo_entry", "reason_names", "cycles", "sessions"),
    [
        # Review finding 1: a loopless node's `on_failure` repair that never
        # launched read "task failed ... repair [agent] (after on_failure)" --
        # no cause, and not even "could not start".
        (
            _exec("build", _FAILS, on_failure={"tasks": [_unstartable_agent("repair")]}),
            NO_SETUP,
            ["could not start repair in node build", "no-such-method"],
            0,
            None,
        ),
        # Review finding 2: a fixer that never launched spent a cycle and
        # stopped as "stuck: 1 finding(s) unchanged" -- telling a human the
        # fixer tried, when it never ran (Kraft-579: a config_error is terminal).
        (
            _exec("build", _FAILS, fix_loop={"tasks": [_unstartable_agent("fixer")]}),
            NO_SETUP,
            ["could not start fixer in node build", "no-such-method"],
            1,
            ["build.main.check", "build.fix_loop.main.fixer"],
        ),
        # Review finding 4: an in-process task's log is Kraft's own account of
        # why it failed, so the card carries it -- here the repos.yaml remedy for
        # a repo with no forge recorded. A launch refused before it starts, not
        # a failed task: it spends no fix cycle (Kraft-hr0xr).
        (
            _OPEN_WITHOUT_A_FORGE,
            NO_SETUP,
            ["could not start open in node draft", "no forge is recorded for this repo"],
            0,
            None,
        ),
        (
            {**_OPEN_WITHOUT_A_FORGE, "fix_loop": _FIX},
            NO_SETUP,
            ["could not start open in node draft", "no forge is recorded for this repo"],
            0,
            None,
        ),
        # Kraft-hr0xr: `run_agent_task` refuses a launch whose merged options
        # name a capability the harness does not declare -- a repo's
        # `deny_tools` on a provider without it. Counted as a failed task, the
        # fix loop spent paid cycles relaunching into the same refusal and the
        # card read "fix_loop exhausted", with the cause only in the server log.
        (
            _exec("impl", _agent("work"), fix_loop={"tasks": [_agent("repair")]}),
            entry_of({"setup_command": "", "deny_tools": ["Bash"]}),
            ["could not start work in node impl", "deny_tools"],
            0,
            ["impl.main.work"],
        ),
        # The same invariant for a subprocess task: a command `shlex` cannot
        # split never started, so it is a config error naming the command.
        (
            _exec("build", {**_FAILS, "command": "echo 'unclosed"}, fix_loop=_FIX),
            NO_SETUP,
            ["could not start check in node build", "echo 'unclosed"],
            0,
            None,
        ),
    ],
    ids=[
        "on-failure-repair",
        "fix-loop-fixer",
        "forge-task-with-no-forge",
        "forge-task-with-no-forge-in-a-fix-loop",
        "refused-agent-launch",
        "unparsable-command",
    ],
)
async def test_a_task_that_cannot_start_stops_naming_its_cause(
    item_on, tmp_path, node, repo_entry, reason_names, cycles, sessions
):
    """A task that never started is a config error: the node stops for a human
    at once, the card names the task and the cause, no fix cycle is spent on it,
    and the task's own session is the `config_error` a human opens."""
    fake_harness_home(tmp_path, [sys.executable, "-c", ""])
    it = await item_on([node])

    assert await _walk(it, repo_entry=repo_entry, policy=_loop_policy(tmp_path)) == "needs_human"
    reason = _stop_reason(it)
    assert all(part in reason for part in reason_names), reason
    assert len(it.events("fix_cycle_started")) == cycles
    assert it.sessions()[-1]["status"] == "config_error"
    if sessions is not None:
        assert [s["hook_point"] for s in it.sessions()] == sessions


async def test_a_fix_loop_that_caps_out_on_a_raising_task_names_the_exception(
    item_on, tmp_path, monkeypatch
):
    """Kraft-hr0xr: the fix loop threw the measuring pass's exceptions away, so
    a node whose task raised every cycle stopped as "exhausted after N fix
    cycle(s)" with nothing on the card about why. The loopless path already
    folds them into its reason; the loop's cap does too now."""
    real_run_task = dispatch._subprocess.run_task

    async def boom(*a, cmd=None, **kw):
        if cmd == ["false"]:
            raise RuntimeError("the-real-cause")
        return await real_run_task(*a, cmd=cmd, **kw)

    monkeypatch.setattr(dispatch._subprocess, "run_task", boom)
    it = await item_on([_exec("build", _FAILS, fix_loop=_FIX)])

    assert await _walk(it, policy=_loop_policy(tmp_path, attempts=1)) == "needs_human"
    assert "exhausted" in _stop_reason(it)
    assert "the-real-cause" in _stop_reason(it)


async def test_a_config_error_stop_carries_a_bounded_cause(item_on, tmp_path):
    """The card carries the cause, but never an unbounded log line: past the
    cap it is cut, and the full line stays in the session log."""
    fake_harness_home(tmp_path, [sys.executable, "-c", ""])
    skill = "no-such-method-" + "x" * 400
    it = await item_on([_exec("spec", _agent("write", skill=skill))])

    await _walk(it)

    reason = it.events("work_item_needs_human")[0]["payload"]["reason"]
    assert "no-such-method-" in reason and reason.endswith("…")
    assert len(reason) < 400
    assert skill in Path(it.sessions()[0]["log_path"]).read_text()


# -- an in-process failure (Kraft-s7c04.24) -------------------------------------------


class _RaisingForge:
    """A forge whose `open_mr` fails with no findings to report -- the shape
    `ForgeError` takes in `run.py`: `findings = None`, so `finish_session`
    writes no result file at all (a blind in-process failure). Every other
    method a test below might touch on is left unimplemented; nothing here
    reaches them."""

    async def find_mr(self, **kwargs):
        return None

    async def open_mr(self, **kwargs):
        raise _forge.ForgeError("boom: no capacity")


@pytest.mark.parametrize(
    ("co_task", "env", "asked", "loop_opens", "reason_names", "reason_lacks"),
    [
        # `on.mr.open` fails with nothing a worker commit could ever change: the
        # handler runs in the orchestrator, not the worktree. No fix cycle, no
        # loop counter bumped, and the reason names the in-process cause.
        (None, {}, None, False, ["open [forge]", "Reinstall"], []),
        # A co-failing worker-fixable task alone justifies the cycle.
        (_sub("suite", [sys.executable, "-c", "import sys; sys.exit(1)"]), {}, None, True, [], []),
        # A co-task's real findings (a review, or the node's own red pipeline)
        # are not discarded by refusing the cycle.
        (
            _agent("review"),
            {"KRAFT_FAKE_AGENT_FINDING": "a real defect a worker can fix"},
            None,
            True,
            [],
            [],
        ),
        # Ordering control (review finding): a question a fix agent already
        # asked reaches the human, even on a round whose re-measure looks
        # unwinnable on its own -- placed after `needs_context_question` in
        # `walk_node` for exactly this.
        (
            None,
            {},
            "should I use library X or Y?",
            False,
            ["needs_context: should I use library X or Y?"],
            ["Reinstall"],
        ),
    ],
    ids=[
        "blind-failure-alone",
        "beside-a-co-failing-subprocess",
        "beside-a-co-tasks-findings",
        "a-pending-question-outranks-it",
    ],
)
async def test_an_in_process_blind_failure_opens_no_fix_cycle_of_its_own(
    item_on,
    tmp_path,
    fake_agent,
    monkeypatch,
    co_task,
    env,
    asked,
    loop_opens,
    reason_names,
    reason_lacks,
):
    monkeypatch.setattr(_forge.run, "resolve", lambda name: _RaisingForge())
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if asked is not None:
        monkeypatch.setattr(dispatch, "needs_context_question", lambda *a, **k: asked)
    tasks = [_forge_task("open", "mr.open_draft"), *([co_task] if co_task else [])]
    it = await item_on([_exec("checks", *tasks, fix_loop={"tasks": [_agent("repair")]})])

    await _walk(it, repo_entry=FORGE_REPO, policy=_loop_policy(tmp_path))

    assert bool(it.events("fix_cycle_started")) is loop_opens
    assert (_counter(it, "checks.fix_loop") is not None) is loop_opens
    if not loop_opens:
        reason = _stop_reason(it)
        assert all(part in reason for part in reason_names), reason
        assert not any(part in reason for part in reason_lacks), reason


async def test_a_red_pipeline_with_failed_jobs_opens_its_fix_cycle_as_before(
    item_on, tmp_path, fake_agent, monkeypatch
):
    """The healthy control. A `kind: forge` task that fails *with* findings (a
    real red pipeline) is worker-fixable and must open its fix cycle exactly as
    before .24. Without this the change is indistinguishable from the bead's
    wrong version, which would have refused every red-pipeline fix loop too."""
    fake = _forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(_forge.FailedJob("test", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(_forge.run, "resolve", lambda name: fake)
    it = await item_on(
        [
            _exec(
                "mr_checks",
                _forge_task("ci_poll", "mr.ci"),
                fix_loop={"tasks": [_agent("repair")]},
            )
        ]
    )

    # attempts=1 and a FakeForge that reports red forever: the loop caps out
    # after exactly one fix cycle, so the counter read is unambiguous.
    status = await _walk(it, repo_entry=FORGE_REPO, policy=_loop_policy(tmp_path, attempts=1))

    assert it.events("fix_cycle_started"), "a real red pipeline must still open a fix cycle"
    # The re-measure is still red, and the next bump (count=2) breaches -- the
    # counter having bumped past 1 is what proves the loop ran through the
    # ordinary bump_counter path rather than being refused.
    assert _counter(it, "mr_checks.fix_loop")["count"] == 2
    assert status == "needs_human"  # capped after its one allotted cycle


# -- a node's `on_failure` repair (Kraft-rv6i) --------------------------------------


def _gate_check(flag: Path) -> list[str]:
    """A command that fails until `flag` exists — a stand-in for `on.ci.poll`
    against a pipeline that is red for a reason outside the code."""
    return [
        sys.executable,
        "-c",
        f"import pathlib, sys; sys.exit(0 if pathlib.Path({str(flag)!r}).exists() else 1)",
    ]


@pytest.mark.parametrize(
    ("repair", "status", "recoveries"),
    [
        ("touch", "completed", 1),
        # The re-measure is the point: a repair task exiting 0 is not evidence
        # that the thing it was repairing is fixed.
        ("pass", "needs_human", 1),
        # The repair pass is opt-in per node: no `on_failure`, no second
        # measurement and no recovery event.
        (None, "needs_human", 0),
    ],
    ids=["repaired", "a-repair-that-did-not-take", "no-on-failure"],
)
async def test_a_failed_node_repairs_itself_once_and_measures_again(
    item_on, tmp_path, repair, status, recoveries
):
    """A node's tasks run concurrently, so nothing in the node can react to
    what another task in it found, and there was no step at all between a task
    failing and the item dropping to needs_human. With `on_failure` the node
    gets one repair pass -- believed only because the node's own task passes on
    the re-measure, and run once per entry into the node."""
    flag = tmp_path / "labelled"
    commands = {
        "touch": [sys.executable, "-c", f"import pathlib; pathlib.Path({str(flag)!r}).touch()"],
        "pass": [sys.executable, "-c", "pass"],
    }
    node = _exec("checks", _sub("poll", _gate_check(flag)))
    if repair:
        node["on_failure"] = {"tasks": [_sub("sync", commands[repair])]}
    it = await item_on([node])

    assert await _walk(it) == status
    started = it.events("node_recovery_started")
    assert len(started) == recoveries
    for recovery in started:
        assert recovery["payload"] == {
            "node_id": "checks",
            "scope": "node",
            "failed_tasks": ["checks.main.poll"],
            "tasks": ["checks.on_failure.main.sync"],
        }
    if status == "needs_human":
        # The stop says whether a repair was already tried.
        assert ("after on_failure" in _stop_reason(it)) is bool(repair)
    else:
        assert not it.events("work_item_needs_human")


async def _recover(item_on, node, *, steer=None, measured_round=0, collect_at=None, failed=None):
    # `failed`: the ids of the node's tasks that failed; every task by default.
    """The real `recover_node`, with `measure_node` and `collect_findings`
    stubbed out, so what it builds and hands to the repair's `Steer` is pinned
    without paying for an agent. `collect_at` maps round -> findings. Returns
    the `steer` the repair's measure received."""
    seen = []

    async def fake_measure(*args, steer=None, **kwargs):
        seen.append(steer)
        return "ok", [], []

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dispatch, "measure_node", fake_measure)
        mp.setattr(
            dispatch,
            "collect_findings",
            lambda db, w, n, round: ((collect_at or {}).get(round, []), set()),
        )
        it = await item_on([node], repo="/r")
        resolved = it.chain.chain.nodes[0]
        await walk.recover_node(
            it.database,
            it.run_dirs,
            it.id,
            resolved,
            it.row(),
            it.repo,
            failed=[t for t in resolved.steps[0].tasks if failed is None or t.task.id in failed],
            steer=steer,
            launch=None,
            budget=_policy.NO_BUDGET,
            measured_round=measured_round,
        )
    return seen[0]


_RECOVERING = _exec(
    "checks", _sub("poll", ["true"]), on_failure={"tasks": [_sub("sync", ["true"])]}
)


def _finding(message):
    return _findings.Finding(
        severity="critical", message=message, file=None, line=None, source_plugin="checks.main.poll"
    )


async def test_recover_node_seeds_the_repair_with_the_measured_rounds_findings(item_on):
    """.26: the repair agent used to be handed nothing but 'this failed' --
    the diagnosis lived only in the failed session's own log, with no path
    given to it (6c712ea8: 4 of its 16 tool calls were spent hunting for
    one). `recover_node` must read `measured_round`'s findings via
    `dispatch.collect_findings` and seed them into the repair hook's prompt."""
    finding = _finding("poll failed; no output captured.\n\nReproduce with: pytest -k x")

    steer = await _recover(item_on, _RECOVERING, measured_round=3, collect_at={3: [finding]})

    assert steer, "no seeded note reached the repair hook"
    text = steer.take()
    assert "Reproduce with: pytest -k x" in text
    assert finding.message.split("\n")[0] in text


async def test_recover_node_reads_findings_from_the_round_the_failure_measured_at(item_on):
    """The round `recover_node` must read is the round the *failing*
    measurement ran at, not `recover_node`'s own dispatch round -- passing
    the wrong one reads a stale or empty round and seeds nothing (or seeds
    a previous round's stale findings). Pinned by giving round 2 findings
    and round 5 none, and asking `recover_node` for round 5: an
    implementation that read the wrong round would still find round 2's
    findings and pass this test's sibling above for the wrong reason."""
    stale = _finding("stale round 2 finding")

    steer = await _recover(item_on, _RECOVERING, measured_round=5, collect_at={2: [stale]})

    assert "stale round 2 finding" not in steer.take(), "seeded a finding from the wrong round"


async def test_recover_node_merges_an_incoming_human_steer_ahead_of_the_seeded_note(item_on):
    """A person's own instruction leads and keeps its own `source`, so a
    fix-loop judge exemption and the human prompt template both survive the
    merge -- the seeded half is self-labelling text, not a relabelling of
    the human's."""
    incoming = Steer("focus on the auth module", source="human")

    steer = await _recover(
        item_on,
        _RECOVERING,
        steer=incoming,
        measured_round=1,
        collect_at={1: [_finding("the ci failure")]},
    )

    assert steer.source == "human"
    text = steer.take()
    assert "focus on the auth module" in text
    assert "the ci failure" in text


async def test_node_level_repair_is_told_which_tasks_failed_and_that_it_is_re_measured(item_on):
    """With no findings for the round, the repair still gets the orchestrator's
    failure note (Task 4) -- no seeded *findings* note invented out of nothing,
    but the note naming what failed, and only that, is always present."""
    node = _exec(
        "n",
        _sub("alpha", ["true"]),
        _sub("beta", ["true"]),
        on_failure={"tasks": [_sub("fix", ["true"])]},
    )

    text = (await _recover(item_on, node, failed=["alpha"])).take()

    assert "in node n: alpha" in text, "names the task that failed"
    assert "beta" not in text, "does not name a task that passed"
    assert "re-measured" in text, "says its own report is not the verdict"


# -- a fix loop's round, cursor and moved base -----------------------------------


class _StopMeasuring(Exception):
    """Sentinel: unwinds `walk_node` the instant it dispatches its first measure."""


_LOOP_NODE = _exec(
    "verify", _sub("suite", ["true"]), fix_loop={"tasks": [_sub("repair", ["true"])]}
)


async def _first_measured_round(
    item_on, monkeypatch, tmp_path, *, seeded, node=_LOOP_NODE, answer=None
):
    """The `round` `walk_node` stamps its first `dispatch.measure_node` with,
    for a loop counter pre-seeded to `seeded` bumps (None: no `retry_counters`
    row, a first-ever entry). Stops at the first measure rather than running a
    whole paid loop: the defect *is* the value of that kwarg."""
    seen: list[int] = []

    async def fake_measure(*args, round=0, **kwargs):
        seen.append(round)
        if answer is not None:
            return answer
        raise _StopMeasuring

    monkeypatch.setattr(dispatch, "measure_node", fake_measure)
    it = await item_on([node], repo="/r")
    cap = _policy.Cap(attempts=9, wall_clock_s=3600)
    for _ in range(seeded or 0):
        await it.database.write(lambda c: store.bump_counter(c, it.id, "verify.fix_loop", cap))
    try:
        await walk.walk_node(
            it.database,
            it.run_dirs,
            it.id,
            it.chain.chain.nodes[0],
            it.row(),
            tmp_path,
            policy=_loop_policy(tmp_path),
        )
    except _StopMeasuring:
        pass
    return seen


@pytest.mark.parametrize(
    ("seeded", "first_round"),
    [(3, 3), (None, 0)],
    ids=["resumed-mid-loop", "first-ever-entry"],
)
async def test_a_fix_loop_entry_measures_first_at_the_round_its_counter_reached(
    item_on, monkeypatch, tmp_path, seeded, first_round
):
    """A resume mid-loop must not re-buy a measurement of an unchanged tree.

    7ced80e6: the counter read 3, the resumed entry measured at round 0, the
    round-3 session could not match on `round`, and $5.85 / 823s bought a
    byte-identical answer -- charged to the wall cap the judge then stopped on.
    With no counter row yet there is nothing to continue from: round 0.

    Asserts on the `round` kwarg of the first `measure_node` rather than on a
    reused session row: `reusable_session`'s `round = ?` filter is what the
    kwarg feeds, and the kwarg is the defect itself.
    """
    assert await _first_measured_round(item_on, monkeypatch, tmp_path, seeded=seeded) == [
        first_round
    ]


async def test_on_failure_repair_still_fires_on_a_resumed_entry(item_on, monkeypatch, tmp_path):
    """`round == 0` used to mean "first iteration of this entry".

    Once `round` seeds from the counter the two part company, and a resumed
    entry -- exactly the case where a loop is already in trouble -- would
    silently stop running its `on_failure` repair.
    """
    repaired = []

    async def fake_recover(*args, **kwargs):
        repaired.append(kwargs.get("round"))
        raise _StopMeasuring

    monkeypatch.setattr(walk, "recover_node", fake_recover)
    node = {**_LOOP_NODE, "on_failure": {"tasks": [_sub("repair", ["true"])]}}

    rounds = await _first_measured_round(
        item_on, monkeypatch, tmp_path, seeded=4, node=node, answer=("failed", [], [])
    )

    assert rounds == [4]
    assert repaired == [walk._REPAIR_ROUND], "on_failure repair did not run on the resumed entry"


@pytest.fixture
def dispatched(monkeypatch):
    """Every task id `dispatch_node` is asked to run, in order; the real
    dispatch still runs unless a test sets `.answer(task_id)`."""
    real = dispatch.dispatch_node
    calls: list[str] = []

    async def spy(db_, run_dirs_, task, node, row_, wt, **kw):
        calls.append(task.task.id)
        answer = getattr(spy, "answer", None)
        if answer is not None:
            return answer(task.task.id)
        return await real(db_, run_dirs_, task, node, row_, wt, **kw)

    monkeypatch.setattr(dispatch, "dispatch_node", spy)
    spy.calls = calls
    return spy


async def test_a_fix_loop_attempt_remeasures_the_node_from_its_first_step(
    item_on, tmp_path, dispatched
):
    """`fix-loop-remeasures-the-whole-node` (Kraft-mq752): verify is
    [[prep], [test]], and after the repair the node is measured again from
    `prep`, not from the step that failed. A repair can break what an earlier
    step already passed -- on the seeded `post_draft_feedback` a repaired merge
    request must be awaited on CI again, not only on the review that failed.

    Inverts `test_a_fix_loop_retry_resumes_at_the_failed_group`, which pinned
    the opposite (Rulings 149/151)."""
    marker = tmp_path / "first-run-done"
    # fails once, then passes: the fix cycle is what turns it green
    fail_once = (
        f"import pathlib,sys; m=pathlib.Path({str(marker)!r}); "
        "sys.exit(0 if m.exists() else (m.write_text('x') or 1))"
    )
    it = await item_on(
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
        ]
    )

    assert await _walk(it, policy=_loop_policy(tmp_path)) == "completed"
    assert dispatched.calls == ["prep", "test", "fix", "prep", "test"]


async def test_fix_cycle_dispatch_gets_the_same_launch_context(
    item_on, tmp_path, fake_agent, monkeypatch
):
    """The fix cycle's `dispatch_node` (executor's fix-cycle call site) is
    separate from the measuring `dispatch_node` inside `measure_node` --
    missing it means the fix agent silently runs on a different model than
    the one that measured."""
    # verify's own task fails every cycle without ever calling the fake agent,
    # so the only agent launch in this run is the fix cycle's.
    it = await item_on(
        [
            _exec(
                "verify",
                _sub("suite", [sys.executable, "-c", "exit(1)"]),
                fix_loop={"tasks": [_agent("repair")]},
            )
        ]
    )

    await _walk(
        it,
        repo_entry=entry_of({"models": {"fake": "haiku"}, "setup_command": ""}),
        policy=_loop_policy(tmp_path, attempts=1),
    )

    [argv] = fake_agent.argv()  # only the fix cycle ever launches the fake agent
    assert argv[argv.index("--model") + 1] == "haiku"


# -- Template Schema V1: one execution shape ----------------------------------------


async def test_steps_are_ordered_while_tasks_inside_a_step_are_concurrent(item_on, tmp_path):
    """`exec-node-orders-concurrent-task-groups`, end to end: the two tasks in
    one step overlap in time, and the next step does not start until both of
    them have finished. (test_measurement pins the same order with a faked
    dispatch; this is its only end-to-end pin.)"""
    log = tmp_path / "order.txt"

    def marker(name: str) -> str:
        return f"sh -c 'echo {name}-start >> {log}; sleep 0.4; echo {name}-end >> {log}'"

    def task(name):
        return {"id": name, "kind": "subprocess", "command": marker(name)}

    it = await item_on(
        [
            {
                "id": "build",
                "kind": "exec",
                "steps": [
                    {"id": "wide", "tasks": [task("a"), task("b")]},
                    {"id": "after", "tasks": [task("c")]},
                ],
            }
        ]
    )

    assert await _walk(it) == "completed"
    lines = log.read_text().split()
    # Concurrent within the step: both started before either finished.
    assert set(lines[:2]) == {"a-start", "b-start"}
    # Ordered between steps: the later step's task started after both ended.
    assert lines.index("c-start") > max(lines.index("a-end"), lines.index("b-end"))
    hooks = [s["hook_point"] for s in it.sessions()]
    assert sorted(hooks[:2]) == ["build.wide.a", "build.wide.b"]
    assert hooks[2:] == ["build.after.c"]


async def test_a_task_is_identified_by_its_canonical_path_in_sessions_and_events(item_on):
    """Ruling 1: the canonical path replaces the hook name in
    `sessions.hook_point` and in every event payload that names a task. A hook
    name reused by two nodes could not tell them apart; a path always can."""
    it = await item_on(
        [
            {
                "id": "verify",
                "kind": "exec",
                "steps": [{"id": "checks", "tasks": [_sub("suite", ["false"])]}],
                "on_failure": {"tasks": [_sub("repair", ["true"])]},
            }
        ]
    )

    assert await _walk(it) == "needs_human"
    hooks = [s["hook_point"] for s in it.sessions()]
    assert "verify.checks.suite" in hooks
    assert "verify.on_failure.main.repair" in hooks
    recovery = it.events("node_recovery_started")[0]["payload"]
    assert recovery["failed_tasks"] == ["verify.checks.suite"]
    assert recovery["tasks"] == ["verify.on_failure.main.repair"]
    # Operator-facing text names the task, not its address (Ruling 1's second
    # half): the reason already says which node it is in.
    assert "suite [subprocess]" in _stop_reason(it)


async def test_the_worktree_and_setup_command_are_prepared_without_an_env_node(item_on):
    """Ruling 4: `env_setup` is not a V1 builtin, so worktree creation, the
    repo's setup command and the uncarried-local-files report are implicit
    runtime preparation done before the first node dispatches."""
    it = await item_on([_exec("build", _sub("run", ["true"]))])

    entry = entry_of({"setup_command": "touch prepared.marker"})
    assert await _walk(it, repo_entry=entry) == "completed"
    assert (it.worktree / "prepared.marker").is_file()
    # No session stands in for the deleted node, and nothing names its hook.
    assert [s["hook_point"] for s in it.sessions()] == ["build.main.run"]
    assert "touch prepared.marker" in it.events("worktree_prepared")[0]["payload"]["report"]


async def test_a_resumed_node_skips_the_steps_that_already_passed(item_on, tmp_path):
    """Ruling 2's `start_step` semantics: a resumed node re-enters at the step
    that stopped it and does not re-buy the ones before it."""
    first, second = tmp_path / "a.marker", tmp_path / "b.marker"
    it = await item_on(
        [
            {
                "id": "verify",
                "kind": "exec",
                "steps": [
                    {"id": "first", "tasks": [_sub("a", ["touch", str(first)])]},
                    {"id": "second", "tasks": [_sub("b", ["touch", str(second)])]},
                ],
            }
        ]
    )

    assert await _walk(it, start_step=1) == "completed"
    assert not first.exists()
    assert second.exists()
    assert [s["hook_point"] for s in it.sessions()] == ["verify.second.b"]


async def test_a_recovery_pass_does_not_move_the_resume_cursor(item_on):
    """The one semantics this task changed on purpose: only the node's *own*
    steps are the node's progress, so a recovery plan's steps neither re-enter
    the node nor overwrite the cursor a later measuring pass reads back. The
    node stopped at its second step; the repair runs two steps of its own and
    the cursor still says 1."""

    def step(step_id, task_id, command):
        return {"id": step_id, "tasks": [_sub(task_id, [command])]}

    it = await item_on(
        [
            {
                "id": "verify",
                "kind": "exec",
                "steps": [step("first", "a", "true"), step("second", "b", "false")],
                # The repair's own first step fails, so `recover_node` returns
                # before its re-measure and nothing else can touch the cursor.
                "on_failure": {"steps": [step("repair", "r", "false"), step("sync", "s", "true")]},
            }
        ]
    )

    assert await _walk(it) == "needs_human"
    assert it.events("node_recovery_started")
    assert "verify.on_failure.repair.r" in [s["hook_point"] for s in it.sessions()]
    assert it.row()["current_step"] == 1


async def test_a_re_entered_walk_does_not_re_prepare_the_worktree(item_on, tmp_path):
    """`env_setup` was a node, so a re-entry at `start_index > 0` skipped it.
    The `ci_wait` poller, `rate_limit_retry`, gate approval and every `/retry`
    re-enter `run_once` that way -- running the repo's `setup_command` per
    dispatch attempt instead of per item would re-`uv sync` a worktree for every
    poll of one pipeline. `ensure_worktree` stays unconditional: a resumed walk
    still needs the worktree to be there."""
    runs = tmp_path / "setup-runs"
    it = await item_on([_exec("first", _sub("a", ["true"])), _exec("second", _sub("b", ["true"]))])

    status = await _walk(
        it, repo_entry=entry_of({"setup_command": f"sh -c 'echo run >> {runs}'"}), start_index=1
    )

    assert status == "completed"
    # Cut the worktree (which prepares it once, inside `ensure_worktree`) and
    # walked only the node it was asked to.
    assert (it.worktree / "calc.py").is_file()
    assert runs.read_text().count("run") == 1
    assert [s["hook_point"] for s in it.sessions()] == ["second.main.b"]
    assert not it.events("worktree_prepared")


async def test_an_exec_node_that_completes_advances_to_the_next_exec_node(item_on, tmp_path):
    """`exec-node-runs-then-advances`. Two *consecutive* execution nodes, which
    no chain in this tree had: every other walk test either has one exec node, or
    a gate between them, so the advancement the requirement is about was pinned
    by nothing (4a's review finding L9).

    The second node's task appends to the same file, so order is observable and
    not just membership.
    """
    log = tmp_path / "order.txt"
    it = await item_on(
        [
            _exec("first", _sub("run", ["sh", "-c", f"echo a >> {log}"])),
            _exec("second", _sub("run", ["sh", "-c", f"echo b >> {log}"])),
        ]
    )

    assert await _walk(it) == "completed"
    assert log.read_text().split() == ["a", "b"]
    assert [s["hook_point"] for s in it.sessions()] == ["first.main.run", "second.main.run"]
    # And the first node is *completed* before the second is entered -- an
    # advance that ran the second node without closing the first would leave the
    # board showing two nodes running at once.
    assert [
        (e["type"], e["payload"]["node_id"])
        for e in it.events()
        if e["type"] in ("node_started", "node_completed")
    ] == [
        ("node_started", "first"),
        ("node_completed", "first"),
        ("node_started", "second"),
        ("node_completed", "second"),
    ]


# -- the work item's bead: closed at completion, and a blocked one pauses the walk ---


@pytest.fixture
def tracker(tmp_path):
    """The instance-wide bead tracker `bd_cwd` names."""
    return isolated_bd(tmp_path)


async def _intake(database, run_dirs, repo, chain, tracker, **kwargs):
    kwargs.setdefault("title", "make the failing test pass")
    return await executor.intake(
        database, run_dirs, repo=str(repo), chain=chain, bd_cwd=str(tracker), **kwargs
    )


def _run(database, run_dirs, wid, tracker):
    return executor.run(
        database,
        run_dirs,
        work_item_id=wid,
        bd_cwd=str(tracker),
        launch=executor.LaunchContext(repo_entry=NO_SETUP),
    )


def _row(database, wid):
    return dict(
        database.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
    )


async def test_run_happy_path_completes_and_closes_bead(
    bd, database, run_dirs, repo, tracker, fake_agent
):
    wid = await _intake(database, run_dirs, repo, fake_agent.quick_task, tracker)

    assert await _run(database, run_dirs, wid, tracker) == "completed"
    worktree = run_dirs.worktrees / wid
    assert "a + b" in (worktree / "calc.py").read_text()
    verify = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=worktree, capture_output=True
    )
    assert verify.returncode == 0
    row = _row(database, wid)
    assert row["status"] == "completed"
    assert bd.status(row["bead_id"], cwd=tracker) == "closed"
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]
    assert types[:2] == ["work_item_created", "chain_loaded"]
    assert types[-1] == "work_item_completed"
    # V1 quick-task is two nodes: `env_setup` is implicit preparation now.
    assert types.count("node_started") == types.count("node_completed") == 2


async def test_run_verify_failure_stops_at_verify(
    bd, database, run_dirs, repo, tracker, fake_agent, monkeypatch
):
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
    chain = [
        _exec("implementation", _agent()),
        _exec("verify", _sub("suite", [sys.executable, "-m", "pytest", "-q"])),
    ]
    wid = await _intake(database, run_dirs, repo, v1_resolved(chain), tracker)

    assert await _run(database, run_dirs, wid, tracker) == "needs_human"
    row = _row(database, wid)
    assert (row["status"], row["current_node_id"]) == ("needs_human", "verify")
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]
    assert "work_item_needs_human" in types
    assert "work_item_completed" not in types
    # verify started but never completed
    assert (types.count("node_started"), types.count("node_completed")) == (2, 1)
    assert bd.status(row["bead_id"], cwd=tracker) == "open"


@ON_FAKE_AND_REAL_BD
async def test_run_closes_an_auto_intaken_bead_in_its_own_workspace(
    bd, database, run_dirs, repo, tracker, fake_agent, tmp_path
):
    """Auto-intake adopts a bead that already lives in its repo's own `.beads`
    workspace, not the instance-wide tracker `bd_cwd` points at. Closing it in
    `bd_cwd` fails: the id does not exist there (Kraft-8mu.5.2)."""
    other = isolated_bd(tmp_path, name="other")
    bead_id = await beads.intake("make the failing test pass", cwd=str(other))
    wid = await _intake(
        database,
        run_dirs,
        repo,
        fake_agent.quick_task,
        tracker,
        bead_id=bead_id,
        bead_cwd=str(other),
    )

    assert await _run(database, run_dirs, wid, tracker) == "completed"
    assert bd.status(bead_id, cwd=other) == "closed"


@ON_FAKE_AND_REAL_BD
async def test_a_blocked_bead_pauses_the_walk_before_any_worktree_is_made(
    bd, database, run_dirs, repo, tracker, fake_agent
):
    """Kraft-tsfpk: a work item whose bead is `blocked_by` something must
    never create a worktree or start a session."""
    blocker_id = await beads.intake("the blocker", cwd=str(tracker))
    wid = await _intake(
        database, run_dirs, repo, fake_agent.quick_task, tracker, title="do the blocked thing"
    )
    bead_id = _row(database, wid)["bead_id"]
    assert bead_id
    bd.block(bead_id, blocker_id, cwd=tracker)

    assert await _run(database, run_dirs, wid, tracker) == "paused"
    assert not (run_dirs.worktrees / wid).exists()
    assert not database.read(lambda c: c.execute("SELECT id FROM worker_sessions").fetchall())
    assert _row(database, wid)["status"] == "paused"
    blocked = [
        e["payload"]
        for e in database.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == "work_item_blocked_by_dependency"
    ]
    assert [p["blocked_by"] for p in blocked] == [[blocker_id]]


async def test_resuming_a_still_blocked_item_re_pauses_cheaply(
    bd, database, run_dirs, repo, tracker, fake_agent
):
    """The 'cheap refusal' the bead asks for: a resume of a still-blocked item
    costs one `bd blocked` call and re-pauses -- no worker_sessions row."""
    blocker_id = await beads.intake("the blocker", cwd=str(tracker))
    wid = await _intake(
        database, run_dirs, repo, fake_agent.quick_task, tracker, title="do the blocked thing"
    )
    bd.block(_row(database, wid)["bead_id"], blocker_id, cwd=tracker)

    assert await _run(database, run_dirs, wid, tracker) == "paused"
    # A resume re-enters through run_once at the same start_index (0).
    assert await _run(database, run_dirs, wid, tracker) == "paused"
    assert not database.read(lambda c: c.execute("SELECT id FROM worker_sessions").fetchall())


async def test_a_blocked_sub_bead_the_item_states_pauses_the_walk(
    bd, database, run_dirs, repo, fake_agent, tmp_path
):
    """The motivating case plan-review finding 1 named: a manually created
    item's own tracking bead is always edge-free (fresh from `entry.intake`),
    so only a check against `implements_beads` -- the sub-beads the
    item states -- ever catches a real dependency for this path."""
    tracker = bd.init(make_repo(tmp_path, name="tracker"))
    sub = await beads.intake("the sub task", cwd=str(tracker))
    blocker = await beads.intake("the blocker", cwd=str(tracker))
    bd.block(sub, blocker, cwd=tracker)
    wid = await _intake(
        database,
        run_dirs,
        repo,
        fake_agent.quick_task,
        tracker,
        title="implements a blocked sub-bead",
        implements_beads=[sub],
    )
    row = _row(database, wid)
    # The tracking bead itself has no edges -- confirms the case this test is
    # pinning actually needs implements_beads to catch it.
    assert await beads.blocked_by([row["bead_id"]], cwd=str(tracker)) == []
    assert json.loads(row["implements_beads"]) == [sub]

    assert await _run(database, run_dirs, wid, tracker) == "paused"
    assert not (run_dirs.worktrees / wid).exists()
    blocked = [
        e["payload"]["blocked_by"]
        for e in database.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == "work_item_blocked_by_dependency"
    ]
    assert blocked == [[blocker]]


async def test_a_bead_blocked_only_by_its_own_bundlemate_dispatches(
    bd, database, run_dirs, repo, fake_agent, tmp_path
):
    """A work item bundling two beads with a `blocks` edge between them (the
    Kraft-5fx.2..5fx.12 shape) must not read as blocked by a bead it is
    itself implementing -- the blocker here is in the item's own bead set,
    not an outside dependency."""
    tracker = bd.init(make_repo(tmp_path, name="tracker"))
    sub = await beads.intake("the sub task", cwd=str(tracker))
    bundlemate = await beads.intake("bundled dependency", cwd=str(tracker))
    bd.block(sub, bundlemate, cwd=tracker)
    wid = await _intake(
        database,
        run_dirs,
        repo,
        fake_agent.quick_task,
        tracker,
        title="implements two bundled beads",
        implements_beads=[sub, bundlemate],
    )
    assert set(json.loads(_row(database, wid)["implements_beads"])) == {sub, bundlemate}

    assert await _run(database, run_dirs, wid, tracker) == "completed"
