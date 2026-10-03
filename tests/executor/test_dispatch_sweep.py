"""The straggler sweep after an agent's run: what it leaves out of the
commit, and that it says so. The sweep's other cases: test_dispatch.py."""

import subprocess

from support.harness import entry_of, v1_chain, v1_walk


async def test_the_sweep_names_the_lockfile_the_setup_wrote_and_leaves_it_out(
    tmp_path, repo, fake_agent
):
    """An entry from before the probe still runs `uv sync` in a repo with no
    `uv.lock`, which writes one into every worktree. It stays out of the
    item's commits, and the item's events say so: a drop is never silent."""
    entry = entry_of({"setup_command": "echo 'version = 1' > uv.lock", "test_command": "true"})
    chain = v1_chain(fake_agent.quick_task, repo=repo)
    status, evts, _sessions, _row = await v1_walk(tmp_path, chain, repo=repo, repo_entry=entry)

    assert status == "completed"
    worktree = tmp_path / "run" / "worktrees" / "w1"
    committed = subprocess.run(
        ["git", "log", "--name-only", "--format=", "main..HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "calc.py" in committed and "uv.lock" not in committed
    left_out = [e["payload"] for e in evts if e["type"] == "sweep_left_out"]
    assert left_out and left_out[0]["paths"] == ["uv.lock"]
