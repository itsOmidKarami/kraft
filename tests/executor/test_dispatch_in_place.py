"""`executor.dispatch` around an in-place item's agent task: it is marked
before it launches and checked after, never swept. The person's edits stay
theirs, and only an agent committing one of them, or the checkout moving
mid-task, stops the item."""

from __future__ import annotations

import shlex
import subprocess

import pytest
from support.harness import entry_of, git, write

from kraft import events, executor, inplace, store
from kraft import policy as _policy
from kraft.executor import dispatch, gates
from kraft.templates.environment import WorkItemTarget

from .test_dispatch import _dispatch_one, _one_task
from .test_walk import _agent, _exec, _loop_policy, _stop_reason, _sub, _walk

_IN_PLACE = WorkItemTarget.for_repository("sample", in_place=True)

#: What the agent commits, by path, as it is told to in a person's checkout.
_COMMIT = "git add -- {f} && git commit -q -m agent -- {f}"


@pytest.fixture
def agent_runs(fake_agent, monkeypatch):
    """The fake agent, running `sh -c <script>` in the checkout."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")

    def run(script: str) -> None:
        monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", script)

    return run


async def test_an_edited_file_the_agent_never_touched_stays_uncommitted_and_unstopped(
    item_on, fake_agent, agent_runs, repo
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    write(repo, "test_calc.py", "# the person's edit\n")
    agent_runs("echo agent > f.txt && " + _COMMIT.format(f="f.txt") + " && echo x > left.txt")

    assert await _walk(it) != "needs_human"

    branch = store.branch_for(it.row())
    assert git(repo, "status", "--porcelain").splitlines() == ["M test_calc.py", "?? left.txt"]
    assert git(repo, "show", f"{branch}:f.txt") == "agent"
    assert "test_calc.py" not in git(repo, "log", "--name-only", "--format=", f"main..{branch}")
    assert it.events("sweep_failed") == []
    [left] = it.events("left_uncommitted")
    assert (left["payload"]["paths"], left["payload"]["count"]) == (["left.txt"], 1)
    assert inplace.leftovers(it.run_dirs, it.id) == {
        "left.txt": git(repo, "hash-object", "left.txt")
    }
    [marked] = [e for e in it.events("task_marked") if e["payload"]["task"].endswith("implement")]
    assert marked["payload"]["dirty"] == ["test_calc.py"]
    run = it.database.read(
        lambda c: c.execute(
            "SELECT dirty FROM node_runs WHERE work_item_id = ? AND node_id = 'implementation'",
            (it.id,),
        ).fetchone()
    )
    assert run["dirty"] == 1, "the run's leftover is the item's own uncommitted work"


async def test_a_large_dirty_set_is_sampled_on_the_event_and_kept_whole_in_a_file(
    item_on, fake_agent, agent_runs, repo
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    for i in range(60):
        write(repo, f"mine/{i:02}.txt", "the person's\n")
    agent_runs("echo agent > mine/59.txt && " + _COMMIT.format(f="mine/59.txt"))

    assert await _walk(it) == "needs_human"

    [marked] = it.events("task_marked")
    assert marked["payload"]["dirty_count"] == 60
    assert len(marked["payload"]["dirty"]) == inplace.SAMPLE
    mark = inplace.load_mark(it.run_dirs, it.id, "implementation.main.implement")
    assert len(mark.dirty) == 60
    assert "the agent committed mine/59.txt" in _stop_reason(it), "overlap read from the file"


@pytest.mark.parametrize("paused", [False, True], ids=["done", "paused-as-it-ended"])
async def test_the_agent_committing_a_persons_edited_file_stops_as_checkout(
    item_on, fake_agent, agent_runs, repo, monkeypatch, paused
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    write(repo, "test_calc.py", "# the person's edit\n")
    agent_runs("echo agent >> test_calc.py && " + _COMMIT.format(f="test_calc.py"))
    if paused:
        real = dispatch._agent.run_agent_task

        async def pause_racing(*args, **kwargs):
            await real(*args, **kwargs)
            return "paused"

        monkeypatch.setattr(dispatch._agent, "run_agent_task", pause_racing)

    assert await _walk(it) == "needs_human"

    assert it.row()["stop_kind"] == "checkout"
    assert "the agent committed test_calc.py" in _stop_reason(it)
    branch = store.branch_for(it.row())
    assert git(repo, "log", "-1", "--format=%s", branch) == "agent", "Kraft undid the commit"
    [session] = it.sessions("implementation")
    assert session["status"] == "done", "the stop rides on the task's own session"


async def _resume(it):
    await it.database.write(lambda c: store.claim_for_run(c, it.id, from_statuses=["needs_human"]))
    await it.database.write(lambda c: store.resume_work_item(c, it.id, None))
    await _walk(it)


@pytest.mark.parametrize(
    ("script", "runs"),
    [
        ("echo agent >> test_calc.py && " + _COMMIT.format(f="test_calc.py"), 1),
        ("git checkout -q main && echo stray >> calc.py && git commit -q -am stray", 2),
    ],
    ids=["overlap-goes-on", "stray-commit-reruns"],
)
async def test_resume_after_a_checkout_stop_reruns_the_task_only_for_stray_commits(
    item_on, fake_agent, agent_runs, repo, monkeypatch, script, runs
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    write(repo, "test_calc.py", "# the person's edit\n")
    agent_runs(script)
    assert await _walk(it) == "needs_human"
    git(repo, "checkout", "-q", "--", "test_calc.py")
    monkeypatch.setenv("KRAFT_FAKE_AGENT_SHELL", "true")

    await _resume(it)

    implement = [s for s in it.sessions("implementation") if s["hook_point"].endswith("implement")]
    assert len(implement) == runs


async def test_a_later_task_committing_an_earlier_tasks_leftover_does_not_stop(
    item_on, agent_runs, repo
):
    chain = [_exec("first", _agent()), _exec("second", _agent())]
    it = await item_on(chain, target=_IN_PLACE)
    agent_runs(
        "if [ -e left.txt ]; then " + _COMMIT.format(f="left.txt") + "; else echo l > left.txt; fi"
    )

    assert await _walk(it) != "needs_human"

    branch = store.branch_for(it.row())
    assert git(repo, "show", f"{branch}:left.txt") == "l"
    assert [e["payload"]["task"] for e in it.events("left_uncommitted")] == ["first.main.implement"]


async def test_resume_reruns_a_task_stopped_in_a_later_round_from_round_0(
    item_on, agent_runs, repo, tmp_path
):
    """Stopped at round 1, after `on_failure` re-measured the node: a resume
    measures from round 0 again, where that round-1 session stands for nothing."""
    node = {
        "id": "checks",
        "kind": "exec",
        "steps": [
            {"id": "work", "tasks": [_agent()]},
            {"id": "suite", "tasks": [_sub("suite", ["false"])]},
        ],
        "on_failure": {"tasks": [_sub("repair", ["true"])]},
    }
    it = await item_on([node], target=_IN_PLACE)
    write(repo, "test_calc.py", "# the person's edit\n")
    # The second run, round 1's, commits the person's file.
    agent_runs(
        "n=$(( $(cat .git/runs 2>/dev/null || echo 0) + 1 )); echo $n > .git/runs; "
        "if [ $n = 2 ]; then echo agent >> test_calc.py && "
        + _COMMIT.format(f="test_calc.py")
        + "; fi"
    )
    policy = _loop_policy(tmp_path, attempts=3)
    assert await _walk(it, policy=policy) == "needs_human"
    assert it.row()["stop_kind"] == "checkout"
    assert [s["round"] for s in _implements(it)] == [0, 1]

    await it.database.write(lambda c: store.claim_for_run(c, it.id, from_statuses=["needs_human"]))
    await it.database.write(lambda c: store.resume_work_item(c, it.id, None))
    await _walk(it, policy=policy)

    assert [s["round"] for s in _implements(it)][:3] == [0, 1, 0], "round 0 reused round 1's"


def _implements(it):
    return [s for s in it.sessions("checks") if s["hook_point"].endswith("implement")]


@pytest.mark.parametrize(
    ("back", "commit"),
    [(False, True), (True, True), (False, False)],
    ids=["stays-away", "switched-back", "stays-away-with-no-commit"],
)
async def test_a_branch_switch_mid_task_stops_and_never_switches_back(
    item_on, fake_agent, agent_runs, repo, back, commit
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    away = "git checkout -q main" + (" && echo stray >> calc.py && git commit -q -am stray" * commit)
    agent_runs(away + (" && git checkout -q -" if back else ""))

    assert await _walk(it) == "needs_human"

    stray = git(repo, "rev-parse", "main")
    on = f"refs/heads/{store.branch_for(it.row())}" if back else "refs/heads/main"
    assert git(repo, "symbolic-ref", "HEAD") == on, "Kraft switched the checkout"
    assert not commit or git(repo, "log", "-1", "--format=%s", "main") == "stray", "moved it"
    assert it.row()["stop_kind"] == "checkout"
    reason = _stop_reason(it)
    assert back or "the checkout moved to main during the run" in reason
    assert (stray in reason) is commit
    assert ("nor move those commits" in reason) is (commit and not back), "no commits to move"


async def test_the_persons_rebase_under_way_after_a_task_is_named_not_a_move(
    item_on, fake_agent, agent_runs, repo
):
    """A rebase detaches HEAD: the stop names it as theirs to finish."""
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    agent_runs(
        "echo a > a.txt && " + _COMMIT.format(f="a.txt") + " && git rebase -x false HEAD~1; true"
    )

    assert await _walk(it) == "needs_human"

    assert it.row()["stop_kind"] == "checkout"
    reason = _stop_reason(it)
    assert "has a git operation in progress" in reason and "at the end of the run of" in reason
    assert "moved to" not in reason


async def test_resume_after_a_fix_loop_tasks_checkout_stop_runs_its_next_round(
    item_on, agent_runs, repo, tmp_path
):
    """The stopped task is kept once, for its own round: the fix loop's next
    cycle still runs its repair."""
    node = _exec("checks", _sub("suite", ["false"]), fix_loop={"tasks": [_agent("repair")]})
    it = await item_on([node], target=_IN_PLACE)
    write(repo, "test_calc.py", "# the person's edit\n")
    commit_once = "touch .git/repaired && echo agent >> test_calc.py && " + _COMMIT.format(
        f="test_calc.py"
    )
    agent_runs(f"[ -e .git/repaired ] || {{ {commit_once}; }}")
    policy = _loop_policy(tmp_path, attempts=3)
    assert await _walk(it, policy=policy) == "needs_human"
    assert it.row()["stop_kind"] == "checkout"

    await it.database.write(lambda c: store.claim_for_run(c, it.id, from_statuses=["needs_human"]))
    await it.database.write(lambda c: store.resume_work_item(c, it.id, None))
    await _walk(it, policy=policy)

    repairs = [s for s in it.sessions("checks") if s["hook_point"].endswith("repair")]
    assert len(repairs) > 1, "the next fix cycle reused the stopped repair"


async def test_a_checkout_stop_is_never_auto_escalated(
    item_on, fake_agent, agent_runs, repo, monkeypatch
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    agent_runs("git checkout -q main")
    dispatched = []

    async def dispatch(*args, **kwargs):
        dispatched.append(kwargs)
        return "done"

    monkeypatch.setattr(gates.escalate, "dispatch", dispatch)
    assert await _walk(it) == "needs_human"
    evts = it.database.read(lambda c: events.read_after(c, 0, it.id))

    assert not gates.stuck_stop(evts)
    status = await gates.auto_escalate_stuck(
        "needs_human",
        it.database,
        it.run_dirs,
        work_item_id=it.id,
        policy=_policy.Policy(loops={}, default=_policy.Cap(1, 1), auto_escalate_delay_s=0),
        launch=executor.LaunchContext(repo_entry=None),
    )
    assert status == "needs_human"
    assert dispatched == []


@pytest.mark.parametrize("task", ["escalation", "implementation.reviewer"])
async def test_a_checkout_stop_from_a_session_outside_the_walk_keeps_no_node_task(
    item_on, fake_agent, repo, task
):
    """Stopped by an escalation's or a reviewer's after-check: its task is not
    one of the node's, so a resume keeps none of them past it and runs them."""
    it = await item_on(fake_agent.quick_task, "implementation", target=_IN_PLACE)
    await inplace.prepare(it.database, it.run_dirs, it.row(), base="main")
    await inplace.take(it.database, it.run_dirs, it.row(), attachments=[])
    await it.session("s0", "implementation.main.implement", "done")
    write(repo, "calc.py", "# the person's edit\n")
    await inplace.mark_task(it.database, it.run_dirs, it.row(), repo, "implementation", task)
    await it.session("s1", task, "done", node="implementation")  # the session it checks
    # At the real time, as an agent's: the check reads a timed reflog.
    subprocess.run(["git", "commit", "-q", "-m", "agent", "--", "calc.py"], cwd=repo, check=True)
    await inplace.check_task(it.database, it.run_dirs, it.row(), repo, "implementation", task)
    assert it.row()["stop_kind"] == "checkout"

    kept = it.database.read(
        lambda c: dispatch.kept_past_checkout_stop(
            c, it.id, "implementation", "implementation.main.implement", 0
        )
    )

    assert kept is None


