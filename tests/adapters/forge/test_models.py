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
        ("", None),
    ],
)
def test_failure_cause_reads_a_credential_or_reachability_failure(text, cause):
    assert forge.failure_cause(text) == cause
