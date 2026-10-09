"""Forge, CI and waits."""

from __future__ import annotations

from enum import StrEnum

from kraft.vocab.events.traits import EventTraits
from kraft.vocab.total import total

__all__ = ["ForgeEvent", "FORGE_EVENT_TRAITS"]


class ForgeEvent(StrEnum):
    MR_OPENED = "mr_opened"
    MR_CLOSED = "mr_closed"
    MR_REOPENED = "mr_reopened"
    MR_LABELS_SET = "mr_labels_set"
    EXTERNAL_WAIT_STARTED = "external_wait_started"
    EXTERNAL_WAIT_OBSERVED = "external_wait_observed"
    EXTERNAL_WAIT_REBOUNDED = "external_wait_rebounded"
    EXTERNAL_WAIT_ENDED = "external_wait_ended"
    CI_INFRA_EXHAUSTED = "ci_infra_exhausted"
    CI_RUN_ABANDONED = "ci_run_abandoned"
    CI_NOT_CONFIGURED = "ci_not_configured"
    AUTOMATED_REVIEW_NOT_CONFIGURED = "automated_review_not_configured"
    AUTOMATED_REVIEW_ERRORED = "automated_review_errored"


_PLAIN = EventTraits()

FORGE_EVENT_TRAITS = total(
    ForgeEvent,
    {
        ForgeEvent.MR_OPENED: _PLAIN,
        ForgeEvent.MR_CLOSED: _PLAIN,
        ForgeEvent.MR_REOPENED: _PLAIN,
        ForgeEvent.MR_LABELS_SET: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_STARTED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_OBSERVED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_REBOUNDED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_ENDED: _PLAIN,
        ForgeEvent.CI_INFRA_EXHAUSTED: _PLAIN,
        ForgeEvent.CI_RUN_ABANDONED: _PLAIN,
        ForgeEvent.CI_NOT_CONFIGURED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_NOT_CONFIGURED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_ERRORED: _PLAIN,
    },
    name="FORGE_EVENT_TRAITS",
)
