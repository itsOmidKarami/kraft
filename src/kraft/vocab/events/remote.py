"""Remote workers."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["REMOTE_EVENT_TRAITS", "RemoteEvent"]


class RemoteEvent(StrEnum):
    STEP_WAITING = "remote_step_waiting"
    DOCS_CONFLICT = "remote_docs_conflict"
    STEP_GIVEN_UP = "remote_step_given_up"
    MACHINE_DISCONNECTED = "remote_machine_disconnected"
    MACHINE_RECONNECTED = "remote_machine_reconnected"


_PLAIN = EventTraits()

REMOTE_EVENT_TRAITS = total(
    RemoteEvent,
    {
        RemoteEvent.STEP_WAITING: _PLAIN,
        RemoteEvent.DOCS_CONFLICT: _PLAIN,
        RemoteEvent.STEP_GIVEN_UP: _PLAIN,
        RemoteEvent.MACHINE_DISCONNECTED: _PLAIN,
        RemoteEvent.MACHINE_RECONNECTED: _PLAIN,
    },
    name="REMOTE_EVENT_TRAITS",
)
