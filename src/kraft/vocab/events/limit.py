"""Caps, budgets and limits."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["LimitEvent", "LIMIT_EVENT_TRAITS"]


class LimitEvent(StrEnum):
    TIME_CAP_REACHED = "time_cap_reached"
    SCOPE_BUDGET_REACHED = "scope_budget_reached"
    BUDGET_CHANGED = "budget_changed"
    BUDGET_RAISED = "budget_raised"
    SPEND_UNPRICED = "spend_unpriced"
    CAP_COUNTERS_RESET = "cap_counters_reset"
    RATE_LIMIT_HIT = "rate_limit_hit"
    LAUNCH_FALLBACK = "launch_fallback"
    LAUNCH_FALLBACK_EXHAUSTED = "launch_fallback_exhausted"


_PLAIN = EventTraits()

LIMIT_EVENT_TRAITS = total(
    LimitEvent,
    {
        LimitEvent.TIME_CAP_REACHED: _PLAIN,
        LimitEvent.SCOPE_BUDGET_REACHED: _PLAIN,
        LimitEvent.BUDGET_CHANGED: _PLAIN,
        LimitEvent.BUDGET_RAISED: _PLAIN,
        LimitEvent.SPEND_UNPRICED: _PLAIN,
        LimitEvent.CAP_COUNTERS_RESET: _PLAIN,
        LimitEvent.RATE_LIMIT_HIT: _PLAIN,
        LimitEvent.LAUNCH_FALLBACK: _PLAIN,
        LimitEvent.LAUNCH_FALLBACK_EXHAUSTED: _PLAIN,
    },
    name="LIMIT_EVENT_TRAITS",
)
