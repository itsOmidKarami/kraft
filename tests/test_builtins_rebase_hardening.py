"""`refresh_worktree_base` where its worktree turns hostile or its git hangs:
a link a worker planted is never followed out of the worktree, and a rebase
cut short (its time cap, a pause, cancel or shutdown) is stopped and aborted
with everything it started, or said to be left mid-rebase. The ordinary
rebase cases: test_builtins_rebase.py."""

import asyncio
import contextlib
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from support import worktree as wtree
from support.harness import git

from kraft import builtins as kraft_builtins
from kraft.config import git_read

hung = wtree.hung


def _link_the_parent_to(victim):
    """A `pre-rebase` hook, which runs after the set-aside and before the
    rebase, as a worker writing its worktree mid-rebase would: it swaps the
    lockfile's emptied parent directory for a link to `victim`."""
    return f'rm -rf sub && ln -s "{victim}" sub\n'


@pytest.mark.parametrize("base_commits_one", [False, True], ids=["put-back", "base-wins"])
@pytest.mark.parametrize("plant", ["set-aside-in-git-dir", "parent-in-worktree"], ids=lambda p: p)
async def test_a_planted_link_never_moves_a_lockfile_outside_the_worktree(
    tmp_path, database, run_dirs, repo, plant, base_commits_one
):
    """R11E-01: the set-aside lived in the worktree's git dir, which a
    sandboxed worker can write, and the host-side move followed a link
    planted there (or in place of a lockfile's parent directory), deleting
    or overwriting a lockfile anywhere the server user can write. Nothing
    outside the worktree is touched, whether the rebase then runs or git
    refuses to check the base's copy out over the link."""
    from kraft.adapters import forge

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    victim = tmp_path / "victim"
    victim.mkdir()
    rel = "uv.lock" if plant == "set-aside-in-git-dir" else "sub/uv.lock"
    if plant == "set-aside-in-git-dir":
        (victim / "uv.lock").write_text("the host's own\n")
        gitdir = Path(git_read(worktree, "rev-parse", "--absolute-git-dir"))
        (gitdir / "kraft-set-aside").symlink_to(victim)
    else:
        hook = repo / ".git" / "hooks" / "pre-rebase"
        hook.write_text(f'#!/bin/sh\ncd "{worktree}" && ' + _link_the_parent_to(victim))
        hook.chmod(0o755)
        (worktree / "sub").mkdir()
    before = await forge.lockfile_digests(worktree)
    (worktree / rel).write_text("the worker's choice\n")
    await forge.record_setup_writes(worktree, before)
    if base_commits_one:
        wtree.commit(repo, rel, "committed on the base\n", "commit a lockfile")
    else:
        wtree.commit(repo, "upstream.txt", "landed meanwhile\n", "upstream change")

    with contextlib.suppress(kraft_builtins.RebaseBlocked):
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")

    expected = ["uv.lock"] if plant == "set-aside-in-git-dir" else []
    assert sorted(p.name for p in victim.iterdir()) == expected
    if expected:
        assert (victim / "uv.lock").read_text() == "the host's own\n"


def _hanging_smudge_filter(repo, pids, seconds: float, *, deaf: bool = False) -> None:
    """The base adds `slow.txt` under a smudge filter that hangs, as an LFS
    filter can: the rebase's checkout of the base hangs in it, holding the
    worktree's `index.lock`. `deaf`: it ignores SIGTERM, so git dies of one
    with the rebase started and the filter needs a SIGKILL."""
    trap = 'trap "" TERM\n' if deaf else ""
    git(
        repo, "config", "filter.slow.smudge", f"sh -c '{trap}{wtree.sleep_recorded(pids, seconds)}'"
    )
    (repo / ".gitattributes").write_text("slow.txt filter=slow\n")
    wtree.commit(repo, "slow.txt", "slow\n", "a filtered file")


