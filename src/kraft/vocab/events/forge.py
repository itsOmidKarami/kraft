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
    #: The hash of a description Kraft is about to send: recorded before the write,
    #: so a write whose answer was lost still reads as Kraft's own text.
    MR_DESCRIPTION_WRITTEN = "mr_description_written"
    #: The forge's description is not one Kraft wrote, so Kraft left it alone.
    MR_DESCRIPTION_KEPT = "mr_description_kept"
    #: Kraft pushed the item's branch and the head it reached is new: the
    #: moment a review bot's activity starts counting for that head
    #: (`waits.pushed_at`).
    BRANCH_PUSHED = "branch_pushed"
    EXTERNAL_WAIT_STARTED = "external_wait_started"
    EXTERNAL_WAIT_OBSERVED = "external_wait_observed"
    EXTERNAL_WAIT_REBOUNDED = "external_wait_rebounded"
    EXTERNAL_WAIT_ENDED = "external_wait_ended"
    #: A forge call got no answer because of the network: the item parks and
    #: tries again (`waits.note_no_answer`). With a `reason`, it gave up.
    FORGE_NO_ANSWER = "forge_no_answer"
    #: The forge answered that task again after `FORGE_NO_ANSWER`.
    FORGE_ANSWERED = "forge_answered"
    CI_INFRA_EXHAUSTED = "ci_infra_exhausted"
    CI_RUN_ABANDONED = "ci_run_abandoned"
    #: A fix-loop repair asked Kraft to re-run the CI jobs that failed outside
    #: the code (`kraft item rerun-ci`), so the round counts as a change.
    CI_RERUN_REQUESTED = "ci_rerun_requested"
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
    #: Kraft is about to write a reply or the summary comment: recorded before any write.
    REPLY_INTENDED = "reply_intended"
    #: Recorded just before a write: if no outcome follows, the send reads as unclear and
    #: is reconciled. Not a failure and not a counted send.
    REPLY_SEND_STARTED = "reply_send_started"
    #: A send that did not post: it may have landed (`unclear`), hit a rate limit or
    #: cannot succeed now (`deferred`).
    REPLY_SEND_FAILED = "reply_send_failed"
    #: The write landed, a reconcile found its copy, or a duplicate check moved it.
    REPLY_POSTED = "reply_posted"
    #: A reconcile read the thread and found no copy of an unclear write.
    REPLY_NOT_FOUND = "reply_not_found"
    #: The intent will never be sent: refused, superseded or given up.
    REPLY_ENDED = "reply_ended"
    #: A duplicate check deleted a later copy of a reply and kept the earliest.
    REPLY_DUPLICATE_DELETED = "reply_duplicate_deleted"
    #: A node's review wait names a bot with replies on, but nothing in the node can
    #: act on a reply (no fix loop, no triage, a multi-repo item), the forge cannot
    #: be resolved, or Kraft writes as one of the bots. Once per node run and reason.
    REPLIES_INERT = "replies_inert"
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
        ForgeEvent.MR_DESCRIPTION_WRITTEN: _PLAIN,
        ForgeEvent.MR_DESCRIPTION_KEPT: _PLAIN,
        ForgeEvent.BRANCH_PUSHED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_STARTED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_OBSERVED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_REBOUNDED: _PLAIN,
        ForgeEvent.FORGE_NO_ANSWER: _PLAIN,
        ForgeEvent.FORGE_ANSWERED: _PLAIN,
        ForgeEvent.EXTERNAL_WAIT_ENDED: _PLAIN,
        ForgeEvent.CI_INFRA_EXHAUSTED: _PLAIN,
        ForgeEvent.CI_RUN_ABANDONED: _PLAIN,
        ForgeEvent.CI_RERUN_REQUESTED: _PLAIN,
        ForgeEvent.CI_NOT_CONFIGURED: _PLAIN,
        ForgeEvent.POST_MERGE_CI_ABSENT: _PLAIN,
        ForgeEvent.POST_MERGE_CI_MOVED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_NOT_CONFIGURED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_ERRORED: _PLAIN,
        ForgeEvent.AUTOMATED_REVIEW_SETTLED: _PLAIN,
        ForgeEvent.REPLY_INTENDED: _PLAIN,
        ForgeEvent.REPLY_SEND_STARTED: _PLAIN,
        ForgeEvent.REPLY_SEND_FAILED: _PLAIN,
        ForgeEvent.REPLY_POSTED: _PLAIN,
        ForgeEvent.REPLY_NOT_FOUND: _PLAIN,
        ForgeEvent.REPLY_ENDED: _PLAIN,
        ForgeEvent.REPLY_DUPLICATE_DELETED: _PLAIN,
        ForgeEvent.REPLIES_INERT: _PLAIN,
        ForgeEvent.MERGE_QUEUE_DROPPED: _PLAIN,
        ForgeEvent.REVIEWED_HEAD_REPINNED: _PLAIN,
        ForgeEvent.MERGE_REFUSED_UNREVIEWED: _PLAIN,
        ForgeEvent.MERGE_LANDED_UNREVIEWED: _PLAIN,
    },
    name="FORGE_EVENT_TRAITS",
)
