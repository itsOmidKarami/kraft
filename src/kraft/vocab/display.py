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
    NEEDS_YOU = "needs_you"
    ESCALATED = "escalated"
    FAILED = "failed"
