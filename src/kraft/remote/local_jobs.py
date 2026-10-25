"""The local worker's job table: the sessions it started, or adopted, and still holds.

A leaf module (psutil, `proc_identity` and the stdlib): the local worker process imports it
and must not load the server's code. The worker `Popen`s a session exactly as given (command,
environment, working directory and log file all come from the server), reaps it, and records
it on disk so a restarted worker finds the sessions its predecessor left running."""

from __future__ import annotations

import asyncio
import json
import os
import string
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from kraft.proc_identity import identity_ok, kill_group

GRACE_S = 10.0
_GONE_S = 2.0  # after SIGKILL, how long a stop waits for the kernel to clear the group


def _members(pgid: int) -> bool:
    """Whether the group has a member left; macOS answers a zombie-only group `PermissionError`."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


@dataclass(frozen=True)
class Spawned:
    pid: int
    pid_start_time: float | None


class SpawnRefused(Exception):
    """The session could not start. `missing` names what was not there (`"working
    directory"`), or is None for any other OS error; `str()` is the OS error."""

    def __init__(self, reason: str, *, missing: str | None = None) -> None:
        super().__init__(reason)
        self.missing = missing


#: The characters of a work item or session id.
_ID_CHARS = frozenset(string.ascii_letters + string.digits + "_-")


def _valid(key: object) -> bool:
    """A session key as the server sends it (`ids.session_ref`): `<work item id>/<session id>`.
    Anything else names no session, and must never become a path under `jobs/`."""
    if not isinstance(key, str) or len(key) > 200:
        return False
    parts = key.split("/")
    return len(parts) == 2 and all(p and set(p) <= _ID_CHARS for p in parts)


class LocalJobs:
    def __init__(self, state_dir: Path) -> None:
        self.dir = Path(state_dir) / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._children: dict[str, subprocess.Popen] = {}
        self._adopted: dict[str, tuple[int, float | None]] = {}

    def _record_path(self, key: str) -> Path:
        if not _valid(key):
            raise ValueError(f"not a session key: {key!r}")
        base = os.path.realpath(self.dir)
        path = os.path.normpath(os.path.join(base, key.replace("/", "__") + ".json"))
        if not path.startswith(base + os.sep):
            raise ValueError(f"not a session key: {key!r}")
        return Path(path)

    def _write(self, key: str, pid: int, start: float | None, log_path: str | None) -> None:
        path = self._record_path(key)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"key": key, "pid": pid, "pid_start_time": start, "log_path": log_path}, f)
        os.replace(tmp, path)

    def _drop(self, key: str) -> None:
        self._children.pop(key, None)
        self._adopted.pop(key, None)
        if _valid(key):
            self._record_path(key).unlink(missing_ok=True)

    def spawn(
        self, key: str, argv: list[str], *, cwd: str, env: dict[str, str], log_path: str
    ) -> Spawned:
        """Start `argv` as a session of its own: a new process group, output appended to
        `log_path` (the server created and truncated it), stdin closed."""
        if not _valid(key):
            raise SpawnRefused(f"not a session key: {key!r}")
        try:
            with open(log_path, "ab") as log:
                proc = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as exc:
            missing = "working directory" if not os.path.exists(cwd) else None
            raise SpawnRefused(str(exc), missing=missing) from exc
        try:
            start = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            start = None
        self._children[key] = proc
        self._write(key, proc.pid, start, log_path)
        return Spawned(proc.pid, start)

    def hold(self, key: str, *, pid: int, pid_start_time: float | None) -> bool:
        """Whether `key` is a live session this table holds. A key it does not know is
        adopted when `pid` is still the process the server recorded; a stale record goes."""
        if not _valid(key):
            return False
        child = self._children.get(key)
        if child is not None:
            if child.poll() is None:
                return True
            self._drop(key)
            return False
        known = self._adopted.get(key)
        if known is not None:
            if identity_ok(*known):
                return True
            self._drop(key)
            return False
        if identity_ok(pid, pid_start_time):
            self._adopted[key] = (pid, pid_start_time)
            self._write(key, pid, pid_start_time, None)
            return True
        self._record_path(key).unlink(missing_ok=True)
        return False

    def recover(self) -> list[str]:
        """Adopt the recorded sessions whose process is still the one recorded (a worker
        that crashed left them running); delete the rest. Returns the adopted keys."""
        kept: list[str] = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                rec = json.loads(path.read_text())
                key, pid, start = rec["key"], int(rec["pid"]), rec.get("pid_start_time")
            except (OSError, ValueError, KeyError, TypeError):
                path.unlink(missing_ok=True)
                continue
            if identity_ok(pid, start):
                self._adopted[key] = (pid, start)
                kept.append(key)
            else:
                path.unlink(missing_ok=True)
        return kept

    def reap(self) -> list[str]:
        """Drop the sessions that ended. Never blocks. Returns the dropped keys."""
        gone = [key for key, child in self._children.items() if child.poll() is not None]
        gone += [key for key, known in self._adopted.items() if not identity_ok(*known)]
        for key in gone:
            self._drop(key)
        return gone

    def running(self) -> list[str]:
        return [*self._children, *self._adopted]

    def _group(self, key: str) -> int | None:
        """The process group of a held session, or None if it is not held or is gone."""
        child = self._children.get(key)
        if child is not None:
            return child.pid if child.poll() is None else None
        known = self._adopted.get(key)
        if known is not None and identity_ok(*known):
            try:
                return known[0] if os.getpgid(known[0]) == known[0] else None
            except OSError:
                return None
        return None

    async def stop(self, key: str, *, grace: float = GRACE_S) -> bool:
        """SIGTERM the session's group, SIGKILL whatever of it is left after `grace` seconds:
        what it backgrounded too, not only its leader. True if it was held."""
        pgid = self._group(key)
        if pgid is None:
            self._drop(key)
            return False
        child = self._children.get(key)
        await kill_group(pgid, grace, reap=child.poll if child is not None else None)
        # `kill_group` returns as it sends SIGKILL; the answer waits for the group to go.
        deadline = time.monotonic() + _GONE_S
        while self._group(key) is not None or _members(pgid):
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.02)
        self._drop(key)
        return True
