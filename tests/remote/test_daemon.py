"""The machine daemon keeps jobs across the ssh sessions that come and go."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import pathlib
import stat
import sys
import threading

from support.remote import send_job

from kraft.remote import daemon, protocol
from kraft.remote.transport import FRAME_LIMIT, StreamTransport


class _Pipe:
    """One direction of a pipe, both ends in this process: the writer feeds the reader."""

    def __init__(self) -> None:
        self.reader = asyncio.StreamReader(limit=FRAME_LIMIT)

    def write(self, data: bytes) -> None:
        self.reader.feed_data(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.reader.feed_eof()

    async def wait_closed(self) -> None:
        pass


class _Ssh:
    """The server's end of an ssh session whose far end is `daemon.relay`."""

    def __init__(self, home, version="2.3.0", spawn=lambda: None) -> None:
        to_relay, from_relay = _Pipe(), _Pipe()
        self.t = StreamTransport(from_relay.reader, to_relay)
        self.relay = asyncio.create_task(
            daemon.relay(
                home,
                kraft_version=version,
                stdin=to_relay.reader,
                stdout=from_relay,
                spawn=spawn,
            )
        )

    async def welcomed(self, server_id="srv") -> None:
        await protocol.server_handshake(
            self.t,
            kraft_version="2.3.0",
            identify=lambda _: None,
            expected_name="box",
            server_id=server_id,
        )

    async def drop(self) -> None:
        """ssh ends: the pipe closes."""
        await self.t.close()
        await asyncio.wait_for(self.relay, 5)


async def _frames_until(t, kind):
    frames = []
    while True:
        frames.append(await asyncio.wait_for(t.recv(), 10))
        if frames[-1]["t"] == kind:
            return frames


def _daemon(home, version="2.3.0", **kw):
    return asyncio.create_task(daemon.serve(home, kraft_version=version, **kw))


async def _until_listening(home):
    path = daemon.socket_path(home)
    async with asyncio.timeout(5):
        while not path.exists():
            await asyncio.sleep(0.01)


async def test_a_job_outlives_the_relay_that_brought_it(tmp_path):
    home = tmp_path / "home"
    served = _daemon(home)
    await _until_listening(home)
    first = _Ssh(home)
    await first.welcomed()
    spec = await send_job(
        first.t, tmp_path, [sys.executable, "-c", "import time; time.sleep(1); print('done')"]
    )
    await _frames_until(first.t, "started")
    await first.drop()
    second = _Ssh(home)
    await second.welcomed()
    await second.t.send({"t": "sync", "jobs": [{"id": spec.key, "offset": 0}]})
    frames = await _frames_until(second.t, "finished")
    log = b"".join(base64.b64decode(f["data"]) for f in frames if f["t"] == "log")
    assert b"done" in log and frames[-1]["code"] == 0
    await second.t.send({"t": "delivered", "id": spec.key})
    await second.drop()
    served.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await served


async def test_a_second_relay_starts_no_second_daemon(tmp_path):
    home, spawned = tmp_path / "home", []
    tasks = []

    def spawn() -> None:
        spawned.append(1)
        tasks.append(_daemon(home))

    first = _Ssh(home, spawn=spawn)
    await first.welcomed()
    second = _Ssh(home, spawn=spawn)
    await second.welcomed()
    assert len(spawned) == 1
    await first.drop()
    await second.drop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_an_idle_daemon_exits(tmp_path):
    home = tmp_path / "home"
    await asyncio.wait_for(daemon.serve(home, kraft_version="2.3.0", idle_exit_s=0.2), 1)
    assert not daemon.socket_path(home).exists()


async def test_a_relay_of_another_version_retires_an_idle_daemon(tmp_path):
    home = tmp_path / "home"
    served = _daemon(home, version="2.4.0")
    await _until_listening(home)
    reader, writer = await asyncio.open_unix_connection(str(daemon.socket_path(home)))
    writer.write(json.dumps({"t": "relay", "kraft": "2.5.0"}).encode() + b"\n")
    await writer.drain()
    assert await asyncio.wait_for(reader.readline(), 2) == b""  # no ack: the relay starts a new one
    await asyncio.wait_for(served, 2)
    assert not daemon.socket_path(home).exists()
    writer.close()


