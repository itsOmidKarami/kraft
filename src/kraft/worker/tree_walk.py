"""Walking a folder a worker can write, by descriptors, never by path.

A worker owns its worktree, its sandbox home, its item's `docs/` and its
session's `out/`, and races the daemon in a tight loop: it can swap any
folder in them for a link (to `/`, to the daemon's home) between two calls.
A walk that ever joins names into a path string and hands it to the kernel
resolves every component of it afresh, and follows whatever link stands
there at that instant. So `walk` resolves nothing but single names, each
relative to the descriptor of the folder it was listed from, with
`O_NOFOLLOW`: whatever a worker swaps in, the walk reaches only folders it
reached from the top through real folders.

It is also bounded. A worker can nest folders tens of thousands deep at
almost no cost, so one descriptor per level (`os.fwalk`, `shutil.rmtree`)
would take the daemon to its open-file limit (256 under launchd), and
reopening a deep folder from the top every time is quadratic. `walk` keeps
descriptors for the deepest `held` folders of the path it is on, as GNU
fts does: going back up past them, it opens `..` from the folder it is
leaving and checks that the folder it gets is the one it came down through,
by device and inode. One that is not (the worker moved a folder while the
walk was below it) is not continued, and is logged.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: How a folder is opened: never through a link, never inherited by a child.
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
#: The most folder descriptors a walk holds open at once, besides the top's
#: and one it is opening.
HELD = 16


@dataclass
class _Frame:
    """A folder on the walk's current path."""

    name: str
    fd: int | None
    key: tuple[int, int]
    names: list[str]
    at: int = 0


def _key(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _list(fd: int) -> list[str]:
    """The names in the folder `fd`, or none when it cannot be listed."""
    try:
        with os.scandir(fd) as entries:
            return [entry.name for entry in entries]
    except OSError:
        return []


def open_child(dir_fd: int, name: str) -> int | None:
    """The folder `name` in `dir_fd`, opened with no link followed; None when
    it is gone, not a folder, a link, or cannot be opened."""
    try:
        return os.open(name, DIR_FLAGS, dir_fd=dir_fd)
    except OSError:
        return None


def walk(
    top: int,
    enter: Callable[[int, str, os.stat_result], bool],
    *,
    open_dir: Callable[[int, str], int | None] = open_child,
    leave: Callable[[int, str], None] | None = None,
    names: list[str] | None = None,
    stop: threading.Event | None = None,
    held: int = HELD,
) -> None:
    """Walk everything below the folder `top` (a descriptor the caller owns
    and closes), depth first.

    For each entry, `enter(dir_fd, name, st)` is called with the folder it is
    in and its `lstat`; when it answers True, the entry is opened with
    `open_dir(dir_fd, name)` (None skips it) and walked, and once all of it
    is walked `leave(dir_fd, name)` is called from the folder it is in.
    `names` replaces `top`'s listing, to walk only those of its entries.

    `dir_fd` is valid only during the call. An entry that cannot be
    `lstat`ed is skipped, as is one that `open_dir` opens to anything but a
    folder; a folder that cannot be listed is walked as empty. The
    callbacks must not raise."""
    root = _Frame("", top, _key(os.fstat(top)), _list(top) if names is None else list(names))
    stack = [root]
    # The deepest `open_count` frames hold a descriptor; those between them
    # and the top, none.
    open_count = 0
    try:
        while stack and not (stop is not None and stop.is_set()):
            frame = stack[-1]
            if frame.at < len(frame.names):
                name = frame.names[frame.at]
                frame.at += 1
                try:
                    st = os.stat(name, dir_fd=frame.fd, follow_symlinks=False)
                except OSError:
                    continue
                if not enter(frame.fd, name, st):
                    continue
                fd = open_dir(frame.fd, name)
                if fd is None:
                    continue
                try:
                    info = os.fstat(fd)
                except OSError:
                    info = None
                if info is None or not stat.S_ISDIR(info.st_mode):
                    os.close(fd)
                    continue
                stack.append(_Frame(name, fd, _key(info), _list(fd)))
                open_count += 1
                if open_count > held:
                    evicted = stack[len(stack) - open_count]
                    os.close(evicted.fd)
                    evicted.fd = None
                    open_count -= 1
                continue
            if frame is root:
                return
            # Done with `frame`: back to its parent, reopened by `..` when its
            # descriptor was let go on the way down.
            stack.pop()
            parent = stack[-1]
            reopened = parent.fd is None and _reopen(frame, parent)
            os.close(frame.fd)
            frame.fd = None
            open_count -= 1
            if parent.fd is None:
                logger.warning(
                    "%s moved while it was walked; not walking the rest of it",
                    "/".join(f.name for f in stack[1:]) or "a folder",
                )
                while stack[-1].fd is None:
                    stack.pop()
                continue
            open_count += reopened
            if leave is not None:
                leave(parent.fd, frame.name)
    finally:
        for frame in stack[1:]:
            if frame.fd is not None:
                os.close(frame.fd)


def _reopen(child: _Frame, parent: _Frame) -> bool:
    """Give `parent` a descriptor again, by opening `..` from `child`: true
    when the folder there is the one the walk came down through, false when
    a worker moved `child` (or a folder between them) somewhere else."""
    try:
        fd = os.open("..", DIR_FLAGS, dir_fd=child.fd)
    except OSError:
        return False
    try:
        same = _key(os.fstat(fd)) == parent.key
    except OSError:
        same = False
    if not same:
        os.close(fd)
        return False
    parent.fd = fd
    return True
