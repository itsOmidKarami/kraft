"""`protocol.Transport` over the two connections a machine can use: the
stdin/stdout of an `ssh host kraft admin worker serve --stdio` (one JSON
object per line) and a WebSocket (one JSON object per text message)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable

from kraft.remote.protocol import Closed

#: The largest frame either side will buffer. Slice 2 chunks anything bigger.
FRAME_LIMIT = 4 << 20


def _decode(raw: str | bytes) -> dict:
    try:
        frame = json.loads(raw)
    except (ValueError, TypeError):
        raise Closed("not a frame: unreadable JSON") from None
    if not isinstance(frame, dict):
        raise Closed("not a frame: expected a JSON object")
    return frame


class StreamTransport:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader, self._writer = reader, writer

    async def send(self, frame: dict) -> None:
        self._writer.write(json.dumps(frame, separators=(",", ":")).encode() + b"\n")
        try:
            await self._writer.drain()
        except OSError as exc:  # ConnectionError is an OSError
            raise Closed(str(exc) or "write failed") from exc

    async def recv(self) -> dict:
        try:
            line = await self._reader.readline()
        except (asyncio.LimitOverrunError, ValueError):
            raise Closed("frame too large") from None
        except OSError as exc:
            raise Closed(str(exc) or "read failed") from exc
        if not line:
            raise Closed("end of stream")
        return _decode(line)

    async def close(self) -> None:
        self._writer.close()
        with contextlib.suppress(OSError, RuntimeError):
            await self._writer.wait_closed()


class WebSocketTransport:
    """Wraps a WebSocket by its three verbs, so one class serves Starlette's
    (`send_text`, `receive_text`, `close`) and `websockets`' (`send`, `recv`,
    `close`)."""

    def __init__(
        self,
        send_text: Callable[[str], Awaitable[None]],
        recv_text: Callable[[], Awaitable[str | bytes]],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        self._send, self._recv, self._close = send_text, recv_text, close

    async def send(self, frame: dict) -> None:
        try:
            await self._send(json.dumps(frame, separators=(",", ":")))
        except Exception as exc:  # noqa: BLE001 -- every library has its own "closed"
            raise Closed(type(exc).__name__) from exc

    async def recv(self) -> dict:
        try:
            raw = await self._recv()
        except Exception as exc:  # noqa: BLE001
            raise Closed(type(exc).__name__) from exc
        if not isinstance(raw, str):
            raise Closed("not a frame: binary message")
        return _decode(raw)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._close()
