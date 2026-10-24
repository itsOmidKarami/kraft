"""The local worker as the separate process the server starts: a session outlives it."""

import asyncio
import os
import signal
import time

import psutil
import pytest

from kraft.remote import local_serve
from kraft.remote.local_link import LocalLink

pytestmark = pytest.mark.slow


async def _until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _worker_pid(run_base) -> int:
    return int(local_serve.pid_path(run_base).read_text())


async def test_a_session_outlives_the_worker_and_the_next_worker_holds_it_again(tmp_path):
    run_base = tmp_path / "run"
    log = tmp_path / "session.log"
    log.write_bytes(b"")
    link = LocalLink(run_base, kraft_version="test")
    task = asyncio.create_task(link.run())
    session = None
    try:
        await asyncio.wait_for(link.wait_connected(), 10)
        first_worker = _worker_pid(run_base)
        session = await link.spawn(
            "w1/s1",
            ["sleep", "30"],
            cwd=str(tmp_path),
            env={"PATH": os.environ["PATH"]},
            log_path=log,
            exit_path=tmp_path / "s1.exit",
        )
        assert psutil.Process(session.pid).ppid() == first_worker

        os.kill(first_worker, signal.SIGKILL)

        assert await _until(lambda: not link.connected, 5)
        assert psutil.pid_exists(session.pid), "the session ran on without its worker"
        assert session.poll() is None
        assert await _until(lambda: link.connected, 10), "the link did not start a new worker"
        assert _worker_pid(run_base) != first_worker
        held = await link.hold([("w1/s1", session.pid, session.pid_start_time)])
        assert held == {"w1/s1": True}

        os.killpg(session.pid, signal.SIGKILL)

        assert await _until(lambda: session.poll() is not None, 2)
        assert session.returncode == -9  # no exit file: the wrapper was killed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if session is not None:
            try:
                os.killpg(session.pid, signal.SIGKILL)
            except OSError:
                pass
        if local_serve.pid_path(run_base).exists():
            try:
                os.kill(_worker_pid(run_base), signal.SIGTERM)
            except OSError:
                pass
