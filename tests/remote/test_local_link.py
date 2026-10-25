"""The server's link to the local worker, against an in-process worker."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time

import psutil
import pytest
from support.local_worker import RunningWorker, bare_env, kill_group

from kraft.remote import local_link, local_serve, protocol
from kraft.remote.local_link import LocalLink, LocalProc


@pytest.fixture(autouse=True)
def _quick_worker_restarts(monkeypatch):
    monkeypatch.setattr(local_link, "_START_EVERY_S", 0.1)


def _gone(pid: int) -> bool:
    return not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def _until_zombie(pid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline, "the child never exited"
        time.sleep(0.01)


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


def test_proc_poll_reaps_a_leader_that_dies_during_the_check(tmp_path, monkeypatch):
    """Kraft-e0bg7: the leader exits between the reap and the liveness check (a SIGTERM
    landing). A code cached without a reap leaves a zombie in the group, which Linux counts
    as a member, so `_kill_group` paid its whole grace."""
    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    real = local_link.identity_ok
    first = [True]

    def dies_now(pid, start):
        if first[0]:
            first[0] = False
            child.kill()
            _until_zombie(pid)
        return real(pid, start)

    monkeypatch.setattr(local_link, "identity_ok", dies_now)
    proc = LocalProc(
        child.pid, psutil.Process(child.pid).create_time(), tmp_path / "x.exit", reap=child.poll
    )
    try:
        assert proc.poll() == -9
        assert child.returncode is not None  # reaped: no zombie left in the group
    finally:
        child.kill()
        child.wait()


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
                env=bare_env(),
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
            env=bare_env(),
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
                env=bare_env(),
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


@pytest.mark.parametrize(
    "drops, stops_answered",
    [(1, True), (99, True), (1, False)],
    ids=["resent-once-the-stop-is-answered", "gives-up-after-three", "stop-unanswered"],
)
async def test_a_dropped_answer_stops_the_session_after_reconnecting(
    tmp_path, monkeypatch, drops, stops_answered
):
    real, spawns, started, workers = local_serve._Worker._frame, [], [], []
    monkeypatch.setattr(local_link, "_STOP_ANSWER_S", 0.5)

    async def drop(self, t, frame):
        workers.append(self)
        if frame.get("t") == "spawn":
            spawns.append(frame["id"])
            if len(spawns) <= drops:
                started.append(self._spawn(frame["id"], frame)["pid"])  # no answer comes
                raise protocol.Closed("dropped")
        if frame.get("t") == "stop" and not stops_answered:
            return
        await real(self, t, frame)

    monkeypatch.setattr(local_serve._Worker, "_frame", drop)
    log = tmp_path / "log"
    log.write_bytes(b"")
    async with _Rig(tmp_path / "run") as rig:
        await asyncio.wait_for(rig.link.wait_connected(), 10)
        spawn = rig.link.spawn(
            "w/s",
            ["sleep", "30"],
            cwd=str(tmp_path),
            env=bare_env(),
            log_path=log,
            exit_path=tmp_path / "x.exit",
        )
        try:
            if drops == 1 and stops_answered:
                proc = await asyncio.wait_for(spawn, 15)
                assert len(spawns) == 2
                assert _gone(started[0]) and proc.pid != started[0]  # one session, not two
            else:
                with pytest.raises(OSError, match="went away"):
                    await asyncio.wait_for(spawn, 15)
                assert len(spawns) == (3 if stops_answered else 1)
                if stops_answered:
                    assert await _until(lambda: _worker_idle(rig), 5)
        finally:
            for pid in [*started, *(c.pid for c in workers[0].jobs._children.values())]:
                if not _gone(pid):
                    kill_group(pid)


async def test_link_reconnects_after_the_worker_dies(tmp_path):
    log = tmp_path / "log"
    log.write_bytes(b"")
    async with _Rig(tmp_path / "run") as rig:
        await asyncio.wait_for(rig.link.wait_connected(), 10)
        proc = await rig.link.spawn(
            "w/s",
            ["sleep", "30"],
            cwd=str(tmp_path),
            env=bare_env(),
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


class _Launches:
    """`subprocess.Popen` as `_start_worker` calls it: each argv kept, and what `systemd-run`
    exits with."""

    def __init__(self, systemd_run_exits: int | None) -> None:
        self.argvs, self.systemd_run_exits = [], systemd_run_exits
        self.fell_back = threading.Event()

    def __call__(self, argv, **kwargs):
        self.argvs.append(list(argv))
        assert kwargs["start_new_session"] is True
        if len(self.argvs) > 1:
            self.fell_back.set()
        exits = self.systemd_run_exits if argv[0] == "systemd-run" else None

        class Process:
            def wait(self, timeout=None):
                if exits is None:
                    raise subprocess.TimeoutExpired(argv, timeout)
                return exits

        return Process()


@pytest.mark.parametrize(
    ("systemd", "exits", "starts"),
    [
        (True, None, ["systemd-run"]),
        (True, 1, ["systemd-run", "worker"]),
        (False, None, ["worker"]),
    ],
    ids=["service-linux", "systemd-run-fails", "detached"],
)
def test_start_worker_goes_out_of_the_units_cgroup_under_systemd(
    tmp_path, monkeypatch, systemd, exits, starts
):
    launches = _Launches(exits)
    monkeypatch.setattr(local_link.subprocess, "Popen", launches)
    monkeypatch.setattr(local_link, "_under_systemd", lambda: systemd)
    monkeypatch.setattr(local_link, "_SYSTEMD_RUN_S", 0.05)
    monkeypatch.setenv("KRAFT_HOME", "/home/k")

    local_link._start_worker(tmp_path / "run", "2.3.0-abcd1234")
    if exits:
        assert launches.fell_back.wait(2)

    first = launches.argvs[0]
    assert [
        ("systemd-run" if a[0] == "systemd-run" else "worker") for a in launches.argvs
    ] == starts
    worker = launches.argvs[-1]
    assert worker[-6:] == [
        "worker",
        "local",
        "--run-dir",
        str(tmp_path / "run"),
        "--install",
        "2.3.0-abcd1234",
    ]
    if systemd:
        assert first[:5] == ["systemd-run", "--user", "--scope", "--collect", "--quiet"]
        assert "--setenv=KRAFT_HOME=/home/k" in first
        assert first[-len(worker) :] == worker
