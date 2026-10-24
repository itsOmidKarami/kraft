from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from support.harness import write

from kraft import carried, storage

MB = 1024 * 1024
RELS = [".testmondata", ".testmondata-wal"]


def _save(run_dirs, repo: Path, worktree: Path, rels=RELS, *, now: float | None = None) -> bool:
    return carried.save(
        run_dirs, repo, worktree, list(rels), worktree.parent / "record.json", now=now
    )


def _generations(store: Path) -> list[Path]:
    return sorted(store.glob("gen-*"))


@pytest.mark.parametrize(
    ("record_files", "now_files", "expected"),
    [
        ({"a": [1, 10]}, {"a": (1, 10)}, False),
        ({"a": [1, 10]}, {"a": (1, 11)}, True),
        ({"a": [1, 10]}, {"a": (2, 10)}, True),
        ({"a": [1, 10]}, {"a": (1, 10), "b": (3, 3)}, True),
        (None, {"a": (1, 10)}, True),
        (None, {}, False),
        ({"gone": [1, 10]}, {}, False),
    ],
    ids=[
        "same-stat",
        "mtime-differs",
        "size-differs",
        "file-appeared",
        "no-record-and-a-file-present",
        "no-record-and-no-file",
        "a-file-only-in-the-record",
    ],
)
def test_changed(record_files, now_files, expected):
    record = None if record_files is None else {"files": record_files}
    assert carried.changed(record, now_files) is expected


def test_save_writes_a_generation_current_points_at(tmp_path, run_dirs):
    repo, worktree = tmp_path / "repo", tmp_path / "wt"
    write(worktree, ".testmondata", "db")
    write(worktree, ".testmondata-wal", "wal")
    store = carried.store_dir(run_dirs, repo)

    assert _save(run_dirs, repo, worktree) is True
    first = carried.current_generation(store)
    assert first is not None
    assert (first / ".testmondata").read_bytes() == b"db"
    assert (first / ".testmondata-wal").read_bytes() == b"wal"

    assert _save(run_dirs, repo, worktree) is False
    assert _generations(store) == [first]

    write(worktree, ".testmondata", "db, newer")
    assert _save(run_dirs, repo, worktree) is True
    second = carried.current_generation(store)
    assert second != first
    assert _generations(store) == sorted([first, second])
    assert (first / ".testmondata").read_bytes() == b"db"
    assert _save(run_dirs, repo, worktree) is False


def test_a_file_the_worktree_no_longer_has_is_absent_from_the_new_set(tmp_path, run_dirs):
    repo, worktree = tmp_path / "repo", tmp_path / "wt"
    write(worktree, ".testmondata", "db")
    write(worktree, ".testmondata-wal", "wal")
    _save(run_dirs, repo, worktree)

    (worktree / ".testmondata-wal").unlink()
    write(worktree, ".testmondata", "db, rewritten")
    _save(run_dirs, repo, worktree)

    current = carried.current_generation(carried.store_dir(run_dirs, repo))
    assert (current / ".testmondata").read_bytes() == b"db, rewritten"
    assert not (current / ".testmondata-wal").exists()


def test_a_save_that_fails_midway_leaves_current_alone(tmp_path, run_dirs, monkeypatch):
    repo, worktree = tmp_path / "repo", tmp_path / "wt"
    write(worktree, ".testmondata", "db")
    _save(run_dirs, repo, worktree)
    store = carried.store_dir(run_dirs, repo)
    earlier = carried.current_generation(store)

    write(worktree, ".testmondata", "db, newer")
    write(worktree, ".testmondata-wal", "wal")
    real, calls = shutil.copyfile, []

    def flaky(src, dst, **kw):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(src, dst, **kw)

    monkeypatch.setattr(shutil, "copyfile", flaky)
    with pytest.raises(OSError, match="disk full"):
        _save(run_dirs, repo, worktree)

    assert carried.current_generation(store) == earlier
    assert _generations(store) == [earlier]
    assert not list(store.glob(".current-*"))


@pytest.mark.parametrize(
    ("minutes_since_superseded", "kept"),
    [(59, True), (61, False)],
    ids=["59-minutes", "61-minutes"],
)
def test_prune_waits_an_hour_after_a_generation_stops_being_current(
    tmp_path, minutes_since_superseded, kept
):
    store = tmp_path / "store"
    old, current = store / "gen-1", store / "gen-2"
    old.mkdir(parents=True)
    current.mkdir()
    (store / "current").symlink_to("gen-2")
    now = 1_000_000.0
    (old / "superseded").write_text(str(now - minutes_since_superseded * 60))
    carried.prune(store, now=now)
    assert old.exists() is kept
    assert current.exists()


