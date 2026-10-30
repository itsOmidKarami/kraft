"""The forge's vocabulary (`adapters/forge/models.py`)."""

import pytest

from kraft.adapters import forge


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ("gh pr create failed: HTTP 401: Bad credentials", "forge_auth"),
        ("glab mr create failed: 401 Unauthorized", "forge_auth"),
        ("gh api failed: HTTP 403: Resource not accessible by personal access token", "forge_auth"),
        ("To get started with GitHub CLI, please run:  gh auth login", "forge_auth"),
        ("fatal: Authentication failed for 'https://github.com/o/r.git/'", "forge_auth"),
        ("fatal: could not read Username for 'https://github.com'", "forge_auth"),
        ("git@github.com: Permission denied (publickey).", "forge_auth"),
        ("fatal: unable to access 'x': Could not resolve host: github.com", "forge_unreachable"),
        ("gh pr list timed out after 60s", "forge_unreachable"),
        ("connect: Connection refused", "forge_unreachable"),
        # A credential the forge refused wins over text that also reads as unreachable.
        ("Authentication failed; Connection timed out", "forge_auth"),
        ("rebase conflict: could not apply abc123", None),
        ("fatal: unable to access 'x': The requested URL returned error: 403", "forge_auth"),
        ("remote: Invalid username or password.", "forge_auth"),
        ("remote: HTTP Basic: Access denied", "forge_auth"),
        # git's own rebase and checkout, not a forge: a timeout is not an unreachable host, a
        # file name is not a refused credential.
        ("git rebase timed out after 600s for /x/wt/abc", None),
        (
            "untracked 403 forbidden.txt would be overwritten by the base branch; move it\n"
            "git rebase could not start in /x/wt/abc, so nothing was rebased",
            None,
        ),
        ("git worktree add timed out after 600s for /x/wt/abc", None),
        ("", None),
    ],
)
def test_failure_cause_reads_a_credential_or_reachability_failure(text, cause):
    assert forge.failure_cause(text) == cause


@pytest.mark.parametrize(
    ("status", "headers", "reason"),
    [
        (401, {}, "auth"),
        (403, {}, "forbidden"),
        (403, {"x-ratelimit-remaining": "0"}, "rate_limited"),
        (403, {"retry-after": "30"}, "rate_limited"),
        (429, {}, "rate_limited"),
        (404, {}, "not_found"),
        (409, {}, "conflict"),
        (502, {}, "unreachable"),
        (200, {}, None),
    ],
    ids=["401", "403", "403-ratelimit", "403-retry-after", "429", "404", "409", "5xx", "ok"],
)
def test_reason_for_maps_status_and_headers(status, headers, reason):
    assert forge.reason_for(status, headers) == reason


def test_head_moved_carries_its_reason():
    assert forge.HeadMoved("x", "abc").reason == "head_moved"
