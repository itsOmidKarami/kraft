import json
import os
import shutil
import threading
from dataclasses import replace

import pytest
from support.harness import git
from support.workspace import member_checkout

from kraft.worker import refstore

BRANCH = "kraft/item-1"


@pytest.fixture
def worktree(repo, tmp_path):
    """A Kraft-shaped linked worktree of the cached `repo` on its own branch."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt))
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
    tree = git(repo, "rev-parse", f"{parent}^{{tree}}")
    return git(repo, "commit-tree", tree, "-p", parent, "-m", message)


def _move_in_store(store, ref, oid):
    """What the worker's git does inside the container: a loose ref in `S`."""
    path = store.shadow / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(oid + "\n")


def test_prepare_lists_every_ref_of_the_repository_in_the_store(repo, prepare):
    store = prepare()
    packed = (store.shadow / "packed-refs").read_text()
    main = git(repo, "rev-parse", "main")
    assert f"{main} refs/heads/main" in packed
    assert f"{main} refs/heads/{BRANCH}" in packed
    assert refstore.read_branch(store.shadow, BRANCH) == main


def test_a_worker_moving_main_in_its_store_never_moves_the_repositorys_main(repo, prepare):
    main = git(repo, "rev-parse", "main")
    store = prepare()
    _move_in_store(store, "refs/heads/main", _commit_object(repo, main, "evil"))
    assert refstore.sync(store, "s1") is None
    assert git(repo, "rev-parse", "main") == main


def test_sync_publishes_the_item_branch_the_worker_committed_to(repo, prepare):
    main = git(repo, "rev-parse", "main")
    store = prepare()
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    assert refstore.sync(store, "s1") is None
    assert git(repo, "rev-parse", BRANCH) == work


def test_sync_never_overwrites_a_branch_that_moved_in_the_repository(repo, prepare):
    main = git(repo, "rev-parse", "main")
    store = prepare()
    moved_on_host = _commit_object(repo, main, "host")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", moved_on_host)
    _move_in_store(store, f"refs/heads/{BRANCH}", _commit_object(repo, main, "worker"))
    problem = refstore.sync(store, "s1")
    assert problem is not None and "moved in the repository" in problem
    assert git(repo, "rev-parse", BRANCH) == moved_on_host


def test_what_the_compare_and_swap_trusts_is_never_the_workers_to_write(repo, prepare):
    """The worker writes `S`: an old value kept there could be set to whatever
    the host branch holds now, publishing over a human's push."""
    main = git(repo, "rev-parse", "main")
    store = prepare()
    host = _commit_object(repo, main, "host")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", host)
    for name in os.listdir(store.shadow):
        if (store.shadow / name).is_file() and name != "packed-refs":
            (store.shadow / name).write_text(host)
    (store.shadow / ".kraft-synced").write_text(host)
    _move_in_store(store, f"refs/heads/{BRANCH}", _commit_object(repo, main, "worker"))
    refstore.sync(store, "s1")
    assert git(repo, "rev-parse", BRANCH) == host


@pytest.mark.parametrize(
    "content",
    ["ref: refs/heads/main\n", "not-an-object-id\n", "0" * 40 + "\n"],
    ids=["symref", "garbage", "missing-object"],
)
def test_sync_publishes_only_a_real_commit_id(repo, prepare, content):
    before = git(repo, "rev-parse", BRANCH)
    store = prepare()
    (store.shadow / "refs" / "heads" / BRANCH).parent.mkdir(parents=True, exist_ok=True)
    (store.shadow / "refs" / "heads" / BRANCH).write_text(content)
    refstore.sync(store, "s1")
    assert git(repo, "rev-parse", BRANCH) == before


