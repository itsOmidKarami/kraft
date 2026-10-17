"""`executor.dispatch` around an in-place item's agent task: it is marked
before it launches and checked after, never swept. The person's edits stay
theirs, and only an agent committing one of them, or the checkout moving
mid-task, stops the item."""

from __future__ import annotations

import pytest
from support.harness import git, write

from kraft import events, executor, store
from kraft import policy as _policy
from kraft.executor import gates
from kraft.templates.environment import WorkItemTarget

from .test_walk import _agent, _exec, _stop_reason, _walk

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
    assert left["payload"]["paths"] == {"left.txt": git(repo, "hash-object", "left.txt")}
    [marked] = it.events("task_marked")
    assert marked["payload"]["dirty"] == ["test_calc.py"]


async def test_the_agent_committing_a_persons_edited_file_stops_as_checkout(
    item_on, fake_agent, agent_runs, repo
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    write(repo, "test_calc.py", "# the person's edit\n")
    agent_runs("echo agent >> test_calc.py && " + _COMMIT.format(f="test_calc.py"))

    assert await _walk(it) == "needs_human"

    assert it.row()["stop_kind"] == "checkout"
    assert "the agent committed test_calc.py" in _stop_reason(it)
    branch = store.branch_for(it.row())
    assert git(repo, "log", "-1", "--format=%s", branch) == "agent", "Kraft undid the commit"


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


async def test_a_branch_switch_mid_task_stops_and_never_switches_back(
    item_on, fake_agent, agent_runs, repo
):
    it = await item_on(fake_agent.quick_task, target=_IN_PLACE)
    agent_runs("git checkout -q main && echo stray >> calc.py && git commit -q -am stray")

    assert await _walk(it) == "needs_human"

    stray = git(repo, "rev-parse", "main")
    assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(repo, "log", "-1", "--format=%s", "main") == "stray", "Kraft moved the commit"
    assert it.row()["stop_kind"] == "checkout"
    assert "moved to main during the run" in _stop_reason(it)
    assert stray in _stop_reason(it)


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
