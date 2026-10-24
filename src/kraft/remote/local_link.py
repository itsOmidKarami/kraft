"""The server's link to the local worker: how a local session gets started.

`LocalLink.run` keeps a connection to the worker's unix socket (starting the worker when
nothing answers), and `spawn` asks it to start a session, handing back a `LocalProc` with
the `pid`, `poll()` and `returncode` that `_run_task`'s poll loop uses on a `Popen`. The
server goes on supervising the session by pid and by the files it writes; the worker
starts, reaps and holds it. Nothing here starts a session in the server's own process:
tests install a `DirectLink` (the same surface over an in-process job table) instead."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from kraft import item_paths
from kraft.proc_identity import identity_ok, pid_alive
from kraft.remote import local_serve, protocol
from kraft.remote.local_jobs import LocalJobs, SpawnRefused
from kraft.remote.transport import FRAME_LIMIT, StreamTransport
from kraft.worker.worktree_read import read_no_follow

log = logging.getLogger(__name__)

WAITING = "waiting for local"
_START_EVERY_S = 5.0
_STOP_WAIT_S = 10.0
_STOP_ANSWER_S = 15.0
_ANSWERS = {"spawned": "spawn", "refused": "spawn", "held": "hold", "stopped": "stop"}


class LocalProc:
    """A session the worker started, with the surface of the `Popen` it replaces. The worker
    reports no exit code (it only reaps), so `poll` reads the exit file the launch wrapper
    writes on the way out, as `reattach` does for a process it did not fork."""

    def __init__(
        self,
        pid: int,
        pid_start_time: float | None,
        exit_path: Path,
        *,
        reap: Callable[[], object] | None = None,
    ) -> None:
        self.pid, self.pid_start_time, self.exit_path = pid, pid_start_time, Path(exit_path)
        self._reap, self._code = reap, None

    def poll(self) -> int | None:
        if self._code is not None:
            return self._code
        if self._reap is not None:
            self._reap()
        alive = (
            identity_ok(self.pid, self.pid_start_time)
            if self.pid_start_time is not None
            else pid_alive(self.pid)
        )
        if alive:
            return None
        self._code = self._exit_code()
        return self._code

    @property
    def returncode(self) -> int | None:
        return self._code

    def _exit_code(self) -> int:
        try:
            code = item_paths.exit_code(read_no_follow(self.exit_path).decode())
        except (OSError, UnicodeDecodeError):
            code = None
        return -9 if code is None else code


def _refusal(missing: str | None, reason: str, cwd: str) -> OSError:
    if missing is not None:
        return FileNotFoundError(errno.ENOENT, reason, cwd)
    return OSError(reason)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _start_worker(run_base: Path) -> None:
    """Start the worker as a process of its own, outliving this server."""
    logs = Path(run_base) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "local-worker.log", "ab") as out:
        subprocess.Popen(
            [
                sys.executable,
                "-P",
                "-m",
                "kraft",
                "admin",
                "worker",
                "local",
                "--run-dir",
                str(run_base),
            ],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
            env=os.environ.copy(),
        )


class _Row:
    """Whether a worker is connected, and what the Settings row says about it."""

    def __init__(self, *, connected: bool = False, peer_version: str | None = None) -> None:
        self.connected, self.peer_version = connected, peer_version
        self.since: str | None = None
        self._up = asyncio.Event()
        if connected:
            self._up.set()

    async def wait_connected(self) -> None:
        await self._up.wait()

    def _set_connected(self, up: bool) -> None:
        if up != self.connected:
            self.since = _now_iso()
        self.connected = up
        if up:
            self._up.set()
        else:
            self._up.clear()

    def snapshot_row(self, running: int) -> dict:
        return {
            "name": "local",
            "mode": "local",
            "online": self.connected,
            "reason": None if self.connected else "starting",
            "slots_used": running,
            "capacity": None,
            "repos": None,
            "last_seen": self.since,
            "kraft_version": self.peer_version,
            "give_up_after": "never",
        }


class LocalLink(_Row):
    def __init__(
        self,
        run_base: Path,
        *,
        kraft_version: str,
        start_worker: Callable[[], None] | None = None,
        retry_s: float = 0.5,
    ) -> None:
        super().__init__()
        self.run_base, self.kraft_version, self.retry_s = Path(run_base), kraft_version, retry_s
        self._start = start_worker or (lambda: _start_worker(self.run_base))
        self._t: protocol.Transport | None = None
        self._pending: dict[tuple[str, str], list[asyncio.Future]] = {}

    async def run(self) -> None:
        """Hold a connection to the worker for as long as this task lives; never raises."""
        last_start = -_START_EVERY_S
        while True:
            pause = self.retry_s
            try:
                if not await self._round():
                    if time.monotonic() - last_start >= _START_EVERY_S:
                        last_start = time.monotonic()
                        self._start()
            except asyncio.CancelledError:
                raise
            except (protocol.Closed, OSError):
                pass  # the worker went away; the next round finds or starts one
            except Exception:
                log.exception("local worker link failed")
                pause = max(pause, _START_EVERY_S)
            await asyncio.sleep(pause)

    async def _round(self) -> bool:
        """One connection, from connect to its end. False when nothing was listening."""
        try:
            reader, writer = await asyncio.open_unix_connection(
                str(local_serve.socket_path(self.run_base)), limit=FRAME_LIMIT
            )
        except OSError:
            return False
        t = StreamTransport(reader, writer)
        try:
            peer = await protocol.server_handshake(
                t, kraft_version=self.kraft_version, identify=lambda _: None, expected_name="local"
            )
            self.peer_version, self._t = peer.kraft, t
            self._set_connected(True)
            await protocol.pump(t, on_frame=self._on_frame)
        finally:
            self._t = None
            self._set_connected(False)
            self._fail_pending()
            await asyncio.shield(t.close())
        return True

    async def _on_frame(self, frame: dict) -> None:
        kind = _ANSWERS.get(str(frame.get("t")))
        waiting = self._pending.get((kind, str(frame.get("id")))) if kind else None
        if waiting:
            future = waiting.pop(0)
            if not future.done():
                future.set_result(frame)

    def _fail_pending(self) -> None:
        for futures in self._pending.values():
            for future in futures:
                if not future.done():
                    future.set_exception(protocol.Closed("the local worker went away"))
        self._pending.clear()

    async def _ask(self, kind: str, key: str, frame: dict, timeout: float | None = None) -> dict:
        t = self._t
        if t is None:
            raise protocol.Closed("not connected to the local worker")
        future = asyncio.get_running_loop().create_future()
        self._pending.setdefault((kind, key), []).append(future)
        try:
            await t.send(frame)
            return await asyncio.wait_for(future, timeout)
        finally:
            futures = self._pending.get((kind, key), [])
            if future in futures:
                futures.remove(future)

    async def spawn(
        self,
        key: str,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        log_path: Path,
        exit_path: Path,
        on_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LocalProc:
        if not self.connected:
            if on_wait is not None:
                await on_wait(WAITING)
            await self.wait_connected()
        frame = {
            "t": "spawn",
            "id": key,
            "argv": argv,
            "cwd": cwd,
            "env": env,
            "log_path": str(log_path),
        }
        try:
            answer = await self._ask("spawn", key, frame)
        except BaseException as exc:
            # The worker may have started the session by now, and nobody would hold it.
            await asyncio.shield(self._stop_quietly(key))
            if isinstance(exc, protocol.Closed):
                raise OSError("the local worker went away while starting the session") from exc
            raise
        if answer["t"] == "refused":
            raise _refusal(answer.get("missing"), str(answer.get("reason") or ""), cwd)
        return LocalProc(answer["pid"], answer.get("pid_start_time"), exit_path)

    async def _stop_quietly(self, key: str) -> None:
        with contextlib.suppress(Exception):
            if not self.connected:
                await asyncio.wait_for(self.wait_connected(), _STOP_WAIT_S)
            await self._ask("stop", key, {"t": "stop", "id": key}, _STOP_ANSWER_S)

    async def hold(
        self, keys: list[tuple[str, int, float | None]], *, timeout: float = 15.0
    ) -> dict[str, bool]:
        """Whether the worker holds each `(key, pid, pid_start_time)`. With no worker to ask
        (it does not come up within `timeout`, or drops mid-question) the answer is the pid
        identity check the server made before the worker existed."""
        try:
            await asyncio.wait_for(self.wait_connected(), timeout)
            frames = [
                {"t": "hold", "id": key, "pid": pid, "pid_start_time": start}
                for key, pid, start in keys
            ]
            answers = await asyncio.wait_for(
                asyncio.gather(*(self._ask("hold", f["id"], f) for f in frames)), timeout
            )
            return {f["id"]: bool(a.get("running")) for f, a in zip(frames, answers, strict=True)}
        except (TimeoutError, protocol.Closed, OSError):
            log.warning("no answer from the local worker; judging live sessions by pid identity")
            return {key: identity_ok(pid, start) for key, pid, start in keys}


class DirectLink(_Row):
    """The link's surface over an in-process job table: what tests install, never the server."""

    def __init__(self, jobs: LocalJobs, *, connected: bool = True) -> None:
        super().__init__(connected=connected, peer_version="direct")
        self.jobs = jobs

    def set_connected(self, up: bool) -> None:
        self._set_connected(up)

    async def spawn(
        self,
        key: str,
        argv: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        log_path: Path,
        exit_path: Path,
        on_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LocalProc:
        if not self.connected:
            if on_wait is not None:
                await on_wait(WAITING)
            await self.wait_connected()
        try:
            spawned = self.jobs.spawn(key, argv, cwd=cwd, env=env, log_path=str(log_path))
        except SpawnRefused as exc:
            raise _refusal(exc.missing, str(exc), cwd) from exc
        # `reap` is what lets `_kill_group(reap=proc.poll)` see the leader gone: unreaped, it
        # would stay a zombie in the group and the kill would wait out its whole grace.
        return LocalProc(spawned.pid, spawned.pid_start_time, exit_path, reap=self.jobs.reap)

    async def hold(
        self, keys: list[tuple[str, int, float | None]], *, timeout: float = 15.0
    ) -> dict[str, bool]:
        return {key: self.jobs.hold(key, pid=pid, pid_start_time=start) for key, pid, start in keys}


_current: LocalLink | DirectLink | None = None


def install(link: LocalLink | DirectLink | None) -> None:
    global _current
    _current = link


def current() -> LocalLink | DirectLink | None:
    return _current
