"""A pidfile a live server holds, without starting one.

`kraft.pidfile` trusts a pid only while a process holds the file's lock (or,
for a pre-lock server, while the pid passes its identity checks). A test that
needs "a server is running" writes the pid and takes the lock on its own
descriptor, which a reader's own `open` cannot share:

    held = hold_pidfile(RunDirs(tmp_path / "run").pid, pid=4171)
    ...
    held.release()   # the "server" exits: the lock goes, the file stays
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class HeldPidfile:
    def __init__(self, path: Path, pid: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid))
        self.path = path
        self.pid = pid
        self._fd: int | None = os.open(path, os.O_RDONLY)
        fcntl.flock(self._fd, fcntl.LOCK_EX)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    __del__ = release


def hold_pidfile(path: Path, pid: int | None = None) -> HeldPidfile:
    return HeldPidfile(path, os.getpid() if pid is None else pid)
