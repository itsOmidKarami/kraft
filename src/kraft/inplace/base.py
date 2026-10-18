"""What every part of an in-place item shares: where its work happens,
how a checkout is refused, and the checkout's lock and git reads."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import threading
from pathlib import Path

from kraft import store
from kraft.adapters.forge import git
from kraft.config import git_read

#: The package's own name, as before it was split: what its log lines say.
logger = logging.getLogger("kraft.inplace")


is_in_place = store.is_in_place


def item_folder(run_dirs, row) -> Path:
    """The folder the item's work happens in: its repository's checkout when
    it runs in place, else its worktree."""
    return folder_for(run_dirs, row, store.materialized_chain_of(row))


def folder_for(run_dirs, row, chain) -> Path:
    """`item_folder` for a caller that already parsed the row's snapshot."""
    in_place = chain is not None and chain.target.in_place
    return Path(row["repo"]) if in_place else run_dirs.worktrees / row["id"]


def head_ref(row) -> str:
    """What a reader that shows the item takes as its tip: `HEAD` in its
    worktree, or in place its branch, since the checkout's HEAD and working
    tree may be the person's."""
    return f"refs/heads/{store.branch_for(row)}" if is_in_place(row) else "HEAD"


class CheckoutRefused(RuntimeError):
    """The checkout cannot be taken without forcing it: a `checkout` stop
    (`StopKind.CHECKOUT`), never a failure and never auto-escalated. The
    message names the files or branch and what the person does next; `paths`
    are the files. `moved_from` is set when the checkout was switched before
    the refusal, so the switch is still recorded."""

    def __init__(self, message: str, paths: list[str] | None = None):
        super().__init__(message)
        self.paths = paths or []
        self.moved_from: dict | None = None


class NotRunning(CheckoutRefused):
    """The walk's take for an item no longer `active`: a pause (a broken
    base's soft one) landed between its steps. Nothing switched; the step
    ends paused, never as a stop."""


class AutostashClash(RuntimeError):
    """An in-place rebase that git reported done, but whose autostash, put
    back, clashed with the newer base: `paths` are left unmerged in the
    checkout, and the person's original edits are the stash commit `stash`,
    which Kraft never pops or drops. A `checkout` stop, never a conflict."""

    def __init__(self, message: str, paths: list[str], stash: str | None):
        super().__init__(message)
        self.paths = paths
        self.stash = stash


#: One lock per checkout: co-tasks of one step take it at once, and git's
#: index lock would refuse the second.
_LOCKS: dict[str, threading.Lock] = {}


_LOCKS_GUARD = threading.Lock()


def _lock(folder: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(folder.resolve()), threading.Lock())


@contextlib.asynccontextmanager
async def _held(folder: Path):
    """`_lock(folder)` from async code. A cancel while waiting for it still
    releases it once taken, so no cancel can leave it held."""
    lock = _lock(folder)
    got = asyncio.ensure_future(asyncio.to_thread(lock.acquire))
    try:
        await asyncio.shield(got)
    except asyncio.CancelledError:
        got.add_done_callback(lambda f: f.cancelled() or lock.release())
        raise
    try:
        yield
    finally:
        lock.release()


async def _wait_out(running: asyncio.Future) -> bool:
    """Wait for `running` (a thread, which a cancel cannot stop) to its end,
    however many cancels arrive; whether one did."""
    cancelled = False
    while not running.done():
        try:
            # Not `shield`: a shielded future that fails after a cancel is
            # reported to the loop as unhandled.
            await asyncio.wait({running})
        except asyncio.CancelledError:
            cancelled = True
    return cancelled


async def _finish(fn, *args, over_cancel: tuple[type[BaseException], ...] = ()):
    """`fn(*args)` in a thread, waited out to its end (what it leaves must be
    known); then a cancel is honoured, unless `fn` raised one of
    `over_cancel`, which is raised instead."""
    running = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    if await _wait_out(running):
        failed = running.exception()
        if isinstance(failed, over_cancel):
            raise failed
        raise asyncio.CancelledError
    return running.result()


def _rebase_file(checkout: Path, name: str) -> str | None:
    """A rebase under way in `checkout`'s record `name` (`onto`, the commit
    it is onto; `head-name`, the branch it rebases), or None."""
    gitdir = git_read(checkout, "rev-parse", "--absolute-git-dir")
    for state in ("rebase-merge", "rebase-apply") if gitdir else ():
        with contextlib.suppress(OSError):
            return Path(gitdir, state, name).read_text().strip()
    return None


