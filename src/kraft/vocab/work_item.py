from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache

from kraft.vocab.display import DisplayStatus
from kraft.vocab.stop import StopKind
from kraft.vocab.total import total

__all__ = [
    "WorkItemStatus",
    "Verb",
    "StatusTraits",
    "TRAITS",
    "ENDED",
    "STOPPED",
    "HOLDS_SLOT",
    "RUNNING",
    "COMMANDS_MAY_START",
    "MR_WATCHED",
    "admitting",
]


class WorkItemStatus(StrEnum):
    """`work_items.status`. Declared in the CHECK's order: the schema text is
    generated from this order and old-schema test fixtures match its lines."""

    ACTIVE = "active"
    NEEDS_HUMAN = "needs_human"
    COMPLETED = "completed"
    PAUSED = "paused"
    ABANDONED = "abandoned"
    RATE_LIMITED = "rate_limited"
    WAITING = "waiting"
    QUEUED = "queued"
    BLOCKED = "blocked"


class Verb(StrEnum):
    """The doors whose admission depends on the status."""

    PAUSE = "pause"
    RESUME = "resume"
    STEER = "steer"
    RETRY = "retry"
    RAISE_BUDGET = "raise_budget"
    SKIP = "skip"
    ESCALATE = "escalate"
    ABANDON = "abandon"
    ARCHIVE = "archive"
    UNBLOCK = "unblock"


@dataclass(frozen=True)
class StatusTraits:
    #: The badge this status shows; None for needs_human, whose badge depends
    #: on the stop (gate, escalation, kind).
    display: DisplayStatus | None
    #: The stop kind a stopped status implies when its row stored none.
    implied_stop_kind: StopKind | None = None
    ended: bool = False
    stopped: bool = False
    #: Counts against capacity and is the one status a restart resumes. Not
    #: "a walk is live": a paused item can still have one.
    holds_slot: bool = False
    #: Reads as running: a walk is or may be live.
    running: bool = False
    #: The node can still start new commands.
    commands_may_start: bool = False
    #: A closed merge request can stop it.
    mr_watched: bool = False
    #: The STATUS half of each door's admission: necessary, not sufficient.
    #: Doors keep their own second facts (a question stop, a started item, a
    #: live walk). Never exported to TS.
    admits_status: frozenset[Verb] = field(default_factory=frozenset)


S = WorkItemStatus
V = Verb

TRAITS = total(
    WorkItemStatus,
    {
        S.ACTIVE: StatusTraits(
            DisplayStatus.RUNNING,
            holds_slot=True,
            running=True,
            commands_may_start=True,
            admits_status=frozenset({V.PAUSE, V.SKIP}),
        ),
        S.NEEDS_HUMAN: StatusTraits(
            None,
            stopped=True,
            mr_watched=True,
            admits_status=frozenset(
                {V.RESUME, V.STEER, V.RETRY, V.RAISE_BUDGET, V.SKIP, V.ESCALATE, V.ABANDON}
            ),
        ),
        S.COMPLETED: StatusTraits(
            DisplayStatus.DONE, ended=True, admits_status=frozenset({V.ARCHIVE, V.ABANDON})
        ),
        S.PAUSED: StatusTraits(
            DisplayStatus.PAUSED,
            commands_may_start=True,
            admits_status=frozenset({V.RESUME, V.STEER, V.SKIP, V.ESCALATE, V.ABANDON, V.UNBLOCK}),
        ),
        S.ABANDONED: StatusTraits(
            DisplayStatus.CANCELLED, ended=True, admits_status=frozenset({V.ARCHIVE, V.ABANDON})
        ),
        S.RATE_LIMITED: StatusTraits(
            DisplayStatus.WAITING,
            StopKind.RATE_LIMIT,
            stopped=True,
            commands_may_start=True,
            admits_status=frozenset({V.PAUSE, V.ABANDON}),
        ),
        S.WAITING: StatusTraits(
            DisplayStatus.WAITING,
            StopKind.WAIT,
            stopped=True,
            running=True,
            commands_may_start=True,
            mr_watched=True,
            admits_status=frozenset({V.PAUSE, V.SKIP, V.ABANDON}),
        ),
        # Asked to start with every slot busy (`kraft.start_queue`). Nothing of
        # it runs; pause takes it out of the queue.
        S.QUEUED: StatusTraits(
            DisplayStatus.QUEUED,
            admits_status=frozenset({V.PAUSE, V.ABANDON}),
        ),
        # Started while an item it comes after is unfinished. Nothing of it
        # runs; `kraft.start_queue` moves it to `queued` when they complete.
        S.BLOCKED: StatusTraits(
            DisplayStatus.BLOCKED,
            admits_status=frozenset({V.PAUSE, V.ABANDON, V.UNBLOCK}),
        ),
    },
    name="TRAITS",
)


def _group(trait: str) -> tuple[WorkItemStatus, ...]:
    return tuple(s for s, t in TRAITS.items() if getattr(t, trait))


ENDED = _group("ended")
STOPPED = _group("stopped")
HOLDS_SLOT = _group("holds_slot")
RUNNING = _group("running")
COMMANDS_MAY_START = _group("commands_may_start")
MR_WATCHED = _group("mr_watched")


@cache
def admitting(verb: Verb) -> tuple[WorkItemStatus, ...]:
    """The statuses whose door `verb` is open (the status half only)."""
    return tuple(s for s, t in TRAITS.items() if verb in t.admits_status)
