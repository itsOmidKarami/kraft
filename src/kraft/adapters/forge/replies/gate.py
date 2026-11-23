"""Whether Kraft may write in one review-bot thread, from what the thread shows and
what the reply record holds: the per-operation checks the posting point asks before
every send (`executor.replies`). Pure: the caller reads the thread and the record."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kraft.adapters.forge.models import ForgeThread
from kraft.adapters.forge.mr import forge_time, same_login
from kraft.adapters.forge.replies import facts
from kraft.adapters.forge.replies.record import Intent

#: What a triage reply may be: `fixed` is the repair's, never the triage's.
TRIAGE_KINDS = ("declined", "answered")
_HELD = ("posted", "unclear")  # an unclear send may have landed


def thread_refusal(
    thread: ForgeThread | None,
    *,
    bots: Iterable[str],
    on: Iterable[str],
    kraft: facts.Kraft,
    needs_reply: bool = True,
) -> str | None:
    """Why no reply may go in `thread` at all: gone, resolved, locked, not started by a
    bot with replies on, or written in by someone who is neither a bot nor Kraft.
    `needs_reply` False leaves the lock out (a locked thread may still be resolved)."""
    bots, on = tuple(bots), tuple(on)
    if thread is None:
        return "not_found"
    if thread.resolved:
        return "resolved"
    if needs_reply and not thread.can_reply:
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
    cap: int,
) -> str | None:
    """`thread_refusal`, then: never after Kraft's own note, never past `cap`: Kraft's
    triage replies in one thread over the work item's life, deleted ones included (the
    `reply_cap_per_thread` policy key)."""
    if (why := thread_refusal(thread, bots=bots, on=on, kraft=kraft)) is not None:
        return why
    assert thread is not None  # thread_refusal refuses a missing one
    if kraft.wrote(thread.notes[-1]):
        return "last_is_kraft"
    if argument_count(thread, intents, kraft=kraft) >= cap:
        return "cap"
    return None


def fix_refusal(
    thread: ForgeThread | None,
    *,
    bots: Iterable[str],
    on: Iterable[str],
    kraft: facts.Kraft,
    intents: Mapping[str, Intent],
    finding: str,
) -> str | None:
    """`thread_refusal`, then: one "Fixed in" per thread and finding, ever. Neither
    the cap nor Kraft's last word applies to a fix."""
    if (why := thread_refusal(thread, bots=bots, on=on, kraft=kraft)) is not None:
        return why
    assert thread is not None  # thread_refusal refuses a missing one
    if any(
        i.payload.get("kind") == "fixed"
        and i.payload.get("thread") == thread.id
        and i.payload.get("finding") == finding
        and i.state in _HELD
        for i in intents.values()
    ):
        return "fixed_before"
    return None


@dataclass(frozen=True)
class Latest:
    """What the latest triage on the current head saw and said: its session, when it
    read the bots, and the threads it wrote a reply for."""

    session: str
    read_at: datetime
    replied: frozenset[str]


#: Kraft's clock may run ahead of the forge's: a bot note created within this of
#: `read_at` is unread.
SKEW = timedelta(seconds=5)
_NEVER = datetime.max.replace(tzinfo=UTC)


def _created(note) -> datetime:
    """When the note was created; a time that does not parse counts as after everything."""
    at = forge_time(note.created_at)
    if at is None:
        return _NEVER
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _latest_kraft_kind(
    thread: ForgeThread, intents: Mapping[str, Intent], kraft: facts.Kraft
) -> str | None:
    """The kind (`fixed`, `declined`, `answered`) of Kraft's latest note in `thread`, from
    the record, or None when the record does not hold it: only a confirmed fix is ever
    resolved, and the record says which note is one."""
    mine = [n for n in thread.notes if kraft.wrote(n)]
    if not mine:
        return None
    last = mine[-1]
    return next(
        (
            o.payload.get("kind")
            for o in intents.values()
            if o.state == "posted"
            and o.posted is not None
            and {last.id, last.rest_id} & set(o.posted["ids"])
        ),
        None,
    )


def _holds(i: Intent, thread: ForgeThread, intents: Mapping[str, Intent]) -> bool:
    """Whether a reply intent of the run for `thread` keeps Kraft from resolving it."""
    if i.state == "posted":
        return False
    if i.state == "superseded" and i.why == "later_cycle":
        # Fixed again in a later cycle, and that fix reply went out: this one is history.
        return not any(
            o.state == "posted"
            and o.payload.get("kind") == "fixed"
            and o.payload.get("thread") == thread.id
            and o.payload.get("run") == i.payload.get("run")
            and o.payload.get("cycle", 0) > i.payload.get("cycle", 0)
            for o in intents.values()
        )
    return True


def resolve_refusal(
    thread: ForgeThread | None,
    *,
    bots: Iterable[str],
    on: Iterable[str],
    kraft: facts.Kraft,
    intents: Mapping[str, Intent],
    run: int,
    node_id: str,
    latest: Latest | None,
    outstanding: Iterable[str],
    dropped: bool,
) -> str | None:
    """Why Kraft may not resolve `thread` now, or None: only a confirmed fix is resolved.
    Pure: the caller reads the thread, the record, the latest triage and the run's
    outstanding findings."""
    if thread is None:
        return "not_found"
    # `thread_refusal` first: a forge may report an already-resolved thread as not
    # resolvable, and "resolved" is the answer that leaves it alone.
    if (
        why := thread_refusal(thread, bots=bots, on=on, kraft=kraft, needs_reply=False)
    ) is not None:
        return why
    if any(
        i.payload.get("op") == "resolve"
        and i.payload.get("thread") == thread.id
        and i.payload.get("node_id") == node_id
        # A resolve that was unclear and then found the thread resolved landed too.
        and (i.state in _HELD or (i.state == "refused" and i.why == "resolved"))
        for i in intents.values()
    ):
        return "reopened"  # Kraft resolved it once, in some run; someone reopened it
    if not kraft.talks_in(thread.notes):
        return "kraft_note_gone"
    if not thread.can_resolve:
        return "cannot_resolve"
    if latest is None:
        return "no_latest_triage"
    if any(not kraft.wrote(n) and _created(n) > latest.read_at - SKEW for n in thread.notes):
        return "answer_unread"
    if dropped:
        return "findings_dropped"
    if _latest_kraft_kind(thread, intents, kraft) != "fixed" or thread.id in latest.replied:
        # A decline or an answer is a person's to resolve; a fix the triage still doubts
        # does not stand.
        return "not_fixed"
    mine = [
        i
        for i in intents.values()
        if i.payload.get("op") == "reply"
        and i.payload.get("thread") == thread.id
        and i.payload.get("node_id") == node_id
        and i.payload.get("run") == run
    ]
    if any(_holds(i, thread, intents) for i in mine):
        return "reply_held_back"
    return "finding_outstanding" if thread.id in set(outstanding) else None
