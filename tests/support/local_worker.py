"""An in-process local worker, and the server's end of a connection to it."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

import pytest

from kraft.remote import local_link, local_serve, protocol
from kraft.remote.local_jobs import LocalJobs
from kraft.remote.local_link import DirectLink, _Row
from kraft.remote.transport import FRAME_LIMIT, StreamTransport

#: The install of the worker `RunningWorker` and `ThreadedWorker` run, and the link a test makes.
INSTALL = local_serve.install_id("2.3.0")


class RunningWorker:
    """`local_serve.serve` as a task of the test's own loop."""

    def __init__(self, run_base: Path, install: str = INSTALL, **kwargs) -> None:
        self.run_base, self.install, self.stop = Path(run_base), install, asyncio.Event()
        self.task = asyncio.create_task(
            local_serve.serve(
                self.run_base,
                kraft_version="2.3.0",
                install=install,
                stop=self.stop,
                **{"reap_s": 0.05, **kwargs},
            )
        )

    async def ready(self) -> None:
        path = local_serve.socket_path(self.run_base, self.install)
        for _ in range(200):
            if path.exists() or self.task.done():
                return
            await asyncio.sleep(0.02)

    async def close(self) -> None:
        self.stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(self.task, 5)


async def connect(run_base: Path, install: str = INSTALL) -> StreamTransport:
    """The server's end of a handshaken connection to the worker of `install` under `run_base`."""
    reader, writer = await asyncio.open_unix_connection(
        str(local_serve.socket_path(run_base, install)), limit=FRAME_LIMIT
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


class ScriptedLink(_Row):
    """A local-worker link whose `hold` answers from a script and counts what it was asked."""

    def __init__(self, answers) -> None:
        super().__init__(connected=True)
        self.answers, self.calls = iter(answers), []

    async def hold(self, keys, *, timeout=15.0):
        self.calls.append([key for key, _pid, _start in keys])
        answer = next(self.answers)
        return {key: answer for key, _pid, _start in keys}


class ThreadedWorker:
    """`local_serve.serve` on a thread of its own: for a test whose server runs on another loop."""

    def __init__(self, run_base: Path, install: str = INSTALL) -> None:
        import threading

        self.run_base, self.install = Path(run_base), install
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        async def main() -> None:
            self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
            await local_serve.serve(
                self.run_base,
                kraft_version="2.3.0",
                install=self.install,
                stop=self._stop,
                idle_exit_s=60,
            )

        asyncio.run(main())

    def close(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self.thread.join(5)


@pytest.fixture(autouse=True)
def _local_worker(tmp_path_factory):
    """Local sessions are started by the local worker, never by the server itself. A test gets
    that worker's surface over an in-process job table (`DirectLink`), through the same
    `local_link.current()` door the server uses, so no test starts a real worker process.
    Not under `tmp_path`: many tests use it as a checkout or a `cwd`, and job records must
    not land in it."""
    local_link.install(DirectLink(LocalJobs(tmp_path_factory.mktemp("local-worker"))))
    yield
    local_link.install(None)
    # `server_stopping` marks the event loop, and a test loop outlives its test: a stop one test
    # simulated must not leave local sessions kept (or stopped) for the next.
    from kraft.adapters import subprocess as sp

    sp._STOPPING.clear()
    sp._KEEPING.clear()


def bare_env() -> dict[str, str]:
    """The environment a launch is given when only a command is wanted: `PATH` and nothing else."""
    return {"PATH": os.environ["PATH"]}