async def test_a_relay_of_another_version_does_not_retire_a_daemon_with_a_job(tmp_path):
    home = tmp_path / "home"
    served = _daemon(home, version="2.4.0")
    await _until_listening(home)
    first = _Ssh(home, version="2.4.0")
    await first.welcomed()
    spec = await send_job(first.t, tmp_path, ["sh", "-c", "sleep 30"])
    await _frames_until(first.t, "started")
    await first.drop()
    newer = _Ssh(home, version="2.5.0")
    await newer.welcomed()  # served by the old daemon: it still holds the job
    assert not served.done()
    await newer.t.send({"t": "signal", "id": spec.key, "sig": "KILL"})
    await _frames_until(newer.t, "finished")
    await newer.t.send({"t": "delivered", "id": spec.key})
    await newer.drop()
    await asyncio.wait_for(served, 2)  # nothing left to hold: the stale daemon goes


async def test_the_socket_is_this_users_only(tmp_path):
    home = tmp_path / "home"
    served = _daemon(home)
    await _until_listening(home)
    path = daemon.socket_path(home)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    served.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await served


async def test_two_daemons_started_together_leave_one_running_and_the_socket_answering(tmp_path):
    home = tmp_path / "home"
    stops = [asyncio.Event(), asyncio.Event()]
    tasks = [_daemon(home, stop=stop) for stop in stops]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=1)
    try:
        assert len(done) == 1 and len(pending) == 1
        assert await daemon._answers(daemon.socket_path(home))
    finally:
        for stop in stops:
            stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_a_retiring_daemons_exit_never_removes_its_successors_socket(tmp_path, monkeypatch):
    home = tmp_path / "home"
    path = daemon.socket_path(home)
    unlink, in_finally, resume = pathlib.Path.unlink, threading.Event(), threading.Event()
    old = {}

    def gated_unlink(self, *args, **kwargs):
        # the old daemon, in its `finally` with the listener already closed, waits here
        if (
            self == path
            and threading.current_thread() is not threading.main_thread()
            and old["stop"].is_set()
        ):
            in_finally.set()
            resume.wait(5)
        return unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", gated_unlink)

    def run_old() -> None:
        async def main() -> None:
            old["loop"], old["stop"] = asyncio.get_running_loop(), asyncio.Event()
            await daemon.serve(home, kraft_version="2.3.0", stop=old["stop"])

        asyncio.run(main())

    thread = threading.Thread(target=run_old)
    thread.start()
    await _until_listening(home)
    old["loop"].call_soon_threadsafe(old["stop"].set)
    await asyncio.to_thread(in_finally.wait, 5)
    successor_stop = asyncio.Event()
    successor = _daemon(home, stop=successor_stop)  # the relay's spawn, seeing no listener
    try:
        await asyncio.wait({successor}, timeout=0.5)
        resume.set()
        await asyncio.to_thread(thread.join, 5)
        assert successor.done() or await daemon._answers(path)
    finally:
        resume.set()
        successor_stop.set()
        await asyncio.gather(successor, return_exceptions=True)
        thread.join()


async def test_two_servers_machines_in_one_daemon_share_the_registry_the_backends_read(
    tmp_path, short_run, monkeypatch
):
    from support.fake_backend import Remote

    from kraft.worker import backends

    fake = Remote(tmp_path)
    monkeypatch.setitem(backends._BACKENDS, fake.kind, fake)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(short_run.base))
    home = tmp_path / "home"
    served = _daemon(home)
    await _until_listening(home)
    sessions = []
    for n, server_id in enumerate(["srv-a", "srv-b"], 1):
        ssh = _Ssh(home)
        await ssh.welcomed(server_id)
        folder = tmp_path / server_id
        folder.mkdir()
        result = str(home / "remote" / server_id / "run" / "result.json")
        await send_job(
            ssh.t,
            folder,
            ["true"],
            sid=f"s{n}",
            sandbox={"kind": "remote", "network": {"runtime": {"allow": ["api.example.com"]}}},
            egress={"phase": "runtime", "allow": ["api.example.com"], "deny": []},
            env={"KRAFT_RESULT_PATH": result},
        )
        sessions.append(ssh)
        await _frames_until(ssh.t, "finished")
    assert fake.channel_found == [True, True]
    for ssh in sessions:
        await ssh.drop()
    served.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await served
