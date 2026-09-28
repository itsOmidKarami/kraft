import os
import subprocess
import threading

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


@pytest.fixture
def prepare(worktree, tmp_path):
    """`prepare(session, live=())` for the worktree's item branch."""

    def go(session="s1", live=(), **kw):
        return refstore.prepare(
            tmp_path / "run", worktree, BRANCH, session_id=session, live=live, **kw
        )

    return go


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


def test_prepare_lists_every_ref_of_the_repository_in_the_store(repo, prepare):
    store = prepare()
    packed = (store.shadow / "packed-refs").read_text()
    main = _git(repo, "rev-parse", "main")
    assert f"{main} refs/heads/main" in packed
    assert f"{main} refs/heads/{BRANCH}" in packed
    assert refstore.read_branch(store.shadow, BRANCH) == main


def test_a_worker_moving_main_in_its_store_never_moves_the_repositorys_main(repo, prepare):
    main = _git(repo, "rev-parse", "main")
    store = prepare()
    _move_in_store(store, "refs/heads/main", _commit_object(repo, main, "evil"))
    assert refstore.sync(store, "s1") is None
    assert _git(repo, "rev-parse", "main") == main


def test_sync_publishes_the_item_branch_the_worker_committed_to(repo, prepare):
    main = _git(repo, "rev-parse", "main")
    store = prepare()
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    assert refstore.sync(store, "s1") is None
    assert _git(repo, "rev-parse", BRANCH) == work


def test_sync_never_overwrites_a_branch_that_moved_in_the_repository(repo, prepare):
    main = _git(repo, "rev-parse", "main")
    store = prepare()
    moved_on_host = _commit_object(repo, main, "host")
    _git(repo, "update-ref", f"refs/heads/{BRANCH}", moved_on_host)
    _move_in_store(store, f"refs/heads/{BRANCH}", _commit_object(repo, main, "worker"))
    problem = refstore.sync(store, "s1")
    assert problem is not None and "moved in the repository" in problem
    assert _git(repo, "rev-parse", BRANCH) == moved_on_host


def test_what_the_compare_and_swap_trusts_is_never_the_workers_to_write(repo, prepare):
    """The worker writes `S`: an old value kept there could be set to whatever
    the host branch holds now, publishing over a human's push."""
    main = _git(repo, "rev-parse", "main")
    store = prepare()
    host = _commit_object(repo, main, "host")
    _git(repo, "update-ref", f"refs/heads/{BRANCH}", host)
    for name in os.listdir(store.shadow):
        if (store.shadow / name).is_file() and name != "packed-refs":
            (store.shadow / name).write_text(host)
    (store.shadow / ".kraft-synced").write_text(host)
    _move_in_store(store, f"refs/heads/{BRANCH}", _commit_object(repo, main, "worker"))
    refstore.sync(store, "s1")
    assert _git(repo, "rev-parse", BRANCH) == host


@pytest.mark.parametrize(
    "content",
    ["ref: refs/heads/main\n", "not-an-object-id\n", "0" * 40 + "\n"],
    ids=["symref", "garbage", "missing-object"],
)
def test_sync_publishes_only_a_real_commit_id(repo, prepare, content):
    before = _git(repo, "rev-parse", BRANCH)
    store = prepare()
    (store.shadow / "refs" / "heads" / BRANCH).parent.mkdir(parents=True, exist_ok=True)
    (store.shadow / "refs" / "heads" / BRANCH).write_text(content)
    refstore.sync(store, "s1")
    assert _git(repo, "rev-parse", BRANCH) == before


def test_a_symlinked_ref_in_the_store_is_never_followed(repo, prepare, tmp_path):
    """The worker writes `S`: a symlink there must not point Kraft at a host
    file whose text happens to be an object id."""
    main = _git(repo, "rev-parse", "main")
    outside = tmp_path / "outside"
    outside.write_text(_commit_object(repo, main) + "\n")
    store = prepare()
    loose = store.shadow / "refs" / "heads" / BRANCH
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.symlink_to(outside)
    assert refstore.read_branch(store.shadow, BRANCH) is None


def test_a_symlinked_ref_directory_is_never_followed(repo, prepare, tmp_path):
    """`O_NOFOLLOW` guards only the last component: a directory on the way
    can be the symlink instead."""
    main = _git(repo, "rev-parse", "main")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "item-1").write_text(_commit_object(repo, main) + "\n")
    store = prepare()
    (store.shadow / "refs" / "heads" / "kraft").symlink_to(outside, target_is_directory=True)
    assert refstore.read_branch(store.shadow, BRANCH) is None


@pytest.mark.parametrize("where", ["packed-refs", f"refs/heads/{BRANCH}"])
def test_a_fifo_in_the_store_never_blocks_kraft(prepare, where):
    """Reading a FIFO blocks until a writer comes; under the store's lock that
    hung every later launch of the worktree, and startup."""
    store = prepare()
    path = store.shadow / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    os.mkfifo(path)
    done = threading.Event()
    threading.Thread(target=lambda: (refstore.sync(store, "s1"), done.set()), daemon=True).start()
    assert done.wait(10)


