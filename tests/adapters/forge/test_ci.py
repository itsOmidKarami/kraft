"""`render_ci`'s verdict on one pipeline read, exercised directly against
`FakeForge` rather than through a CLI. Waiting for a pipeline to settle is
`kraft.waits` (tests/test_waits.py) -- nothing here sleeps."""

from __future__ import annotations

import pytest

from kraft.adapters import forge

MR = forge.MR(1, "http://x")


@pytest.mark.parametrize(
    "fake_kw, head_sha, expected",
    [
        # The exact !171 bug: a transient unmergeable during GitLab's post-push
        # recompute window must never fail the node.
        (
            {"ci_states": ["pending"], "mergeable": False, "merge_detail": "conflict"},
            None,
            "waiting",
        ),
        # A pipeline not for the branch head is not a result.
        ({"ci_states": ["success"], "ci_shas": ["old-sha"]}, "new-sha", "waiting"),
        # A settled green pipeline fails only after a re-fetch confirms the
        # conflict.
        (
            {"ci_states": ["success", "success"], "mergeable": False, "merge_detail": "conflict"},
            None,
            "conflict",
        ),
    ],
    ids=["pending-and-unmergeable-waits", "other-sha-waits", "confirmed-conflict"],
)
async def test_render_ci_verdict(tmp_path, fake_kw, head_sha, expected):
    fake = forge.FakeForge(**fake_kw)
    first = await fake.ci_status(repo=tmp_path, mr=MR)

    _, verdict = await forge.ci.render_ci(
        first, forge=fake, repo=tmp_path, branch="b", head_sha=head_sha
    )

    assert verdict == expected


@pytest.mark.parametrize(
    "reason, expected",
    [("runner_system_failure", "infra"), ("script_failure", "failed")],
    ids=["infra-red", "code-red"],
)
async def test_render_ci_tells_infra_red_from_code_red(tmp_path, reason, expected):
    ci = forge.CIStatus(
        state="failed", url="u", failed_jobs=(forge.FailedJob("build", "failed", reason),)
    )

    _, verdict = await forge.ci.render_ci(
        ci, forge=forge.FakeForge(), repo=tmp_path, branch="b", head_sha=None
    )

    assert verdict == expected