@pytest.mark.parametrize(
    "target, check_clean", [(_IN_PLACE, False), (None, True)], ids=["in-place", "worktree"]
)
async def test_open_mr_refuses_a_dirty_folder_only_for_a_worktree_item(
    item_on, monkeypatch, target, check_clean
):
    """The person's own edits in their checkout never stop `open_mr`; a
    worktree item's uncommitted work still does."""
    seen: dict = {}

    async def run_task(_db, _rd, **kw):
        seen.update(kw)
        return "done"

    monkeypatch.setattr(dispatch._forge, "run_task", run_task)
    it = await item_on(
        _one_task({"id": "t", "kind": "forge", "target": "mr.open_draft"}), target=target
    )

    assert await _dispatch_one(it) == "done"
    assert seen["check_clean"] is check_clean


_AWAY = ["sh", "-c", "git checkout -q main"]


@pytest.mark.parametrize(
    ("task", "test_command"),
    [
        (_sub("suite", _AWAY), None),
        ({"id": "suite", "kind": "builtin", "ref": "kraft.verify_changed_test_scopes"}, _AWAY),
    ],
    ids=["command", "changed-test-scopes"],
)
async def test_a_branch_switch_during_a_command_task_stops_and_its_pass_never_counts(
    item_on, repo, task, test_command
):
    """A passing verdict measured on the person's branch is no verdict on the
    item's: the walk stops as `checkout`, and a resume runs the command again."""
    it = await item_on([_exec("checks", task)], target=_IN_PLACE)
    entry = {"setup_command": ""} | (
        {"test_command": shlex.join(test_command)} if test_command else {}
    )

    assert await _walk(it, repo_entry=entry_of(entry)) == "needs_human"

    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main", "Kraft switched it back"
    assert it.row()["stop_kind"] == "checkout"
    assert "the checkout moved to main during the run of checks.main.suite in " in _stop_reason(it)
    assert it.events("node_completed") == []

    await _resume(it)

    assert len(it.sessions("checks")) == 2, "the resume kept the command's pass"
