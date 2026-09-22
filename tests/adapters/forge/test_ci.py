"""`render_ci`'s verdict on one pipeline read, exercised directly against
`FakeForge` rather than through a CLI. Waiting for a pipeline to settle is
`kraft.waits` (tests/test_waits.py) -- nothing here sleeps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def _ago(seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


@pytest.mark.parametrize(
    "cancelled_at, sha, expected",
    [
        # Kraft-kbqmk: still the latest run for this head long after it was
        # cancelled -- nobody is re-running it, so a person decides now rather
        # than at the wait's full timeout.
        (_ago(3600), "head", "abandoned"),
        # Freshly cancelled: a successor for the same head (a relabel, GitLab's
        # auto-cancel) registers within seconds, so this is still a wait (#116).
        (_ago(5), "head", "waiting"),
        # A push superseded it: a run for another head is never a result.
        (_ago(3600), "old", "waiting"),
        # A time with no zone cannot be aged against ours: it waits, and the
        # wait's own timeout still stops it.
        ("2026-01-01T00:00:00", "head", "waiting"),
    ],
    ids=["no-successor-stops", "fresh-cancel-waits", "other-head-waits", "zoneless-time-waits"],
)
async def test_render_ci_stops_only_on_a_cancel_nobody_followed_up(
    tmp_path, cancelled_at, sha, expected
):
    ci = forge.CIStatus(state="pending", url="u", sha=sha, cancelled_at=cancelled_at)

    log, verdict = await forge.ci.render_ci(
        ci, forge=forge.FakeForge(), repo=tmp_path, branch="b", head_sha="head"
    )

    assert verdict == expected
    assert ("cancelled" in log) == (expected == "abandoned")


async def test_a_re_read_right_after_an_infra_kick_is_never_abandoned(tmp_path):
    """The kick itself is the follow-up, so an old cancel on the re-read is a
    wait, never the "abandoned" no caller of `retry_infra_once` handles."""
    fake = forge.FakeForge(ci_states=["pending"], ci_cancelled_at=[_ago(3600)])
    first = forge.CIStatus(state="failed", url="u")

    _, verdict = await forge.ci.retry_infra_once(
        fake, repo=tmp_path, branch="b", head_sha=None, first=first
    )

    assert verdict == "waiting"
