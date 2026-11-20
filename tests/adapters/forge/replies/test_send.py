"""One posting point's sends: the class of a failed write, and what a send does with
each class, within its budgets."""

from __future__ import annotations

import pytest

from kraft.adapters.forge.git import CliUnavailable
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.replies.send import classify


@pytest.mark.parametrize(
    "exc, failure",
    [
        (ForgeError("gh api … timed out after 120s"), "unclear"),
        (ForgeError("POST x: gh printed no HTTP response"), "unclear"),
        (ForgeError("POST x: did not answer in 120s", reason="unreachable"), "unclear"),
        (ForgeError("x (HTTP 502)", status=502, reason="unreachable"), "unclear"),
        (
            ForgeError("x (HTTP 429)", status=429, reason="rate_limited", retry_after=30),
            "rate_limited",
        ),
        (ForgeError("x (HTTP 403)", status=403, reason="rate_limited"), "rate_limited"),
        (ForgeError("graphql: slow down", status=200, reason="rate_limited"), "rate_limited"),
        (CliUnavailable("gh is not installed or not on PATH"), "deferred"),
        (ForgeError("x (HTTP 401)", status=401, reason="auth"), "deferred"),
        (ForgeError("x", reason="tls"), "deferred"),
        (
            ForgeError(
                "x (HTTP 403)", status=403, reason="forbidden", sso_url="https://github.com/sso"
            ),
            "deferred",
        ),
        (
            ForgeError("POST x: To get started with GitHub CLI, please run:  gh auth login"),
            "deferred",
        ),
        (ForgeError("POST x: could not resolve host: api.github.com"), "deferred"),
        (ForgeError("POST x: connection refused"), "deferred"),
        (ForgeError("POST x: connection timed out"), "unclear"),
        (
            ForgeError(
                "x: Validation Failed; user_id can only have one pending review per pull "
                "request (HTTP 422)",
                status=422,
            ),
            "deferred",
        ),
        (ForgeError("x (HTTP 422)", status=422), "refused"),
        (ForgeError("x (HTTP 400)", status=400), "refused"),
        (ForgeError("x (HTTP 403)", status=403, reason="forbidden"), "refused"),
        (ForgeError("x (HTTP 404)", status=404, reason="not_found"), "refused"),
        (ForgeError("x (HTTP 409)", status=409, reason="conflict"), "refused"),
        (ForgeError("x (HTTP 410)", status=410), "refused"),
        (ForgeError("x (HTTP 413)", status=413), "refused"),
        (ForgeError("graphql: not allowed", status=200, reason="forbidden"), "refused"),
        (ForgeError("graphql: odd", status=200), "refused"),
    ],
    ids=[
        "cli-timeout",
        "no-http-response",
        "http-timeout",
        "5xx",
        "429",
        "403-rate-limit",
        "graphql-rate-limit",
        "cli-never-started",
        "401",
        "tls",
        "403-sso",
        "not-signed-in",
        "dns-failure",
        "connection-refused",
        "connection-timed-out",
        "422-pending-review",
        "422-other",
        "400",
        "403-forbidden",
        "404",
        "409",
        "410",
        "413",
        "graphql-forbidden",
        "graphql-unknown",
    ],
)
def test_a_failed_write_is_classified(exc, failure):
    assert classify(exc) == failure