def test_a_symlinked_ref_in_the_store_is_never_followed(repo, prepare, tmp_path):
    """The worker writes `S`: a symlink there must not point Kraft at a host
    file whose text happens to be an object id."""
    main = git(repo, "rev-parse", "main")
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
    main = git(repo, "rev-parse", "main")
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
    main = git(repo, "rev-parse", "main")
    store = prepare("s1")
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    _move_in_store(store, "refs/heads/scratch", work)
    again = prepare("s2", live=["s1"])
    assert again.shadow == store.shadow
    assert (again.shadow / "refs" / "heads" / "scratch").exists()
    assert git(repo, "rev-parse", BRANCH) == main


def test_a_store_no_live_session_mounted_is_rebuilt_with_todays_branch(repo, prepare):
    """Co-tasks each see the other as live before either mounts anything; a
    store the previous step left must not be taken for theirs, or their
    commits start from a branch the host has since moved."""
    main = git(repo, "rev-parse", "main")
    previous_step = prepare("s0")
    assert refstore.sync(previous_step, "s0") is None
    moved_on_host = _commit_object(repo, main, "host")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", moved_on_host)
    store = prepare("s1", live=["s2"])
    assert refstore.read_branch(store.shadow, BRANCH) == moved_on_host


def test_a_rebuild_publishes_what_a_crashed_session_left_first(repo, prepare):
    main = git(repo, "rev-parse", "main")
    store = prepare("s1")
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    _move_in_store(store, "refs/heads/scratch", work)
    rebuilt = prepare("s2")
    assert git(repo, "rev-parse", BRANCH) == work
    assert rebuilt.carried is None
    assert not (rebuilt.shadow / "refs" / "heads" / "scratch").exists()


def test_a_rebuild_keeps_a_commit_it_could_not_publish(repo, prepare):
    main = git(repo, "rev-parse", "main")
    store = prepare("s1")
    work = _commit_object(repo, main, "worker")
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    git(repo, "update-ref", f"refs/heads/{BRANCH}", _commit_object(repo, main, "host"))
    rebuilt = prepare("s2")
    kept = f"{refstore.UNSYNCED_PREFIX}{BRANCH}"
    assert git(repo, "rev-parse", kept) == work
    assert rebuilt.carried is not None and kept in rebuilt.carried


def test_a_setup_command_mounts_the_store_and_never_publishes(repo, worktree, prepare, tmp_path):
    """No session, no branch: a setup command's store names nothing Kraft
    would move, whatever the worktree's HEAD says (the worker writes it)."""
    main = git(repo, "rev-parse", "main")
    session_store = prepare("s1")
    setup_store = refstore.prepare(tmp_path / "run", worktree, None)
    assert setup_store.shadow == session_store.shadow
    evil = _commit_object(repo, main, "evil")
    _move_in_store(setup_store, "refs/heads/main", evil)
    _move_in_store(setup_store, f"refs/heads/{BRANCH}", evil)
    assert refstore.sync(setup_store) is None
    assert git(repo, "rev-parse", "main") == main


def test_a_store_a_setup_command_built_is_rebuilt_for_the_first_session(
    repo, worktree, prepare, tmp_path
):
    built = refstore.prepare(tmp_path / "run", worktree, None)
    _move_in_store(built, "refs/heads/scratch", git(repo, "rev-parse", "main"))
    store = prepare("s1", live=["s1"])
    assert not (store.shadow / "refs" / "heads" / "scratch").exists()


@pytest.mark.parametrize("entry", ["prepare", "prepare_direct"])
def test_a_reftable_repository_is_refused(repo, worktree, tmp_path, entry):
    git(repo, "config", "core.repositoryformatversion", "1")  # else git ignores the extension
    git(repo, "config", "extensions.refStorage", "reftable")
    call = {
        "prepare": lambda: refstore.prepare(tmp_path / "run", worktree, BRANCH, session_id="s1"),
        "prepare_direct": lambda: refstore.prepare_direct(tmp_path / "run", worktree, BRANCH),
    }[entry]
    with pytest.raises(RuntimeError, match="reftable"):
        call()


def test_a_plain_repository_needs_no_ref_store(repo, tmp_path):
    assert refstore.prepare(tmp_path / "run", repo, "main", session_id="s1") is None


