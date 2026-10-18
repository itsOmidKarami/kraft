"""Worktree storage: what is measured, and what the limit decides."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from support.archive import days_ago, seed_completed_item
from support.deep_tree import deep_chain, opens_during
from support.harness import IN_PLACE, v1_named_chain

from kraft import item_paths, policy, storage

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


def _usage(governed, items=None, categories=None) -> storage.Usage:
    return storage.Usage(
        "2026-01-01T00:00:00+00:00", governed, dict(items or {}), dict(categories or {})
    )


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


@pytest.mark.parametrize("items", ["folder", "link"], ids=["items-folder", "items-is-a-link"])
def test_measure_counts_an_item_s_sandbox_home_under_the_item(tmp_path, items):
    """Of an item's own folder, what a worker writes is governed: its
    sandbox home, which is also the item's (archiving removes it), and its
    `docs/` and sessions' `out/`, which archiving keeps. Kraft's copies and
    session files are not. A `run/items` moved elsewhere and linked is
    Kraft's, and counted as `worktrees/` is."""
    run = tmp_path / "run"
    run.mkdir()
    if items == "link":
        (tmp_path / "elsewhere").mkdir()
        (run / "items").symlink_to(tmp_path / "elsewhere")
    item = run / "items" / "w1"
    _fill(item / "sandbox-home" / "f")
    _fill(item / "docs" / "spec.md", 2 * MB)
    _fill(item / "sessions" / "s1" / "out" / "result.json", 2 * MB)
    for part in ("history/spec.1.md", "filed/spec.md", "sessions/s1/session.log"):
        _fill(item / part, 4096)

    used = storage.measure(run)

    assert set(used.items) == {"w1"}
    assert used.items["w1"] == used.categories["sandboxes"]
    assert MB <= used.items["w1"] < 2 * MB
    assert 4 * MB <= used.categories["documents"] < 5 * MB
    assert used.documents == {"w1": used.categories["documents"]}
    assert 2 * 4096 <= used.categories["attachments"] < MB
    assert 4096 <= used.categories["logs"] < MB
    assert used.categories["other"] == 0
    assert used.governed == sum(used.categories[c] for c in ("worktrees", "sandboxes", "documents"))


@pytest.mark.parametrize("when", ["open", "scandir"], ids=["before-open", "before-listing"])
def test_measure_counts_what_a_folder_held_when_it_was_opened(tmp_path, monkeypatch, when):
    """A worker can swap a folder in its home or worktree for a link to `/`
    while the walk runs: after the walk saw it in its parent and before it
    opens it, or after it opened it and before it lists it. The walk opens
    each folder with no link followed and lists it by that descriptor, so
    it counts what was there, never what the link points at."""
    _fill(tmp_path / "outside" / "big", 4 * MB)
    cache = tmp_path / "run" / "items" / "w1" / "sandbox-home" / "cache"
    _fill(cache / "f", 4096)
    original = os.stat(cache)
    real = getattr(os, when)
    swapped = []

    def swap():
        swapped.append(True)
        cache.rename(cache.with_name("moved"))
        cache.symlink_to(tmp_path / "outside")

    def swap_then_open(name, flags, *a, dir_fd=None, **kw):
        if not swapped and dir_fd is not None and name == "cache":
            swap()
        return real(name, flags, *a, dir_fd=dir_fd, **kw)

    def swap_then_list(arg="."):
        try:
            seen = os.fstat(arg) if isinstance(arg, int) else os.stat(arg)
        except OSError:
            seen = None
        if not swapped and seen is not None and os.path.samestat(seen, original):
            swap()
        return real(arg)

    monkeypatch.setattr(os, when, swap_then_open if when == "open" else swap_then_list)

    used = storage.measure(tmp_path / "run")

    assert swapped, "the walk never reached the folder"
    assert used.categories["sandboxes"] < MB


_DEEP_MEASURE = """
import errno, json, os, resource, sys
from pathlib import Path
from kraft import storage

real_open, emfile = os.open, []

def counting_open(*a, **kw):
    try:
        return real_open(*a, **kw)
    except OSError as exc:
        if exc.errno == errno.EMFILE:
            emfile.append(a[0])
        raise

os.open = counting_open
resource.setrlimit(resource.RLIMIT_NOFILE, (64, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
used = storage.measure(Path(sys.argv[1]))
print(json.dumps({"a": used.items.get("a", 0), "emfile": len(emfile)}))
"""