@pytest.mark.parametrize("lock", ["stale", "retaken", "never-started"])
async def test_a_rebase_killed_at_its_time_cap_is_aborted_with_everything_it_started(
    database, run_dirs, repo, hung, monkeypatch, lock
):
    """R11E-02: a smudge filter that hangs mid-rebase was left running past
    the time cap, and the abort failed silently on the `index.lock` the killed
    git left, so the item went on mid-rebase without its commits. The whole
    process group is killed, the lock it left is cleared, and the abort runs.
    A lock that is not the one read right after the kill is another git's,
    kept: the abort cannot run, and says so. A `pre-rebase` hook killed at
    the cap started no rebase, so there is nothing to abort or to report."""
    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    work = wtree.commit(worktree, "work.txt", "work\n", "worktree work")
    if lock == "never-started":
        hook = repo / ".git" / "hooks" / "pre-rebase"
        hook.write_text("#!/bin/sh\n" + wtree.sleep_recorded(hung, 60))
        hook.chmod(0o755)
        wtree.commit(repo, "moved.txt", "moved on\n", "moved on")
    else:
        _hanging_smudge_filter(repo, hung, seconds=60)
    if lock == "retaken":
        # As if a person's git took the lock again between the kill and the abort.
        read = iter([(1, 1)])
        real = kraft_builtins._lock_identity
        monkeypatch.setattr(
            kraft_builtins, "_lock_identity", lambda wt: next(read, None) or real(wt)
        )

    with pytest.raises(kraft_builtins.RebaseTimedOut) as raised:
        await kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main", timeout=2.0)

    [pid] = hung.read_text().split()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid), 0)
    if lock == "retaken":
        assert "`git rebase --abort` failed" in str(raised.value)
        assert str(raised.value).endswith("was left mid-rebase for a human")
        return
    if lock == "never-started":
        assert str(raised.value) == f"git rebase timed out after 2s for {worktree}"
    else:
        assert "--abort" not in str(raised.value)
    assert git_read(worktree, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
    assert git_read(worktree, "rev-parse", "HEAD") == work


#: What a rebase cut short leaves in the git dir.
LEFT_MID_REBASE = {"rebase-merge", "rebase-apply", "index.lock"}


async def _hung_pid(pids) -> int:
    """The pid a hanging hook or filter wrote to `pids` (`sleep_recorded`),
    once it has."""
    for _ in range(200):
        if pids.exists() and pids.read_text().strip():
            return int(pids.read_text().split()[0])
        await asyncio.sleep(0.05)
    raise AssertionError("the hanging hook never started")


async def _rebase_hung_mid_way(database, run_dirs, repo, hung, tmp_path, hang):
    """Item w1's worktree, with an agent commit and a setup `uv.lock`, in a
    `refresh_worktree_base` that hangs where `hang` says. Returns the
    worktree, its branch, the agent commit, the running rebase and the pid
    of what hangs, once it does."""
    from kraft.adapters import forge

    await wtree.make_item(database, repo)
    worktree = await wtree.ensure(database, run_dirs, repo)
    branch = wtree.branch(database)
    work = wtree.commit(worktree, "work.txt", "work\n", "worktree work")
    before = await forge.lockfile_digests(worktree)
    (worktree / "uv.lock").write_text("by the setup\n")
    await forge.record_setup_writes(worktree, before)
    if hang == "pre-rebase-hook":
        hook = repo / ".git" / "hooks" / "pre-rebase"
        hook.write_text("#!/bin/sh\n" + wtree.sleep_recorded(hung, 60))
        hook.chmod(0o755)
        wtree.commit(repo, "moved.txt", "moved on\n", "moved on")
    elif hang == "conflict-abort":
        wtree.commit(repo, "work.txt", "the base's\n", "conflicting work")
        wtree.hanging_rebase_hook(repo, tmp_path, hung, "abort", seconds=3)
    else:
        _hanging_smudge_filter(repo, hung, seconds=60, deaf=hang.startswith("smudge-deaf"))
    rebase = asyncio.create_task(
        kraft_builtins.refresh_worktree_base(worktree, repo, branch, base="main")
    )
    pid = await _hung_pid(hung)
    assert not (worktree / "uv.lock").exists()  # set aside while git runs
    return worktree, branch, work, rebase, pid


def _left_mid_rebase(worktree) -> list[str]:
    gitdir = Path(git_read(worktree, "rev-parse", "--absolute-git-dir"))
    return sorted(p.name for p in gitdir.glob("*") if p.name in LEFT_MID_REBASE)


@pytest.mark.parametrize(
    ("hang", "cancel_again_after", "workers"),
    [
        ("smudge", None, None),
        ("smudge-deaf-to-sigterm", None, None),
        ("smudge-deaf-to-sigterm", 0.3, None),
        ("smudge-deaf-to-sigterm", 2.5, None),
        ("pre-rebase-hook", None, None),
        ("conflict-abort", None, None),
        ("smudge", None, 1),
    ],
    ids=[
        "smudge",
        "smudge-deaf-to-sigterm",
        "cancelled-again-in-the-sigterm-grace",
        "cancelled-again-in-the-sigkill-wait",
        "pre-rebase-hook",
        "cancelled-in-a-conflicts-abort",
        "the-executors-only-thread-waits-on-the-git",
    ],
)
async def test_a_cancelled_rebase_stops_its_git_before_it_cleans_up(
    tmp_path,
    database,
    run_dirs,
    repo,
    hung,
    hang,
    cancel_again_after,
    workers,
    request,
    monkeypatch,
):
    """R12E-04: a pause, cancel or shutdown cancelled only the await, so the
    git in its thread rebased the branch after the stop, and the setup's
    lockfile was put back while that git was still checking the base out.
    The whole group is stopped first; then the rebase it started is aborted
    and the lockfile goes back, so the branch is as it was before the stop.
    A second cancel (a pause, then a cancel) changes none of it, and an abort
    already running when the cancel comes is waited out, not raced. The stop
    needs no free thread: every one can be waiting on a git."""
    if workers is not None:
        # Put back at teardown: anyio may run the next test on this same loop.
        pool = ThreadPoolExecutor(workers)
        request.addfinalizer(lambda: pool.shutdown(wait=False))
        monkeypatch.setattr(asyncio.get_running_loop(), "_default_executor", pool)
    worktree, branch, work, rebase, pid = await _rebase_hung_mid_way(
        database, run_dirs, repo, hung, tmp_path, hang
    )

    rebase.cancel()
    if cancel_again_after is not None:
        await asyncio.sleep(cancel_again_after)
        rebase.cancel()
    await asyncio.wait({rebase}, timeout=15)
    assert rebase.done()
    with pytest.raises(asyncio.CancelledError):
        rebase.result()

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not kraft_builtins._RUNNING_GIT_GROUPS
    assert _left_mid_rebase(worktree) == []
    assert git_read(worktree, "symbolic-ref", "HEAD") == f"refs/heads/{branch}"
    assert git_read(worktree, "rev-parse", "HEAD") == work
    assert (worktree / "uv.lock").read_text() == "by the setup\n"
    assert not kraft_builtins.set_aside_dir(worktree).exists()


@pytest.mark.parametrize("why", ["still-there-after-sigkill", "the-stop-itself-failed"])
async def test_a_stopped_git_that_will_not_die_is_left_alone(
    tmp_path, database, run_dirs, repo, hung, monkeypatch, why
):
    """A git group still there after its SIGKILL, or one whose stop failed,
    may still be writing the worktree: no abort runs beside it and the
    setup's lockfile stays set aside, for the next walk to stop on its
    `rebase-merge` for a person. The cancel stays a cancel."""
    if why == "still-there-after-sigkill":

        async def never_gone(*_args):
            return False

        monkeypatch.setattr(kraft_builtins, "_groups_gone", never_gone)
    else:
        real_stop = kraft_builtins._stop_git_group

        async def failing_stop(*args):
            await real_stop(*args)
            raise OSError("the stop failed")

        monkeypatch.setattr(kraft_builtins, "_stop_git_group", failing_stop)
    worktree, _, _, rebase, _ = await _rebase_hung_mid_way(
        database, run_dirs, repo, hung, tmp_path, "smudge"
    )

    rebase.cancel()
    with pytest.raises(asyncio.CancelledError):
        await rebase

    assert "rebase-merge" in _left_mid_rebase(worktree)
    assert not (worktree / "uv.lock").exists()
    assert (kraft_builtins.set_aside_dir(worktree) / "uv.lock").read_text() == "by the setup\n"


async def test_a_shutdown_ends_a_running_git_group_and_everything_it_started(hung):
    """A git a request (`/retry`'s rebase) still runs at shutdown is its own
    session, so no signal to the server's group reaches it: it ran on after
    `kraft admin stop`, holding the worktree's `index.lock` against the next
    start. Shutdown ends it."""
    running = asyncio.create_task(
        asyncio.to_thread(
            kraft_builtins._run_git_group,
            ["sh", "-c", wtree.sleep_recorded(hung, 60)],
            timeout=None,
        )
    )
    pid = await _hung_pid(hung)

    kraft_builtins.end_running_git_groups()

    done = await asyncio.wait_for(running, 10)
    assert done.returncode == -signal.SIGTERM
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
