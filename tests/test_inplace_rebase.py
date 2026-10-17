"""`inplace.rebase`: an in-place item's branch brought onto a newer base
without moving the person's checkout, or forcing anything in it. A sibling
of test_inplace.py, which is at its line budget."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest
from support.addons import with_origin
from support.api import _force_node, _paused, _poll_events, _set_status
from support.harness import commit_all, git, v1_named_chain, write
from test_inplace import _IN_PLACE, _head, _on_branch, _prepared

from kraft import builtins, inplace, store


async def _behind_origin(
    item_on, database, run_dirs, repo, *, main_writes="main.txt", base_tracks="f"
):
    """An in-place item with one commit of its own, and `origin/main` one
    commit past where the branch forked, writing `main_writes`; the person
    on `main` (at the item's base, not origin's newer one). The base tracks
    `f` and `base_tracks`."""
    write(repo, "f", "base\n")
    write(repo, base_tracks, "base\n")
    commit_all(repo, "f")
    with_origin(repo)
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    _on_branch(repo, branch, "item.txt", "the item's\n")
    git(repo, "checkout", "-q", "-b", "upstream")
    write(repo, main_writes, "main's newer\n")
    commit_all(repo, "main moves")
    git(repo, "push", "-q", "origin", "upstream:main")
    git(repo, "checkout", "-q", "main")
    git(repo, "branch", "-q", "-D", "upstream")
    return it, branch


def _contains_origin_main(repo, branch) -> bool:
    done = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "refs/remotes/origin/main", branch], cwd=repo
    )
    return done.returncode == 0


async def test_rebase_in_the_checkout_keeps_the_persons_edits(item_on, database, run_dirs, repo):
    it, branch = await _behind_origin(item_on, database, run_dirs, repo)
    git(repo, "checkout", "-q", branch)
    write(repo, "f", "the person's edit\n")
    git(repo, "config", "rebase.updateRefs", "true")
    git(repo, "branch", "theirs", branch)  # never moved along with the item's

    head = await inplace.rebase(run_dirs, it.row(), base="main")

    assert head == git(repo, "rev-parse", "refs/remotes/origin/main")
    assert _contains_origin_main(repo, branch)
    assert _head(repo) == f"refs/heads/{branch}"
    assert (repo / "main.txt").is_file() and (repo / "item.txt").is_file()
    assert (repo / "f").read_text() == "the person's edit\n"
    assert git(repo, "status", "--porcelain") == "M f"
    assert git(repo, "stash", "list") == ""
    assert not _contains_origin_main(repo, "theirs")


@pytest.mark.parametrize(
    ("main_writes", "hook", "unmerged"),
    [("f", None, ["f"]), ("main.txt", "echo the hook >> f", [])],
    ids=["clash", "put-back-refused"],
)
async def test_an_autostash_clash_raises_with_the_stash_and_leaves_it(
    item_on, database, run_dirs, repo, main_writes, hook, unmerged
):
    """The 2026-10-17 experiment: git exits 0, leaves `f` unmerged and keeps
    the person's edit as a stash entry. A hook that writes `f` mid-rebase
    makes git refuse the put-back outright: exit 0, and nothing unmerged."""
    it, branch = await _behind_origin(item_on, database, run_dirs, repo, main_writes=main_writes)
    git(repo, "checkout", "-q", branch)
    write(repo, "f", "the person's edit\n")
    if hook is not None:
        write(repo, ".git/hooks/post-rewrite", f"#!/bin/sh\n{hook}\n").chmod(0o755)

    with pytest.raises(inplace.AutostashClash) as raised:
        await inplace.rebase(run_dirs, it.row(), base="main")

    assert raised.value.paths == unmerged
    assert git(repo, "rev-parse", "refs/stash") == raised.value.stash
    assert git(repo, "show", f"{raised.value.stash}:f") == "the person's edit"
    assert git(repo, "stash", "list") == "stash@{0}: autostash"
    assert raised.value.stash in str(raised.value) and "f" in str(raised.value)


async def test_a_conflicting_rebase_in_the_checkout_aborts_and_puts_the_edits_back(
    item_on, database, run_dirs, repo
):
    it, branch = await _behind_origin(item_on, database, run_dirs, repo, main_writes="item.txt")
    git(repo, "checkout", "-q", branch)
    write(repo, "f", "the person's edit\n")
    tip = git(repo, "rev-parse", branch)

    with pytest.raises(builtins.RebaseConflict, match="item.txt"):
        await inplace.rebase(run_dirs, it.row(), base="main")

    assert (_head(repo), git(repo, "rev-parse", branch)) == (f"refs/heads/{branch}", tip)
    assert git(repo, "status", "--porcelain") == "M f"
    assert (repo / "f").read_text() == "the person's edit\n"
    assert git(repo, "stash", "list") == ""


@pytest.mark.parametrize("clashes", [False, True], ids=["clean", "clash-named-over-the-cancel"])
async def test_a_cancel_mid_rebase_in_the_checkout_waits_for_git(
    item_on, database, run_dirs, repo, clashes
):
    it, branch = await _behind_origin(
        item_on, database, run_dirs, repo, main_writes="f" if clashes else "main.txt"
    )
    git(repo, "checkout", "-q", branch)
    write(repo, "f", "the person's edit\n")
    write(repo, ".git/hooks/pre-rebase", "#!/bin/sh\nsleep 1\n").chmod(0o755)
    rebasing = asyncio.ensure_future(inplace.rebase(run_dirs, it.row(), base="main"))
    await asyncio.sleep(0.5)
    rebasing.cancel()

    with pytest.raises(inplace.AutostashClash if clashes else asyncio.CancelledError):
        await rebasing

    assert _contains_origin_main(repo, branch), "git was cut off, or left running"
    assert git(repo, "status", "--porcelain") == ("UU f" if clashes else "M f")
    assert not inplace._lock(repo).locked()


def _ignored_file_the_base_tracks(repo, branch) -> str:
    write(repo, ".git/info/exclude", "secret.env\n")
    write(repo, "secret.env", "MY SECRET\n")
    return "secret.env"


def _side_branch(repo, branch, name, body) -> None:
    """`side`, forked where `branch` did, writing `name`."""
    git(repo, "checkout", "-q", "-b", "side", f"{branch}~1")
    write(repo, name, body)
    commit_all(repo, "side")
    git(repo, "checkout", "-q", branch)


def _the_persons_merge_under_way(repo, branch) -> str:
    _side_branch(repo, branch, "side.txt", "side\n")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    return "operation in progress"


def _unmerged_after_a_stash_pop(repo, branch) -> str:
    write(repo, "f", "the person's edit\n")
    git(repo, "stash", "push", "-q")
    write(repo, "f", "committed meanwhile\n")
    commit_all(repo, "f again")
    subprocess.run(["git", "stash", "pop", "-q"], cwd=repo, capture_output=True)
    return "unmerged paths: f"


def _ignored_after_the_branch_stopped_tracking_it(repo, branch) -> str:
    """The base still tracks it: checking the base out writes it, before the
    branch's own commit deletes it again."""
    git(repo, "rm", "-q", "--cached", "secret.env")
    write(repo, ".gitignore", "secret.env\n")
    commit_all(repo, "stop tracking secret.env")
    write(repo, "secret.env", "MY SECRET\n")
    return "secret.env"


