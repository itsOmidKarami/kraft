"""Two ends of an in-process connection for `kraft.remote` tests."""

from __future__ import annotations

import asyncio
import json

from kraft.remote.protocol import Closed

_EOF = object()


class MemoryTransport:
    def __init__(self, inbox: asyncio.Queue, outbox: asyncio.Queue) -> None:
        self._in, self._out, self.closed = inbox, outbox, False

    async def send(self, frame: dict) -> None:
        if self.closed:
            raise Closed("closed")
        self._out.put_nowait(json.loads(json.dumps(frame)))  # as a wire would: copies, JSON-only

    async def recv(self) -> dict:
        item = await self._in.get()
        if item is _EOF:
            self._in.put_nowait(_EOF)
            raise Closed("closed")
        return item

    async def close(self) -> None:
        self.closed = True
        self._out.put_nowait(_EOF)
        self._in.put_nowait(_EOF)


def memory_pair() -> tuple[MemoryTransport, MemoryTransport]:
    """`(a, b)`: what one sends the other receives; closing either ends both."""
    qa, qb = asyncio.Queue(), asyncio.Queue()
    return MemoryTransport(qa, qb), MemoryTransport(qb, qa)