def test_sync_item_publishes_only_that_items_stores(repo, prepare, tmp_path):
    main = git(repo, "rev-parse", "main")
    store = prepare(work_item_id="w1")
    work = _commit_object(repo, main)
    _move_in_store(store, f"refs/heads/{BRANCH}", work)
    assert refstore.sync_item(tmp_path / "run", "w2") == []
    assert git(repo, "rev-parse", BRANCH) == main
    assert refstore.sync_item(tmp_path / "run", "w1") == []
    assert git(repo, "rev-parse", BRANCH) == work


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


@pytest.fixture(params=[False, True], ids=["own-repository", "inside-the-roots-gitdir"])
def workspace(request, tmp_path):
    """A root worktree and its member, and every store a session there mounts."""
    ws = member_checkout(tmp_path, BRANCH, nested=request.param)
    ws.stores = refstore.prepare_stores(
        tmp_path / "run",
        ws.wt,
        BRANCH,
        members=ws.checkout.members,
        session_id="s1",
        work_item_id="w1",
    )
    return ws


def test_a_member_store_publishes_only_the_item_branch_into_its_repository(workspace):
    """Kraft-ju36l, I4 for a member: its store is its connected repository's,
    and of what the worker moves there only the item branch reaches it."""
    ws = workspace
    member_main, root_branch = git(ws.m, "rev-parse", "main"), git(ws.root, "rev-parse", BRANCH)
    work = _commit_object(ws.m, member_main)
    member = ws.stores[1]
    _move_in_store(member, f"refs/heads/{BRANCH}", work)
    _move_in_store(member, "refs/heads/main", work)

    assert refstore.sync_all(ws.stores, "s1") == []

    assert git(ws.m, "rev-parse", BRANCH) == work
    assert git(ws.m, "rev-parse", "main") == member_main
    assert git(ws.root, "rev-parse", BRANCH) == root_branch


def test_a_member_common_inside_the_root_common_gets_its_mount_point_from_kraft(tmp_path):
    """The member's store mounts at its common gitdir, inside the root's store
    here: docker would otherwise create the mount point there as root, where
    the next rebuild cannot remove it."""
    ws = member_checkout(tmp_path, BRANCH, nested=True)
    root, member = refstore.prepare_stores(
        tmp_path / "run", ws.wt, BRANCH, members=ws.checkout.members, session_id="s1"
    )
    assert (root.shadow / member.common.relative_to(root.common)).is_dir()


def test_a_mount_point_is_never_made_through_what_a_worker_left_in_the_store(tmp_path):
    """A live co-task keeps the root's store as it is, with anything its
    worker put there: a symlink where the member's mount point goes must not
    have Kraft make directories wherever it points."""
    ws = member_checkout(tmp_path, BRANCH, nested=True)
    kw = {"members": ws.checkout.members}
    root, _ = refstore.prepare_stores(tmp_path / "run", ws.wt, BRANCH, session_id="s1", **kw)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(root.shadow / "modules")
    (root.shadow / "modules").symlink_to(elsewhere)

    with pytest.raises(RuntimeError, match="not a directory Kraft made"):
        refstore.prepare_stores(tmp_path / "run", ws.wt, BRANCH, session_id="s2", live=["s1"], **kw)
    assert list(elsewhere.iterdir()) == []


def test_a_member_kraft_did_not_check_out_is_never_mounted(repo, worktree, tmp_path):
    with pytest.raises(RuntimeError, match="repos/pkg"):
        refstore.prepare_stores(tmp_path / "run", worktree, BRANCH, members={"repos/pkg": None})


@pytest.fixture
def direct(worktree, tmp_path):
    """The direct store of the worktree's item branch: `(run base, store, its ref file)`."""
    base = tmp_path / "run"
    (store,) = refstore.prepare_direct(base, worktree, BRANCH)
    return base, store, store.common / "refs" / "heads" / BRANCH


_NOT_FILE = "something that is not a regular file"