def test_a_live_co_task_keeps_the_store_it_is_committing_through(repo, prepare):
    main = _git(repo, "rev-parse", "main")
    store = prepare("s1")
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    _move_in_store(store, "refs/heads/scratch", work)
    again = prepare("s2", live=["s1"])
    assert again.shadow == store.shadow
    assert (again.shadow / "refs" / "heads" / "scratch").exists()
    assert _git(repo, "rev-parse", BRANCH) == main


def test_a_store_no_live_session_mounted_is_rebuilt_with_todays_branch(repo, prepare):
    """Co-tasks each see the other as live before either mounts anything; a
    store the previous step left must not be taken for theirs, or their
    commits start from a branch the host has since moved."""
    main = _git(repo, "rev-parse", "main")
    previous_step = prepare("s0")
    assert refstore.sync(previous_step, "s0") is None
    moved_on_host = _commit_object(repo, main, "host")
    _git(repo, "update-ref", f"refs/heads/{BRANCH}", moved_on_host)
    store = prepare("s1", live=["s2"])
    assert refstore.read_branch(store.shadow, BRANCH) == moved_on_host


def test_a_rebuild_publishes_what_a_crashed_session_left_first(repo, prepare):
    main = _git(repo, "rev-parse", "main")
    store = prepare("s1")
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    _move_in_store(store, "refs/heads/scratch", work)
    rebuilt = prepare("s2")
    assert _git(repo, "rev-parse", BRANCH) == work
    assert rebuilt.carried is None
    assert not (rebuilt.shadow / "refs" / "heads" / "scratch").exists()


def test_a_rebuild_keeps_a_commit_it_could_not_publish(repo, prepare):
    main = _git(repo, "rev-parse", "main")
    store = prepare("s1")
    work = _commit_object(repo, main, "worker")
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    _git(repo, "update-ref", f"refs/heads/{BRANCH}", _commit_object(repo, main, "host"))
    rebuilt = prepare("s2")
    kept = f"{refstore.UNSYNCED_PREFIX}{BRANCH}"
    assert _git(repo, "rev-parse", kept) == work
    assert rebuilt.carried is not None and kept in rebuilt.carried


def test_a_setup_command_mounts_the_store_and_never_publishes(repo, worktree, prepare, tmp_path):
    """No session, no branch: a setup command's store names nothing Kraft
    would move, whatever the worktree's HEAD says (the worker writes it)."""
    main = _git(repo, "rev-parse", "main")
    session_store = prepare("s1")
    setup_store = refstore.prepare(tmp_path / "run", worktree, None)
    assert setup_store.shadow == session_store.shadow
    evil = _commit_object(repo, main, "evil")
    _move_in_store(setup_store, "refs/heads/main", evil)
    _move_in_store(setup_store, f"refs/heads/{BRANCH}", evil)
    assert refstore.sync(setup_store) is None
    assert _git(repo, "rev-parse", "main") == main


def test_a_store_a_setup_command_built_is_rebuilt_for_the_first_session(
    repo, worktree, prepare, tmp_path
):
    built = refstore.prepare(tmp_path / "run", worktree, None)
    _move_in_store(built, "refs/heads/scratch", _git(repo, "rev-parse", "main"))
    store = prepare("s1", live=["s1"])
    assert not (store.shadow / "refs" / "heads" / "scratch").exists()


def test_a_reftable_repository_is_refused(repo, prepare):
    _git(repo, "config", "extensions.refStorage", "reftable")
    with pytest.raises(RuntimeError, match="reftable"):
        prepare()


def test_a_plain_repository_needs_no_ref_store(repo, tmp_path):
    assert refstore.prepare(tmp_path / "run", repo, "main", session_id="s1") is None


def test_sync_item_publishes_only_that_items_stores(repo, prepare, tmp_path):
    main = _git(repo, "rev-parse", "main")
    store = prepare(work_item_id="w1")
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    assert refstore.sync_item(tmp_path / "run", "w2") == []
    assert _git(repo, "rev-parse", BRANCH) == main
    assert refstore.sync_item(tmp_path / "run", "w1") == []
    assert _git(repo, "rev-parse", BRANCH) == work


def test_what_sync_item_trusts_is_never_inside_the_store(prepare):
    """The worker writes `S`; the record of which repository and branch a
    store publishes to must live where it cannot."""
    store = prepare(work_item_id="w1")
    meta = store.shadow.parent / f"{store.shadow.name}.json"
    assert '"work_item_id": "w1"' in meta.read_text()
    assert not meta.is_relative_to(store.shadow)


def test_discard_forgets_the_store(worktree, prepare, tmp_path):
    store = prepare(work_item_id="w1")
    refstore.discard(tmp_path / "run", worktree)
    assert not store.shadow.exists()
    assert not list((tmp_path / "run" / "sandbox-git").iterdir())
