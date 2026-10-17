"""`kraft.worker.reattach`: an agent task that ends across a restart has what
it left uncommitted swept into the branch, the same as one that ended under
the server that launched it (`dispatch.sweep_stragglers`)."""

from __future__ import annotations

import subprocess
import sys

import psutil
import pytest
from support import worktree as wtree
from support.harness import git, write

from kraft import events, inplace
from kraft.config import git_read
from kraft.executor import dispatch, resuming
from kraft.templates.environment import WorkItemTarget
from kraft.worker import reattach

_IN_PLACE = WorkItemTarget.for_repository("sample", in_place=True)

_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]
"""


@pytest.mark.parametrize("found", ["adopted", "dead"])
async def test_an_agent_task_that_ended_across_a_restart_is_swept(
    item_on, database, run_dirs, repo, found
):
    """R11E-03: only dispatch ran the sweep, so a re-adopted agent's
    uncommitted work stayed untracked, and the item went on, or completed,
    without it. An adopted live session, and one found dead with its result
    file, are both swept once they end."""
    it = await item_on(_CHAIN, "implementation")
    worktree = await wtree.ensure(database, run_dirs, repo)
    (worktree / "work.txt").write_text("the agent's, never committed\n")
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    path = "implementation.main.implement"

    if found == "adopted":
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
            start_new_session=True,
        )
        await it.session("s1", path, running=(child.pid, psutil.Process(child.pid).create_time()))
        _summary, adopted = await reattach.reattach(database, run_dirs)
        child.stdin.close()
        child.wait()
        await adopted["w1-s1"]
    else:
        await it.session("s1", path, running=(2_000_000_000, 123.0))
        await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert it.sessions()[0]["status"] == "done"
    assert git_read(worktree, "log", "-1", "--format=%s") == (
        "wip: uncommitted work from implementation"
    )
    assert git_read(worktree, "ls-files", "work.txt") == "work.txt"


def _agent_commits(repo, *args: str) -> None:
    """git at the real time, as an agent's: `check` reads a timed reflog, and
    `support.harness.git` commits at a fixed date in the past."""
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True
    )


@pytest.mark.parametrize("what", ["moved-with-a-stray-commit", "overlap"])
async def test_a_reattached_in_place_task_is_checked_not_swept(
    item_on, database, run_dirs, repo, what
):
    """In place, a restart reads the task's mark back and checks it, as
    dispatch would have: nothing committed, no switch back, and the stop
    decides, as dispatch's does, whether a resume runs the task again."""
    it = await item_on(_CHAIN, "implementation", target=_IN_PLACE)
    await inplace.prepare(database, run_dirs, it.row(), base="main")
    await inplace.take(database, run_dirs, it.row(), attachments=[])
    write(repo, "calc.py", "# the person's edit\n")
    path = "implementation.main.implement"
    await inplace.mark_task(database, run_dirs, it.row(), repo, "implementation", path)
    write(repo, "work.txt", "the agent's, never committed\n")
    if what == "overlap":
        _agent_commits(repo, "commit", "-q", "-m", "agent", "--", "calc.py")
    else:
        _agent_commits(repo, "checkout", "-q", "main")
        _agent_commits(repo, "commit", "-q", "-am", "stray")
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    await it.session("s1", path, running=(2_000_000_000, 123.0))

    await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
    assert await resuming.resume_once(database, run_dirs, work_item_id=it.id, adopted={}) == (
        "needs_human"
    )

    assert it.row()["stop_kind"] == "checkout"
    assert it.sessions()[0]["status"] == "done"
    assert it.events("node_completed") == [], "the stopped node was recorded done"
    assert git(repo, "status", "--porcelain", "--", "work.txt") == "?? work.txt"
    assert list(it.events("left_uncommitted")[-1]["payload"]["paths"]) == ["work.txt"]
    stop = it.events("work_item_needs_human")[-1]["payload"]
    kept = database.read(
        lambda c: dispatch.kept_past_checkout_stop(c, it.id, "implementation", path, 0)
    )
    if what == "overlap":
        assert "the agent committed calc.py" in stop["reason"]
        assert kept == "done", "a resume would run the task again"
    else:
        assert git(repo, "symbolic-ref", "HEAD") == "refs/heads/main"
        assert git(repo, "rev-parse", "main") in stop["reason"]
        assert kept is None, "a resume would go on past work that may be on main"


@pytest.mark.parametrize("found", ["adopted", "dead"])
async def test_a_reattached_in_place_escalation_is_checked_before_its_self_retry(
    item_on, database, run_dirs, repo, found
):
    """An escalation turn re-adopted across a restart is checked from the mark
    it left, as its own launch would have, before the retry it asked for is
    consumed: the checkout stop stands, and the retry is dropped."""
    it = await item_on(_CHAIN, "implementation", target=_IN_PLACE, status="needs_human")
    await inplace.prepare(database, run_dirs, it.row(), base="main")
    await inplace.take(database, run_dirs, it.row(), attachments=[])
    write(repo, "calc.py", "# the person's edit\n")
    await inplace.mark_task(database, run_dirs, it.row(), repo, "implementation", "escalation")
    await database.write(
        lambda c: events.append(c, it.id, "escalation_message", {"session_id": "s1"})
    )
    _agent_commits(repo, "commit", "-q", "-m", "agent", "--", "calc.py")
    retry = {"node_id": "implementation", "key": None, "gate_key": None, "steer": "fixed it"}
    await database.write(lambda c: events.append(c, it.id, "work_item_self_retry_requested", retry))
    (run_dirs.results / "s1.json").write_text('{"status": "done"}')

    if found == "adopted":
        child = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
            start_new_session=True,
        )
        await it.session(
            "s1", "escalation", running=(child.pid, psutil.Process(child.pid).create_time())
        )
        _summary, adopted = await reattach.reattach(database, run_dirs)
        child.stdin.close()
        child.wait()
    else:
        await it.session("s1", "escalation", running=(2_000_000_000, 123.0))
        _summary, adopted = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
    for task in adopted.values():
        await task

    assert it.row()["stop_kind"] == "checkout"
    assert (
        "the agent committed calc.py" in it.events("work_item_needs_human")[-1]["payload"]["reason"]
    )
    assert len(it.events("work_item_self_retry_dropped")) == 1
    assert not it.events("work_item_retried")