def _ignored_file_a_branch_commit_wrote(repo, branch) -> str:
    """Neither end tracks it, but replaying the commit that did writes it."""
    write(repo, "secret.env", "the branch's\n")
    commit_all(repo, "track secret.env")
    _ignored_after_the_branch_stopped_tracking_it(repo, branch)
    return "secret.env"


_NEW_TRACKED = {"main_writes": "secret.env"}


@pytest.mark.parametrize(
    ("arrange", "setup"),
    [
        (_ignored_file_the_base_tracks, _NEW_TRACKED),
        (_ignored_after_the_branch_stopped_tracking_it, {"base_tracks": "secret.env"}),
        (_ignored_file_a_branch_commit_wrote, {}),
        (_the_persons_merge_under_way, _NEW_TRACKED),
        (_unmerged_after_a_stash_pop, _NEW_TRACKED),
    ],
    ids=[
        "ignored-file-the-base-tracks",
        "ignored-file-the-branch-untracked",
        "ignored-file-a-branch-commit-wrote",
        "the-persons-merge",
        "unmerged-path",
    ],
)
async def test_a_rebase_in_the_checkout_refuses_what_it_would_force(
    item_on, database, run_dirs, repo, arrange, setup
):
    it, branch = await _behind_origin(item_on, database, run_dirs, repo, **setup)
    git(repo, "checkout", "-q", branch)
    named = arrange(repo, branch)
    before = _head(repo), git(repo, "rev-parse", branch), git(repo, "status", "--porcelain")
    files = {p: (repo / p).read_bytes() for p in ("f", "secret.env") if (repo / p).exists()}

    with pytest.raises(inplace.CheckoutRefused, match=named):
        await inplace.rebase(run_dirs, it.row(), base="main")

    assert (_head(repo), git(repo, "rev-parse", branch), git(repo, "status", "--porcelain")) == (
        before
    )
    assert {p: (repo / p).read_bytes() for p in files} == files