@pytest.mark.parametrize(
    ("kind", "text", "left", "final", "lock_stays"),
    [
        pytest.param("commit", "{work}", None, "{work}", False, id="commit"),
        pytest.param(
            "symref",
            "ref: refs/heads/main",
            "'ref: refs/heads/main'",
            "{start}",
            False,
            id="symref",
        ),
        pytest.param("tree", "{tree}", "'{tree}'", "{start}", False, id="tree"),
        pytest.param("garbage", "not an oid", "'not an oid'", "{start}", False, id="garbage"),
        pytest.param("revname", "main", "'main'", "{start}", False, id="revname"),
        pytest.param("stale-lock", "{start}", None, "{start}", True, id="stale-lock"),
        pytest.param("no-branch", "junk", None, "junk", False, id="no-branch"),
        pytest.param("lock-dir", "not an oid", "'not an oid'", "{start}", False, id="lock-dir"),
        pytest.param(
            "lock-symlink", "not an oid", "'not an oid'", "{start}", False, id="lock-symlink"
        ),
        pytest.param("ref-symlink", None, _NOT_FILE, "{start}", False, id="ref-symlink"),
        pytest.param("ref-dir", None, _NOT_FILE, "{start}", False, id="ref-dir"),
        pytest.param("ref-deleted", None, _NOT_FILE, "{start}", False, id="ref-deleted"),
        pytest.param("reflog-symlink", None, None, "{start}", False, id="reflog-symlink"),
        pytest.param("reflog-dir", None, None, "{start}", False, id="reflog-dir"),
        pytest.param(
            "no-recorded-value", "not an oid", "'not an oid'", None, False, id="no-recorded-value"
        ),
    ],
)
def test_check_direct(repo, direct, tmp_path, kind, text, left, final, lock_stays):
    base, store, ref = direct
    main = git(repo, "rev-parse", "main")
    start = ref.read_text().strip()
    work = _commit_object(repo, main, "work")
    tree = git(repo, "rev-parse", f"{main}^{{tree}}")
    fmt = dict(work=work, tree=tree, start=start)
    lock = ref.with_name(ref.name + ".lock")
    reflog = store.common / "logs" / "refs" / "heads" / BRANCH
    outside = tmp_path / "outside"
    outside.mkdir()
    if text:
        ref.write_text(text.format(**fmt) + "\n")
    if kind == "stale-lock":
        lock.write_text("")
    if kind == "lock-dir":
        (lock / "x").mkdir(parents=True)
    if kind == "lock-symlink":
        lock.symlink_to(outside / "victim")
    if kind == "ref-symlink":
        ref.unlink()
        ref.symlink_to(outside / "victim")
    if kind == "ref-dir":
        ref.unlink()
        (ref / "x").mkdir(parents=True)
    if kind == "ref-deleted":
        ref.unlink()
    if kind == "reflog-symlink":
        reflog.unlink(missing_ok=True)
        reflog.symlink_to(outside / "victim")
    if kind == "reflog-dir":
        reflog.unlink(missing_ok=True)
        reflog.mkdir()
    if kind == "no-recorded-value":
        refstore._meta_path(refstore.shadow_dir(base, store.worktree_gitdir)).unlink()
    if kind == "no-branch":
        store = replace(store, branch=None)

    got = refstore.check_direct(base, store)

    assert os.path.lexists(lock) == lock_stays
    assert not (outside / "victim").exists()
    if kind.startswith("reflog"):
        assert not os.path.lexists(reflog)
    fix = "removed it" if final is None else f"restored {start}"
    want = f"the worker left refs/heads/{BRANCH} as {(left or '').format(**fmt)}; {fix}"
    assert got == (want if left else None)
    if final is None:
        assert not os.path.lexists(ref)
    else:
        assert ref.is_file() and not ref.is_symlink()
        assert ref.read_text().strip() == final.format(**fmt)
    if kind == "commit":
        # The accepted value is the one a later bad write is put back to.
        ref.write_text("not an oid\n")
        assert "restored " + work in refstore.check_direct(base, store)


