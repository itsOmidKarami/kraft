from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from kraft.vocab.total import total

__all__ = ["StopKind", "StopTraits", "STOP_TRAITS", "FAILURE_KINDS"]


class StopKind(StrEnum):
    """Why a stopped work item is stopped (`work_items.stop_kind`, `stop.kind`)."""

    GATE = "gate"
    QUESTION = "question"
    CAP = "cap"
    BUDGET = "budget"
    FAILED = "failed"
    CONFLICT = "conflict"
    MR_CLOSED = "mr_closed"
    CONFIG = "config"
    INFRA = "infra"
    STUCK = "stuck"
    WAIT = "wait"
    RATE_LIMIT = "rate_limit"


@dataclass(frozen=True)
class StopTraits:
    #: The value of the `WorkItemStatus` the kind belongs to. A str, not the
    #: enum: `work_item.py` imports this module.
    status: str
    #: False only for `gate`: the API reports it, nothing writes the column.
    stored: bool = True
    #: Shows as `failed` rather than `needs_you` (`board.display_status`).
    failure: bool = False


_NH = "needs_human"

STOP_TRAITS = total(
    StopKind,
    {
        StopKind.GATE: StopTraits(_NH, stored=False),
        StopKind.QUESTION: StopTraits(_NH),
        StopKind.CAP: StopTraits(_NH),
        StopKind.BUDGET: StopTraits(_NH),
        StopKind.FAILED: StopTraits(_NH, failure=True),
        StopKind.CONFLICT: StopTraits(_NH),
        StopKind.MR_CLOSED: StopTraits(_NH),
        StopKind.CONFIG: StopTraits(_NH, failure=True),
        StopKind.INFRA: StopTraits(_NH, failure=True),
        StopKind.STUCK: StopTraits(_NH),
        StopKind.WAIT: StopTraits("waiting"),
        StopKind.RATE_LIMIT: StopTraits("rate_limited"),
    },
    name="STOP_TRAITS",
)

FAILURE_KINDS: tuple[StopKind, ...] = tuple(k for k, t in STOP_TRAITS.items() if t.failure)
