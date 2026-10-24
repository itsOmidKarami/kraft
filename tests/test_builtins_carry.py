"""`carry`: seeding a new worktree with a selection tool's state, the whole set
from one source (`builtins._carry_set`, `kraft.carried`)."""

import os

import pytest
from support import worktree as wtree
from support.harness import commit_all, entry_of, git, write
from test_builtins_local_files import _linked_worktree

from kraft import builtins as kraft_builtins
from kraft import carried

RELS = [".testmondata", ".testmondata-wal"]


def _ignored_repo(repo, *names):
    write(repo, ".gitignore", "".join(f"{n}\n" for n in names))
    commit_all(repo, "ignore the state")


def _saved(run_dirs, repo, files, tmp_path):
    scratch = tmp_path / "scratch"
    for name, body in files.items():
        write(scratch, name, body)
    carried.save(run_dirs, repo, scratch, list(files), tmp_path / "scratch-record.json")


def _worktree_files(wt):
    return {rel: (wt / rel).read_text() for rel in RELS if (wt / rel).exists()}


@pytest.mark.parametrize(
    ("saved", "checkout", "expect_worktree", "expect_source"),
    [
        (
            {".testmondata": "saved"},
            {".testmondata": "co", ".testmondata-wal": "old"},
            {".testmondata": "saved"},
            "saved",
        ),
        (
            {},
            {".testmondata": "co", ".testmondata-wal": "wal"},
            {".testmondata": "co", ".testmondata-wal": "wal"},
            "checkout",
        ),
        ({}, {}, {}, "none"),
        ({"other": "x"}, {".testmondata": "co"}, {".testmondata": "co"}, "checkout"),
    ],
    ids=[
        "saved-copy-wins-over-the-checkout",
        "checkout-when-nothing-is-saved",
        "cold-when-there-is-neither",
        "a-saved-copy-with-none-of-the-listed-files-is-no-saved-copy",
    ],
)
def test_carry_set_takes_the_whole_set_from_one_source(
    tmp_path, run_dirs, repo, saved, checkout, expect_worktree, expect_source
):
    _ignored_repo(repo, *RELS)
    for name, body in checkout.items():
        write(repo, name, body)
    if saved:
        _saved(run_dirs, repo, saved, tmp_path)
    wt = _linked_worktree(repo, tmp_path)

    carry_in = kraft_builtins._carry_set(run_dirs, repo, wt, RELS, None)

    assert _worktree_files(wt) == expect_worktree
    assert carry_in.source == expect_source
    assert carry_in.carried == list(expect_worktree)
    assert carry_in.refused == []
    assert not carry_in.torn


def _touch_mtime(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda repo: _touch_mtime(repo / ".testmondata"),
        lambda repo: (repo / ".testmondata").write_text("co, grown"),
        lambda repo: (repo / ".testmondata-wal").write_text("appeared"),
    ],
    ids=["mtime", "size", "appeared"],
)
def test_a_checkout_copy_that_changed_while_it_was_copied_is_discarded(
    tmp_path, run_dirs, repo, monkeypatch, mutate
):
    _ignored_repo(repo, *RELS)
    write(repo, ".testmondata", "co")
    wt = _linked_worktree(repo, tmp_path)
    real = kraft_builtins._carry_local_files

    def copy_then_the_person_writes(source, worktree, rels):
        result = real(source, worktree, rels)
        mutate(repo)
        return result

    monkeypatch.setattr(kraft_builtins, "_carry_local_files", copy_then_the_person_writes)

    carry_in = kraft_builtins._carry_set(run_dirs, repo, wt, RELS, None)

    assert carry_in.torn
    assert carry_in.source == "none"
    assert carry_in.carried == []
    assert _worktree_files(wt) == {}


def test_an_unignored_carry_file_is_refused_not_copied(tmp_path, run_dirs, repo):
    write(repo, ".testmondata", "co")
    wt = _linked_worktree(repo, tmp_path)

    carry_in = kraft_builtins._carry_set(run_dirs, repo, wt, RELS, None)

    assert carry_in.refused == [".testmondata"]
    assert not (wt / ".testmondata").exists()
    assert git(wt, "status", "--porcelain") == ""


def test_the_record_is_the_baseline_of_what_was_copied_in(tmp_path, run_dirs, repo):
    _ignored_repo(repo, *RELS)
    write(repo, ".testmondata", "co")
    wt = _linked_worktree(repo, tmp_path)
    record = tmp_path / "carried.json"

    kraft_builtins._carry_set(run_dirs, repo, wt, RELS, record)

    on_disk = carried.read_record(record)
    assert on_disk["files"] == {k: list(v) for k, v in carried.stats(wt, RELS).items()}
    assert on_disk["files"].keys() == {".testmondata"}
    assert on_disk["source"] == "checkout"
    assert not carried.changed(on_disk, carried.stats(wt, RELS))

    other = _linked_worktree(repo, tmp_path, "other")
    kraft_builtins._carry_set(run_dirs, repo, other, RELS, None)
    kraft_builtins._carry_set(run_dirs, repo, other, [], tmp_path / "empty.json")
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["carried.json"]


async def test_ensure_worktree_seeds_carry_before_setup(database, run_dirs, repo):
    _ignored_repo(repo, ".testmondata")
    write(repo, ".testmondata", "db\n")

    await wtree.make_item(database, repo)
    wt = await kraft_builtins.ensure_worktree(
        database,
        run_dirs,
        repo=str(repo),
        work_item_id="w1",
        repo_entry=entry_of(
            {"setup_command": "cp .testmondata seen.txt", "carry": [".testmondata"]}
        ),
    )

    assert (wt / "seen.txt").read_text() == "db\n"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"source": "none", "carried": [], "refused": [".testmondata"], "torn": False},
            ["carry entries NOT carried into this worktree", "  .testmondata"],
        ),
        (
            {"source": "none", "carried": [], "refused": [], "torn": True},
            ["changed while they were being copied"],
        ),
        (
            {"source": "checkout", "carried": [".testmondata"], "refused": [], "torn": False},
            ["carried from the checkout: .testmondata"],
        ),
        (
            {"source": "none", "carried": [], "refused": [], "torn": False},
            ["nothing to carry yet (no saved copy, none in the checkout)"],
        ),
    ],
    ids=["refused", "torn", "carried", "cold"],
)
async def test_prepare_runtime_says_what_carry_did(tmp_path, repo, record, expected):
    wt = _linked_worktree(repo, tmp_path)
    write(repo, ".testmondata", "db")
    if record["carried"]:
        write(wt, ".testmondata", "db")
    path = tmp_path / "carried.json"
    carried.write_record(path, {**record, "files": {}})

    report = await kraft_builtins.prepare_runtime(
        wt,
        repo,
        entry_of({"setup_command": "", "carry": [".testmondata"]}),
        carried_record=path,
    )

    for line in expected:
        assert line in report
    # The checkout has `.testmondata` untracked, but it is a carry entry, not noise.
    _, _, untracked = report.partition("untracked root-level files")
    assert ".testmondata" not in untracked