def test_a_branch_the_host_packed_since_is_not_put_back(repo, direct):
    """`pack-refs` (or `gc`) on the host after a session removes the loose file; the
    branch is then in `packed-refs`, at whatever the host moved it to."""
    base, store, ref = direct
    moved = _commit_object(repo, git(repo, "rev-parse", "main"), "rebased")
    git(repo, "update-ref", f"refs/heads/{BRANCH}", moved)
    git(repo, "pack-refs", "--all", "--prune")
    assert not ref.exists()

    assert refstore.check_direct(base, store) is None
    assert git(repo, "rev-parse", BRANCH) == moved
    assert not ref.exists()


@pytest.mark.parametrize(
    ("item", "gone", "problems"),
    [("w1", False, 1), ("w2", False, 0), ("w1", True, 1)],
    ids=["its-item", "another-item", "branch-gone"],
)
def test_check_direct_item(direct, worktree, item, gone, problems):
    """A restart leaves no `run_task` to call `check_direct`: the item's own
    stores are found by their sidecars."""
    base, store, ref = direct
    refstore.prepare_direct(base, worktree, BRANCH, work_item_id="w1")
    start = ref.read_text().strip()
    ref.write_text("ref: refs/heads/main\n")
    if gone:
        ref.unlink()  # the host (abandon) or the worker deleted it

    got = refstore.check_direct_item(base, item)

    assert len(got) == problems
    # Never brought back by the recorded value, and a person is told.
    assert ref.exists() != gone
    if gone:
        assert got == [f"refs/heads/{BRANCH} is gone; not restored"]
    else:
        assert ref.read_text().strip() == (start if problems else "ref: refs/heads/main")


def test_sync_item_leaves_a_direct_store_alone(direct):
    """An item that ran under both kinds: a leftover private store beside a direct sidecar."""
    base, store, ref = direct
    leftover = refstore.shadow_dir(base, store.worktree_gitdir)
    (leftover / "refs" / "heads" / BRANCH).parent.mkdir(parents=True)
    (leftover / "refs" / "heads" / BRANCH).write_text(git(store.common, "rev-parse", "main") + "\n")
    meta = refstore._meta_path(refstore.shadow_dir(base, store.worktree_gitdir))
    meta.write_text(json.dumps({**json.loads(meta.read_text()), "work_item_id": "w1"}))

    assert refstore.sync_item(base, "w1") == []


def test_check_direct_item_leaves_a_private_store_alone(repo, worktree, prepare, tmp_path):
    """A store `prepare` built has no recorded value to put back: checked as a direct
    one, its branch would be removed."""
    prepare(work_item_id="w1")
    ref = repo / ".git" / "refs" / "heads" / BRANCH
    ref.write_text("junk\n")

    assert refstore.check_direct_item(tmp_path / "run", "w1") == []
    assert ref.read_text() == "junk\n"


@pytest.mark.parametrize(
    ("call", "live", "survives"),
    [("prepare_direct", ["s1"], True), ("prepare_direct", [], False), ("check_direct", [], True)],
    ids=["prepare-with-a-live-co-task", "prepare-with-none-live", "check-never-removes-it"],
)
def test_a_regular_lock_is_a_co_tasks_to_keep(direct, worktree, call, live, survives):
    """Co-tasks of one worktree run concurrently: a lock may be one's ref update
    in flight. Only a launch with no other live session may clear it."""
    base, store, ref = direct
    lock = ref.with_name(ref.name + ".lock")
    lock.write_text("")
    if call == "prepare_direct":
        refstore.prepare_direct(base, worktree, BRANCH, live=live)
    else:
        refstore.check_direct(base, store)
    assert os.path.lexists(lock) == survives


