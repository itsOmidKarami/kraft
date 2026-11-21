"""Whether Kraft may write in one review-bot thread, from what the thread shows and
what the reply record holds: the per-operation checks the posting point asks before
every send (`executor.replies`). Pure: the caller reads the thread and the record."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from kraft.adapters.forge.models import ForgeThread
from kraft.adapters.forge.mr import same_login
from kraft.adapters.forge.replies import facts
from kraft.adapters.forge.replies.record import Intent

#: Kraft's triage replies in one thread over the work item's life, deleted ones
#: included; Part 2's third item makes it the `reply_cap_per_thread` policy key.
REPLY_CAP_PER_THREAD = 2
#: What a triage reply may be: `fixed` is the repair's, never the triage's.
TRIAGE_KINDS = ("declined", "answered")
_HELD = ("posted", "unclear")  # an unclear send may have landed


def thread_refusal(
    thread: ForgeThread | None, *, bots: Iterable[str], on: Iterable[str], kraft: facts.Kraft
) -> str | None:
    """Why no reply may go in `thread` at all: gone, resolved, locked, not started by a
    bot with replies on, or written in by someone who is neither a bot nor Kraft."""
    bots, on = tuple(bots), tuple(on)
    if thread is None:
        return "not_found"
    if thread.resolved:
        return "resolved"
    if not thread.can_reply:
        return "cannot_reply"
    first = thread.notes[0] if thread.notes else None
    if first is None or not facts.is_bot_note(first, on):
        if first is not None and any(same_login(first.author, b) for b in on):
            return "bot posts as a user"
        return "not_bot_started"
    if any(not facts.is_bot_note(n, bots) and not kraft.wrote(n) for n in thread.notes):
        return "person_written"
    return None


def argument_count(
    thread: ForgeThread, intents: Mapping[str, Intent], *, kraft: facts.Kraft
) -> int:
    """Kraft's triage replies in `thread` over every run of the item: each one the
    record holds as posted or unclear, and each marked note of the viewer's that the
    record does not hold as a fix reply."""
    counted = {
        i.id
        for i in intents.values()
        if i.payload.get("op") == "reply"
        and i.payload.get("kind") in TRIAGE_KINDS
        and i.payload.get("thread") == thread.id
        and i.state in _HELD
    }
    for n in thread.notes:
        intent = facts.marker_of(n.body)
        mine = kraft.viewer and same_login(n.author, kraft.viewer)
        if intent and mine and intent.startswith(f"{kraft.work_item_id}-"):
            held = intents.get(intent)
            if held is None or held.payload.get("kind") in TRIAGE_KINDS:
                counted.add(intent)
    return len(counted)


def triage_refusal(
    thread: ForgeThread | None,
    *,
    bots: Iterable[str],
    on: Iterable[str],
    kraft: facts.Kraft,
    intents: Mapping[str, Intent],
    cap: int = REPLY_CAP_PER_THREAD,
) -> str | None:
    """`thread_refusal`, then: never after Kraft's own note, never past the cap."""
    if (why := thread_refusal(thread, bots=bots, on=on, kraft=kraft)) is not None:
        return why
    assert thread is not None  # thread_refusal refuses a missing one
    if kraft.wrote(thread.notes[-1]):
        return "last_is_kraft"
    if argument_count(thread, intents, kraft=kraft) >= cap:
        return "cap"
    return None
