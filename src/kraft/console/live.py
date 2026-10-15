"""What keeps the console current (spec §4.6): one event stream, read again
from the last event seen whenever it drops, and a health poll.

It knows nothing of widgets. The app hands it three callbacks and runs `run()`
as a worker, so each callback runs on the app's own loop."""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Callable

from kraft import client

#: How long a burst of events is gathered before the board is read again, so a
#: burst redraws once.
BATCH_S = 0.2
#: How often the top line asks the server how it is.
HEALTH_EVERY_S = 5.0
#: The waits between reconnect attempts; the last one repeats.
BACKOFF_S = (0.5, 1.0, 2.0, 5.0, 10.0)
#: How long a server that was live and stopped answering reads as
#: "reconnecting" before the views say there is no server: long enough for
#: `kraft admin restart` to come back without blanking every view.
GRACE_S = 15.0


class Link(enum.StrEnum):
    """The live dot's state."""

    LIVE = "live"
    RECONNECTING = "reconnecting"
    # No server, or one that refused this machine's token: every view says which.
    DOWN = "down"


#: `on_event(rows, events)`: the board read again after `events`. Empty
#: `events` means the stream (re)connected: whatever a view shows, it reads again.
OnEvent = Callable[[list[dict], list[dict]], None]
#: `on_health(health)`: `/health`'s answer, or None when there was none.
OnHealth = Callable[[dict | None], None]
#: `on_link(link, error)`: the dot changed, and the failure that changed it.
OnLink = Callable[[Link, client.ClientError | None], None]


class Live:
    def __init__(self, *, on_event: OnEvent, on_health: OnHealth, on_link: OnLink) -> None:
        self.on_event = on_event
        self.on_health = on_health
        self.on_link = on_link
        #: The last event seen, so a reconnect resumes after it.
        self.seq: int | None = None
        self._connected = False
        self._dropped_at: float | None = None
        self._pending: list[dict] = []
        self._flush: asyncio.Task | None = None

    async def run(self) -> None:
        try:
            await asyncio.gather(self._follow(), self._poll())
        finally:
            if self._flush is not None:
                self._flush.cancel()

    async def _follow(self) -> None:
        failures = 0
        while True:
            try:
                rows, cursor = await client.board_rows()
                # From the cursor, not 0, so the stream does not replay history.
                self.seq = cursor if self.seq is None else self.seq
                self._connected, self._dropped_at, failures = True, None, 0
                self.on_link(Link.LIVE, None)
                self.on_event(rows, [])
                async for event in client.stream_events(self.seq):
                    self.seq = event["seq"]
                    self._pending.append(event)
                    if self._flush is None or self._flush.done():
                        self._flush = asyncio.create_task(self._refresh())
                # The server closed the stream without an error: a drop all the same.
                self._dropped()
                self.on_link(Link.RECONNECTING, None)
            except client.ClientError as exc:
                self._dropped()
                self.on_link(self._link_after(exc), exc)
            await asyncio.sleep(BACKOFF_S[min(failures, len(BACKOFF_S) - 1)])
            failures += 1

    def _dropped(self) -> None:
        if self._connected:
            self._connected, self._dropped_at = False, time.monotonic()

    def _link_after(self, exc: client.ClientError) -> Link:
        if exc.kind == client.Failure.UNAUTHORIZED:
            return Link.DOWN
        if exc.kind == client.Failure.UNREACHABLE and (
            self._dropped_at is None or time.monotonic() - self._dropped_at > GRACE_S
        ):
            return Link.DOWN
        return Link.RECONNECTING

    async def _refresh(self) -> None:
        await asyncio.sleep(BATCH_S)
        batch, self._pending = self._pending, []
        try:
            rows, _cursor = await client.board_rows()
        except client.ClientError:
            return  # the stream is failing too; its reconnect reads the board again
        self.on_event(rows, batch)

    async def _poll(self) -> None:
        while True:
            try:
                health = await client.health()
            except client.ClientError:
                health = None
            self.on_health(health)
            await asyncio.sleep(HEALTH_EVERY_S)
