from __future__ import annotations

from kraft.adapters import subprocess as _subprocess


async def env_setup(
    db, run_dirs, *, session_id: str, work_item_id: str, node_id: str, repo: str
) -> str:
    worktree = run_dirs.worktrees / work_item_id
    branch = f"kraft/{work_item_id}"
    if worktree.is_dir():
        # idempotent: a prior (crashed) run already created the worktree.
        return "done"
    return await _subprocess.run_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point="on.env.prepare",
        cmd=["git", "worktree", "add", str(worktree), "-b", branch],
        cwd=repo,
    )