def test_a_deep_tree_is_counted_within_a_low_open_file_limit(tmp_path):
    """A worker can nest folders far deeper than the daemon's open-file limit
    (256 under launchd). The walk holds a bounded number of descriptors, so
    it never runs the daemon out of them, and counts the file at the bottom."""
    deep_chain(tmp_path / "run" / "worktrees" / "a", 300, leaf=os.urandom(4 * MB))

    done = subprocess.run(
        [sys.executable, "-c", _DEEP_MEASURE, str(tmp_path / "run")],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["emfile"] == 0
    assert seen["a"] >= 4 * MB


def test_a_deep_tree_is_counted_with_opens_linear_in_its_folders(tmp_path):
    """Folders deeper than the walk holds descriptors for are gone back up to
    by `..`, not reopened from the top each time: reopening made 2,000 levels
    take 2.6 seconds and 50,000 half an hour, and a worker makes either at
    almost no cost. 5,000 levels take about two opens a folder, where
    reopening took some 12.5 million. Counted, not timed, so a loaded
    machine cannot fail it, and by the audit hook, which sees every open
    however the walk binds `os.open`."""
    run = tmp_path / "run"
    deep_chain(run / "worktrees" / "a", 5000, leaf=os.urandom(MB))
    try:
        opens, items = opens_during(
            "from kraft import storage", f"storage.measure({str(run)!r}).items"
        )

        assert items["a"] >= MB
        assert 5000 <= opens < 10 * 5000
    finally:
        item_paths.remove_tree(run)


def test_measure_counts_a_hard_linked_file_once(tmp_path):
    _fill(tmp_path / "worktrees" / "a" / "f")
    before = storage.measure(tmp_path).governed
    os.link(tmp_path / "worktrees" / "a" / "f", tmp_path / "worktrees" / "a" / "g")
    assert storage.measure(tmp_path).governed - before < MB // 2


@pytest.mark.parametrize(
    "where",
    ["worktrees/a", "items/a/sandbox-home", "items/a/docs", "items/a"],
    ids=["worktree", "item-sandbox-home", "item-docs", "item-folder"],
)
def test_measure_does_not_follow_a_symlink(tmp_path, where):
    _fill(tmp_path / "outside" / "big", 4 * MB)
    (tmp_path / "run" / where).mkdir(parents=True)
    (tmp_path / "run" / where / "link").symlink_to(tmp_path / "outside")
    assert sum(storage.measure(tmp_path / "run").categories.values()) < MB


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
    ("governed", "documents", "state"),
    [
        (80, 0, "ok"),
        (81, 0, "over_quota"),
        (100, 0, "over_quota"),
        (101, 0, "held"),
        (81, 81, "over_quota"),
        (150, 50, "over_quota"),
        (150, 49, "held"),
    ],
    ids=[
        "at-quota",
        "over-quota",
        "at-limit",
        "over-limit",
        "documents-over-quota",
        "documents-over-limit",
        "reclaimable-over-limit",
    ],
)
def test_state_reads_usage_against_quota_and_limit(governed, documents, state):
    """Documents count toward the quota, but archiving keeps them: only what
    archiving frees over the limit holds starts, or one worker filling its
    `docs/` would hold every start on the machine for good."""
    used = _usage(governed, categories={"documents": documents})
    assert storage.state_of(_policy(100, 80), used) == state


def test_state_is_none_until_measured():
    assert storage.state_of(_policy(100, 80), None) is None


def test_state_is_none_without_a_limit():
    assert storage.state_of(_policy(), _usage(10**12)) is None


async def test_only_a_start_that_would_make_a_worktree_is_held(item_on, database, run_dirs):
    """An in-place item never makes one: its folder is the person's checkout."""
    chain = v1_named_chain(run_dirs.base / "t")
    await item_on(chain, wid="new")
    await item_on(chain, wid="here", target=IN_PLACE)
    (run_dirs.worktrees / "has").mkdir(parents=True)
    st = SimpleNamespace(
        policy=_policy(100, 80), storage_usage=_usage(101), run_dirs=run_dirs, db=database
    )
    assert storage.holds(st, "new") is True
    assert storage.holds(st, "has") is False
    assert storage.holds(st, "here") is False
    st.storage_usage = _usage(100)
    assert storage.holds(st, "new") is False


def test_a_held_start_is_told_the_figure_compared_with_the_limit():
    """Not the governed figure: the documents in it hold no start."""
    st = SimpleNamespace(policy=_policy(100, 80), storage_usage=_usage(150, {}, {"documents": 40}))
    assert storage.state_of(st.policy, st.storage_usage) == "held"
    assert storage.figures(st) == {"used_bytes": 110, "limit_bytes": 100}


