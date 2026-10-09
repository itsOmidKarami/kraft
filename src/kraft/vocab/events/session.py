"""Agent sessions: one worker session's life."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["SessionEvent", "SESSION_EVENT_TRAITS"]


class SessionEvent(StrEnum):
    WORKER_SESSION_CREATED = "worker_session_created"
    WORKER_SESSION_STARTED = "worker_session_started"
    WORKER_SESSION_EXITED = "worker_session_exited"
    WORKER_SESSION_PAUSED = "worker_session_paused"
    AGENT_SESSION_RESUMED = "agent_session_resumed"
    SESSION_REATTACHED = "session_reattached"
    SESSION_UNKNOWN = "session_unknown"
    BACKGROUND_JOBS_ABANDONED = "background_jobs_abandoned"
    PERMISSION_DECISION = "permission_decision"


_PLAIN = EventTraits()

SESSION_EVENT_TRAITS = total(
    SessionEvent,
    {
        SessionEvent.WORKER_SESSION_CREATED: _PLAIN,
        SessionEvent.WORKER_SESSION_STARTED: _PLAIN,
        SessionEvent.WORKER_SESSION_EXITED: _PLAIN,
        SessionEvent.WORKER_SESSION_PAUSED: _PLAIN,
        SessionEvent.AGENT_SESSION_RESUMED: _PLAIN,
        SessionEvent.SESSION_REATTACHED: _PLAIN,
        SessionEvent.SESSION_UNKNOWN: _PLAIN,
        SessionEvent.BACKGROUND_JOBS_ABANDONED: _PLAIN,
        SessionEvent.PERMISSION_DECISION: _PLAIN,
    },
    name="SESSION_EVENT_TRAITS",
)
