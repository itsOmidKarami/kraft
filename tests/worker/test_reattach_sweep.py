"""`kraft.worker.reattach`: an agent task that ends across a restart has what
it left uncommitted swept into the branch, the same as one that ended under
the server that launched it (`dispatch.sweep_stragglers`)."""

from __future__ import annotations

import subprocess
import sys

import psutil
import pytest
from support import worktree as wtree

from kraft.config import git_read
from kraft.worker import reattach

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
        await adopted["s1"]
    else:
        await it.session("s1", path, running=(2_000_000_000, 123.0))
        await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert it.sessions()[0]["status"] == "done"
    assert git_read(worktree, "log", "-1", "--format=%s") == (
        "wip: uncommitted work from implementation"
    )
    assert git_read(worktree, "ls-files", "work.txt") == "work.txt"
