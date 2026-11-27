"""One posting point's sends, and the class of a failed write.

`classify` decides whether a write that failed may have landed (so it is reconciled
before any resend), may succeed later, or never will. `PostingPoint` sends within its
budgets: at most three counted sends an intent, a wait after a rate limit or a
deferred failure, a second between writes on one forge account, and 60 seconds a
posting point."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from kraft.adapters.forge.git import CliUnavailable
from kraft.adapters.forge.models import ForgeError, ForgeNote, Posted, failure_cause
from kraft.adapters.forge.mr import same_login
from kraft.adapters.forge.replies import record
from kraft.adapters.forge.replies.facts import body_hash, has_summary_marker, marker_of

MAX_SENDS = 3
DEFERRED_EVERY = timedelta(minutes=5)
BUDGET_S = 60.0
SPACING_S = 1.0

#: Item 2's per-operation gate: None to go ahead, or `("refused" | "superseded", why)`.
Gate = Callable[[record.Intent], Awaitable[tuple[str, str] | None]]
Write = Callable[[], Awaitable[Posted]]
#: Where an intent's copy would be: the thread's notes, or the merge request's comments.
Notes = Callable[[record.Intent], Awaitable[Sequence[ForgeNote]]]
Delete = Callable[[ForgeNote], Awaitable[None]]
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


def _copies(
    intent: record.Intent, found: Iterable[ForgeNote], viewer: str, work_item_id: str
) -> list[ForgeNote]:
    """The viewer's notes in `found` that are this intent's write. A marker on anyone
    else's note is nothing."""
    mine = [n for n in found if same_login(n.author, viewer)]
    if intent.payload["op"] == "summary":
        return [
            n
            for n in mine
            if has_summary_marker(n.body, work_item_id)
            and body_hash(n.body) == intent.payload["body_sha256"]
        ]
    return [n for n in mine if marker_of(n.body) == intent.id]


def _ids(note: ForgeNote) -> tuple[str, ...]:
    return tuple(dict.fromkeys((note.rest_id, note.id)))  # GitLab: both are one id


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

    async def reconcile(
        self, intents: Iterable[record.Intent], *, viewer: str, notes: Notes
    ) -> None:
        """First at a posting point, over every `unclear` intent of the item: read where
        its copy would be, and record it posted when a note the viewer wrote carries its
        marker (a summary: its marker and body hash), else not found, which makes the
        intent pending again for the caller's gate to decide any resend. A read that
        fails leaves it unclear."""
        for intent in intents:
            if intent.state != "unclear" or not self.in_budget():
                continue
            try:
                found = await notes(intent)
            except ForgeError:
                continue  # still unclear: the next posting point reads again
            copies = sorted(
                _copies(intent, found, viewer, self.work_item_id), key=lambda n: n.created_at
            )
            if copies:
                first = copies[0]
                posted = Posted(_ids(first), first.created_at, first.body)
                await self._write_event(record.posted, intent.id, posted, via="reconcile")
            else:
                await self._write_event(record.not_found, intent.id)

    async def sweep_duplicates(
        self, intents: Iterable[record.Intent], *, viewer: str, notes: Notes, delete: Delete
    ) -> None:
        """At a later posting point, over every intent that was ever unclear and is now
        posted: when two or more notes the viewer wrote carry its marker and the same
        returned-body hash, keep the earliest by `created_at`, delete the others (a 404
        counts as deleted), and move the record to the survivor when it is not the note
        the record holds. Reads and deletes stop at the time budget; deletes are spaced.

        The horizon is the caller's: pass the intents of the node's current run and
        those of the run before it for one posting point (the late-copy window), never
        the item's whole history."""
        for intent in intents:
            if not intent.ever_unclear or intent.state != "posted":
                continue
            if not self.in_budget():
                return
            try:
                found = await notes(intent)
            except ForgeError:
                continue
            groups: dict[str, list[ForgeNote]] = {}
            for n in _copies(intent, found, viewer, self.work_item_id):
                groups.setdefault(body_hash(n.body), []).append(n)
            for group in groups.values():
                if len(group) > 1:
                    await self._delete_later_copies(
                        intent, sorted(group, key=lambda n: n.created_at), delete
                    )

    async def _delete_later_copies(
        self, intent: record.Intent, group: list[ForgeNote], delete: Delete
    ) -> None:
        first, *later = group
        gone: list[str] = []
        for n in later:
            if not self.in_budget():
                break
            await self._spaced()
            try:
                await delete(n)
            except ForgeError as exc:
                if exc.status != 404:
                    continue  # left for the next posting point
            gone.append(n.id)
        if not gone:
            return
        await self._write_event(record.duplicate_deleted, intent.id, _ids(first), gone)
        held = set((intent.posted or {}).get("ids", ()))
        if not held & set(_ids(first)):
            posted = Posted(_ids(first), first.created_at, first.body)
            await self._write_event(record.posted, intent.id, posted, via="duplicate")
