from __future__ import annotations

import subprocess

import pytest
from support.harness import IN_PLACE, commit_all, git, make_repo, v1_named_chain, write
from support.store_fixtures import mk_item

from kraft import node_runs, store


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
    await node_runs.entered(it.database, repo, it.id, "implementation", branch=branch)
    tip = commit_all(repo, "the item's")
    git(repo, "checkout", "-q", "main")
    write(repo, "theirs.txt", "the person's\n")
    commit_all(repo, "the person's")

    await getattr(node_runs, end)(it.database, repo, it.id, "implementation")

    pinned = git(repo, "for-each-ref", "--format=%(objectname)", f"refs/kraft/{it.id}/")
    assert pinned == tip


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
