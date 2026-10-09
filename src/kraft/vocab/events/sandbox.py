"""Sandbox."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["SandboxEvent", "SANDBOX_EVENT_TRAITS"]


class SandboxEvent(StrEnum):
    EGRESS_REFUSED = "sandbox_egress_refused"
    OOM_KILLED = "sandbox_oom_killed"
    BRANCH_NOT_SYNCED = "sandbox_branch_not_synced"
    KIT_RESOLVED = "sandbox_kit_resolved"


_PLAIN = EventTraits()

SANDBOX_EVENT_TRAITS = total(
    SandboxEvent,
    {
        SandboxEvent.EGRESS_REFUSED: _PLAIN,
        SandboxEvent.OOM_KILLED: _PLAIN,
        SandboxEvent.BRANCH_NOT_SYNCED: _PLAIN,
        SandboxEvent.KIT_RESOLVED: _PLAIN,
    },
    name="SANDBOX_EVENT_TRAITS",
)
