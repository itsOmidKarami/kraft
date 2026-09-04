from __future__ import annotations

from kraft import store
from kraft.adapters import subprocess as _subprocess


async def env_setup(
    db, run_dirs, *, session_id: str, work_item_id: str, node_id: str, repo: str, round: int = 0
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
        round=round,
    )


async def noop(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int = 0,
) -> str:
    """Placeholder task for a hook with no plugin yet: records a done session, does no work."""
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = run_dirs.results / f"{session_id}.json"
    log_path.write_text(f"noop placeholder for {hook_point}\n")
    await db.write(
        lambda c: store.create_session(
            c,
            id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            log_path=str(log_path),
            result_path=str(result_path),
            round=round,
        )
    )
    await db.write(lambda c: store.session_exited(c, session_id, "done"))
    return "done"
