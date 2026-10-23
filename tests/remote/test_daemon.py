"""The machine daemon keeps jobs across the ssh sessions that come and go."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import stat
import sys

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

    async def welcomed(self) -> None:
        await protocol.server_handshake(
            self.t,
            kraft_version="2.3.0",
            identify=lambda _: None,
            expected_name="box",
            server_id="srv",
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
