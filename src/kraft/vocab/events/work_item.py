"""Work item: filing, stopping, resuming, ending and editing an item."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["WorkItemEvent", "WORK_ITEM_EVENT_TRAITS"]


class WorkItemEvent(StrEnum):
    CREATED = "work_item_created"
    ATTACHMENTS = "work_item_attachments"
    RESUMED = "work_item_resumed"
    PAUSE_REQUESTED = "pause_requested"
    BLOCKED_BY_DEPENDENCY = "work_item_blocked_by_dependency"
    PAUSED_BY_BROKEN_BASE = "paused_by_broken_base"
    NEEDS_HUMAN = "work_item_needs_human"
    WAITING = "work_item_waiting"
    RATE_LIMITED = "work_item_rate_limited"
    RETRIED = "work_item_retried"
    COMPLETED = "work_item_completed"
    MANUALLY_COMPLETED = "work_item_manually_completed"
    CANCELLED = "work_item_cancelled"
    ABANDONED = "work_item_abandoned"
    ARCHIVED = "work_item_archived"
    RESTORED = "work_item_restored"
    TITLE_EDITED = "work_item_title_edited"
    DESCRIPTION_EDITED = "work_item_description_edited"
    PLAN_PROGRESS = "plan_progress"
    STEER_CONTEXT_SET = "steer_context_set"
    STEER_UNDELIVERED = "steer_undelivered"


_PLAIN = EventTraits()

WORK_ITEM_EVENT_TRAITS = total(
    WorkItemEvent,
    {
        WorkItemEvent.CREATED: EventTraits(run_boundary=True),
        WorkItemEvent.ATTACHMENTS: _PLAIN,
        WorkItemEvent.RESUMED: EventTraits(run_boundary=True),
        WorkItemEvent.PAUSE_REQUESTED: EventTraits(run_boundary=True),
        WorkItemEvent.BLOCKED_BY_DEPENDENCY: _PLAIN,
        WorkItemEvent.PAUSED_BY_BROKEN_BASE: _PLAIN,
        WorkItemEvent.NEEDS_HUMAN: _PLAIN,
        WorkItemEvent.WAITING: _PLAIN,
        WorkItemEvent.RATE_LIMITED: _PLAIN,
        WorkItemEvent.RETRIED: EventTraits(run_boundary=True, restarts_run=True),
        WorkItemEvent.COMPLETED: EventTraits(run_boundary=True, closes_gate=True),
        WorkItemEvent.MANUALLY_COMPLETED: _PLAIN,
        WorkItemEvent.CANCELLED: _PLAIN,
        WorkItemEvent.ABANDONED: EventTraits(run_boundary=True, closes_gate=True),
        WorkItemEvent.ARCHIVED: _PLAIN,
        WorkItemEvent.RESTORED: EventTraits(run_boundary=True),
        WorkItemEvent.TITLE_EDITED: _PLAIN,
        WorkItemEvent.DESCRIPTION_EDITED: _PLAIN,
        WorkItemEvent.PLAN_PROGRESS: _PLAIN,
        WorkItemEvent.STEER_CONTEXT_SET: _PLAIN,
        WorkItemEvent.STEER_UNDELIVERED: _PLAIN,
    },
    name="WORK_ITEM_EVENT_TRAITS",
)
