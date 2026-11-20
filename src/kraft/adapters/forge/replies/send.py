"""One posting point's sends, and the class of a failed write.

`classify` decides whether a write that failed may have landed (so it is reconciled
before any resend), may succeed later, or never will. `PostingPoint` sends within its
budgets: at most three counted sends an intent, a wait after a rate limit or a
deferred failure, a second between writes on one forge account, and 60 seconds a
posting point."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from kraft.adapters.forge.git import CliUnavailable
from kraft.adapters.forge.models import ForgeError, Posted, failure_cause
from kraft.adapters.forge.replies import record

MAX_SENDS = 3
DEFERRED_EVERY = timedelta(minutes=5)
BUDGET_S = 60.0
SPACING_S = 1.0

#: Item 2's per-operation gate: None to go ahead, or `("refused" | "superseded", why)`.
Gate = Callable[[record.Intent], Awaitable[tuple[str, str] | None]]
Write = Callable[[], Awaitable[Posted]]
Sent = Literal[
    "posted",
    "skipped",
    "out_of_time",
    "waiting",
    "given_up",
    "refused",
    "superseded",
    "unclear",
    "rate_limited",
    "deferred",
]

Failure = Literal["unclear", "rate_limited", "deferred", "refused"]
#: `models._UNREACHABLE_MARKERS` less the timeouts: the request never left. A timeout
#: ("timed out after", "connection timed out") may have landed.
_NO_REQUEST = (
    "could not resolve host",
    "temporary failure in name resolution",
    "connection refused",
    "network is unreachable",
    "failed to connect",
    "could not connect",
)


def classify(exc: ForgeError) -> Failure:
    """Whether a failed write may have landed (`unclear`, reconciled before any resend),
    may succeed later (`rate_limited` after its `retry_after`, `deferred`), or never will."""
    if isinstance(exc, CliUnavailable):
        return "deferred"
    if exc.reason == "rate_limited":
        return "rate_limited"
    if exc.reason in ("auth", "tls") or exc.sso_url:
        return "deferred"
    if exc.status is None:
        # No answer: a timeout or a lost connection may have landed. A CLI that is not
        # signed in, or a host that never answered, made no request at all.
        text = str(exc).lower()
        if failure_cause(text) == "forge_auth" or any(m in text for m in _NO_REQUEST):
            return "deferred"
        return "unclear"
    if exc.status >= 500:
        return "unclear"
    if exc.status == 422 and "one pending review" in str(exc).lower():
        return "deferred"  # the person has an unfinished review open
    return "refused"


#: When each forge account last began a write, on `_clock`, across items in this
#: process: GitHub allows about 80 content-creating requests a minute. Spacing is per
#: server process: a second Kraft process on the account is not seen.
_last_write: dict[str, float] = {}
#: One lock per event loop and account: an `asyncio.Lock` belongs to one loop.
_locks: dict[tuple[int, str], asyncio.Lock] = {}
_clock = time.monotonic
_sleep = asyncio.sleep


def _now() -> datetime:
    return datetime.now(UTC)


class PostingPoint:
    """One posting point: every send it makes stops 60 seconds after it began (a send
    under way is never cut short), and every write is spaced per forge account."""

    def __init__(self, db, work_item_id: str, *, account: str):
        self.db, self.work_item_id, self.account = db, work_item_id, account
        self._began = _clock()

    def in_budget(self) -> bool:
        return _clock() - self._began < BUDGET_S

    async def _write_event(self, fn, *args, **kw) -> None:
        await self.db.write(lambda c: fn(c, self.work_item_id, *args, **kw))

    async def _spaced(self) -> None:
        """Wait until a second has gone since the last write on this account, then
        take the slot."""
        key = (id(asyncio.get_running_loop()), self.account)
        lock = _locks.setdefault(key, asyncio.Lock())
        async with lock:
            wait = _last_write.get(self.account, -SPACING_S) + SPACING_S - _clock()
            if wait > 0:
                await _sleep(wait)
            _last_write[self.account] = _clock()

    @staticmethod
    def _waiting(intent: record.Intent) -> bool:
        f = intent.failure
        if f is None:
            return False
        at = datetime.fromisoformat(f["at"])
        if f["failure"] == "rate_limited":
            return _now() < at + timedelta(seconds=f.get("retry_after") or 0)
        return f["failure"] == "deferred" and _now() < at + DEFERRED_EVERY

    async def send(self, intent: record.Intent, *, gate: Gate, write: Write) -> Sent:
        """Send one intent if it is pending, the gate allows it and its budgets do.
        Nothing here parks or stops the item: what does not go waits for the next
        posting point."""
        if intent.state != "pending":
            return "skipped"
        if not self.in_budget():
            return "out_of_time"
        if intent.sends >= MAX_SENDS:
            await self._write_event(record.ended, intent.id, "given_up", f"{MAX_SENDS} sends")
            return "given_up"
        if self._waiting(intent):
            return "waiting"
        if (verdict := await gate(intent)) is not None:
            await self._write_event(record.ended, intent.id, *verdict)
            return verdict[0]  # type: ignore[return-value]
        await self._spaced()
        # Recorded first: a crash or cancel past here leaves the intent unclear, and the
        # next posting point reconciles before any resend.
        await self._write_event(record.send_began, intent.id)
        try:
            note = await write()
        except Exception as exc:  # noqa: BLE001 -- an OSError may have landed too
            failure = classify(exc) if isinstance(exc, ForgeError) else "unclear"
            if failure == "refused":
                await self._write_event(record.ended, intent.id, "refused", str(exc)[:300])
            else:
                await self._write_event(record.send_failed, intent.id, failure, exc)
            return failure
        await self._write_event(record.posted, intent.id, note, via="send")
        return "posted"
