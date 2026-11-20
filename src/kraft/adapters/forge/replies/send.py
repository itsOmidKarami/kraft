"""One posting point's sends, and the class of a failed write.

`classify` decides whether a write that failed may have landed (so it is reconciled
before any resend), may succeed later, or never will. `PostingPoint` sends within its
budgets: at most three counted sends an intent, a wait after a rate limit or a
deferred failure, a second between writes on one forge account, and 60 seconds a
posting point."""

from __future__ import annotations

from typing import Literal

from kraft.adapters.forge.git import CliUnavailable
from kraft.adapters.forge.models import ForgeError, failure_cause

Failure = Literal["unclear", "rate_limited", "deferred", "refused"]
#: `models._UNREACHABLE_MARKERS` less the timeouts: the request never left. A timeout
#: ("timed out after", "connection timed out") may have landed.
_NO_REQUEST = (
    "could not resolve host",
    "temporary failure in name resolution",
    "connection refused",
    "network is unreachable",
    "failed to connect",
    "could not connect",
)


def classify(exc: ForgeError) -> Failure:
    """Whether a failed write may have landed (`unclear`, reconciled before any resend),
    may succeed later (`rate_limited` after its `retry_after`, `deferred`), or never will."""
    if isinstance(exc, CliUnavailable):
        return "deferred"
    if exc.reason == "rate_limited":
        return "rate_limited"
    if exc.reason in ("auth", "tls") or exc.sso_url:
        return "deferred"
    if exc.status is None:
        # No answer: a timeout or a lost connection may have landed. A CLI that is not
        # signed in, or a host that never answered, made no request at all.
        text = str(exc).lower()
        if failure_cause(text) == "forge_auth" or any(m in text for m in _NO_REQUEST):
            return "deferred"
        return "unclear"
    if exc.status >= 500:
        return "unclear"
    if exc.status == 422 and "one pending review" in str(exc).lower():
        return "deferred"  # the person has an unfinished review open
    return "refused"
