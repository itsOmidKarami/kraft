"""The server's link to the local worker, against an in-process worker."""

from __future__ import annotations

import asyncio
import os
import time

import psutil
import pytest
from support.local_worker import RunningWorker, kill_group

from kraft.remote import local_link, local_serve, protocol
from kraft.remote.local_link import LocalLink, LocalProc


@pytest.fixture(autouse=True)
def _quick_worker_restarts(monkeypatch):
    monkeypatch.setattr(local_link, "_START_EVERY_S", 0.1)


def _env():
    return {"PATH": os.environ["PATH"]}


def _gone(pid: int) -> bool:
    return not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


async def _until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


class _Rig:
    """A link whose `start_worker` starts an in-process worker after `delay` seconds."""

    def __init__(self, run_base, delay=0.0):
        self.run_base, self.delay, self.workers, self.starts = run_base, delay, [], 0
        self.link = LocalLink(
            run_base, kraft_version="2.3.0", start_worker=self.start, retry_s=0.05
        )
        self.task = None

    def start(self):
        self.starts += 1

        async def later():
            await asyncio.sleep(self.delay)
            worker = RunningWorker(self.run_base, idle_exit_s=60)
            self.workers.append(worker)

        asyncio.get_running_loop().create_task(later())

    async def __aenter__(self):
        self.task = asyncio.create_task(self.link.run())
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self.task
        for worker in self.workers:
            await worker.close()


@pytest.mark.parametrize(
    "exit_file, code",
    [("0", 0), ("3", 3), (None, -9), ("²", -9)],
    ids=["clean", "failed", "wrapper-killed", "planted-garbage"],
)
def test_proc_poll(tmp_path, exit_file, code):
    exit_path = tmp_path / "result.exit"
    if exit_file is not None:
        exit_path.write_text(exit_file)
    proc = LocalProc(2_000_000_000, None, exit_path)
    assert proc.returncode is None
    assert proc.poll() == code
    exit_path.write_text("7")
    assert proc.poll() == code and proc.returncode == code  # cached once the process is gone


def test_proc_poll_alive_without_a_start_time(tmp_path):
    assert LocalProc(os.getpid(), None, tmp_path / "x.exit").poll() is None


async def test_spawn_waits_for_the_worker_and_says_so_once(tmp_path):
    log = tmp_path / "log"
    log.write_bytes(b"")
    heard = []

    async def hear(text):
        heard.append(text)

    async with _Rig(tmp_path / "run", delay=0.3) as rig:
        proc = await asyncio.wait_for(
            rig.link.spawn(
                "w/s",
                ["sleep", "30"],
                cwd=str(tmp_path),
                env=_env(),
                log_path=log,
                exit_path=tmp_path / "x.exit",
                on_wait=hear,
            ),
            10,
        )
        try:
            assert heard == ["waiting for local"]
            assert psutil.Process(proc.pid).ppid() == os.getpid()
            assert rig.link.peer_version == "2.3.0"
        finally:
            kill_group(proc.pid)


async def test_cancel_while_waiting_starts_nothing(tmp_path):
    link = LocalLink(tmp_path / "run", kraft_version="2.3.0", start_worker=lambda: None)
    task = asyncio.create_task(
        link.spawn(
            "w/s",
            ["sleep", "30"],
            cwd=str(tmp_path),
            env=_env(),
            log_path=tmp_path / "log",
            exit_path=tmp_path / "x.exit",
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "log").exists()


async def test_cancel_after_the_spawn_frame_stops_what_the_worker_started(tmp_path, monkeypatch):
    real, started = local_serve._Worker._frame, []

    async def slow(self, t, frame):
        if frame.get("t") == "spawn":
            await asyncio.sleep(1.0)  # the answer is late; the session starts, then the stop comes
        await real(self, t, frame)
        if frame.get("t") == "spawn":
            started.append(self.jobs._children[frame["id"]].pid)

    monkeypatch.setattr(local_serve._Worker, "_frame", slow)
    log = tmp_path / "log"
    log.write_bytes(b"")
    async with _Rig(tmp_path / "run") as rig:
        await asyncio.wait_for(rig.link.wait_connected(), 10)
        task = asyncio.create_task(
            rig.link.spawn(
                "w/s",
                ["sleep", "30"],
                cwd=str(tmp_path),
                env=_env(),
                log_path=log,
                exit_path=tmp_path / "x.exit",
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert await _until(lambda: started, 4), "the worker never started the session"
        assert await _until(lambda: _gone(started[0]), 3)
        assert _worker_idle(rig)


def _worker_idle(rig) -> bool:
    return not list((local_serve.state_dir(rig.run_base) / "jobs").glob("*.json"))


async def test_a_dropped_answer_stops_the_session_after_reconnecting(tmp_path, monkeypatch):
    real = local_serve._Worker._frame

    async def drop(self, t, frame):
        if frame.get("t") == "spawn":
            self._spawn(frame["id"], frame)  # started, but the answer never comes
            raise protocol.Closed("dropped")
        await real(self, t, frame)

    monkeypatch.setattr(local_serve._Worker, "_frame", drop)
    log = tmp_path / "log"
    log.write_bytes(b"")
    async with _Rig(tmp_path / "run") as rig:
        await asyncio.wait_for(rig.link.wait_connected(), 10)
        with pytest.raises(OSError, match="went away"):
            await asyncio.wait_for(
                rig.link.spawn(
                    "w/s",
                    ["sleep", "30"],
                    cwd=str(tmp_path),
                    env=_env(),
                    log_path=log,
                    exit_path=tmp_path / "x.exit",
                ),
                15,
            )
        assert await _until(lambda: _worker_idle(rig), 5)


async def test_link_reconnects_after_the_worker_dies(tmp_path):
    log = tmp_path / "log"
    log.write_bytes(b"")
    async with _Rig(tmp_path / "run") as rig:
        await asyncio.wait_for(rig.link.wait_connected(), 10)
        proc = await rig.link.spawn(
            "w/s",
            ["sleep", "30"],
            cwd=str(tmp_path),
            env=_env(),
            log_path=log,
            exit_path=tmp_path / "x.exit",
        )
        try:
            await rig.workers[0].close()  # the worker is gone, the session runs on
            assert await _until(lambda: not rig.link.connected, 5)
            assert await _until(lambda: len(rig.workers) == 2 and rig.link.connected, 10)
            held = await rig.link.hold([("w/s", proc.pid, proc.pid_start_time)])
            assert held == {"w/s": True}
        finally:
            kill_group(proc.pid)


async def test_hold_falls_back_to_identity_when_no_worker_comes(tmp_path):
    link = LocalLink(tmp_path / "run", kraft_version="2.3.0", start_worker=lambda: None)
    mine = psutil.Process(os.getpid()).create_time()
    held = await link.hold(
        [("w/live", os.getpid(), mine), ("w/dead", 2_000_000_000, 1.0)], timeout=0.2
    )
    assert held == {"w/live": True, "w/dead": False}


def test_install_and_current():
    link = LocalLink("/nowhere", kraft_version="2.3.0")
    local_link.install(link)
    try:
        assert local_link.current() is link
    finally:
        local_link.install(None)
    assert local_link.current() is None
