"""`inplace.copy`: the throwaway shared clone of an in-place item's branch
that a read-only agent works in instead of the person's checkout."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest
from support.harness import IN_PLACE, git, v1_named_chain, write

from kraft import builtins, inplace, store


async def _item(item_on, run_dirs, repo):
    """An in-place item whose branch is one commit past `main`, the person on `main`."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), wid="w1", target=IN_PLACE)
    branch = store.branch_for(it.row())
    git(repo, "checkout", "-q", "-b", branch)
    write(repo, "item.txt", "the item's\n")
    git(repo, "add", "item.txt")
    git(repo, "commit", "-qm", "item")
    git(repo, "checkout", "-q", "main")
    return it.row(), branch


def _dest(tmp_path):
    dest = tmp_path / "copy"
    dest.mkdir()
    return dest


async def test_a_copy_is_the_branch_tip_detached(item_on, run_dirs, repo, tmp_path):
    row, branch = await _item(item_on, run_dirs, repo)
    dest = _dest(tmp_path)

    await inplace.make_copy(row, dest, timeout_s=None)

    assert git(dest, "rev-parse", "HEAD") == git(repo, "rev-parse", f"refs/heads/{branch}")
    assert subprocess.run(["git", "-C", str(dest), "symbolic-ref", "-q", "HEAD"]).returncode != 0
    assert git(dest, "remote") == ""
    assert (dest / "item.txt").read_text() == "the item's\n"
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("_", [None], ids=["isolation"])
async def test_nothing_done_in_a_copy_reaches_the_person(item_on, run_dirs, repo, tmp_path, _):
    row, branch = await _item(item_on, run_dirs, repo)
    write(repo, "stashed.txt", "stashed\n")
    git(repo, "stash", "push", "-q", "-u")
    write(repo, "calc.py", "the person's unsaved edit\n")

    def person():
        return (
            git(repo, "for-each-ref"),
            git(repo, "stash", "list"),
            (repo / ".git/config").read_bytes(),
            (repo / "calc.py").read_bytes(),
            git(repo, "symbolic-ref", "HEAD"),
        )

    was = person()
    dest = _dest(tmp_path)
    await inplace.make_copy(row, dest, timeout_s=None)

    write(dest, "calc.py", "the agent's\n")
    git(dest, "stash", "push", "-q")
    git(dest, "branch", "-f", branch, "HEAD~1")
    git(dest, "config", "x.y", "z")
    pushed = subprocess.run(
        ["git", "-C", str(dest), "push", "origin", "HEAD:refs/heads/x"], capture_output=True
    )

    assert pushed.returncode != 0
    assert person() == was


def _fails_at(monkeypatch, step: str, script: str) -> None:
    """The `step` git command of the copy replaced by `sh -c script`."""
    real = builtins._git_group

    async def _git_group(args, **kw):
        if step in args:
            args = ["sh", "-c", script]
        return await real(args, **kw)

    monkeypatch.setattr(builtins, "_git_group", _git_group)


@pytest.mark.parametrize("how", ["missing-branch", "not-a-repo", "checkout-fails", "not-empty"])
async def test_a_copy_that_cannot_be_made_leaves_nothing(
    item_on, run_dirs, repo, tmp_path, monkeypatch, how
):
    row, branch = await _item(item_on, run_dirs, repo)
    dest = _dest(tmp_path)
    if how == "missing-branch":
        git(repo, "branch", "-q", "-D", branch)
    elif how == "not-a-repo":
        row["repo"] = str(tmp_path / "plain")
        (tmp_path / "plain").mkdir()
    elif how == "not-empty":
        write(dest, "theirs.txt", "not Kraft's\n")
    else:
        _fails_at(monkeypatch, "checkout", f"touch '{dest}/half'; exit 1")

    with pytest.raises(inplace.CopyFailed, match=f"could not make a copy of {branch} to work in"):
        await inplace.make_copy(row, dest, timeout_s=None)

    # A folder it did not fill is never emptied.
    assert [p.name for p in dest.iterdir()] == (["theirs.txt"] if how == "not-empty" else [])
    if how == "missing-branch":
        gone = subprocess.run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", branch])
        assert gone.returncode != 0, "a failed copy never makes the branch"


@pytest.mark.parametrize("_", [None], ids=["cancelled"])
async def test_a_cancelled_copy_is_removed(item_on, run_dirs, repo, tmp_path, monkeypatch, _):
    row, _branch = await _item(item_on, run_dirs, repo)
    dest = _dest(tmp_path)
    marker = tmp_path / "cloning"
    _fails_at(monkeypatch, "clone", f"touch '{dest}/half' '{marker}'; exec sleep 30")
    making = asyncio.ensure_future(inplace.make_copy(row, dest, timeout_s=None))
    while not marker.exists():
        await asyncio.sleep(0.02)
    started = time.monotonic()

    making.cancel()
    with pytest.raises(asyncio.CancelledError):
        await making

    assert time.monotonic() - started < 5
    assert list(dest.iterdir()) == []


async def test_dead_copies_go_and_live_ones_stay(item_on, database, run_dirs):
    it = await item_on(v1_named_chain(run_dirs.base / "t"), wid="w1", target=IN_PLACE)
    await it.session("running", "review.reply", running=(os.getpid(), 0.0))
    await it.session("finished", "review.reply", "done")
    before = time.time()
    for sid, mtime in [("running", -10), ("finished", -10), ("no-row", -10), ("fresh", 10)]:
        copy = inplace.copy_path(run_dirs, it.id, sid)
        write(copy, "f", "x\n")
        os.utime(copy, (before + mtime, before + mtime))

    removed = await inplace.drop_dead_copies(database, run_dirs, before=before)

    assert sorted(p.parent.name for p in removed) == ["finished", "no-row"]
    sids = ["running", "finished", "no-row", "fresh"]
    left = [sid for sid in sids if inplace.copy_path(run_dirs, it.id, sid).exists()]
    assert left == ["running", "fresh"]
