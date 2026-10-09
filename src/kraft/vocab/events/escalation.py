"""Escalation: an agent asked to help with a stop."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["EscalationEvent", "ESCALATION_EVENT_TRAITS"]


class EscalationEvent(StrEnum):
    ESCALATION_MESSAGE = "escalation_message"
    STUCK_ESCALATION_STARTED = "stuck_escalation_started"
    STUCK_ESCALATION_FINISHED = "stuck_escalation_finished"
    WORK_ITEM_AUTO_ESCALATE_SKIPPED = "work_item_auto_escalate_skipped"
    WORK_ITEM_AUTO_ESCALATE_CAPPED = "work_item_auto_escalate_capped"
    WORK_ITEM_SELF_RETRY_REQUESTED = "work_item_self_retry_requested"
    WORK_ITEM_SELF_RETRY_DROPPED = "work_item_self_retry_dropped"


_PLAIN = EventTraits()

ESCALATION_EVENT_TRAITS = total(
    EscalationEvent,
    {
        EscalationEvent.ESCALATION_MESSAGE: _PLAIN,
        EscalationEvent.STUCK_ESCALATION_STARTED: _PLAIN,
        EscalationEvent.STUCK_ESCALATION_FINISHED: _PLAIN,
        EscalationEvent.WORK_ITEM_AUTO_ESCALATE_SKIPPED: _PLAIN,
        EscalationEvent.WORK_ITEM_AUTO_ESCALATE_CAPPED: _PLAIN,
        EscalationEvent.WORK_ITEM_SELF_RETRY_REQUESTED: _PLAIN,
        EscalationEvent.WORK_ITEM_SELF_RETRY_DROPPED: _PLAIN,
    },
    name="ESCALATION_EVENT_TRAITS",
)
