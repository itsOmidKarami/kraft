"""What keeps the console current (spec §4.6): one event stream, read again
from the last event seen whenever it drops, and a health poll.

It knows nothing of widgets. The app hands it three callbacks and runs `run()`
as a worker, so each callback runs on the app's own loop."""

from __future__ import annotations

import asyncio
import contextlib
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
#: How long a stream must stay open, without an event, to count as having
#: worked: only then does the backoff start over.
STABLE_S = 5.0
#: How many board reads in a row the server may answer with an error (a 5xx,
#: a 404, a refusal, an answer that is not JSON, no answer in time) before the
#: views stop saying "reconnecting" and show the server's own words instead.
STUCK_AFTER = 3


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
        self._wake = asyncio.Event()
        self._retry = asyncio.Event()

    async def run(self) -> None:
        # A TaskGroup, so a failure in any of the three ends the others and
        # reaches the caller; only ClientError is handled inside.
        async with asyncio.TaskGroup() as group:
            for work in (self._follow, self._flusher, self._poll):
                group.create_task(work())

    def retry(self) -> None:
        """Reconnect now rather than at the end of the backoff: the server is
        known to answer again."""
        self._retry.set()

    def reread(self) -> None:
        """Read the board again now, as after an event: a refused action
        leaves the row it was about possibly stale, and no event says so."""
        self._wake.set()

    async def _follow(self) -> None:
        failures = 0
        # Failures in a row from a server that answered, which a retry is not
        # going to fix by itself.
        stuck = 0
        while True:
            try:
                rows, cursor = await client.board_rows()
                stuck = 0
                # A retry asked for before this read answered is spent.
                self._retry.clear()
                # From the cursor, not 0, so the stream does not replay history.
                self.seq = cursor if self.seq is None else self.seq
                self._connected, self._dropped_at = True, None
                self.on_link(Link.LIVE, None)
                self.on_event(rows, [])
                opened = time.monotonic()
                try:
                    async for event in client.stream_events(self.seq):
                        failures = 0
                        self.seq = event["seq"]
                        self._pending.append(event)
                        self._wake.set()
                    # The server closed the stream without an error: a drop all the same.
                    self._dropped()
                    self.on_link(Link.RECONNECTING, None)
                finally:
                    if time.monotonic() - opened >= STABLE_S:
                        failures = 0
            except client.ClientError as exc:
                stuck = 0 if exc.kind == client.Failure.UNREACHABLE else stuck + 1
                self._dropped()
                self.on_link(self._link_after(exc, stuck), exc)
            await self._rest(BACKOFF_S[min(failures, len(BACKOFF_S) - 1)])
            failures += 1

    async def _rest(self, seconds: float) -> None:
        """The backoff's wait, cut short by `retry()`."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._retry.wait()
        self._retry.clear()

    def _dropped(self) -> None:
        if self._connected:
            self._connected, self._dropped_at = False, time.monotonic()

    def _link_after(self, exc: client.ClientError, stuck: int) -> Link:
        if exc.kind == client.Failure.UNAUTHORIZED:
            return Link.DOWN
        if exc.kind == client.Failure.UNREACHABLE:
            gone = self._dropped_at is None or time.monotonic() - self._dropped_at > GRACE_S
            return Link.DOWN if gone else Link.RECONNECTING
        return Link.DOWN if stuck >= STUCK_AFTER else Link.RECONNECTING

    async def _flusher(self) -> None:
        while True:
            await self._wake.wait()
            # Cleared before the read, so an event arriving during it wakes the next round.
            self._wake.clear()
            await asyncio.sleep(BATCH_S)
            batch, self._pending = self._pending, []
            try:
                rows, _cursor = await client.board_rows()
            except client.ClientError:
                # The stream is failing too; keep the batch for the next read.
                self._pending = batch + self._pending
                continue
            self.on_event(rows, batch)

    async def _poll(self) -> None:
        while True:
            try:
                health = await client.health()
            except client.ClientError:
                health = None
            self.on_health(health)
            if health is not None and not self._connected:
                self.retry()
            await asyncio.sleep(HEALTH_EVERY_S)
