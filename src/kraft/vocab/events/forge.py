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
    #: Kraft pushed the item's branch and the head it reached is new: the
    #: moment a review bot's activity starts counting for that head
    #: (`waits.pushed_at`).
    BRANCH_PUSHED = "branch_pushed"
    EXTERNAL_WAIT_STARTED = "external_wait_started"
    EXTERNAL_WAIT_OBSERVED = "external_wait_observed"
    EXTERNAL_WAIT_REBOUNDED = "external_wait_rebounded"
    EXTERNAL_WAIT_ENDED = "external_wait_ended"
    CI_INFRA_EXHAUSTED = "ci_infra_exhausted"
    CI_RUN_ABANDONED = "ci_run_abandoned"
    CI_NOT_CONFIGURED = "ci_not_configured"
    #: The merge commit had no CI run at all once the grace was up, so the
    #: post-merge wait passed without checking anything (Kraft-rbjo3).
    POST_MERGE_CI_ABSENT = "post_merge_ci_absent"
    #: The post-merge wait's commit had its run cancelled for a newer push to the
    #: base, so the wait moved onto the base's tip, which holds the merge
    #: (Kraft-h6wi2).
    POST_MERGE_CI_MOVED = "post_merge_ci_moved"
    AUTOMATED_REVIEW_NOT_CONFIGURED = "automated_review_not_configured"
    AUTOMATED_REVIEW_ERRORED = "automated_review_errored"
    #: A review wait settled: which bots were heard since the push and which
    #: never posted. `kraft admin doctor` reads a bot's run of silences.
    AUTOMATED_REVIEW_SETTLED = "automated_review_settled"
    MERGE_QUEUE_DROPPED = "merge_queue_dropped"
    #: Kraft rewrote the head the final review approved itself -- a clean
    #: rebase, a pointer bump -- and pinned the new one (`kraft.reviewed_head`).
    REVIEWED_HEAD_REPINNED = "reviewed_head_repinned"
    #: The merge would have pushed a head the final review did not approve.
    MERGE_REFUSED_UNREVIEWED = "merge_refused_unreviewed"
    #: The forge merged a head the final review did not approve: Kraft asked for
    #: the merge, and a commit reached the merge request before it landed.
    MERGE_LANDED_UNREVIEWED = "merge_landed_unreviewed"


_PLAIN = EventTraits()

FORGE_EVENT_TRAITS = total(
    ForgeEvent,
    {
        ForgeEvent.MR_OPENED: _PLAIN,
        ForgeEvent.MR_CLOSED: _PLAIN,
        ForgeEvent.MR_REOPENED: _PLAIN,
        ForgeEvent.MR_LABELS_SET: _PLAIN,
        ForgeEvent.BRANCH_PUSHED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_STARTED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_OBSERVED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_REBOUNDED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_ENDED: _PLAIN,
        ForgeEvent.CI_INFRA_EXHAUSTED: _PLAIN,
        ForgeEvent.CI_RUN_ABANDONED: _PLAIN,
        ForgeEvent.CI_NOT_CONFIGURED: _PLAIN,
        ForgeEvent.POST_MERGE_CI_ABSENT: _PLAIN,
        ForgeEvent.POST_MERGE_CI_MOVED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_NOT_CONFIGURED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_ERRORED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_SETTLED: _PLAIN,
        ForgeEvent.MERGE_QUEUE_DROPPED: _PLAIN,
        ForgeEvent.REVIEWED_HEAD_REPINNED: _PLAIN,
        ForgeEvent.MERGE_REFUSED_UNREVIEWED: _PLAIN,
        ForgeEvent.MERGE_LANDED_UNREVIEWED: _PLAIN,
    },
    name="FORGE_EVENT_TRAITS",
)
