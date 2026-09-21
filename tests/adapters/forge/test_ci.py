"""Waiting for a pipeline to settle: `poll_ci`'s backoff, cap and error
tolerance, and `render_ci`'s verdict on one read, exercised directly against
`FakeForge` rather than through a CLI."""

from __future__ import annotations

import asyncio

import pytest

from kraft.adapters import forge

MR = forge.MR(1, "http://x")


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """Every `asyncio.sleep` the poll asks for, without sleeping."""
    calls: list[float] = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


@pytest.mark.parametrize(
    "pending, interval, expected",
    [
        # Without a cap, a long pipeline would stretch the gap between checks
        # without bound; without doubling, a 30-minute wait costs 360 CLI calls.
        (5, 5, [5, 10, 20, 40, forge._MAX_POLL_INTERVAL]),
        # The cap bounds the *growth*, not the human's choice: a registry asking
        # for 300s between checks (a rate-limited forge) must not silently get
        # 60s and five times the CLI calls, nor double past what it asked for.
        (3, 300, [300, 300, 300]),
    ],
    ids=["backs-off-then-caps", "a-longer-configured-interval-is-kept"],
)
async def test_poll_ci_spaces_its_checks(tmp_path, slept, pending, interval, expected):
    fake = forge.FakeForge(ci_states=["pending"] * pending + ["success"])

    ci, timed_out = await forge.poll_ci(
        fake, repo=tmp_path, branch="b", timeout=10_000, interval=interval
    )

    assert (ci.state, timed_out) == ("success", False)
    assert slept == expected


async def test_poll_never_sleeps_past_its_deadline(tmp_path, slept):
    """An interval longer than what is left would overshoot the timeout and
    report the pipeline late."""
    fake = forge.FakeForge(ci_states=["pending"])

    _, timed_out = await forge.poll_ci(fake, repo=tmp_path, branch="b", timeout=0.05, interval=100)

    assert timed_out is True
    assert slept and all(s <= 0.05 for s in slept)


async def test_poll_returns_early_on_a_conflict_rather_than_waiting_out_the_pipeline(
    tmp_path, slept
):
    """A conflict will not resolve itself in thirty minutes."""
    fake = forge.FakeForge(ci_states=["pending"], mergeable=False, merge_detail="conflict")

    ci, timed_out = await forge.poll_ci(fake, repo=tmp_path, branch="b", timeout=10_000, interval=5)

    assert (ci.state, ci.mergeable, timed_out) == ("pending", False, False)
    assert slept == [], "it waited out a pipeline for a branch that cannot merge"


class _Flaky(forge.FakeForge):
    """Raises for its first `errors` reads, then answers normally."""

    errors = 0
    calls = 0

    async def ci_status(self, *, repo, mr, branch=""):
        self.calls += 1
        if self.calls <= self.errors:
            raise forge.ForgeError("transient")
        return await super().ci_status(repo=repo, mr=mr, branch=branch)


async def test_poll_ci_tolerates_a_short_burst_of_transient_forge_errors(tmp_path):
    """Kraft-x92: a flaky/rate-limited `ci_status` call used to fail the whole
    wait, indistinguishable from a red pipeline. A run of errors under the cap
    must not end the poll."""
    fake = _Flaky(ci_states=["success"])
    fake.errors = forge.ci._MAX_CONSECUTIVE_POLL_ERRORS

    ci, timed_out = await forge.poll_ci(
        fake, repo=tmp_path, branch="kraft/w1", timeout=5, interval=0
    )

    assert (ci.state, timed_out) == ("success", False)


async def test_poll_ci_gives_up_after_too_many_consecutive_forge_errors(tmp_path):
    """One more error than the tolerance still ends the wait -- a burst
    allowance, not an unlimited retry."""
    fake = _Flaky(ci_states=["success"])
    fake.errors = forge.ci._MAX_CONSECUTIVE_POLL_ERRORS + 1

    with pytest.raises(forge.ForgeError):
        await forge.poll_ci(fake, repo=tmp_path, branch="kraft/w1", timeout=5, interval=0)


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
