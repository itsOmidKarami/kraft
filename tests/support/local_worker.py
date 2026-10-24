"""An in-process local worker, and the server's end of a connection to it."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from kraft.remote import local_serve, protocol
from kraft.remote.transport import FRAME_LIMIT, StreamTransport


class RunningWorker:
    """`local_serve.serve` as a task of the test's own loop."""

    def __init__(self, run_base: Path, **kwargs) -> None:
        self.run_base, self.stop = Path(run_base), asyncio.Event()
        self.task = asyncio.create_task(
            local_serve.serve(
                self.run_base, kraft_version="2.3.0", stop=self.stop, **{"reap_s": 0.05, **kwargs}
            )
        )

    async def ready(self) -> None:
        path = local_serve.socket_path(self.run_base)
        for _ in range(200):
            if path.exists() or self.task.done():
                return
            await asyncio.sleep(0.02)

    async def close(self) -> None:
        self.stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(self.task, 5)


async def connect(run_base: Path) -> StreamTransport:
    """The server's end of a handshaken connection to the worker under `run_base`."""
    reader, writer = await asyncio.open_unix_connection(
        str(local_serve.socket_path(run_base)), limit=FRAME_LIMIT
    )
    t = StreamTransport(reader, writer)
    await protocol.server_handshake(
        t, kraft_version="2.3.0", identify=lambda _: None, expected_name="local"
    )
    return t


async def request(t: StreamTransport, frame: dict, *, answer: str) -> dict:
    """Send `frame` and return the first `answer` frame for its id."""
    await t.send(frame)
    while True:
        got = await asyncio.wait_for(t.recv(), 5)
        if got.get("t") == answer and got.get("id") == frame["id"]:
            return got


def kill_group(pid: int) -> None:
    with contextlib.suppress(OSError):
        os.killpg(pid, signal.SIGKILL)


class ScriptedLink:
    """A local-worker link whose `hold` answers from a script and counts what it was asked."""

    def __init__(self, answers) -> None:
        self.answers, self.calls = iter(answers), []
        self.connected = True

    async def hold(self, keys, *, timeout=15.0):
        self.calls.append([key for key, _pid, _start in keys])
        answer = next(self.answers)
        return {key: answer for key, _pid, _start in keys}


class ThreadedWorker:
    """`local_serve.serve` on a thread of its own: for a test whose server runs on another loop."""

    def __init__(self, run_base: Path) -> None:
        import threading

        self.run_base = Path(run_base)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        async def main() -> None:
            self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
            await local_serve.serve(
                self.run_base, kraft_version="2.3.0", stop=self._stop, idle_exit_s=60
            )

        asyncio.run(main())

    def close(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self.thread.join(5)
