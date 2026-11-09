from __future__ import annotations

from enum import StrEnum


class DisplayStatus(StrEnum):
    """The badge the server derives for a work item (`board.display_status`)."""

    ARCHIVED = "archived"
    DONE = "done"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    RUNNING = "running"
    WAITING = "waiting"
    QUEUED = "queued"
    BLOCKED = "blocked"
    NEEDS_YOU = "needs_you"
    ESCALATED = "escalated"
    FAILED = "failed"


class GroupPhase(StrEnum):
    """Where an item's slice group stands, as its views draw it (`board.group_view`):
    deciding whether to split, building the plan's shared groundwork in the item
    itself, its slices running as child items, joining them back, or joined."""

    DECIDING = "deciding"
    GROUNDWORK = "groundwork"
    SLICES = "slices"
    JOINING = "joining"
    JOINED = "joined"
