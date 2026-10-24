"""Unix-socket housekeeping shared by the machine daemon and the local worker.

A leaf module (stdlib only): the local worker process imports it."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
from pathlib import Path

#: A unix socket path must fit `sockaddr_un.sun_path`: 104 bytes on macOS, 108 on Linux.
_SOCKET_PATH_MAX = 100


def short_socket_path(path: Path) -> Path:
    """`path`, or where a path that is too long for a unix socket is kept instead: a
    private directory under /tmp named by a hash of the path, holding a file of the same name."""
    if len(os.fsencode(path)) < _SOCKET_PATH_MAX:
        return path
    digest = hashlib.sha1(os.fsencode(path)).hexdigest()[:12]
    return Path("/tmp") / f"kraft-{os.getuid()}-{digest}" / path.name


def private_dir(path: Path) -> None:
    """`path`, made if missing, as a directory only this user can enter."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid():
        raise PermissionError(f"{path} belongs to another user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        path.chmod(0o700)


def take_lock(lock_file: Path) -> int | None:
    """The exclusive lock on `lock_file`, as an open fd (closing it releases the lock), or
    None when another process holds it. The holder alone binds and removes the socket."""
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd
