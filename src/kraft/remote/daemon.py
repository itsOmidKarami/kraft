"""The machine daemon: what an ssh machine's state lives in, so ssh can drop and come back.

`kraft admin worker serve --stdio` is what the server's ssh runs, and it ends
with the ssh session. Jobs cannot end with it, so the state (`Machine`: running
jobs, working copies, what is still to hand in) lives in this one background
process per machine user, listening on a unix socket under `$KRAFT_HOME/remote`.
`--stdio` becomes `relay`: it copies bytes between ssh and the socket, starting
the daemon first when none is running."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from collections.abc import Callable
from pathlib import Path

from kraft.remote import machine_config, protocol
from kraft.remote.channels import Channels
from kraft.remote.machine import Machine
from kraft.remote.sockets import private_dir, short_socket_path, take_lock
from kraft.remote.transport import FRAME_LIMIT, StreamTransport
from kraft.worker import channel

SOCKET = "machine.sock"
PID_FILE = "machine.pid"
LOCK_FILE = "machine.lock"
IDLE_EXIT_S = 600
RELAY_CONNECT_S = 10.0
RELAY_RESPAWN_S = 1.0  # a daemon that lost the lock to a retiring one is not the one to wait for
_COPY_CHUNK = 1 << 16


def socket_path(home: Path) -> Path:
    """`$KRAFT_HOME/remote/machine.sock`, or where a path that is too long for a
    unix socket is kept instead: a private directory under /tmp named by a hash of the home."""
    return short_socket_path(Path(home) / "remote" / SOCKET)


async def _answers(path: Path) -> bool:
    """Is a daemon listening on `path`?"""
    try:
        _, writer = await asyncio.open_unix_connection(str(path))
    except OSError:
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


class _Daemon:
    def __init__(self, home: Path, kraft_version: str) -> None:
        self.home, self.kraft_version = Path(home), kraft_version
        self.machines: dict[str, Machine] = {}
        self.channels = Channels()  # one for the process, whichever servers' machines it hosts
        self.current: dict[str, protocol.Transport] = {}  # server id -> its live connection
        self.connections = 0
        self.stale = False  # a relay of another Kraft version has been here
        self.wake = asyncio.Event()

    def busy(self) -> bool:
        """Is any job unfinished, or finished and not yet taken by the server?"""
        return any(m.busy() for m in self.machines.values())

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            await self._handle(reader, writer)
        except (protocol.Closed, protocol.Refused, OSError, ValueError, TimeoutError):
            pass  # the relay went away, or said something that is not a hello
        finally:
            self.connections -= 1
            writer.close()
            self.wake.set()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        hello = json.loads(await asyncio.wait_for(reader.readline(), protocol.HANDSHAKE_S))
        if hello.get("t") != "relay":
            return
        if hello.get("kraft") != self.kraft_version:
            self.stale = True
        if self.stale and not self.busy():
            return  # closed without an ack: the relay starts the daemon of its own version
        writer.write(b'{"t":"relay","ok":true}\n')
        await writer.drain()
        t = StreamTransport(reader, writer)
        machine_welcome = await protocol.machine_handshake(
            t,
            kraft_version=self.kraft_version,
            host=socket.gethostname(),
            caps=protocol.machine_caps(machine_config.limit()),
        )
        server = str(machine_welcome.get("server") or "default")
        if (old := self.current.get(server)) is not None:
            await old.close()  # a relay whose ssh is gone but whose pipe has not noticed yet
        self.current[server] = t
        machine = self.machines.get(server)
        if machine is None:
            machine = self.machines[server] = Machine(
                self.home,
                kraft_version=self.kraft_version,
                channels=self.channels,
                limit=machine_config.limit,
            )
        try:
            await machine.serve(t, machine_welcome)
        finally:
            if self.current.get(server) is t:
                del self.current[server]

    async def run(self, idle_exit_s: float, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        idle_since = loop.time()
        poll = min(1.0, max(idle_exit_s / 4, 0.02))
        while not stop.is_set():
            now = loop.time()
            if self.connections or self.busy():
                idle_since = now
            elif self.stale or now - idle_since >= idle_exit_s:
                return
            self.wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), poll)


async def serve(
    home: Path,
    *,
    kraft_version: str,
    idle_exit_s: float = IDLE_EXIT_S,
    stop: asyncio.Event | None = None,
) -> None:
    """Run the daemon until it has been idle for `idle_exit_s` (no connection, no
    unfinished job), a relay of another Kraft version has found it with nothing
    to do, or `stop` is set. Returns at once when another daemon holds the lock."""
    path = socket_path(home)
    private_dir(path.parent)
    lock = take_lock(path.parent / LOCK_FILE)
    if lock is None:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()  # left by a daemon that died
        daemon = _Daemon(home, kraft_version)
        before = channel.current()
        channel.install(daemon.channels.registry)  # the sandbox backends find their channels here
        server = await asyncio.start_unix_server(daemon.handle, path=str(path), limit=FRAME_LIMIT)
        path.chmod(0o600)
        pid_file = path.parent / PID_FILE
        pid_file.write_text(str(os.getpid()))
        try:
            await daemon.run(idle_exit_s, stop or asyncio.Event())
        finally:
            channel.install(before)
            await daemon.channels.close()
            server.close()
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            with contextlib.suppress(FileNotFoundError):
                pid_file.unlink()
    finally:
        os.close(lock)  # last: until now no other daemon could bind or remove the socket


async def _copy(reader, writer) -> None:
    while data := await reader.read(_COPY_CHUNK):
        writer.write(data)
        await writer.drain()


async def relay(
    home: Path,
    *,
    kraft_version: str,
    stdin: asyncio.StreamReader,
    stdout: asyncio.StreamWriter,
    spawn: Callable[[], None],
) -> None:
    """Connect to the daemon (starting it with `spawn` when none answers, and
    again when it retires), say which Kraft this is, then copy bytes both ways
    until either side closes."""
    path = socket_path(home)
    loop = asyncio.get_running_loop()
    deadline, spawned_at = loop.time() + RELAY_CONNECT_S, None
    hello = json.dumps({"t": "relay", "kraft": kraft_version}).encode() + b"\n"
    while True:
        try:
            reader, writer = await asyncio.open_unix_connection(str(path), limit=FRAME_LIMIT)
        except OSError:
            if spawned_at is None or loop.time() - spawned_at > RELAY_RESPAWN_S:
                spawn()  # twice is safe: the daemon without the lock returns at once
                spawned_at = loop.time()
        else:
            writer.write(hello)
            await writer.drain()
            if await reader.readline():
                break
            writer.close()  # a daemon of another version, retiring
            spawned_at = None
        if loop.time() > deadline:
            raise ConnectionError("the machine daemon did not start")
        await asyncio.sleep(0.1)
    uplink = asyncio.ensure_future(_copy(stdin, writer))
    downlink = asyncio.ensure_future(_copy(reader, stdout))
    try:
        await asyncio.wait({uplink, downlink}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (uplink, downlink):
            task.cancel()
        await asyncio.gather(uplink, downlink, return_exceptions=True)
        writer.close()
        stdout.close()
        for closing in (writer, stdout):
            with contextlib.suppress(OSError, RuntimeError):
                await closing.wait_closed()
