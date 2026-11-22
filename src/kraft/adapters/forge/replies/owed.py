"""Whether a bot thread is owed a look: the conversation facts, pure. Every comparison reads
`created_at`, never `updated_at` (a GitLab resolve moves a note's `updated_at`)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from kraft.adapters.forge.models import ForgeNote
from kraft.adapters.forge.mr import forge_time, same_login
from kraft.adapters.forge.replies import facts


@dataclass(frozen=True)
class ReplyWait:
    """What a reply round's review wait waits for: the threads it was started for and how
    long to wait for each answer."""

    threads: tuple[str, ...]
    timeout: timedelta
    #: When the round the wait belongs to ends (its start plus the reply timeout): it bounds
    #: the round's CI look (`run.ci_poll`), which never stops the item. None for a bare wait.
    deadline: datetime | None = None


@dataclass(frozen=True)
class Standing:
    kraft_at: datetime  # Kraft's latest note, by created_at
    deadline: datetime  # kraft_at plus the reply timeout
    answered_at: datetime | None  # the first author's note after Kraft's latest, by created_at


def standing(
    notes: Sequence[ForgeNote],
    *,
    on: Iterable[str],
    bots: Iterable[str],
    kraft: facts.Kraft,
    timeout: timedelta,
    resolved: bool = False,
) -> Standing | None:
    """None unless this is a thread the conversation is about: open, first note a bot's with
    replies on, no person in it, a Kraft note in it."""
    on, bots = tuple(on), tuple(bots)
    if resolved or not notes or not facts.is_bot_note(notes[0], on):
        return None
    if any(not facts.is_bot_note(n, bots) and not kraft.wrote(n) for n in notes):
        return None
    last = max((i for i, n in enumerate(notes) if kraft.wrote(n)), default=None)
    if last is None or (kraft_at := forge_time(notes[last].created_at)) is None:
        return None
    answer = next(
        (
            at
            for n in notes[last + 1 :]
            if same_login(n.author, notes[0].author) and (at := forge_time(n.created_at))
        ),
        None,
    )
    return Standing(kraft_at, kraft_at + timeout, answer)


def is_owed(s: Standing, *, read_at: datetime | None, now: datetime) -> bool:
    """Answered after the triage read the thread (or no triage has: any answer), or not
    answered yet and not past its deadline."""
    if s.answered_at is not None:
        return read_at is None or s.answered_at > read_at
    return now < s.deadline


def is_done(s: Standing | None, *, now: datetime) -> bool:
    """For the reply wait: nothing left to wait for in this thread."""
    return s is None or s.answered_at is not None or now >= s.deadline