def _left(checkout: Path, branch: str) -> CheckoutRefused:
    return CheckoutRefused(
        f"{checkout} left {branch} before Kraft could rebase it, and Kraft does not "
        "rebase another branch: retry the item"
    )


def _ref(folder: Path, branch: str) -> str | None:
    return git_read(
        folder, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", expected_failure=True
    )


def _git_lines(folder: Path, command: str, *args: str) -> list[str]:
    """`git <command> -z *args`, one path a line, unquoted. Fails closed: a
    read git refuses is a `CheckoutRefused`, never an empty answer."""
    done = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "--literal-pathspecs"]
        + [command, "-z", *args],
        cwd=folder,
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )
    if done.returncode != 0:
        raise CheckoutRefused(
            f"Kraft could not read {folder} (`git {command}` said: {done.stderr.strip()}), "
            "and does not go on without it: fix that, then retry the item"
        )
    return [p for p in done.stdout.split("\0") if p]


def _gone(folder: Path, branch: str) -> CheckoutRefused:
    return CheckoutRefused(
        f"the branch {branch} is gone from {folder}, and Kraft does not make it again: "
        "restore it (`git reflog` may still have its commits) and retry, or abandon the item"
    )


def _settled(folder: Path) -> None:
    """Refuse a checkout with the person's own git operation under way, or
    paths they have left unmerged: Kraft never finishes, aborts or works
    over either."""
    if _operation(folder):
        raise _under_way(folder)
    unmerged = _git_lines(folder, "diff", "--name-only", "--diff-filter=U")
    if unmerged:
        raise CheckoutRefused(
            f"{folder} has unmerged paths: {', '.join(unmerged)}; resolve them yourself, "
            "then retry the item",
            unmerged,
        )


def _operation(folder: Path) -> bool:
    try:
        git.assert_no_operation(folder)
    except git.UnsafeWorktree:
        return True
    return False


def _under_way(folder: Path, when: str = "") -> CheckoutRefused:
    held = (
        "; your uncommitted edits are held by its autostash and come back when it "
        "finishes or is aborted"
        if _rebase_file(folder, "autostash") is not None
        else ""
    )
    return CheckoutRefused(
        f"{folder} has a git operation in progress (a rebase, merge, cherry-pick, "
        f"revert or bisect){when}: finish or abort it yourself, then retry the item{held}"
    )


def _off(folder: Path, branch: str) -> CheckoutRefused:
    """Why git would not act on `branch` in `folder`: a rebase of the
    person's detaches HEAD, and is theirs to finish, not a switch away."""
    gone = not _on(folder, branch) and not _operation(folder)
    return _left(folder, branch) if gone else _under_way(folder)


def _on(folder: Path, branch: str) -> bool:
    head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    return head == f"refs/heads/{branch}"


def _ignored_in_the_way(folder: Path, changed: list[str]) -> list[str]:
    """The ignored files on disk a switch writing `changed` would replace: a
    changed path itself, or a file where it needs a directory."""
    if not changed:
        return []
    ignored = [
        e.rstrip("/")
        for e in _git_lines(
            folder, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory"
        )
    ]
    # On a case-insensitive filesystem `Gen.txt` on the branch is `gen.txt` here.
    insensitive = (
        git_read(folder, "config", "--bool", "core.ignorecase", expected_failure=True) == "true"
    )
    fold = str.casefold if insensitive else str
    hits = []
    for c in changed:
        blocker = _first_on_disk(folder, c)
        for e in ignored if blocker else []:
            if fold(blocker) == fold(e):
                hits.append(e)  # the person's own spelling
            elif fold(blocker).startswith(fold(e) + "/"):
                hits.append(blocker)
    return sorted(set(hits))


def _first_on_disk(folder: Path, path: str) -> str | None:
    """`path` when it is on disk, or the file (not directory) standing where
    one of its parents should be; None when nothing is in the way."""
    parts = path.split("/")
    for i in range(1, len(parts) + 1):
        p = "/".join(parts[:i])
        full = folder / p
        if not os.path.lexists(full):
            return None
        if i == len(parts) or full.is_symlink() or not full.is_dir():
            return p
    return None
