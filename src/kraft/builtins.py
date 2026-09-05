from __future__ import annotations

import logging
import shutil
from pathlib import Path

from kraft import store
from kraft.adapters import subprocess as _subprocess
from kraft.config import git_read

logger = logging.getLogger(__name__)


def _copy_attachments(repo: Path, worktree: Path, attachments: list[dict]) -> None:
    """Intake attachments that are not committed do not exist in a fresh
    worktree (`git worktree add` branches from HEAD), so copy them in. A
    committed one arrived through git and is left exactly as git wrote it.

    The path was validated against the *repo's* working tree, not this
    worktree (checked out from HEAD, possibly a different tree). If HEAD has
    a symlink here, `dest.exists()` follows it, which for a dangling symlink
    reports False and `shutil.copyfile` would then write through it to
    wherever it points. So a symlinked destination, dangling or not, is
    always skipped, and the resolved destination must stay inside the
    worktree."""
    worktree_root = worktree.resolve()
    for attachment in attachments:
        dest = worktree / attachment["path"]
        src = repo / attachment["path"]
        if dest.is_symlink():
            continue
        resolved = dest.resolve()
        if resolved != worktree_root and worktree_root not in resolved.parents:
            continue
        if dest.exists() or not src.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


async def env_setup(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    repo: str,
    round: int = 0,
    attachments: list[dict] | None = None,
) -> str:
    worktree = run_dirs.worktrees / work_item_id
    branch = f"kraft/{work_item_id}"
    if worktree.is_dir():
        # idempotent: a prior (crashed) run already created the worktree, and
        # with it any attachment copies.
        return "done"
    # Pin the base *before* the worktree exists, so the early return above
    # guarantees a crashed-and-retried run never re-pins to a moved HEAD.
    head = git_read(Path(repo), "rev-parse", "HEAD")
    if head:
        await db.write(lambda c: store.set_base_ref(c, work_item_id, head))
    else:
        # Without a base_ref the diff endpoint answers "no diff available" for
        # the rest of the item's life; the reason belongs in the log rather
        # than in a reviewer's guesswork.
        logger.warning("no base_ref for %s: rev-parse HEAD failed in %s", work_item_id, repo)
    status = await _subprocess.run_task(
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
    if status == "done":
        _copy_attachments(Path(repo), worktree, attachments or [])
    return status


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