@pytest.mark.parametrize(
    ("limit", "items", "wid", "charged"),
    [
        (100, {"a": 30, "b": 50}, "new", 40),
        (100, {"a": 30, "b": 50}, "has", 0),
        (100, {"a": 30, "b": 50}, "here", 0),
        (100, {"a": 30, "b": 50}, "a", 0),
        (None, {"a": 30, "b": 50}, "new", 0),
    ],
    ids=["new-worktree", "worktree-exists", "in-place", "already-counted", "no-limit"],
)
async def test_charge_counts_a_new_worktree_at_once(
    item_on, database, run_dirs, limit, items, wid, charged
):
    """The mean of the items on the cache stands in until the next walk."""
    chain = v1_named_chain(run_dirs.base / "t")
    for name, target in (("new", None), ("has", None), ("here", IN_PLACE)):
        await item_on(chain, wid=name, **({"target": target} if target else {}))
    (run_dirs.worktrees / "has").mkdir(parents=True)
    st = SimpleNamespace(
        policy=_policy(limit, limit and limit * 8 // 10),
        storage_usage=_usage(80, items, {"worktrees": 80}),
        run_dirs=run_dirs,
        db=database,
    )
    storage.charge(SimpleNamespace(state=st), wid)
    got = st.storage_usage
    assert (got.governed, got.categories["worktrees"]) == (80 + charged, 80 + charged)
    assert got.items.get(wid, 0) == (charged or items.get(wid, 0))


async def test_charge_with_nothing_to_average_kicks_a_walk(
    item_on, database, run_dirs, monkeypatch
):
    chain = v1_named_chain(run_dirs.base / "t")
    await item_on(chain, wid="new")
    kicked = []
    monkeypatch.setattr(storage, "kick", kicked.append)
    st = SimpleNamespace(
        policy=_policy(100, 80), storage_usage=_usage(0), run_dirs=run_dirs, db=database
    )
    app = SimpleNamespace(state=st)
    storage.charge(app, "new")
    assert (kicked, st.storage_usage.items) == ([app], {})


def test_forget_takes_an_items_bytes_off_the_cache():
    st = SimpleNamespace(
        storage_usage=_usage(
            120, {"a": 40, "b": 80}, {"worktrees": 100, "sandboxes": 20, "logs": 5}
        )
    )
    storage.forget(st, "a")
    got = st.storage_usage
    assert (got.governed, got.items) == (80, {"b": 80})
    assert got.categories == {"worktrees": 60, "sandboxes": 20, "logs": 5}
    # More than `worktrees` holds: the rest comes off `sandboxes`, never below zero.
    storage.forget(st, "b")
    got = st.storage_usage
    assert (got.governed, got.items) == (0, {})
    assert got.categories == {"worktrees": 0, "sandboxes": 0, "logs": 5}
    storage.forget(st, "never-measured")
    assert st.storage_usage.governed == 0


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


@pytest.mark.parametrize(
    "left", ["worktrees/here", "items/here/sandbox-home"], ids=["worktree", "item-sandbox-home"]
)
async def test_refresh_drops_an_item_removed_while_it_walked(tmp_path, monkeypatch, left):
    (tmp_path / left).mkdir(parents=True)
    (tmp_path / "items" / "gone" / "docs").mkdir(parents=True)  # kept by archive, not governed
    monkeypatch.setattr(
        storage, "measure", lambda base, stop=None: _usage(7, {"here": 2, "gone": 5})
    )
    app = SimpleNamespace(state=SimpleNamespace(run_dirs=SimpleNamespace(base=tmp_path)))

    used = await storage.refresh(app)

    assert (used.governed, used.items) == (2, {"here": 2})


@pytest.mark.parametrize("abandoned", [False, True], ids=["archived", "abandoned"])
async def test_refresh_drops_the_documents_of_an_item_abandoned_while_it_walked(
    tmp_path, monkeypatch, abandoned
):
    """Abandoning removes an item's folder and takes its documents off the
    figure; a walk that measured them before must not put them back. An
    archived item keeps its folder, and its documents stay counted."""
    if not abandoned:
        (tmp_path / "items" / "w1" / "docs").mkdir(parents=True)
    monkeypatch.setattr(
        storage,
        "measure",
        lambda base, stop=None: storage.Usage(
            "2026-01-01T00:00:00+00:00", 10, {}, {"documents": 10}, {"w1": 10}
        ),
    )
    app = SimpleNamespace(state=SimpleNamespace(run_dirs=SimpleNamespace(base=tmp_path)))

    used = await storage.refresh(app)

    left = (0, 0, {}) if abandoned else (10, 10, {"w1": 10})
    assert (used.governed, used.categories["documents"], used.documents) == left


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


async def test_tick_with_no_measurement_acts_on_nothing(tmp_path, stub_app, monkeypatch):
    """Shutdown can stop the first walk before it caches anything."""
    app = stub_app(policy=_policy(100, 80, 0))

    async def stopped(_app):
        return None

    monkeypatch.setattr(storage, "refresh", stopped)

    assert await storage.tick(app) == []


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


async def test_an_in_place_item_is_never_measured_or_auto_archived(
    tmp_path, repo, stub_app, item_on, run_dirs
):
    """Its folder is the person's checkout, outside the run folder: the walk
    never reaches it, and the clean-up never picks the item."""
    app = stub_app(**_state(tmp_path, limit=1, quota=0, min_age_s=0))
    old = await seed_completed_item(app, repo, updated_at=days_ago(2), wid="old")
    _fill(old / "big")
    here = await item_on(
        v1_named_chain(run_dirs.base / "t"),
        wid="here",
        target=IN_PLACE,
        status="completed",
    )
    _fill(repo / "the-person-s-own.bin")
    await app.state.db.write(
        lambda c: c.execute(
            "UPDATE work_items SET updated_at = ? WHERE id = 'here'", (days_ago(3),)
        )
    )

    assert await storage.tick(app) == ["old"]

    assert here.row()["archived_at"] is None
    assert "here" not in storage.usage(app.state).items


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


@pytest.mark.parametrize(
    ("worktrees", "documents", "archived", "named"),
    [
        (110, 80, ["old", "mid"], True),
        (200, 100, ["old", "mid", "new"], False),
        (60, 80, [], True),
    ],
    ids=["documents-keep-it-over", "worktrees-keep-it-over", "only-documents-over"],
)
async def test_tick_archives_only_down_to_what_archiving_can_free(
    tmp_path, repo, stub_app, monkeypatch, caplog, worktrees, documents, archived, named
):
    """Items' documents are governed, but archiving keeps them: a worker that
    filled its `docs/` must not have every finished item archived without
    the figure coming down. Archiving stops once what it can free is under
    quota, and the log names the item whose documents hold the rest, but
    only when documents are what keeps usage over the limit: with more to
    archive than the limit allows, abandoning would not bring it under.
    Documents alone over the limit hold nothing, so nothing is archived."""
    app = stub_app(**_state(tmp_path, limit=100, quota=50, min_age_s=0))
    old, mid, new = await _three(app, repo)
    # `mid` was archived, not abandoned: its folder, and so its documents, stay.
    item_paths.item_dir(app.state.run_dirs, "mid").mkdir(parents=True)
    monkeypatch.setattr(
        storage,
        "measure",
        lambda base, stop=None: storage.Usage(
            "2026-01-01T00:00:00+00:00",
            worktrees + documents,
            {"old": 30, "mid": 30, "new": 30},
            {"worktrees": worktrees, "documents": documents},
            {"mid": documents},
        ),
    )

    assert await storage.tick(app) == archived

    assert [p.exists() for p in (old, mid, new)] == [wid not in archived for wid in _IDS]
    assert storage.usage(app.state).governed == worktrees + documents - 30 * len(archived)
    holders = [r.getMessage() for r in caplog.records if "archiving keeps" in r.getMessage()]
    assert len(holders) == named
    assert all("mid" in h and "abandon" in h for h in holders)


_IDS = ("old", "mid", "new")


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
    return {
        "title": title,
        "status": status,
        "archived_at": archived_at,
        "updated_at": updated_at,
        "in_place": None,
    }


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
    """Each item with its worktree and home (`bytes`, what archiving frees)
    and its documents (kept by archiving), so the page can name the item
    whose documents hold the space, an archived one included."""
    used = storage.Usage(
        "2026-01-01T00:00:00+00:00",
        199,
        {"done": 30, "live": 50, "old": 5, "ghost": 70},
        {"worktrees": 155, "sandboxes": 0, "documents": 44},
        {"done": 4, "kept": 40},
    )
    rows = {
        "done": _row("completed", title="Done one"),
        "live": _row("active", title="Live one"),
        "old": _row("completed", "2026-01-02T00:00:00+00:00"),
        "kept": _row("completed", "2026-01-02T00:00:00+00:00"),
    }

    got = storage.report(_policy(150, 120), used, rows)

    assert [
        (i["id"], i["bytes"], i["documents_bytes"], i["reclaimable"], i["archived"])
        for i in got["items"]
    ] == [
        ("live", 50, 0, False, False),
        ("kept", 0, 40, False, True),
        ("done", 30, 4, True, False),
        ("old", 5, 0, False, True),
    ]
    assert got["items"][2] == {
        "id": "done",
        "title": "Done one",
        "status": "completed",
        "archived": False,
        "bytes": 30,
        "documents_bytes": 4,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "reclaimable": True,
    }
    assert got["orphans"] == [{"name": "ghost", "bytes": 70}]
    assert got["reclaimable_bytes"] == 30
    assert got["categories"] == used.categories
    assert (got["measured_at"], got["state"], got["used_bytes"]) == (
        "2026-01-01T00:00:00+00:00",
        "held",
        199,
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
