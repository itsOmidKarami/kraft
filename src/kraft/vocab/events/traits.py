"""What code needs to know about an event type beyond its name."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["EventTraits"]


@dataclass(frozen=True)
class EventTraits:
    #: A person closed out a run of stuck-ness: the escalation cap's scan
    #: stops here (`executor.gates`, `analytics`).
    run_boundary: bool = False
    #: Closes a pending gate.
    closes_gate: bool = False
    #: Starts the item's work over: a retry, a fork, a base-change restart.
    restarts_run: bool = False