async def test_a_failed_rebase_never_aborts_the_persons_own(
    item_on, database, run_dirs, repo, monkeypatch
):
    """Theirs, started between Kraft's check and its rebase: left alone."""
    it, branch = await _behind_origin(item_on, database, run_dirs, repo)
    monkeypatch.setattr(inplace, "_on", lambda *_: True)  # theirs began after the look
    git(repo, "checkout", "-q", branch)
    _side_branch(repo, branch, "item.txt", "side's\n")
    subprocess.run(["git", "rebase", "side"], cwd=repo, capture_output=True)
    theirs = git(repo, "rev-parse", "side")
    head = git(repo, "rev-parse", "refs/remotes/origin/main")

    with pytest.raises(inplace.CheckoutRefused, match="did not start"):
        inplace._rebase_in_checkout(repo, branch, head, "main", None)

    gitdir = Path(git(repo, "rev-parse", "--absolute-git-dir"))
    assert (gitdir / "rebase-merge" / "onto").read_text().strip() == theirs


async def test_the_rebase_in_the_checkout_never_rebases_the_branch_it_moved_to(
    item_on, database, run_dirs, repo
):
    """Switched off the item's branch after Kraft chose to rebase in place."""
    it, branch = await _behind_origin(item_on, database, run_dirs, repo)
    head, tip = git(repo, "rev-parse", "refs/remotes/origin/main"), git(repo, "rev-parse", "main")

    with pytest.raises(inplace.CheckoutRefused, match=f"left {branch}"):
        inplace._rebase_in_checkout(repo, branch, head, "main", None)

    assert (_head(repo), git(repo, "rev-parse", "main")) == ("refs/heads/main", tip)


async def test_a_branch_deleted_at_the_throwaway_add_is_never_made_again(
    item_on, database, run_dirs, repo, monkeypatch
):
    it, branch = await _behind_origin(item_on, database, run_dirs, repo)
    real = builtins._run_git_group

    def deleted_first(args, **kw):
        if args[1:3] == ["worktree", "add"]:
            git(repo, "branch", "-q", "-D", branch)
        return real(args, **kw)

    monkeypatch.setattr(builtins, "_run_git_group", deleted_first)

    with pytest.raises(inplace.CheckoutRefused, match="is gone"):
        await inplace.rebase(run_dirs, it.row(), base="main")

    assert git(repo, "branch", "--list", branch) == ""
    assert list(run_dirs.scratch.iterdir()) == []


def _slow_take(*_):
    time.sleep(0.4)
    raise inplace.CheckoutRefused("refused")


def _slow_conflict(*_):
    time.sleep(0.4)  # an abort that failed, say: the unmerged path is still there
    raise builtins.RebaseConflict("conflict", ("f",))


@pytest.mark.parametrize("which", ["take", "rebase"])
async def test_a_cancel_reports_nothing_but_the_cancel(
    item_on, database, run_dirs, repo, monkeypatch, which
):
    """What the thread ended with is never logged as unhandled, nor read
    again as something else."""
    it, branch = await _prepared(item_on, database, run_dirs, repo)
    write(repo, "f", "base\n")
    commit_all(repo, "f")
    _unmerged_after_a_stash_pop(repo, branch)
    assert git(repo, "status", "--porcelain") == "UU f"
    unhandled = []
    asyncio.get_running_loop().set_exception_handler(lambda _, ctx: unhandled.append(ctx))
    if which == "take":
        monkeypatch.setattr(inplace, "_take", _slow_take)
        running = inplace.take(database, run_dirs, it.row(), attachments=[])
    else:
        monkeypatch.setattr(inplace, "_rebase_in_checkout", _slow_conflict)
        running = inplace.rebase_in_checkout(repo, branch, "HEAD", "main", None)
    task = asyncio.ensure_future(running)
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)

    assert unhandled == []


async def test_a_slow_throwaway_checkout_does_not_hold_up_the_server(
    item_on, database, run_dirs, repo
):
    it, branch = await _behind_origin(item_on, database, run_dirs, repo)
    write(repo, ".git/hooks/post-checkout", "#!/bin/sh\nsleep 1\n").chmod(0o755)
    rebasing = asyncio.ensure_future(inplace.rebase(run_dirs, it.row(), base="main"))
    longest, last = 0.0, time.monotonic()
    while not rebasing.done():
        await asyncio.sleep(0.05)
        longest, last = max(longest, time.monotonic() - last), time.monotonic()

    await rebasing
    assert longest < 0.8, "the loop stood still while git checked the branch out"


