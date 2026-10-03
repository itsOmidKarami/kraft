"""`run/kraft.pid`: which process is this run dir's server.

A pid on its own proves nothing. The server can die without removing the file
(SIGKILL, a power cut), and the kernel then hands its pid to some other
process, which `kraft admin stop` would SIGTERM. So the server holds an
exclusive `flock` on the file for its whole life, and a file nobody holds a
lock on is stale whatever pid it names: the kernel drops the lock when the
process dies, however it dies.

A server from before the lock (1.5.0rc13 and older) never takes one, and
`kraft admin restart` right after an update is talking to exactly that server.
For an unlocked file the pid is still trusted when it passes the checks a
recycled pid fails: it is this user's, it started no later than the file was
written, and its command line names kraft.
"""

from __future__ import annotations

import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

#: A process's start time and a file's mtime come from different clocks
#: (boot time plus ticks, and the filesystem's), so allow this much slack
#: before calling a process younger than the pidfile naming it.
_CLOCK_SLACK_S = 2.0


@dataclass(frozen=True)
class State:
    """What the pidfile says. `pid` is set whenever the file held one;
    `running` only when that pid is this run dir's server; `why` says why
    not, for the stale case."""

    pid: int | None
    running: bool
    why: str = ""


def _locked(path: Path) -> bool:
    """True while a process holds the server's lock on `path`.

    Takes a shared lock and drops it at once: it fails only against the
    server's exclusive one."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _not_a_kraft_server(pid: int, path: Path) -> str | None:
    """Why `pid` cannot be the server that wrote `path`, or None if it can be."""
    try:
        proc = psutil.Process(pid)
        if proc.uids().real != os.getuid():
            return "belongs to another user"
        created = proc.create_time()
        cmdline = proc.cmdline()
    except psutil.NoSuchProcess:
        return "is not running"
    except psutil.AccessDenied:
        return "belongs to another user"
    try:
        written = path.stat().st_mtime
    except OSError:
        return "is not running"
    if created > written + _CLOCK_SLACK_S:
        return "started after the pidfile was written, so the pid was reused"
    if not runs_the_server(cmdline):
        name = cmdline[0] if cmdline else "another program"
        return f"is {os.path.basename(name)}, not Kraft"
    return None


def runs_the_server(cmdline: list[str]) -> bool:
    """Whether `cmdline` is a Kraft server's: the `kraft` command (as its own
    program, or the script a Python runs) or `python -m kraft`, with no verb
    (bare `kraft`, options and all) or `admin start`, which is also what a
    detached start and the service unit run. Not any argv holding the text
    "kraft": a shell started in a `kraft-*` directory, `tail -f
    ~/.kraft/run/kraft.log`, or an editor on `~/src/kraft` all do."""
    entry = None
    if cmdline and os.path.basename(cmdline[0]) == "kraft":
        entry = 0
    elif len(cmdline) > 1 and os.path.basename(cmdline[0]).startswith("python"):
        if os.path.basename(cmdline[1]) == "kraft":
            entry = 1
        else:
            entry = next(
                (
                    i + 1
                    for i, a in enumerate(cmdline[1:-1], 1)
                    if a == "-m" and cmdline[i + 1] == "kraft"
                ),
                None,
            )
    if entry is None:
        return False
    args = cmdline[entry + 1 :]
    return not args or args[0].startswith("-") or args[:2] == ["admin", "start"]


def read(path: Path) -> State:
    """What `path` says, without changing it."""
    try:
        text = path.read_text()
    except (FileNotFoundError, NotADirectoryError):
        return State(None, False, "no pidfile")
    try:
        pid = int(text)
    except ValueError:
        return State(None, False, f"{text.strip()[:40]!r} is not a pid")
    if _locked(path):
        return State(pid, True)
    why = _not_a_kraft_server(pid, path)
    return State(pid, why is None, why or "")


def clear_stale(path: Path, pid: int | None) -> bool:
    """Remove `path` if it still names `pid` and no server holds it.

    Under the lock a starting server takes, so a server that opened the file
    a moment ago either sees it gone (and makes a new one) or holds it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        try:
            current = int(os.read(fd, 64) or b"x")
        except ValueError:
            current = None
        if current != pid:
            return False
        path.unlink(missing_ok=True)
        return True
    finally:
        os.close(fd)


def hold(path: Path, *, attempts: int = 20) -> int | None:
    """Write this process's pid to `path` and lock it for the rest of its life.

    Returns the open descriptor, which must stay open (and is not inherited by
    child processes), or None when another live server holds the lock. A file
    another process removed while this one was taking the lock is retried, so
    the pid never lands in a file nobody can find."""
    for _ in range(attempts):
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            time.sleep(0.05)
            continue
        try:
            same = os.fstat(fd).st_ino == os.stat(path).st_ino
        except FileNotFoundError:
            same = False
        if not same:
            os.close(fd)
            continue
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        return fd
    return None
