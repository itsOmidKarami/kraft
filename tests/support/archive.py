"""Finished work items with a real worktree, for the archive and storage tests."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kraft import store


def days_ago(days: int) -> str:
    """An `updated_at` value `days` before the wall clock."""
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


async def seed_completed_item(
    app, repo: Path, *, updated_at: str, wid: str = "w1", end=store.mark_completed
) -> Path:
    """A completed item with a real worktree, its `updated_at` backdated to
    `updated_at`. `end` is how it ended: `store.abandon_work_item` for an
    abandoned one."""
    branch = f"kraft/{wid}"
    worktree = app.state.run_dirs.worktrees / wid
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    def _write(c):
        store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="quick-task",
            chain_definition="{}",
        )
        end(c, wid)
        c.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (updated_at, wid))

    await app.state.db.write(_write)
    return worktree