@pytest.mark.parametrize("kind", ["loose", "packed-only", "slash-branch"])
def test_prepare_direct(repo, worktree, tmp_path, kind):
    base = tmp_path / "run"
    branch = "kraft/fix-x" if kind == "slash-branch" else BRANCH
    if branch != BRANCH:
        git(repo, "worktree", "add", "-q", "-b", branch, str(tmp_path / "wt2"))
        worktree = tmp_path / "wt2"
    common = repo / ".git"
    ref = common / "refs" / "heads" / branch
    if kind == "packed-only":
        git(repo, "pack-refs", "--all", "--prune")
        assert not ref.exists()
    want = git(repo, "rev-parse", branch)
    logs = common / "logs" / "refs" / "heads" / branch
    if kind == "slash-branch":
        shutil.rmtree(logs.parent)  # git made it with the branch; Kraft must be able to

    (store,) = refstore.prepare_direct(base, worktree, branch, work_item_id="w1")

    assert store.direct and store.shadow == store.common == common and store.checkout is None
    assert ref.read_text().strip() == want
    assert logs.parent.is_dir()
    sidecar = refstore._meta_path(refstore.shadow_dir(base, store.worktree_gitdir))
    assert json.loads(sidecar.read_text()) == {
        "branch": branch,
        "value": want,
        "direct": True,
        "work_item_id": "w1",
        "common": str(common),
        "worktree_gitdir": str(store.worktree_gitdir),
    }
    assert [p for p in (base / "sandbox-git").iterdir() if p.is_dir()] == []


def test_prepare_direct_puts_back_what_the_last_session_left(direct, worktree):
    """A session that ended without `check_direct` (a daemon restart) left a
    symref to main; the next prepare must not record it as the branch's value."""
    base, store, ref = direct
    start = ref.read_text().strip()
    ref.write_text("ref: refs/heads/main\n")

    refstore.prepare_direct(base, worktree, BRANCH)

    assert ref.read_text().strip() == start
    sidecar = refstore._meta_path(refstore.shadow_dir(base, store.worktree_gitdir))
    assert json.loads(sidecar.read_text())["value"] == start


def test_prepare_direct_removes_planted_links_the_last_session_left(direct, worktree, tmp_path):
    """The lock and the reflog are writable by the worker; a link left there would
    be followed by the next launch, so a restart removes them like `check_direct`."""
    base, store, ref = direct
    outside = tmp_path / "outside"
    outside.mkdir()
    lock = ref.with_name(ref.name + ".lock")
    reflog = store.common / "logs" / "refs" / "heads" / BRANCH
    lock.symlink_to(outside / "victim")
    reflog.unlink(missing_ok=True)
    reflog.symlink_to(outside / "victim")

    refstore.prepare_direct(base, worktree, BRANCH)

    assert not os.path.lexists(lock) and not os.path.lexists(reflog)
    assert not (outside / "victim").exists()


@pytest.mark.parametrize("call", ["sync", "sync_all", "prepare"])
def test_a_direct_store_is_never_synced_or_rebuilt(direct, call):
    """Its shadow is the real common gitdir: sync and rebuild rmtree it."""
    base, store, _ = direct
    run = {
        "sync": lambda: refstore.sync(store),
        "sync_all": lambda: refstore.sync_all([store]),
        "prepare": lambda: refstore._prepare(base, store),
    }[call]
    with pytest.raises(ValueError, match="direct"):
        run()
    assert (store.common / "objects").is_dir()


def test_prepare_direct_needs_a_linked_worktree(repo, tmp_path):
    assert refstore.prepare_direct(tmp_path / "run", repo, BRANCH) == []


def test_prepare_direct_gives_a_member_its_own_store(tmp_path):
    ws = member_checkout(tmp_path, BRANCH)
    root, member = refstore.prepare_direct(
        tmp_path / "run", ws.wt, BRANCH, members=ws.checkout.members
    )
    assert (root.checkout, member.checkout) == (None, ws.wt / "repos" / "pkg")
    assert member.shadow == member.common == ws.checkout.members["repos/pkg"][0]
    assert (member.common / "refs" / "heads" / BRANCH).is_file()
