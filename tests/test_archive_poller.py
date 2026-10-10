"""The auto-archive poller (UI v2 · 03)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from support.archive import days_ago, seed_completed_item

from kraft import archive, policy, storage, store


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


async def test_tick_archives_a_completed_item_past_after_days(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    worktree = await seed_completed_item(app, repo, updated_at=days_ago(31))

    archived = await archive.tick(app)

    assert archived == ["w1"]
    row = app.state.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
    )
    assert row["archived_by"] == "auto"
    assert not worktree.exists()


async def test_tick_archives_an_abandoned_item_past_after_days(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    worktree = await seed_completed_item(
        app, repo, updated_at=days_ago(31), end=store.abandon_work_item
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
    await seed_completed_item(app, repo, updated_at=(now - timedelta(days=30)).isoformat())
    await seed_completed_item(
        app, repo, wid="w2", updated_at=(now - timedelta(days=30, seconds=-1)).isoformat()
    )

    assert await archive.tick(app) == ["w1"]


async def test_tick_ignores_an_item_not_yet_due(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    await seed_completed_item(app, repo, updated_at=days_ago(1))
    assert await archive.tick(app) == []


@pytest.mark.parametrize("after_days", [None, 0], ids=["none", "zero"])
async def test_tick_does_nothing_when_after_days_is_none_or_zero(
    tmp_path, repo, stub_app, after_days
):
    app = stub_app(**_state(tmp_path, archive_after_days=after_days))
    await seed_completed_item(app, repo, updated_at=days_ago(999))
    assert await archive.tick(app) == []


async def test_tick_archives_an_item_whose_repository_is_gone(tmp_path, repo, stub_app):
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    worktree = await seed_completed_item(app, repo, updated_at=days_ago(31))
    repo.rename(repo.with_name("moved"))

    assert await archive.tick(app) == ["w1"]
    assert not worktree.exists()


async def test_one_failing_row_does_not_stop_the_tick(tmp_path, repo, stub_app, monkeypatch):
    """Each row is archived on its own: a raise from the first due row must
    not skip the rest until the next hour's tick, which would meet it first
    again."""
    from kraft.api.routes import lifecycle

    app = stub_app(**_state(tmp_path, archive_after_days=30))
    await seed_completed_item(app, repo, updated_at=days_ago(31), wid="w1")
    await seed_completed_item(app, repo, updated_at=days_ago(31), wid="w2")
    real = lifecycle._archive_one

    async def first_one_fails(app, row, by):
        if row["id"] == "w1":
            raise FileNotFoundError("no such repository")
        return await real(app, row, by)

    monkeypatch.setattr(lifecycle, "_archive_one", first_one_fails)

    assert await archive.tick(app) == ["w2"]


async def test_archiving_takes_the_items_bytes_off_the_storage_cache(tmp_path, repo, stub_app):
    """A clean-up must release held starts without waiting for the next walk."""
    app = stub_app(**_state(tmp_path, archive_after_days=30))
    await seed_completed_item(app, repo, updated_at=days_ago(31))
    app.state.storage_usage = storage.Usage("t", 120, {"w1": 40, "other": 80}, {})

    assert await archive.tick(app) == ["w1"]

    assert (app.state.storage_usage.governed, app.state.storage_usage.items) == (80, {"other": 80})


async def test_an_item_is_archived_once_when_two_callers_race(tmp_path, repo, stub_app):
    """The age poller, the storage poller and the route can hold the same row."""
    import asyncio

    from kraft.api.routes import lifecycle

    app = stub_app(**_state(tmp_path, archive_after_days=30))
    await seed_completed_item(app, repo, updated_at=days_ago(31))
    row = app.state.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id='w1'").fetchone()
    )

    first, second = await asyncio.gather(
        lifecycle._archive_one(app, row, "auto"), lifecycle._archive_one(app, row, "you")
    )

    # The loser answers with who archived it; the winner's answer has no such key.
    assert [first.get("archived_by"), second.get("archived_by")] == [None, "auto"]
    events = app.state.db.read(
        lambda c: c.execute(
            "SELECT count(*) AS n FROM events WHERE work_item_id='w1' AND type='work_item_archived'"
        ).fetchone()
    )
    assert events["n"] == 1
