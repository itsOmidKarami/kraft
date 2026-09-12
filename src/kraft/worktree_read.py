"""Reading a file an agent wrote inside its own worktree, for a caller that is
about to do something public with the bytes (publish them, hand them to a
reviewer with no filesystem access). The agent owns the worktree and can put
anything at the path it hands back -- an absolute path, a `..` escape, a
symlink at any component -- so every read through here treats that path as
untrusted, resolves it, and refuses to follow a link.

Extracted from `api.routes.artifacts` (the gate-artifact reader), which
solved this once already; `mr_body`'s session-summary reader is the second
caller, and both trust the exact same containment walk rather than each
carrying its own, slightly different, version of it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeRead:
    text: str
    truncated: bool


def open_no_symlinks(root: Path, rel: str) -> int:
    """Open `rel` beneath `root`, refusing a symlink at every component.

    A resolved path is a fact about the filesystem at the instant it was
    resolved. The agent owns this worktree and can swap any component --
    including a parent directory -- between the resolve and the open, so
    containment has to be enforced by the open itself: walk down from the
    root, one `openat` per component, never following a link.
    """
    parts = Path(rel).parts
    # The root's own ancestors are server-owned, so following links above the
    # worktree is fine (and necessary on macOS, where /tmp is a symlink).
    dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def read_worktree_file(
    root: Path, rel: str, max_bytes: int
) -> tuple[WorktreeRead | None, str | None]:
    """Resolve `rel` under `root` and read it, or say why not.

    Returns `(result, refusal_reason)` -- exactly one of the pair is None.
    A refusal reason is one of `"absent"`, `"escaped_containment"`, or
    `"unreadable"`; callers that want to record why (an event, a log line)
    can, but the read itself never raises, so a missing or hostile path is
    always safe to just fall back past.
    """
    try:
        resolved_root = root.resolve(strict=True)
        target = (root / rel).resolve(strict=True)
    except OSError:
        return None, "absent"
    except ValueError:
        # A NUL byte in `rel` makes resolve() raise ValueError instead of
        # OSError -- still just an agent-supplied path that doesn't exist.
        return None, "absent"
    if not target.is_relative_to(resolved_root):
        # A symlink out of the worktree is the one way a derived, unstored path
        # can still point somewhere it should not. Same answer as a missing
        # file: the caller learns nothing about the server's disk.
        return None, "escaped_containment"
    try:
        # The resolve+is_relative_to check above is a fact about the instant it
        # ran, not a guarantee. This walk is the actual containment: the agent
        # that owns this worktree can swap any component -- not just the leaf
        # -- for a symlink between that check and this open.
        fd = open_no_symlinks(resolved_root, rel)
    except OSError:
        return None, "unreadable"
    except IndexError:
        # `rel` of "." or "./" makes Path(rel).parts empty, so parts[-1] raises.
        return None, "unreadable"
    except ValueError:
        # A NUL byte in `rel` also reaches here via the open(2) call.
        return None, "unreadable"
    try:
        fh = os.fdopen(fd, "rb")
    except OSError:
        # fdopen failed before taking ownership of fd (e.g. the walk landed on
        # a directory) -- close it ourselves, or it leaks.
        os.close(fd)
        return None, "unreadable"
    try:
        with fh:
            # Capped at the read, not just at the return: a file that is a
            # mistake of scale must not be readable into memory just to learn
            # it is too big. One byte over the cap is how `truncated` is known
            # without a stat race.
            data = fh.read(max_bytes + 1)
    except OSError:
        return None, "unreadable"
    truncated = len(data) > max_bytes
    # Slicing bytes can land mid-codepoint; `errors="replace"` is what makes
    # that a single replacement character instead of a crash.
    return WorktreeRead(data[:max_bytes].decode(errors="replace"), truncated), None
