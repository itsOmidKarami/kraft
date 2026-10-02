"""The auto-archive poller (UI v2 · 03)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kraft import archive, policy, store


def _state(tmp_path, *, archive_after_days) -> dict:
    """What `archive.tick` reads off `app.state` besides the database (`stub_app`)."""
    return {
        "templates_dir": tmp_path / "templates",
        "skills_dir": tmp_path / "skills",
        "policy": policy.Policy(
            loops={},
            default=policy.Cap(attempts=3, wall_clock_s=3600),
            archive_after_days=archive_after_days,
        ),
    }


async def _seed_completed_item(
    app,
    repo: Path,
    *,
    updated_days_ago: int = 0,
    wid: str = "w1",
    updated_at: str | None = None,
    end=store.mark_completed,
) -> Path:
    """A completed item with a real worktree, backdated `updated_at` (to
    exactly `updated_at` when given). `end` is how it ended:
    `store.abandon_work_item` for an abandoned one."""
    branch = f"kraft/{wid}"
    worktree = app.state.run_dirs.worktrees / wid
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    if updated_at is None:
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
        end(c, wid)
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


async def test_tick_archives_an_abandoned_item_past_after_days(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    worktree = await _seed_completed_item(
        app, repo, updated_days_ago=31, end=store.abandon_work_item
    )
    assert (
        app.state.db.read(
            lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
        )["status"]
        == "abandoned"
    )

    assert await archive.tick(app) == ["w1"]
    assert not worktree.exists()


async def test_tick_archives_an_item_exactly_at_the_cutoff(tmp_path, repo, stub_app, monkeypatch):
    """`archive_after_days` old is old enough: the cutoff itself is due."""
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(archive, "_now", lambda: now.isoformat())
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    await _seed_completed_item(app, repo, updated_at=(now - timedelta(days=30)).isoformat())
    await _seed_completed_item(
        app, repo, wid="w2", updated_at=(now - timedelta(days=30, seconds=-1)).isoformat()
    )

    assert await archive.tick(app) == ["w1"]


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


async def test_tick_archives_an_item_whose_repository_is_gone(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    worktree = await _seed_completed_item(app, repo, updated_days_ago=31)
    repo.rename(repo.with_name("moved"))

    assert await archive.tick(app) == ["w1"]
    assert not worktree.exists()


async def test_one_failing_row_does_not_stop_the_tick(tmp_path, repo, stub_app, monkeypatch):
    """Each row is archived on its own: a raise from the first due row must
    not skip the rest until the next hour's tick, which would meet it first
    again."""
    from kraft.api.routes import lifecycle

    app = stub_app(**_state(tmp_path, archive_after_days=30))
    await _seed_completed_item(app, repo, updated_days_ago=31, wid="w1")
    await _seed_completed_item(app, repo, updated_days_ago=31, wid="w2")
    real = lifecycle._archive_one

    async def first_one_fails(app, row, by):
        if row["id"] == "w1":
            raise FileNotFoundError("no such repository")
        return await real(app, row, by)

    monkeypatch.setattr(lifecycle, "_archive_one", first_one_fails)

    assert await archive.tick(app) == ["w2"]
