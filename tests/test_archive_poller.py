"""The auto-archive poller (UI v2 · 03)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support.harness import fake_registry

from kraft import archive, policy, store

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _state(tmp_path, *, archive_after_days) -> dict:
    """What `archive.tick` reads off `app.state` besides the database (`stub_app`)."""
    return {
        "registry": fake_registry(sys.executable, _FAKE_AGENT),
        "templates_dir": tmp_path / "templates",
        "skills_dir": tmp_path / "skills",
        "policy": policy.Policy(
            loops={},
            default=policy.Cap(attempts=3, wall_clock_s=3600),
            archive_after_days=archive_after_days,
        ),
    }


async def _seed_completed_item(app, repo: Path, *, updated_days_ago: int, wid: str = "w1") -> Path:
    """A completed item with a real worktree, backdated `updated_at`."""
    branch = f"kraft/{wid}"
    worktree = app.state.run_dirs.worktrees / wid
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    updated_at = (datetime.now(UTC) - timedelta(days=updated_days_ago)).isoformat()

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
        store.mark_completed(c, wid)
        c.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (updated_at, wid))

    await app.state.db.write(_write)
    return worktree


async def test_tick_archives_a_completed_item_past_after_days(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    worktree = await _seed_completed_item(app, repo, updated_days_ago=31)

    archived = await archive.tick(app)

    assert archived == ["w1"]
    row = app.state.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["archived_by"] == "auto"
    assert not worktree.exists()


async def test_tick_ignores_an_item_not_yet_due(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    await _seed_completed_item(app, repo, updated_days_ago=1)
    assert await archive.tick(app) == []


@pytest.mark.parametrize("after_days", [None, 0], ids=["none", "zero"])
async def test_tick_does_nothing_when_after_days_is_none_or_zero(
    tmp_path, repo, stub_app, after_days
):
    app = stub_app(**_state(tmp_path, archive_after_days=after_days))
    await _seed_completed_item(app, repo, updated_days_ago=999)
    assert await archive.tick(app) == []
