"""The straggler sweep after an agent's run in a workspace member (Kraft-ju36l)."""

from pathlib import Path

from support.harness import entry_of
from support.workspace import (
    ROOT_EMAIL,
    only_the_root_has_an_identity,
    repositories,
    workspace_item,
)

from kraft.adapters import agent as agent_mod
from kraft.config import git_read
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext


async def test_a_members_leftover_work_is_committed_as_the_root(
    database, run_dirs, tmp_path, monkeypatch, fake_agent
):
    """J3: the member is a worktree of its connected repository, whose config
    Kraft never writes. With no identity there or in the global config, the
    sweep still commits what the agent left in the member, as the root's
    identity handed to that one commit."""
    task = {"id": "a", "kind": "agent", "harness": "fake", "prompt": "Do it."}
    row, node, worktree = await workspace_item(
        database, run_dirs, tmp_path, [task | {"scope": "each_repository"}]
    )
    member, connected = worktree / "repos" / "pkg", tmp_path / "pkg-connected"
    only_the_root_has_an_identity(monkeypatch, tmp_path, row)
    before = (connected / ".git" / "config").read_text()

    async def run_task(*_a, cwd, **_k):
        if Path(cwd) == member:
            (member / "left.txt").write_text("left behind\n")
        return "done"

    monkeypatch.setattr(agent_mod._subprocess, "run_task", run_task)
    launch = LaunchContext(
        repo_entry=entry_of({"setup_command": ""}), repositories=repositories(tmp_path, "pkg")
    )

    status = await dispatch.dispatch_node(
        database, run_dirs, node.steps[0].tasks[0], node, row, worktree, launch=launch
    )

    assert status == "done"
    assert "left.txt" in git_read(member, "ls-files")
    assert git_read(member, "log", "-1", "--format=%ae %ce") == f"{ROOT_EMAIL} {ROOT_EMAIL}"
    assert (connected / ".git" / "config").read_text() == before
