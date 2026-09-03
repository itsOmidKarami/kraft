from __future__ import annotations

import asyncio
import logging

from kraft import events
from kraft.db import Database

logger = logging.getLogger(__name__)


class Client:
    __slots__ = ("queue", "dropped")

    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.dropped: bool = False


class Broadcaster:
    """Fans committed `events` rows to every connected WebSocket client.

    Fed by `Database.on_commit` -> `notify()`. The fan-out task is the only
    reader of the `events` tail; `_cursor` is the last seq it has delivered.
    A client whose bounded queue fills is marked `dropped` and starved — its
    WebSocket handler closes the socket and the browser reconnects, catching
    up from its own tracked `after_seq`.
    """

    def __init__(self, db: Database, *, maxsize: int = 1000) -> None:
        self._db = db
        self._maxsize = maxsize
        self._clients: set[Client] = set()
        self._cursor: int = 0
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def cursor(self) -> int:
        return self._cursor

    async def start(self) -> None:
        self._cursor = self._db.read(
            lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        )
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def notify(self) -> None:
        self._wakeup.set()

    def register(self) -> Client:
        client = Client(self._maxsize)
        self._clients.add(client)
        return client

    def unregister(self, client: Client) -> None:
        self._clients.discard(client)

    async def _run(self) -> None:
        while True:
            try:
                await self._wakeup.wait()
                self._wakeup.clear()
                new = self._db.read(lambda c: events.read_after(c, self._cursor))
                for ev in new:
                    for client in self._clients:
                        if client.dropped:
                            continue
                        try:
                            client.queue.put_nowait(ev)
                        except asyncio.QueueFull:
                            client.dropped = True
                            logger.warning("ws client queue overflow; dropping client")
                    self._cursor = ev["seq"]
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("broadcaster fan-out iteration failed")
