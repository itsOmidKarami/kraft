"""The local worker: the process that starts, reaps and holds this machine's local sessions.

`kraft admin worker local` runs it, started by the server and found again after a server
restart. It listens on a unix socket under the run directory (file permissions are the
auth) and speaks the remote-worker framing to the server: `spawn` starts a session exactly
as the server built it, `hold` says whether a session is still held, `stop` ends one.
The server keeps supervising a running session by pid and by the files it writes; this
process starts it, reaps it and remembers it, so a restarted worker (or server) still finds it.

Only leaf modules are imported, all at the top: `kraft admin update` replaces the package
under a running worker, and a lazy import would load the new code into this old process."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from kraft.remote import protocol, sockets
from kraft.remote.local_jobs import LocalJobs, SpawnRefused
from kraft.remote.transport import FRAME_LIMIT, StreamTransport

log = logging.getLogger(__name__)

SOCKET = "worker.sock"


def state_dir(run_base: Path) -> Path:
    return Path(run_base) / "local-worker"


def socket_path(run_base: Path) -> Path:
    return sockets.short_socket_path(state_dir(run_base) / SOCKET)


def pid_path(run_base: Path) -> Path:
    return state_dir(run_base) / "worker.pid"


class _Worker:
    def __init__(self, jobs: LocalJobs, kraft_version: str) -> None:
        self.jobs, self.kraft_version = jobs, kraft_version
        self.connections = 0
        self.wake = asyncio.Event()
        self._stopping: set[asyncio.Task] = set()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        t = StreamTransport(reader, writer)
        try:
            await protocol.machine_handshake(t, kraft_version=self.kraft_version)
            await protocol.pump(t, on_frame=lambda frame: self._frame(t, frame))
        except (protocol.Closed, protocol.Refused, OSError, ValueError):
            pass  # the server went away; sessions run on
        except Exception:
            log.exception("local worker connection failed")
        finally:
            self.connections -= 1
            writer.close()
            self.wake.set()

    async def _frame(self, t: protocol.Transport, frame: dict) -> None:
        kind, key = frame.get("t"), frame.get("id")
        if kind == "spawn":
            await t.send(self._spawn(key, frame))
        elif kind == "hold":
            held = self.jobs.hold(
                key, pid=int(frame["pid"]), pid_start_time=frame.get("pid_start_time")
            )
            await t.send({"t": "held", "id": key, "running": held})
        elif kind == "stop":
            task = asyncio.create_task(self._stop(t, key))
            self._stopping.add(task)
            task.add_done_callback(self._stopping.discard)
        else:
            log.warning("local worker: ignoring a %r frame", kind)

    def _spawn(self, key: str, frame: dict) -> dict:
        try:
            spawned = self.jobs.spawn(
                key,
                list(frame["argv"]),
                cwd=frame["cwd"],
                env=dict(frame["env"]),
                log_path=frame["log_path"],
            )
        except SpawnRefused as exc:
            return {"t": "refused", "id": key, "missing": exc.missing, "reason": str(exc)}
        return {
            "t": "spawned",
            "id": key,
            "pid": spawned.pid,
            "pid_start_time": spawned.pid_start_time,
        }

    async def _stop(self, t: protocol.Transport, key: str) -> None:
        had = await self.jobs.stop(key)
        with contextlib.suppress(protocol.Closed):
            await t.send({"t": "stopped", "id": key, "had": had})

    async def run(self, stop: asyncio.Event, idle_exit_s: float, reap_s: float) -> None:
        loop = asyncio.get_running_loop()
        idle_since = loop.time()
        while not stop.is_set():
            self.jobs.reap()
            now = loop.time()
            if self.connections or self.jobs.running():
                idle_since = now
            elif now - idle_since >= idle_exit_s:
                return
            self.wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), reap_s)
        for task in list(self._stopping):
            task.cancel()


async def serve(
    run_base: Path,
    *,
    kraft_version: str,
    stop: asyncio.Event,
    idle_exit_s: float = 5.0,
    reap_s: float = 0.2,
) -> None:
    """Run the worker until `stop` is set, or until no server is connected and no session
    runs for `idle_exit_s`. Returns at once when another worker holds the lock."""
    directory, path = state_dir(run_base), socket_path(run_base)
    sockets.private_dir(directory)
    if path.parent != directory:
        sockets.private_dir(path.parent)
    lock = sockets.take_lock(directory / "worker.lock")
    if lock is None:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()  # left by a worker that died
        jobs = LocalJobs(directory)
        worker = _Worker(jobs, kraft_version)
        jobs.recover()
        server = await asyncio.start_unix_server(worker.handle, path=str(path), limit=FRAME_LIMIT)
        path.chmod(0o600)
        pid_file = pid_path(run_base)
        pid_file.write_text(str(os.getpid()))
        try:
            await worker.run(stop, idle_exit_s, reap_s)
        finally:
            server.close()
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            with contextlib.suppress(FileNotFoundError):
                pid_file.unlink()
    finally:
        os.close(lock)  # last: until now no other worker could bind or remove the socket
