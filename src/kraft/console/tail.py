"""Following `server.log` on this machine (spec §4.2 route 3, §7.3): its last
lines, then each line as the server writes it. The server rotates it at start
once it is over 8 MB, and a person may truncate it: either way the follower
reads the new file from its start."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

from kraft.paths import RunDirs, default_run_dir

#: How often the file is looked at again. shortcut: polls, as the standard
#: library has no file watcher; a watcher only if the poll ever shows in a profile.
POLL_S = 0.25
#: How far back from the end the first read starts, for the last lines.
BACKLOG_BYTES = 64 * 1024


def server_log() -> Path:
    """The `server.log` of the run directory the client reads, as
    `kraft admin start` resolves it."""
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    return RunDirs(run_dir).logs / "server.log"


async def follow(path: Path, *, backlog: int = 200) -> AsyncIterator[str]:
    """The last `backlog` lines of `path`, then every line written after it,
    without the newline. A file replaced or cut shorter is read again from
    its start; a missing one is waited for."""
    file = None
    identity: tuple[int, int] | None = None
    rest = b""
    first, cut = True, False
    try:
        while True:
            lines: list[str] = []
            try:
                now = path.stat()  # a missing file raises: waited for, below
                if file is not None and (
                    (now.st_ino, now.st_dev) != identity or now.st_size < file.tell()
                ):
                    file.close()
                    file, rest = None, b""
                if file is None:
                    file = path.open("rb")
                    opened = os.fstat(file.fileno())
                    identity = (opened.st_ino, opened.st_dev)
                    cut = first and opened.st_size > BACKLOG_BYTES
                    if cut:
                        # One byte early, to tell a seek that lands mid-line from one on a boundary.
                        file.seek(opened.st_size - BACKLOG_BYTES - 1)
                if file is not None:
                    data = rest + file.read()
                    if cut:
                        # Drop the line the seek landed in, unless it landed on a boundary.
                        data = data[1:] if data[:1] == b"\n" else data.partition(b"\n")[2]
                        cut = False
                    *done, rest = data.split(b"\n")
                    lines = [line.decode(errors="replace") for line in done]
                    if first:
                        lines, first = (lines[-backlog:] if backlog else []), False
            except OSError:
                # Missing, unreadable, a directory, or gone between stat and open: wait.
                if file is not None:
                    file.close()
                file, rest, lines = None, b"", []
            for line in lines:
                yield line
            await asyncio.sleep(POLL_S)
    finally:
        if file is not None:
            file.close()
