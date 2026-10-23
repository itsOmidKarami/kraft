"""The machine's side of a connection: `kraft admin worker serve`.

Two ways in. Dial-in: this process opens a WebSocket to the server and keeps
redialling. `--stdio`: the server ran `ssh host kraft admin worker serve
--stdio`, and the protocol is this process's stdin and stdout."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from collections.abc import Callable
from urllib.parse import urlsplit

from kraft.remote import protocol
from kraft.remote.transport import FRAME_LIMIT, StreamTransport, WebSocketTransport

CONNECT_PATH = "/api/workers/connect"
_SCHEMES = {"http": "ws", "https": "wss"}


def dial_url(server: str) -> str:
    """The WebSocket URL for a server's address. A path prefix (a reverse proxy
    that serves Kraft under one) is kept."""
    parts = urlsplit(server)
    scheme = _SCHEMES.get(parts.scheme)
    if scheme is None or not parts.netloc:
        raise ValueError(f"server {server!r} must be an http:// or https:// address")
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}{CONNECT_PATH}"


async def _ignore(frame: dict) -> None:
    """Frames beyond the keep-alive are not used yet."""


def _say(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


async def dial_in(
    server: str,
    token: str,
    *,
    kraft_version: str,
    stop: asyncio.Event,
    log: Callable[[str], None] = _say,
    first_delay: float = 1.0,
    max_delay: float = 30.0,
) -> None:
    """Stay connected to `server` until `stop` is set: redial after a refusal,
    a drop or an unreachable server, backing off from `first_delay` to
    `max_delay` (reset after a connection that was welcomed). A refused token
    or version is retried too: an upgrade or a fresh token fixes it."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import WebSocketException

    url, delay = dial_url(server), first_delay

    async def once() -> bool:
        """One connection; True when the server welcomed it."""
        try:
            async with connect(
                url, max_size=FRAME_LIMIT, ping_interval=None, open_timeout=10
            ) as ws:
                t = WebSocketTransport(ws.send, ws.recv, ws.close)
                welcome = await protocol.machine_handshake(
                    t, kraft_version=kraft_version, token=token, host=socket.gethostname()
                )
                log(f"connected as {welcome['name']}")
                try:
                    await protocol.pump(t, on_frame=_ignore)
                except protocol.Closed as exc:
                    log(f"disconnected: {exc}")
                return True
        except protocol.Refused as exc:
            log(f"refused: {exc}")
        except (OSError, TimeoutError, WebSocketException, protocol.Closed) as exc:
            log(f"cannot connect: {exc or type(exc).__name__}")
        return False

    while not stop.is_set():
        attempt = asyncio.ensure_future(once())
        stopper = asyncio.ensure_future(stop.wait())
        await asyncio.wait({attempt, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if not attempt.done():
            attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)
            break
        stopper.cancel()
        delay = first_delay if attempt.result() else min(delay * 2, max_delay)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), delay)


async def run_stdio(*, kraft_version: str, stdin=None, stdout=None) -> None:
    """The ssh side: handshake with no token, then keep alive until ssh closes
    the pipe. `stdout` is the binary file the protocol is written to, which is
    not `sys.stdout`: anything else this process prints must not corrupt it."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=FRAME_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), stdin or sys.stdin)
    w_transport, w_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, stdout or sys.stdout
    )
    t = StreamTransport(reader, asyncio.StreamWriter(w_transport, w_protocol, reader, loop))
    try:
        await protocol.machine_handshake(t, kraft_version=kraft_version, host=socket.gethostname())
        await protocol.pump(t, on_frame=_ignore)
    except (protocol.Closed, protocol.Refused):
        pass  # ssh closed the pipe, or the server said no: either way this process is done
    finally:
        await t.close()
