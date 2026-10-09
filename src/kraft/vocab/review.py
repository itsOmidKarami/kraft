from __future__ import annotations

from enum import StrEnum

__all__ = ["ReviewOutcome", "ThreadLabel", "ThreadState", "ReplyClaim", "DiffSide"]


class ReviewOutcome(StrEnum):
    """How a review was submitted (`reviews.outcome`)."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    COMMENT = "comment"


class ThreadLabel(StrEnum):
    """A review thread's severity (`review_threads.label`)."""

    MUST_FIX = "must_fix"
    QUESTION = "question"
    NIT = "nit"


class ThreadState(StrEnum):
    """Where a review thread stands (`review_threads.state`)."""

    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"


class ReplyClaim(StrEnum):
    """What a worker's reply says it did (`review_comments.claim`)."""

    FIXED = "fixed"
    ANSWERED = "answered"
    SHOULD_FIX = "should_fix"


class DiffSide(StrEnum):
    """Which side of a diff a line is on (`review_threads.side`, `start_side`)."""

    OLD = "old"
    NEW = "new"