def test_prune_measures_from_the_marker_not_the_directory(tmp_path):
    """Created two hours ago, superseded just now: a reader may still be in it."""
    store = tmp_path / "store"
    old, current = store / "gen-1", store / "gen-2"
    old.mkdir(parents=True)
    current.mkdir()
    (store / "current").symlink_to("gen-2")
    now = 1_000_000.0
    os.utime(old, (now - 7200, now - 7200))
    (old / "superseded").write_text(str(now))
    carried.prune(store, now=now)
    assert old.exists()


def test_prune_never_removes_the_current_generation(tmp_path):
    store = tmp_path / "store"
    current = store / "gen-1"
    current.mkdir(parents=True)
    (store / "current").symlink_to("gen-1")
    (current / "superseded").write_text("0")
    carried.prune(store, now=10_000_000.0)
    assert current.exists()


def test_prune_stamps_a_generation_with_no_marker_and_keeps_it(tmp_path):
    store = tmp_path / "store"
    leftover = store / "gen-1"
    leftover.mkdir(parents=True)
    carried.prune(store, now=1_000_000.0)
    assert leftover.exists()
    assert float((leftover / "superseded").read_text()) == 1_000_000.0


def test_a_superseded_generation_goes_an_hour_after_the_save_that_replaced_it(tmp_path, run_dirs):
    repo, worktree = tmp_path / "repo", tmp_path / "wt"
    store = carried.store_dir(run_dirs, repo)
    write(worktree, ".testmondata", "one")
    _save(run_dirs, repo, worktree, now=1000.0)
    (first,) = _generations(store)
    write(worktree, ".testmondata", "two, longer")
    _save(run_dirs, repo, worktree, now=2000.0)
    assert first.exists()
    write(worktree, ".testmondata", "three, longer still")
    _save(run_dirs, repo, worktree, now=2000.0 + carried.PRUNE_AFTER_S + 1)
    assert not first.exists()


def test_saved_set_needs_a_listed_file(tmp_path, run_dirs):
    repo, scratch = tmp_path / "repo", tmp_path / "scratch"
    store = carried.store_dir(run_dirs, repo)
    assert carried.saved_set(store, [".testmondata"]) is None

    write(scratch, "other", "x")
    carried.save(run_dirs, repo, scratch, ["other"], tmp_path / "r1.json")
    assert carried.saved_set(store, [".testmondata"]) is None

    write(scratch, ".testmondata", "db")
    carried.save(run_dirs, repo, scratch, [".testmondata"], tmp_path / "r2.json")
    assert carried.saved_set(store, [".testmondata"]) == carried.current_generation(store)


def test_store_dir_is_per_repo(tmp_path, run_dirs):
    a, b = tmp_path / "x" / "app", tmp_path / "y" / "app"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(a)
    assert carried.store_dir(run_dirs, a) != carried.store_dir(run_dirs, b)
    assert carried.store_dir(run_dirs, a) == carried.store_dir(run_dirs, f"{a}/")
    assert carried.store_dir(run_dirs, a) == carried.store_dir(run_dirs, link)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"source": "none", "carried": [], "refused": [], "torn": True},
            [
                "carried files in the checkout changed while they were being copied, "
                "so none were carried and the tool starts cold"
            ],
        ),
        (
            {"source": "saved", "carried": [".testmondata"], "refused": []},
            ["carried from the saved copy: .testmondata"],
        ),
        (
            {"source": "checkout", "carried": ["a", "b"], "refused": []},
            ["carried from the checkout: a, b"],
        ),
        (
            {"source": "none", "carried": [], "refused": []},
            ["nothing to carry yet (no saved copy, none in the checkout): the tool starts cold"],
        ),
        (
            {"source": "none", "carried": [], "refused": ["x", "y"]},
            [
                "nothing to carry yet (no saved copy, none in the checkout): the tool starts cold",
                "carry entries NOT carried into this worktree -- only a file the worktree's "
                ".gitignore covers is copied, so ignore these or drop them:",
                "  x",
                "  y",
            ],
        ),
        (None, []),
    ],
    ids=["torn", "from-saved", "from-checkout", "cold", "refused", "no-record"],
)
def test_report_lines(record, expected):
    assert carried.report_lines(record) == expected


def test_a_saved_copy_is_counted_under_other_and_not_governed(tmp_path, run_dirs):
    repo, worktree = tmp_path / "repo", tmp_path / "wt"
    (worktree).mkdir()
    (worktree / ".testmondata").write_bytes(b"x" * MB)
    _save(run_dirs, repo, worktree, [".testmondata"])
    usage = storage.measure(run_dirs.base)
    assert usage.categories["other"] >= MB
    assert usage.governed == 0
