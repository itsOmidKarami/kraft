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


def _worker_pid(run_base, install: str) -> int:
    return int(local_serve.pid_path(run_base, install).read_text())


async def test_a_session_outlives_the_worker_and_the_next_worker_holds_it_again(tmp_path):
    run_base = tmp_path / "run"
    log = tmp_path / "session.log"
    log.write_bytes(b"")
    link = LocalLink(run_base, kraft_version="test")
    task = asyncio.create_task(link.run())
    session = None
    try:
        await asyncio.wait_for(link.wait_connected(), 10)
        first_worker = _worker_pid(run_base, link.install)
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
        assert _worker_pid(run_base, link.install) != first_worker
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
        if local_serve.pid_path(run_base, link.install).exists():
            try:
                os.kill(_worker_pid(run_base, link.install), signal.SIGTERM)
            except OSError:
                pass


async def test_new_install_worker_adopts_the_old_workers_sessions(tmp_path):
    """An update within a major: the new server's worker (a real process) takes over a session
    the old one started, which reaps nothing else, has no server, and exits on its own."""
    from support.local_worker import RunningWorker, connect, kill_group, request

    run_base = tmp_path / "run"
    log = tmp_path / "session.log"
    log.write_bytes(b"")
    old = RunningWorker(run_base, install="old-aaaaaaaa", idle_exit_s=0.5)
    await old.ready()
    t = await connect(run_base, "old-aaaaaaaa")
    spawn = {
        "t": "spawn",
        "id": "w1/s1",
        "argv": ["sleep", "30"],
        "cwd": str(tmp_path),
        "env": {"PATH": os.environ["PATH"]},
        "log_path": str(log),
    }
    pid = (await request(t, spawn, answer="spawned"))["pid"]
    await t.close()  # the old server is gone; its worker holds the session

    link = LocalLink(run_base, kraft_version="test", install="new-bbbbbbbb")
    task = asyncio.create_task(link.run())
    try:
        await asyncio.wait_for(link.wait_connected(), 10)
        assert _worker_pid(run_base, "new-bbbbbbbb") != os.getpid()
        start = psutil.Process(pid).create_time()
        assert await link.hold([("w1/s1", pid, start)]) == {"w1/s1": True}
        assert not old.task.done(), "the old worker holds a session and stays"

        kill_group(pid)

        await asyncio.wait_for(old.task, 0.5 + 3)  # reaped; idle with no server
        assert await _until(lambda: not list((run_base / "local-worker/jobs").glob("*.json")), 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        kill_group(pid)
        os.kill(_worker_pid(run_base, "new-bbbbbbbb"), signal.SIGTERM)
        await old.close()
