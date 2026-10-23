from __future__ import annotations

import subprocess

import pytest
from support.harness import IN_PLACE, commit_all, git, make_repo, v1_named_chain, write
from support.store_fixtures import mk_item

from kraft import inplace, node_runs, store


async def test_completed_pins_a_ref_that_survives_a_reset(database, tmp_path):
    await mk_item(database)
    repo = make_repo(tmp_path)
    await node_runs.entered(database, repo, "w1", "impl")
    write(repo, "a.py", "x = 1\n")
    sha = commit_all(repo)
    await node_runs.completed(database, repo, "w1", "impl")
    rows = database.read(lambda c: store.node_run_rows(c, "w1"))
    assert [(r["node_id"], r["attempt"], r["end_sha"]) for r in rows] == [("impl", 1, sha)]
    git(repo, "reset", "-q", "--hard", "HEAD~1")  # what a rebase does to the old tip
    assert git(repo, "rev-parse", "refs/kraft/w1/impl/1") == sha


@pytest.mark.parametrize("end", ["completed", "gate_requested"])
async def test_in_place_the_items_branch_is_pinned_not_the_persons_head(
    item_on, run_dirs, repo, end
):
    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation", target=IN_PLACE)
    branch = store.branch_for(it.row())
    git(repo, "checkout", "-q", "-b", branch)
    await node_runs.entered(it.database, repo, it.id, "implementation")
    tip = commit_all(repo, "the item's")
    git(repo, "checkout", "-q", "main")
    write(repo, "theirs.txt", "the person's\n")
    commit_all(repo, "the person's")

    await getattr(node_runs, end)(it.database, repo, it.id, "implementation")

    pinned = git(repo, "for-each-ref", "--format=%(objectname)", f"refs/kraft/{it.id}/")
    assert pinned == tip


def _dirty_flag(it) -> int:
    """The run's, or the gate's pin's, whichever ended last."""
    return it.database.read(
        lambda c: c.execute(
            "SELECT dirty FROM node_runs WHERE work_item_id = ? AND end_sha IS NOT NULL "
            "ORDER BY attempt DESC LIMIT 1",
            (it.id,),
        ).fetchone()["dirty"]
    )


@pytest.mark.parametrize("end", ["completed", "gate_requested"])
@pytest.mark.parametrize("left", [False, True], ids=["the-persons-edit", "the-items-leftover"])
async def test_in_place_dirty_is_the_items_leftovers_not_the_persons_edits(
    item_on, run_dirs, repo, end, left
):
    """The person's uncommitted edit in the shared checkout never marks the
    item's run dirty; what its own tasks left there, unchanged since, does."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation", target=IN_PLACE)
    git(repo, "checkout", "-q", "-b", store.branch_for(it.row()))
    await node_runs.entered(it.database, repo, it.id, "implementation")
    write(repo, "calc.py", "# the person's edit\n")
    if left:
        write(repo, "left.txt", "a task left this\n")
        inplace.save_leftovers(run_dirs, it.id, {"left.txt": git(repo, "hash-object", "left.txt")})

    await getattr(node_runs, end)(it.database, repo, it.id, "implementation", run_dirs=run_dirs)

    assert _dirty_flag(it) == left


@pytest.mark.parametrize(
    "upstream_in, moves",
    [("merged", True), ("rebased", True), ("forge", True), (None, False), ("landed", False)],
    ids=[
        "main-merged-into-the-branch",
        "branch-rebased-by-hand",
        "forge-merged-a-main-never-fetched-here",
        "main-moved-alone",
        "branch-already-merged-into-main",
    ],
)
async def test_gate_moves_the_base_past_upstream_the_branch_took_in(
    item_on, run_dirs, repo, tmp_path, upstream_in, moves
):
    """A branch that took main in outside Kraft's own rebase must not show
    main's commits as the item's change at the gate."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation")
    old = git(repo, "rev-parse", "HEAD")
    await it.database.write(lambda c: store.set_base_ref(c, it.id, old))
    if upstream_in == "forge":
        git(tmp_path, "init", "-q", "--bare", "origin.git")
        git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
        git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "-b", "item")
    write(repo, "mine.py", "x = 1\n")
    commit_all(repo, "the item's")
    git(repo, "checkout", "-q", "main")
    write(repo, "theirs.py", "y = 1\n")
    upstream = commit_all(repo, "upstream")
    git(repo, "checkout", "-q", "item")
    if upstream_in == "merged":
        git(repo, "merge", "-q", "--no-edit", "main")
    elif upstream_in == "rebased":
        git(repo, "rebase", "-q", "main")
    elif upstream_in == "forge":
        # main moved on origin only, and the forge's merge of it came home:
        # this checkout's own copies of main still say `old`.
        git(repo, "push", "-q", "origin", "main")
        git(repo, "merge", "-q", "--no-edit", upstream)
        git(repo, "branch", "-f", "main", old)
        git(repo, "update-ref", "refs/remotes/origin/main", old)
    elif upstream_in == "landed":
        git(repo, "merge", "-q", "--no-edit", "main")
        git(repo, "checkout", "-q", "main")
        git(repo, "merge", "-q", "--no-ff", "--no-edit", "item")
        git(repo, "checkout", "-q", "item")

    await node_runs.gate_requested(it.database, repo, it.id, "implementation")

    want = upstream if moves else old
    assert it.row()["base_ref"] == want
    # What the review page reads: the gate attempt's own base.
    attempts = it.database.read(lambda c: store.gate_attempts(c, it.id, "implementation"))
    assert attempts[-1]["base_sha"] == want


async def test_drop_refs_removes_only_this_items_refs(database, tmp_path):
    repo = make_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    node_runs.pin_ref(repo, "w1", "impl", 1, head)
    node_runs.pin_ref(repo, "w2", "impl", 1, head)
    node_runs.drop_refs(repo, "w1")
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/kraft/")
    assert refs.splitlines() == ["refs/kraft/w2/impl/1"]


async def test_no_worktree_still_writes_the_events(database):
    """The first node runs before the worktree exists: events, no row."""
    await mk_item(database)
    await node_runs.entered(database, None, "w1", "env_setup")
    await node_runs.completed(database, None, "w1", "env_setup")
    assert database.read(lambda c: store.node_run_rows(c, "w1")) == []


async def test_a_hanging_ref_write_is_bounded_and_never_fails_the_walk(
    database, tmp_path, monkeypatch
):
    """Kraft-dl5fl 4: `update-ref` runs off the event loop with a timeout; one
    that hangs costs the old attempt's ref, never the node's completion."""
    await mk_item(database)
    repo = make_repo(tmp_path)
    seen = {}

    def hang(cmd, **kw):
        if cmd[:2] == ["git", "update-ref"]:
            seen["timeout"] = kw.get("timeout")
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 0)
        return real(cmd, **kw)

    real = subprocess.run
    monkeypatch.setattr(node_runs.subprocess, "run", hang)
    await node_runs.entered(database, repo, "w1", "impl")
    await node_runs.completed(database, repo, "w1", "impl")

    assert seen["timeout"] == 10
    rows = database.read(lambda c: store.node_run_rows(c, "w1"))
    assert rows[0]["end_sha"] is not None
