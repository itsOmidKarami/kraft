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
