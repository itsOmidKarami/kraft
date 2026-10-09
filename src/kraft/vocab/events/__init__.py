"""Event types, one `StrEnum` per family (see the event-types spec).

The families are the sections of the docs' events page. A type's stored
string never changes; `EVENT_TRAITS` says what shared code needs to know
about each one.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from kraft.vocab.events.chain import CHAIN_EVENT_TRAITS, ChainEvent
from kraft.vocab.events.escalation import ESCALATION_EVENT_TRAITS, EscalationEvent
from kraft.vocab.events.forge import FORGE_EVENT_TRAITS, ForgeEvent
from kraft.vocab.events.gate import GATE_EVENT_TRAITS, GateEvent
from kraft.vocab.events.limit import LIMIT_EVENT_TRAITS, LimitEvent
from kraft.vocab.events.sandbox import SANDBOX_EVENT_TRAITS, SandboxEvent
from kraft.vocab.events.session import SESSION_EVENT_TRAITS, SessionEvent
from kraft.vocab.events.settings import SETTINGS_EVENT_TRAITS, SettingsEvent
from kraft.vocab.events.traits import EventTraits
from kraft.vocab.events.work_item import WORK_ITEM_EVENT_TRAITS, WorkItemEvent

__all__ = [
    "BY_VALUE",
    "EVENT_TRAITS",
    "EVENT_TYPES",
    "FAMILIES",
    "GATE_CLOSED",
    "RESTARTS_RUN",
    "RUN_BOUNDARY",
    "ChainEvent",
    "EscalationEvent",
    "EventTraits",
    "EventType",
    "ForgeEvent",
    "GateEvent",
    "LimitEvent",
    "SandboxEvent",
    "SessionEvent",
    "SettingsEvent",
    "WorkItemEvent",
]

type EventType = (
    WorkItemEvent
    | ChainEvent
    | GateEvent
    | SessionEvent
    | LimitEvent
    | EscalationEvent
    | ForgeEvent
    | SandboxEvent
    | SettingsEvent
)

#: In the order of the docs page's sections.
FAMILIES: tuple[type[StrEnum], ...] = (
    WorkItemEvent,
    ChainEvent,
    GateEvent,
    SessionEvent,
    LimitEvent,
    EscalationEvent,
    ForgeEvent,
    SandboxEvent,
    SettingsEvent,
)


def by_value(families: tuple[type[StrEnum], ...]) -> Mapping[str, StrEnum]:
    """Every member by its stored string. Two families sharing a string is a
    mistake no reader could tell apart, so it raises, naming both."""
    found: dict[str, StrEnum] = {}
    for family in families:
        for member in family:
            other = found.get(member.value)
            if other is not None:
                raise ValueError(
                    f"{type(other).__name__}.{other.name} and "
                    f"{family.__name__}.{member.name} are both {member.value!r}"
                )
            found[member.value] = member
    return MappingProxyType(found)


BY_VALUE: Mapping[str, EventType] = by_value(FAMILIES)
EVENT_TYPES: tuple[EventType, ...] = tuple(BY_VALUE.values())

EVENT_TRAITS: Mapping[EventType, EventTraits] = MappingProxyType(
    {
        **WORK_ITEM_EVENT_TRAITS,
        **CHAIN_EVENT_TRAITS,
        **GATE_EVENT_TRAITS,
        **SESSION_EVENT_TRAITS,
        **LIMIT_EVENT_TRAITS,
        **ESCALATION_EVENT_TRAITS,
        **FORGE_EVENT_TRAITS,
        **SANDBOX_EVENT_TRAITS,
        **SETTINGS_EVENT_TRAITS,
    }
)

RUN_BOUNDARY: tuple[EventType, ...] = tuple(m for m, t in EVENT_TRAITS.items() if t.run_boundary)
GATE_CLOSED: tuple[EventType, ...] = tuple(m for m, t in EVENT_TRAITS.items() if t.closes_gate)
RESTARTS_RUN: tuple[EventType, ...] = tuple(m for m, t in EVENT_TRAITS.items() if t.restarts_run)
