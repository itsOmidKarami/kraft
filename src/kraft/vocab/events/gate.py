"""Gates and review: the events of a human decision point."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["GateEvent", "GATE_EVENT_TRAITS"]


class GateEvent(StrEnum):
    REQUESTED = "gate_requested"
    APPROVED = "gate_approved"
    REJECTED = "gate_rejected"
    REOPENED = "gate_reopened"
    AUTO_REVIEW_STARTED = "gate_auto_review_started"
    AUTO_REVIEW_SKIPPED = "gate_auto_review_skipped"
    AUTO_REVIEW_DISCARDED = "gate_auto_review_discarded"
    CHAIN_REVISION_SHOWN = "chain_revision_shown"
    CHAIN_REVISION_UNCHANGED = "chain_revision_unchanged"
    CHAIN_REVISED = "chain_revised"
    ARTIFACT_REFUSED = "artifact_refused"
    REVIEW_SUBMITTED = "review_submitted"
    THREAD_UPDATED = "thread_updated"
    REWIND_REQUESTED = "rewind_requested"
    REWIND_CANCELLED = "rewind_cancelled"
    REPLY_AGENT_WROTE = "reply_agent_wrote"
    REPLY_AGENT_SKIPPED = "reply_agent_skipped"
    REPLY_AGENT_FAILED = "reply_agent_failed"


_PLAIN = EventTraits()

GATE_EVENT_TRAITS = total(
    GateEvent,
    {
        GateEvent.REQUESTED: _PLAIN,
        GateEvent.APPROVED: EventTraits(run_boundary=True, closes_gate=True),
        GateEvent.REJECTED: EventTraits(run_boundary=True, closes_gate=True),
        GateEvent.REOPENED: _PLAIN,
        GateEvent.AUTO_REVIEW_STARTED: _PLAIN,
        GateEvent.AUTO_REVIEW_SKIPPED: _PLAIN,
        GateEvent.AUTO_REVIEW_DISCARDED: _PLAIN,
        GateEvent.CHAIN_REVISION_SHOWN: _PLAIN,
        GateEvent.CHAIN_REVISION_UNCHANGED: _PLAIN,
        GateEvent.CHAIN_REVISED: _PLAIN,
        GateEvent.ARTIFACT_REFUSED: _PLAIN,
        GateEvent.REVIEW_SUBMITTED: _PLAIN,
        GateEvent.THREAD_UPDATED: _PLAIN,
        GateEvent.REWIND_REQUESTED: _PLAIN,
        GateEvent.REWIND_CANCELLED: _PLAIN,
        GateEvent.REPLY_AGENT_WROTE: _PLAIN,
        GateEvent.REPLY_AGENT_SKIPPED: _PLAIN,
        GateEvent.REPLY_AGENT_FAILED: _PLAIN,
    },
    name="GATE_EVENT_TRAITS",
)
