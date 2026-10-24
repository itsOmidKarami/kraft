"""The local worker process: its socket, and what it does with a frame."""

from __future__ import annotations

import asyncio
import os

import psutil
import pytest
from support.local_worker import RunningWorker, connect, kill_group, request

from kraft.remote import local_serve


def _spawn(key, argv, cwd, log, **extra):
    return {
        "t": "spawn",
        "id": key,
        "argv": argv,
        "cwd": str(cwd),
        "env": {"PATH": os.environ["PATH"]},
        "log_path": str(log),
        **extra,
    }


@pytest.fixture
async def worker(tmp_path):
    w = RunningWorker(tmp_path / "run", idle_exit_s=60)
    await w.ready()
    t = await connect(tmp_path / "run")
    yield w, t
    await t.close()
    await w.close()


async def test_spawn_frame_starts_the_session_as_the_workers_child(worker, tmp_path):
    _, t = worker
    log = tmp_path / "log"
    log.write_bytes(b"")
    got = await request(t, _spawn("w/s", ["sleep", "30"], tmp_path, log), answer="spawned")
    try:
        assert psutil.Process(got["pid"]).ppid() == os.getpid()
        assert got["pid_start_time"] == pytest.approx(psutil.Process(got["pid"]).create_time())
        assert os.getpgid(got["pid"]) == got["pid"]
    finally:
        kill_group(got["pid"])


@pytest.mark.parametrize("known", [True, False], ids=["own-job", "unknown-dead-pid"])
async def test_hold_frame(worker, tmp_path, known):
    _, t = worker
    log = tmp_path / "log"
    log.write_bytes(b"")
    pid, start = 2_000_000_000, 1.0
    if known:
        got = await request(t, _spawn("w/s", ["sleep", "30"], tmp_path, log), answer="spawned")
        pid, start = got["pid"], got["pid_start_time"]
    try:
        held = await request(
            t, {"t": "hold", "id": "w/s", "pid": pid, "pid_start_time": start}, answer="held"
        )
        assert held["running"] is known
    finally:
        if known:
            kill_group(pid)


@pytest.mark.parametrize(
    "cwd_is, missing",
    [("absent", "working directory"), ("a-file", None)],
    ids=["missing-working-directory", "cwd-is-a-file"],
)
async def test_refused_frames(worker, tmp_path, cwd_is, missing):
    _, t = worker
    log = tmp_path / "log"
    log.write_bytes(b"")
    cwd = tmp_path / "nope"
    if cwd_is == "a-file":
        cwd.write_text("x")
    got = await request(t, _spawn("w/s", ["true"], cwd, log), answer="refused")
    assert got["missing"] == missing and got["reason"]
    held = await request(
        t, {"t": "hold", "id": "w/s", "pid": 2_000_000_000, "pid_start_time": 1.0}, answer="held"
    )
    assert held["running"] is False  # the connection is still open


async def test_unknown_frames_are_ignored(worker, tmp_path):
    _, t = worker
    await t.send({"t": "from-the-future", "id": "x"})
    held = await request(
        t, {"t": "hold", "id": "w/s", "pid": 2_000_000_000, "pid_start_time": 1.0}, answer="held"
    )
    assert held["running"] is False


async def test_stop_frame_ends_the_group(worker, tmp_path):
    _, t = worker
    log = tmp_path / "log"
    log.write_bytes(b"")
    got = await request(
        t, _spawn("w/s", ["/bin/sh", "-c", "sleep 30 & wait"], tmp_path, log), answer="spawned"
    )
    members: list[psutil.Process] = []
    for _ in range(100):  # the shell has to fork its background child
        members = psutil.Process(got["pid"]).children()
        if members:
            break
        await asyncio.sleep(0.02)
    assert members
    stopped = await request(t, {"t": "stop", "id": "w/s"}, answer="stopped")
    assert stopped["had"] is True
    assert not psutil.pid_exists(got["pid"]) or not psutil.Process(got["pid"]).is_running()
    assert all(not m.is_running() or m.status() == psutil.STATUS_ZOMBIE for m in members)
    again = await request(t, {"t": "stop", "id": "w/s"}, answer="stopped")
    assert again["had"] is False


async def test_a_second_worker_on_the_same_run_dir_returns_at_once(worker, tmp_path):
    other = RunningWorker(tmp_path / "run", idle_exit_s=60)
    await asyncio.wait_for(other.task, 5)
    assert local_serve.socket_path(tmp_path / "run").exists()


async def test_idle_worker_exits(tmp_path):
    w = RunningWorker(tmp_path / "run", idle_exit_s=0.2)
    await asyncio.wait_for(w.task, 5)
    assert not local_serve.socket_path(tmp_path / "run").exists()
    assert not local_serve.pid_path(tmp_path / "run").exists()


async def test_worker_with_a_running_session_does_not_exit_idle(tmp_path):
    w = RunningWorker(tmp_path / "run", idle_exit_s=0.2)
    await w.ready()
    t = await connect(tmp_path / "run")
    log = tmp_path / "log"
    log.write_bytes(b"")
    got = await request(t, _spawn("w/s", ["sleep", "30"], tmp_path, log), answer="spawned")
    await t.close()
    try:
        await asyncio.sleep(0.8)
        assert not w.task.done()
    finally:
        kill_group(got["pid"])
        await w.close()
