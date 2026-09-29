"""What a sandboxed launch of a workspace item is handed to mount (Kraft-ju36l)."""

from pathlib import Path

import pytest
from support.harness import entry_of
from support.workspace import repositories, workspace_item

from kraft.adapters import agent as agent_mod
from kraft.executor import dispatch
from kraft.executor.context import LaunchContext
from kraft.policy import InstancePolicy, InstancePolicyInput, SandboxPolicy, TemplatePolicyOverride
from kraft.worker.sandbox import Checkout, member_gitdirs


@pytest.mark.parametrize(
    "task",
    [
        {"id": "t", "kind": "subprocess", "command": "true"},
        {"id": "t", "kind": "agent", "harness": "fake", "prompt": "Do it."},
    ],
    ids=["subprocess", "agent"],
)
async def test_every_sandboxed_run_is_handed_the_whole_checkout(
    database, run_dirs, tmp_path, monkeypatch, fake_agent, task
):
    """Each run of a fanned-out task, the root's and the member's, mounts the
    item's whole checkout: every member's gitdirs, derived from its connected
    repository, so each member's `.git` and admin-dir files are read-only in
    whichever container runs (J6)."""
    policy = InstancePolicy.from_input(InstancePolicyInput()).apply_template_override(
        # With `network:`: a claude-shaped agent is refused a sandbox without one.
        TemplatePolicyOverride(
            sandbox=SandboxPolicy(
                kind="docker", image="img", network={"runtime": {"allow": ["x.io"]}}
            )
        )
    )
    row, node, worktree = await workspace_item(
        database,
        run_dirs,
        tmp_path,
        [task | {"scope": "each_repository"}],
        effective_policy=policy,
        repository_policies={"pkg": policy},
    )
    seen = []

    async def run_task(*_a, cwd, checkout=None, **_k):
        seen.append((Path(cwd), checkout))
        return "done"

    monkeypatch.setattr(dispatch._subprocess, "run_task", run_task)
    monkeypatch.setattr(agent_mod._subprocess, "run_task", run_task)
    launch = LaunchContext(
        repo_entry=entry_of({"setup_command": ""}), repositories=repositories(tmp_path, "pkg")
    )

    status = await dispatch.dispatch_node(
        database, run_dirs, node.steps[0].tasks[0], node, row, worktree, launch=launch
    )

    member = "repos/pkg"
    whole = Checkout(
        worktree, {member: member_gitdirs(tmp_path / "pkg-connected", worktree, member)}
    )
    assert whole.members[member] is not None
    assert (status, seen) == ("done", [(worktree, whole), (worktree / member, whole)])
