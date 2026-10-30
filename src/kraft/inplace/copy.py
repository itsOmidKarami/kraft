"""A throwaway copy of an in-place item's branch, for an agent that only reads.

The checks-first gate reviewer and the review reply agent work in one instead
of the person's checkout, so nothing they do there reaches the person's files.
It is a shared clone (`git clone --shared --no-checkout`), not a linked
worktree: a linked worktree shares the person's refs, stash and config, so a
`git stash` or `git branch -f` in it would change theirs. A shared clone reads
their objects through `alternates` and writes only into its own `.git`; its
`origin` is removed, so a plain `git push` has nowhere to go.

It lives in the session's own folder (`copy_path`), never under
`run_dirs.scratch`, whose startup sweep runs before reattach and would take a
re-adopted session's copy from under it.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from kraft import item_paths, store
from kraft.adapters import subprocess as _subprocess
from kraft.vocab import LIVE
from kraft.worker import sandbox as _sandbox

logger = logging.getLogger(__name__)


class CopyFailed(Exception):
    """The copy could not be made. Never falls back to the person's checkout."""

    def __init__(self, branch: str, why: str) -> None:
        super().__init__(f"Kraft could not make a copy of {branch} to work in: {why}")


def copy_path(run_dirs, wid: str, sid: str) -> Path:
    """Where session `sid` of item `wid` keeps its copy."""
    return item_paths.session_files(run_dirs, wid, sid).folder / "copy"


def remove_copy(path: Path) -> None:
    # A global `core.fsmonitor` may have started a daemon in it: best effort.
    shutil.rmtree(path, ignore_errors=True)


def watch_copy(db, row, launch, copy: Path, folder: Path) -> Callable[[], list[str]] | None:
    """Snapshot `copy` whole, and the item's branch tip in the person's
    `folder`, which a commit by any route moves (a push from the copy, or the
    person). Returns what lists the files changed in either since, or None
    when either cannot be read."""
    from kraft.executor import read_only  # it imports this package

    was = (
        read_only.snapshot(db, row, launch, copy, full=True),
        read_only.snapshot(db, row, launch, folder),
    )
    if None in was:
        return None
    return lambda: (
        read_only.changed(db, row, launch, copy, was[0], full=True)
        + read_only.changed(db, row, launch, folder, was[1])
    )


def _empty(dest: Path) -> None:
    remove_copy(dest)
    dest.mkdir(parents=True, exist_ok=True)


async def make_copy(row, dest: Path, *, timeout_s: float | None) -> None:
    """Fill the empty folder `dest` with `row`'s branch tip, detached. Raises
    `CopyFailed` (with `dest` empty again) when the branch is gone, git fails,
    `timeout_s` runs out (None means no limit of its own) or anything else
    goes wrong. A cancel stops git at once, empties `dest` and is re-raised.
    A `dest` that is not empty is refused untouched: the failure path empties
    it."""
    from kraft import builtins  # builtins imports this package

    branch = store.branch_for(row)
    if dest.exists() and any(dest.iterdir()):
        raise CopyFailed(branch, f"{dest} is not empty")
    repo = str(row["repo"])
    # Hooks off, and LFS pointers left as they are: nothing is fetched into the
    # person's object store, and no hook of theirs runs.
    env = _sandbox.hardened_git_env() | {"GIT_LFS_SKIP_SMUDGE": "1"}
    deadline = None if timeout_s is None else time.monotonic() + timeout_s

    async def git(*args: str, silent: str = "git failed") -> str:
        left = None if deadline is None else max(deadline - time.monotonic(), 0.0)
        done = await builtins._git_group(["git", *args], timeout=left, env=env)
        if done.returncode != 0:
            lines = (done.stderr or done.stdout).strip().splitlines()
            raise CopyFailed(branch, lines[-1] if lines else silent)
        return done.stdout.strip()

    try:
        # Read once, in the person's repository; nothing ever creates it.
        ref = f"refs/heads/{branch}"
        sha = await git(
            "-C", repo, "rev-parse", "-q", "--verify", ref, silent=f"{repo} has no {ref}"
        )
        await git("clone", "-q", "--shared", "--no-checkout", repo, str(dest))
        await git("-C", str(dest), "remote", "remove", "origin")
        # The checkout's branches and origin/* refs, for `git diff main...HEAD`:
        # read from the person's repository, objects through alternates, no
        # remote left behind.
        refs = [f"+refs/{kind}/*:refs/{kind}/*" for kind in ("heads", "remotes", "tags")]
        await git("-C", str(dest), "fetch", "-q", "--no-tags", "--update-head-ok", repo, *refs)
        await git("-C", str(dest), "checkout", "-q", "--detach", sha)
    except asyncio.CancelledError:
        await _subprocess.uncancellable(asyncio.to_thread(_empty, dest))
        raise
    except Exception as exc:  # anything at all: a failed copy never stops the item
        await asyncio.to_thread(_empty, dest)
        if isinstance(exc, CopyFailed):
            raise
        why = "git took too long" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        raise CopyFailed(branch, why) from exc


async def drop_dead_copies(db, run_dirs, *, before: float) -> list[Path]:
    """Remove every copy whose session is gone or no longer live, made before
    `before` (the boot time: a session started since has its folder before its
    row). What it removed."""
    dead = []
    for copy in sorted((run_dirs.base / item_paths.ITEMS).glob("*/sessions/*/copy")):
        sid, wid = copy.parent.name, copy.parent.parent.parent.name
        status = db.read(lambda c, sid=sid, wid=wid: store.session_status(c, sid, work_item_id=wid))
        if status not in LIVE and copy.lstat().st_mtime < before:
            dead.append(copy)
    for copy in dead:
        await asyncio.to_thread(remove_copy, copy)
        logger.info("removed %s: its session is no longer running", copy)
    return dead
