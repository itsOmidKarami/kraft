"""Where a stand-in (remote/standin.py) reaches the server: a listener on the loopback, whose port
and a random key are in files only this user can read. It carries one step's log and signals;
nothing else."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import os
import secrets
from pathlib import Path

from kraft.remote import protocol
from kraft.remote.standin import KEY_FILE, PORT_FILE, state_dir
from kraft.remote.transport import FRAME_LIMIT, StreamTransport


class Listener:
    def __init__(self, runs, run_dir: Path) -> None:
        self._runs, self._dir = runs, state_dir(run_dir)
        self._server: asyncio.Server | None = None
        self._key = ""
        self.attached: set[str] = set()

    async def start(self) -> int:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._key = secrets.token_hex(16)
        fd = os.open(self._dir / KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(self._key)
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0, limit=FRAME_LIMIT)
        port = self._server.sockets[0].getsockname()[1]
        (self._dir / PORT_FILE).write_text(str(port))
        return port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _serve(self, reader, writer) -> None:
        t = StreamTransport(reader, writer)
        key = None
        try:
            hello = await asyncio.wait_for(t.recv(), 10)
            if hello.get("t") != "attach" or not hmac.compare_digest(
                str(hello.get("key", "")), self._key
            ):
                await t.send({"t": "gone", "reason": "refused"})
                return
            wid, sid = str(hello["item"]), str(hello["session"])
            run = self._runs.find(wid, sid) or await self._runs.recover(wid, sid)
            if run is None:
                await t.send(
                    {"t": "gone", "reason": f"this server holds no step {wid}/{sid} on a machine"}
                )
                return
            key = run.spec.key
            self.attached.add(key)
            sender = asyncio.create_task(self._send(run, t, int(hello.get("offset", 0))))
            try:
                while True:
                    frame = await t.recv()
                    if frame.get("t") == "signal" and frame.get("sig") in ("INT", "KILL"):
                        await self._runs.signal(run, frame["sig"])
            finally:
                sender.cancel()
        except (protocol.Closed, TimeoutError, OSError, KeyError, ValueError):
            pass
        finally:
            if key is not None:
                self.attached.discard(key)
            await t.close()

    async def _send(self, run, t, offset: int) -> None:
        while True:
            data = b""
            if run.offset > offset:
                with open(run.spool, "rb") as fh:
                    fh.seek(offset)
                    data = fh.read(min(run.offset - offset, 1 << 16))
            if data:
                await t.send({"t": "log", "data": base64.b64encode(data).decode("ascii")})
                offset += len(data)
                continue
            if run.exit_code is not None and offset >= run.offset:
                await t.send({"t": "exit", "code": run.exit_code})
                return
            async with run.changed:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(run.changed.wait(), 1.0)
