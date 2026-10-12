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
    "ItemAction",
    "DoorFacts",
    "open_doors",
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


class ItemAction(StrEnum):
    """The doors `GET /work-items` and `GET /work-items/{id}` list in
    `actions`, in the order they list them: every `Verb`, plus the doors whose
    admission the status alone does not decide (a pending gate, an ended
    item, an archived one)."""

    APPROVE = "approve"
    REJECT = "reject"
    RESUME = "resume"
    STEER = "steer"
    RETRY = "retry"
    RAISE_BUDGET = "raise_budget"
    REOPEN_MR = "reopen_mr"
    UNBLOCK = "unblock"
    SKIP = "skip"
    PAUSE = "pause"
    ESCALATE = "escalate"
    COMPLETE = "complete"
    CANCEL = "cancel"
    RESTORE = "restore"
    ARCHIVE = "archive"
    ABANDON = "abandon"


@dataclass(frozen=True)
class DoorFacts:
    """What the doors check beyond the status, read by the board routes
    (`kraft.api.routes.board.item_actions`) for `open_doors`."""

    status: WorkItemStatus
    archived: bool = False
    #: The item has a current node.
    started: bool = False
    #: Its current node is one of its chain's nodes.
    on_chain: bool = False
    #: The node a node skip would pass (the pending gate, or the current node)
    #: allows skipping.
    skippable: bool = False
    pending_gate: bool = False
    #: Stopped on a `needs_context` question.
    question: bool = False
    #: Stopped by a spend cap, the daily one included.
    budget_stop: bool = False
    #: That cap is one `budget/raise` takes: the item's own, or its policy's
    #: item-wide `budget_usd`.
    raisable: bool = False
    #: Stopped on a closed merge request it has a ref for.
    mr_closed: bool = False
    #: A walk task is registered for it (`deps.task_is_live`).
    walk_live: bool = False
    #: An escalation session is pending or running.
    escalation_live: bool = False
    #: An agent task of its current node is paused, to take a steer.
    agent_paused: bool = False
    #: It comes after an abandoned item, which will never complete.
    dead_dependency: bool = False
    #: Its gate has unresolved must-fix review threads.
    must_fix_open: bool = False


def open_doors(f: DoorFacts) -> tuple[ItemAction, ...]:
    """The doors a request would be admitted through now, in `ItemAction`
    order: the status half (`admitting`) and each route's second facts.
    Who is asking, transient races and request bodies are not item state, so
    not here. `tests/api/lifecycle_doors.json` holds it to the routes."""
    s = f.status
    held = s in (S.QUEUED, S.BLOCKED)

    def a(verb: Verb) -> bool:
        return s in admitting(verb)

    # retry: `_retry` refuses a node off the chain, a walk that is not the
    # escalation turn it would stop, and an abandoned dependency (`blocked_answer`).
    retry_ok = (
        a(V.RETRY)
        and f.on_chain
        and not (f.walk_live and not f.escalation_live)
        and not f.dead_dependency
    )
    open_ = {
        # gates.py approve_gate: a pending gate on a live, unheld item, no open must-fix.
        ItemAction.APPROVE: f.pending_gate and not held and s not in ENDED and not f.must_fix_open,
        # gates.py reject_gate.
        ItemAction.REJECT: f.pending_gate and not held and s not in ENDED,
        # resume_work_item: paused or a question; no escalation turn, no walk,
        # no abandoned dependency.
        ItemAction.RESUME: (s == S.PAUSED or (s == S.NEEDS_HUMAN and f.question))
        and not f.escalation_live
        and not f.walk_live
        and not f.dead_dependency,
        # steer_work_item: a question, or a paused item never started or with
        # a paused agent task (`paused_steer_refusal`).
        ItemAction.STEER: a(V.STEER)
        and (f.question if s == S.NEEDS_HUMAN else (not f.started or f.agent_paused)),
        ItemAction.RETRY: retry_ok,
        # raise_budget: a cap it takes, then `_refuse_a_retry_that_cannot_start`.
        ItemAction.RAISE_BUDGET: retry_ok and f.raisable and not f.walk_live,
        # reopen_mr: a closed merge request, then `_retry`.
        ItemAction.REOPEN_MR: retry_ok and f.mr_closed,
        ItemAction.UNBLOCK: a(V.UNBLOCK),
        # skip_work_item: a skippable node, no escalation turn, and no walk
        # but a running item's own.
        ItemAction.SKIP: a(V.SKIP)
        and f.skippable
        and not f.escalation_live
        and not (f.walk_live and s not in RUNNING),
        ItemAction.PAUSE: a(V.PAUSE),
        # escalate_work_item: started, not a spend-cap stop, nothing live.
        ItemAction.ESCALATE: a(V.ESCALATE)
        and f.started
        and not (s == S.NEEDS_HUMAN and f.budget_stop)
        and not f.escalation_live
        and not f.walk_live,
        # _end_work_item.
        ItemAction.COMPLETE: s not in ENDED,
        ItemAction.CANCEL: s not in ENDED,
        # restore_work_item.
        ItemAction.RESTORE: f.archived,
        # archive_work_item answers an archived item 200 and changes nothing.
        ItemAction.ARCHIVE: a(V.ARCHIVE) and not f.archived,
        ItemAction.ABANDON: a(V.ABANDON),
    }
    return tuple(action for action in ItemAction if open_[action])
