"""Worktree storage: what is measured, and what the limit decides."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from support.archive import days_ago, seed_completed_item

from kraft import policy, storage

MB = 1024**2


def _fill(path, size=MB):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))


def _policy(limit=None, quota=None, min_age_s=None) -> policy.Policy:
    return policy.Policy(
        loops={},
        default=policy.Cap(attempts=3, wall_clock_s=3600),
        storage_limit_bytes=limit,
        storage_quota_bytes=quota,
        storage_auto_cleanup_min_age_s=min_age_s,
    )


def _usage(governed, items=None) -> storage.Usage:
    return storage.Usage("2026-01-01T00:00:00+00:00", governed, dict(items or {}), {})


def test_measure_of_an_empty_run_folder_is_zero(tmp_path):
    used = storage.measure(tmp_path)
    assert (used.governed, used.items) == (0, {})


def test_measure_totals_each_item_and_category(tmp_path):
    _fill(tmp_path / "worktrees" / "a" / "f")
    _fill(tmp_path / "worktrees" / "b" / "f", 2 * MB)
    _fill(tmp_path / "sandbox-home" / "a" / "cache")
    _fill(tmp_path / "sandbox-git" / "store" / "pack")
    _fill(tmp_path / "logs" / "s.log")
    _fill(tmp_path / "orchestrator.db")

    used = storage.measure(tmp_path)

    assert used.items["a"] >= 2 * MB and used.items["b"] >= 2 * MB
    assert used.items["a"] < 3 * MB
    assert used.governed >= 5 * MB
    assert used.governed == used.categories["worktrees"] + used.categories["sandboxes"]
    assert used.categories["logs"] >= MB and used.categories["databases"] >= MB
    assert used.governed < 6 * MB  # logs and databases are not governed


def test_measure_counts_a_hard_linked_file_once(tmp_path):
    _fill(tmp_path / "worktrees" / "a" / "f")
    before = storage.measure(tmp_path).governed
    os.link(tmp_path / "worktrees" / "a" / "f", tmp_path / "worktrees" / "a" / "g")
    assert storage.measure(tmp_path).governed - before < MB // 2


def test_measure_does_not_follow_a_symlink(tmp_path):
    _fill(tmp_path / "outside" / "big", 4 * MB)
    (tmp_path / "run" / "worktrees" / "a").mkdir(parents=True)
    (tmp_path / "run" / "worktrees" / "a" / "link").symlink_to(tmp_path / "outside")
    assert storage.measure(tmp_path / "run").governed < MB


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 directory")
def test_measure_skips_what_it_cannot_read(tmp_path):
    _fill(tmp_path / "worktrees" / "a" / "f")
    locked = tmp_path / "worktrees" / "b"
    _fill(locked / "f")
    locked.chmod(0)
    try:
        assert storage.measure(tmp_path).items["a"] >= MB
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize(
    ("governed", "state"),
    [(80, "ok"), (81, "over_quota"), (100, "over_quota"), (101, "held")],
    ids=["at-quota", "over-quota", "at-limit", "over-limit"],
)
def test_state_reads_usage_against_quota_and_limit(governed, state):
    assert storage.state_of(_policy(100, 80), _usage(governed)) == state


def test_state_is_none_until_measured():
    assert storage.state_of(_policy(100, 80), None) is None


def test_state_is_none_without_a_limit():
    assert storage.state_of(_policy(), _usage(10**12)) is None


def test_only_a_start_that_would_make_a_worktree_is_held(tmp_path):
    (tmp_path / "worktrees" / "has").mkdir(parents=True)
    st = SimpleNamespace(
        policy=_policy(100, 80),
        storage_usage=_usage(101),
        run_dirs=SimpleNamespace(worktrees=tmp_path / "worktrees"),
    )
    assert storage.holds(st, "new") is True
    assert storage.holds(st, "has") is False
    st.storage_usage = _usage(100)
    assert storage.holds(st, "new") is False


def test_forget_takes_an_items_bytes_off_the_cache():
    st = SimpleNamespace(storage_usage=_usage(120, {"a": 40, "b": 80}))
    storage.forget(st, "a")
    assert (st.storage_usage.governed, st.storage_usage.items) == (80, {"b": 80})
    storage.forget(st, "never-measured")
    assert st.storage_usage.governed == 80


async def test_refresh_runs_one_walk_for_two_callers(tmp_path, monkeypatch):
    import asyncio

    walks = []

    def slow(base, stop=None):
        walks.append(base)
        return _usage(7)

    monkeypatch.setattr(storage, "measure", slow)
    app = SimpleNamespace(state=SimpleNamespace(run_dirs=SimpleNamespace(base=tmp_path)))
    first, second = await asyncio.gather(storage.refresh(app), storage.refresh(app))
    assert (first.governed, second.governed, len(walks)) == (7, 7, 1)
    assert storage.usage(app.state).governed == 7


async def test_refresh_drops_an_item_removed_while_it_walked(tmp_path, monkeypatch):
    (tmp_path / "worktrees" / "here").mkdir(parents=True)
    monkeypatch.setattr(
        storage, "measure", lambda base, stop=None: _usage(7, {"here": 2, "gone": 5})
    )
    app = SimpleNamespace(state=SimpleNamespace(run_dirs=SimpleNamespace(base=tmp_path)))

    used = await storage.refresh(app)

    assert (used.governed, used.items) == (2, {"here": 2})


def test_a_stopped_walk_returns_early(tmp_path):
    import threading

    _fill(tmp_path / "worktrees" / "a" / "f")
    stopped = threading.Event()
    stopped.set()
    assert storage.measure(tmp_path, stopped).governed == 0


async def test_tick_measures_when_a_limit_is_set(tmp_path, stub_app, monkeypatch):
    app = stub_app(policy=_policy(100, 80))
    monkeypatch.setattr(storage, "measure", lambda base, stop=None: _usage(120))

    await storage.tick(app)

    assert storage.state_of(app.state.policy, storage.usage(app.state)) == "held"


async def test_tick_without_a_limit_does_not_walk(tmp_path, stub_app, monkeypatch):
    app = stub_app(policy=_policy())
    monkeypatch.setattr(
        storage, "measure", lambda base, stop=None: pytest.fail("walked with no limit set")
    )

    await storage.tick(app)

    assert storage.usage(app.state) is None


def _state(tmp_path, *, limit, quota, min_age_s) -> dict:
    return {
        "templates_dir": tmp_path / "templates",
        "skills_dir": tmp_path / "skills",
        "policy": _policy(limit, quota, min_age_s),
    }


async def _three(app, repo):
    """Three completed items: `old` ended 3 days ago, `mid` 2, `new` 1."""
    return [
        await seed_completed_item(app, repo, updated_at=days_ago(age), wid=wid)
        for wid, age in (("old", 3), ("mid", 2), ("new", 1))
    ]


def _measures(monkeypatch, governed, each=40):
    monkeypatch.setattr(
        storage,
        "measure",
        lambda base, stop=None: _usage(governed, {"old": each, "mid": each, "new": each}),
    )


async def test_tick_over_limit_archives_oldest_first_down_to_quota(
    tmp_path, repo, stub_app, monkeypatch
):
    app = stub_app(**_state(tmp_path, limit=100, quota=50, min_age_s=0))
    old, mid, new = await _three(app, repo)
    _measures(monkeypatch, 120)

    assert await storage.tick(app) == ["old", "mid"]

    assert not old.exists() and not mid.exists() and new.exists()
    assert storage.usage(app.state).governed == 40
    event = app.state.db.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE work_item_id='old' AND type='work_item_archived'"
        ).fetchone()
    )
    payload = json.loads(event["payload"])
    assert (payload["by"], payload["reason"]) == ("auto", "storage")


async def test_tick_never_takes_an_item_newer_than_min_age(tmp_path, repo, stub_app, monkeypatch):
    app = stub_app(**_state(tmp_path, limit=100, quota=50, min_age_s=60 * 3600))
    old, mid, new = await _three(app, repo)
    _measures(monkeypatch, 120)

    assert await storage.tick(app) == ["old"]

    assert mid.exists() and new.exists()
    assert storage.state_of(app.state.policy, storage.usage(app.state)) == "over_quota"
    assert app.state.storage_too_recent == 2
    assert storage.health(app.state)["too_recent"] == 2


async def test_tick_without_auto_cleanup_archives_nothing(tmp_path, repo, stub_app, monkeypatch):
    app = stub_app(**_state(tmp_path, limit=100, quota=50, min_age_s=None))
    old, _mid, _new = await _three(app, repo)
    _measures(monkeypatch, 120)

    assert await storage.tick(app) == []
    assert old.exists()


async def test_tick_between_quota_and_limit_archives_nothing(tmp_path, repo, stub_app, monkeypatch):
    app = stub_app(**_state(tmp_path, limit=100, quota=50, min_age_s=0))
    old, _mid, _new = await _three(app, repo)
    _measures(monkeypatch, 100)

    assert await storage.tick(app) == []
    assert old.exists()


async def test_tick_goes_on_past_an_item_whose_archive_fails(tmp_path, repo, stub_app, monkeypatch):
    from kraft.api.routes import lifecycle

    app = stub_app(**_state(tmp_path, limit=100, quota=50, min_age_s=0))
    await _three(app, repo)
    _measures(monkeypatch, 120)
    real = lifecycle._archive_one

    async def flaky(app, row, by, **kw):
        if row["id"] == "old":
            raise RuntimeError("git fell over")
        return await real(app, row, by, **kw)

    monkeypatch.setattr(lifecycle, "_archive_one", flaky)

    assert await storage.tick(app) == ["mid", "new"]


async def test_tick_goes_on_when_an_archive_frees_nothing(tmp_path, repo, stub_app, monkeypatch):
    """An archive that kept its worktree (a failed rescue) is not counted and
    takes nothing off the figure; the loop moves to the next item."""
    from kraft.api.routes import lifecycle

    app = stub_app(**_state(tmp_path, limit=100, quota=50, min_age_s=0))
    await _three(app, repo)
    _measures(monkeypatch, 120)
    real = lifecycle._archive_one

    async def kept(app, row, by, **kw):
        if row["id"] == "old":
            return {"worktree_removed": False, "worktree_kept": "rescue failed"}
        return await real(app, row, by, **kw)

    monkeypatch.setattr(lifecycle, "_archive_one", kept)

    assert await storage.tick(app) == ["mid", "new"]


async def test_tick_stops_after_three_archives_that_free_nothing(
    tmp_path, repo, stub_app, monkeypatch
):
    """A broken repo must not get every finished item marked archived."""
    from kraft.api.routes import lifecycle

    app = stub_app(**_state(tmp_path, limit=100, quota=0, min_age_s=0))
    for n in range(4):
        await seed_completed_item(app, repo, updated_at=days_ago(9 - n), wid=f"w{n}")
    monkeypatch.setattr(
        storage, "measure", lambda base, stop=None: _usage(120, {f"w{n}": 30 for n in range(4)})
    )
    asked = []

    async def frees_nothing(app, row, by, **kw):
        asked.append(row["id"])
        return {"worktree_removed": False}

    monkeypatch.setattr(lifecycle, "_archive_one", frees_nothing)

    assert await storage.tick(app) == []
    assert asked == ["w0", "w1", "w2"]


async def test_two_kicks_while_one_runs_start_one_tick_and_the_task_is_kept(monkeypatch):
    import asyncio

    release = asyncio.Event()
    ticks = []

    async def tick(app):
        ticks.append(app)
        await release.wait()

    monkeypatch.setattr(storage, "tick", tick)
    app = SimpleNamespace(state=SimpleNamespace())

    storage.kick(app)
    storage.kick(app)
    await asyncio.sleep(0)

    assert len(ticks) == 1
    task = app.state.storage_kick
    assert not task.done()
    release.set()
    await task
    assert task.done()


def _row(status="completed", archived_at=None, title="t", updated_at="2026-01-01T00:00:00+00:00"):
    return {"title": title, "status": status, "archived_at": archived_at, "updated_at": updated_at}


@pytest.mark.parametrize(
    ("row", "why"),
    [
        (_row("completed"), None),
        (_row("abandoned"), None),
        (_row("active"), "only a completed or abandoned item can be archived"),
        (_row("paused"), "only a completed or abandoned item can be archived"),
        (_row("completed", "2026-01-02T00:00:00+00:00"), "already archived"),
        (None, "unknown work item"),
    ],
    ids=["completed", "abandoned", "active", "paused", "archived", "unknown"],
)
def test_refusal_says_why_an_item_cannot_be_archived(row, why):
    assert storage.refusal(row) == why
    assert storage.reclaimable(row) is (why is None)


def test_report_joins_the_measurement_to_the_work_items():
    used = storage.Usage(
        "2026-01-01T00:00:00+00:00",
        155,
        {"done": 30, "live": 50, "old": 5, "ghost": 70},
        {"worktrees": 155, "sandboxes": 0},
    )
    rows = {
        "done": _row("completed", title="Done one"),
        "live": _row("active", title="Live one"),
        "old": _row("completed", "2026-01-02T00:00:00+00:00"),
    }

    got = storage.report(_policy(150, 120), used, rows)

    assert [(i["id"], i["bytes"], i["reclaimable"], i["archived"]) for i in got["items"]] == [
        ("live", 50, False, False),
        ("done", 30, True, False),
        ("old", 5, False, True),
    ]
    assert got["items"][1] == {
        "id": "done",
        "title": "Done one",
        "status": "completed",
        "archived": False,
        "bytes": 30,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "reclaimable": True,
    }
    assert got["orphans"] == [{"name": "ghost", "bytes": 70}]
    assert got["reclaimable_bytes"] == 30
    assert got["categories"] == used.categories
    assert (got["measured_at"], got["state"], got["used_bytes"]) == (
        "2026-01-01T00:00:00+00:00",
        "held",
        155,
    )
    assert (got["quota_bytes"], got["limit_bytes"]) == (120, 150)


def test_report_without_a_limit_has_no_state_quota_or_limit():
    got = storage.report(_policy(), _usage(155, {"done": 30}), {"done": _row()})
    assert (got["state"], got["quota_bytes"], got["limit_bytes"]) == (None, None, None)
    assert (got["used_bytes"], got["reclaimable_bytes"]) == (155, 30)


@pytest.mark.parametrize(
    ("freed", "left", "state"),
    [(0, 140, "held"), (40, 100, "over_quota"), (70, 70, "ok"), (500, 0, "ok")],
    ids=["nothing", "down-to-the-limit", "down-to-the-quota", "more-than-used"],
)
def test_after_says_where_usage_lands(freed, left, state):
    assert storage.after(_policy(100, 80), _usage(140), freed) == (left, state)


def test_after_without_a_limit_has_no_state():
    assert storage.after(_policy(), _usage(140), 40) == (100, None)
