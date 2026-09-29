"""An escalation turn of a sandboxed workspace item (Kraft-ju36l)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support.harness import entry_of
from support.workspace import repositories, workspace_item

from kraft import escalate, executor
from kraft.policy import InstancePolicy, InstancePolicyInput, SandboxPolicy, TemplatePolicyOverride
from kraft.worker import refstore

_REL = "repos/pkg"


def _tree(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("swapped", ["member", "gitfile"])
async def test_a_member_symlinked_to_another_worktree_never_launches_a_turn(
    database, run_dirs, tmp_path, templates_dir, monkeypatch, swapped
):
    """The worker swaps its member, or just the member's `.git`, for a symlink
    into another checkout of the same connected repository. The turn, which
    runs no drift check of its own, must not mount that checkout's admin dir
    or take over its ref store: it never launches."""
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates_dir))
    policy = InstancePolicy.from_input(InstancePolicyInput()).apply_template_override(
        TemplatePolicyOverride(
            sandbox=SandboxPolicy(
                kind="docker", image="img", network={"runtime": {"allow": ["x.io"]}}
            )
        )
    )
    task = {"id": "t", "kind": "subprocess", "command": "true"}
    row, node, worktree = await workspace_item(
        database,
        run_dirs,
        tmp_path,
        [task],
        effective_policy=policy,
        repository_policies={"pkg": policy},
    )
    await database.write(
        lambda c: c.execute(
            "UPDATE work_items SET status = 'needs_human', current_node_id = ? WHERE id = ?",
            (node.id, row["id"]),
        )
    )
    victim = tmp_path / "victim"
    connected = tmp_path / "pkg-connected"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "kraft/victim", str(victim)],
        cwd=connected,
        check=True,
    )
    victim_admin = connected / ".git" / "worktrees" / "victim"
    before = _tree(victim_admin)
    if swapped == "member":
        shutil.rmtree(worktree / _REL)
        os.symlink(victim, worktree / _REL)
    else:
        (worktree / _REL / ".git").unlink()
        os.symlink(victim / ".git", worktree / _REL / ".git")

    status = await escalate.dispatch(
        database,
        run_dirs,
        work_item_id=row["id"],
        message="go on",
        launch=executor.LaunchContext(
            repo_entry=entry_of({"setup_command": ""}), repositories=repositories(tmp_path, "pkg")
        ),
    )

    log = next(run_dirs.logs.glob("*.log")).read_text()
    assert status == "config_error"
    assert f"workspace member {_REL} of" in log and "no checkout Kraft made" in log, log
    assert _tree(victim_admin) == before
    assert not refstore.shadow_dir(run_dirs.base, victim_admin.resolve()).exists()
