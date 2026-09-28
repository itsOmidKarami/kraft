import os
import subprocess

import pytest

from kraft.worker import refstore

BRANCH = "kraft/item-1"


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def worktree(repo, tmp_path):
    """A Kraft-shaped linked worktree of the cached `repo` on its own branch."""
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt))
    return wt


def _commit_object(repo, parent, message="work"):
    """A commit in the repository's object store that no ref points at: what a
    sandboxed worker's `git commit` leaves behind in the shared objects."""
    tree = _git(repo, "rev-parse", f"{parent}^{{tree}}")
    return _git(repo, "commit-tree", tree, "-p", parent, "-m", message)


def _move_in_store(store, ref, oid):
    """What the worker's git does inside the container: a loose ref in `S`."""
    path = store.shadow / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(oid + "\n")


def test_prepare_lists_every_ref_of_the_repository_in_the_store(repo, worktree, tmp_path):
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    packed = (store.shadow / "packed-refs").read_text()
    main = _git(repo, "rev-parse", "main")
    assert f"{main} refs/heads/main" in packed
    assert f"{main} refs/heads/{BRANCH}" in packed
    assert refstore.read_branch(store.shadow, BRANCH) == main


def test_a_worker_moving_main_in_its_store_never_moves_the_repositorys_main(
    repo, worktree, tmp_path
):
    main = _git(repo, "rev-parse", "main")
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    evil = _commit_object(repo, main, "evil")
    _move_in_store(store, "refs/heads/main", evil)
    assert refstore.sync(store) is None
    assert _git(repo, "rev-parse", "main") == main


def test_sync_publishes_the_item_branch_the_worker_committed_to(repo, worktree, tmp_path):
    main = _git(repo, "rev-parse", "main")
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    assert refstore.sync(store) is None
    assert _git(repo, "rev-parse", BRANCH) == work


def test_sync_never_overwrites_a_branch_that_moved_in_the_repository(repo, worktree, tmp_path):
    main = _git(repo, "rev-parse", "main")
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    moved_on_host = _commit_object(repo, main, "host")
    _git(repo, "update-ref", f"refs/heads/{BRANCH}", moved_on_host)
    _move_in_store(store, f"refs/heads/{BRANCH}", _commit_object(repo, main, "worker"))
    problem = refstore.sync(store)
    assert problem is not None and "moved in the repository" in problem
    assert _git(repo, "rev-parse", BRANCH) == moved_on_host


@pytest.mark.parametrize(
    "content",
    ["ref: refs/heads/main\n", "not-an-object-id\n", "0" * 40 + "\n"],
    ids=["symref", "garbage", "missing-object"],
)
def test_sync_publishes_only_a_real_commit_id(repo, worktree, tmp_path, content):
    before = _git(repo, "rev-parse", BRANCH)
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    (store.shadow / "refs" / "heads" / BRANCH).parent.mkdir(parents=True, exist_ok=True)
    (store.shadow / "refs" / "heads" / BRANCH).write_text(content)
    refstore.sync(store)
    assert _git(repo, "rev-parse", BRANCH) == before


def test_a_symlinked_ref_in_the_store_is_never_followed(repo, worktree, tmp_path):
    """The worker writes `S`: a symlink there must not point Kraft at a host
    file whose text happens to be an object id."""
    main = _git(repo, "rev-parse", "main")
    outside = tmp_path / "outside"
    outside.write_text(_commit_object(repo, main) + "\n")
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    loose = store.shadow / "refs" / "heads" / BRANCH
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.symlink_to(outside)
    assert refstore.read_branch(store.shadow, BRANCH) is None


def test_a_live_co_task_keeps_the_store_it_is_committing_through(repo, worktree, tmp_path):
    main = _git(repo, "rev-parse", "main")
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    again = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=True)
    assert again.shadow == store.shadow
    assert refstore.read_branch(again.shadow, BRANCH) == work


def test_a_rebuild_publishes_what_a_crashed_session_left_first(repo, worktree, tmp_path):
    main = _git(repo, "rev-parse", "main")
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    _move_in_store(store, "refs/heads/scratch", work)
    rebuilt = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False)
    assert _git(repo, "rev-parse", BRANCH) == work
    assert not (rebuilt.shadow / "refs" / "heads" / "scratch").exists()


def test_the_branch_defaults_to_what_the_worktree_head_names(repo, worktree, tmp_path):
    store = refstore.prepare(tmp_path / "run", worktree, None, reuse=False)
    assert store.branch == BRANCH


def test_a_detached_worktree_has_no_ref_store_branch(repo, worktree, tmp_path):
    _git(worktree, "checkout", "-q", "--detach")
    with pytest.raises(RuntimeError, match="not on a branch"):
        refstore.prepare(tmp_path / "run", worktree, None, reuse=False)


def test_a_plain_repository_needs_no_ref_store(repo, tmp_path):
    assert refstore.prepare(tmp_path / "run", repo, "main", reuse=False) is None
    assert os.path.isdir(repo / ".git")


def test_sync_item_publishes_only_that_items_stores(repo, worktree, tmp_path):
    main = _git(repo, "rev-parse", "main")
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False, work_item_id="w1")
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    assert refstore.sync_item(tmp_path / "run", "w2") == []
    assert _git(repo, "rev-parse", BRANCH) == main
    assert refstore.sync_item(tmp_path / "run", "w1") == []
    assert _git(repo, "rev-parse", BRANCH) == work


def test_what_sync_item_trusts_is_never_inside_the_store(repo, worktree, tmp_path):
    """The worker writes `S`; the record of which repository and branch a
    store publishes to must live where it cannot."""
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=False, work_item_id="w1")
    meta = store.shadow.parent / f"{store.shadow.name}.json"
    assert meta.is_file()
    assert not meta.is_relative_to(store.shadow)


def test_a_session_reusing_a_live_store_still_records_its_owner(repo, worktree, tmp_path):
    """A store first built by a sandboxed setup command has no owner; the
    session that reuses it must record one, or a restart cannot publish it."""
    store = refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=True)
    meta = store.shadow.parent / f"{store.shadow.name}.json"
    assert not meta.exists()
    refstore.prepare(tmp_path / "run", worktree, BRANCH, reuse=True, work_item_id="w1")
    assert '"work_item_id": "w1"' in meta.read_text()