async def test_a_rebase_waits_for_the_checkouts_lock(item_on, database, run_dirs, repo):
    """A take holds it while it switches: which way to rebase is read after."""
    it, branch = await _behind_origin(item_on, database, run_dirs, repo)
    lock = inplace._lock(repo)
    lock.acquire()
    try:
        rebasing = asyncio.ensure_future(inplace.rebase(run_dirs, it.row(), base="main"))
        await asyncio.sleep(0.3)
        assert not rebasing.done()
        git(repo, "checkout", "-q", branch)  # what the take did meanwhile
    finally:
        lock.release()

    await rebasing

    assert _contains_origin_main(repo, branch)
    assert not run_dirs.scratch.exists(), "rebased in the checkout, as it now is"


async def test_rebase_of_an_item_not_yet_started_does_nothing(item_on, run_dirs, repo):
    it = await item_on(v1_named_chain(run_dirs.base / "t"), wid="w1", target=_IN_PLACE)

    assert await inplace.rebase(run_dirs, it.row(), base="main") is None
    assert git(repo, "branch", "--list", store.branch_for(it.row())) == ""


def _snapshot(repo) -> tuple:
    files = {p: (repo / p).read_bytes() for p in ("f", "calc.py", "mine.txt")}
    return _head(repo), git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"), files


@pytest.mark.parametrize(
    ("conflicts", "left_by_a_killed_server"),
    [(False, False), (True, False), (False, True)],
    ids=["rebased", "conflict", "after-a-killed-server"],
)
async def test_rebase_off_the_branch_never_moves_the_checkout(
    item_on, database, run_dirs, repo, conflicts, left_by_a_killed_server
):
    it, branch = await _behind_origin(
        item_on, database, run_dirs, repo, main_writes="item.txt" if conflicts else "main.txt"
    )
    if left_by_a_killed_server:  # it holds the branch: git checks it out once
        git(repo, "worktree", "add", "-q", str(run_dirs.scratch / "w1-0dead0ff"), branch)
    write(repo, "calc.py", "# the person's edit\n")
    write(repo, "mine.txt", "the person's own file\n")
    before, tip = _snapshot(repo), git(repo, "rev-parse", branch)

    if conflicts:
        with pytest.raises(builtins.RebaseConflict, match="item.txt"):
            await inplace.rebase(run_dirs, it.row(), base="main")
        assert git(repo, "rev-parse", branch) == tip
    else:
        await inplace.rebase(run_dirs, it.row(), base="main")
        assert _contains_origin_main(repo, branch)

    assert _snapshot(repo) == before
    assert list(run_dirs.scratch.iterdir()) == []
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def _stop_after_rebase_raises(client, repo, monkeypatch, verb, raised, paused_meanwhile=False):
    """The stop `verb` writes when the in-place rebase raises `raised`."""
    wid = _paused(client, repo, in_place=True)
    _force_node(wid, "implementation", "paused" if verb == "resume" else "needs_human")

    async def rebase(*_, **__):
        if paused_meanwhile:  # the stash is named all the same
            _set_status(wid, "paused")
        raise raised

    monkeypatch.setattr(inplace, "rebase", rebase)

    r = client.post(f"/api/work-items/{wid}/{verb}", json={})

    assert r.status_code == 200, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
    events = _poll_events(client, wid, "work_item_needs_human")
    return [e for e in events if e["type"] == "work_item_needs_human"][-1]["payload"]


@pytest.mark.parametrize(
    "paused_meanwhile", [False, True], ids=["autostash-clash", "clash-as-a-pause-lands"]
)
@pytest.mark.parametrize("verb", ["resume", "retry"])
def test_an_autostash_clash_stops_the_item_as_checkout(
    client, repo, monkeypatch, verb, paused_meanwhile
):
    raised = inplace.AutostashClash("your edits to f clash", ["f"], "5ca1ab1e")

    stop = _stop_after_rebase_raises(client, repo, monkeypatch, verb, raised, paused_meanwhile)

    assert (stop["kind"], stop["reason"]) == ("checkout", str(raised))
    assert stop["facts"] == {"paths": ["f"], "stash": "5ca1ab1e"}


@pytest.mark.parametrize("verb", ["resume", "retry"])
def test_a_refused_in_place_rebase_stops_the_item_as_checkout(client, repo, monkeypatch, verb):
    raised = inplace.CheckoutRefused("the branch is gone", ["g"])

    stop = _stop_after_rebase_raises(client, repo, monkeypatch, verb, raised)

    assert (stop["kind"], stop["reason"]) == ("checkout", str(raised))
