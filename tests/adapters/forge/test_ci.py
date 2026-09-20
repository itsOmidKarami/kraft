"""Waiting for a pipeline to settle: `poll_ci`'s backoff, cap and error
tolerance, exercised directly against `FakeForge` rather than through a
CLI."""

from __future__ import annotations

import asyncio

import pytest

from kraft.adapters import forge


def test_poll_backs_off_and_caps_the_interval(tmp_path, monkeypatch):
    """Without a cap, a long pipeline would stretch the gap between checks
    without bound; without doubling, a 30-minute wait costs 360 CLI calls."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"] * 5 + ["success"])
    ci, timed_out = asyncio.run(
        forge.poll_ci(fake, repo=tmp_path, branch="b", timeout=10_000, interval=5)
    )
    assert (ci.state, timed_out) == ("success", False)
    assert slept == [5, 10, 20, 40, forge._MAX_POLL_INTERVAL]


def test_a_configured_interval_longer_than_the_cap_is_not_clamped_down(tmp_path, monkeypatch):
    """The cap bounds the *growth*, not the human's choice. A registry asking
    for 300s between checks -- a rate-limited forge -- must not silently get
    60s and five times the CLI calls. It holds at 300 rather than doubling
    past it: the configured interval is what was asked for."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"] * 3 + ["success"])
    asyncio.run(forge.poll_ci(fake, repo=tmp_path, branch="b", timeout=10_000, interval=300))
    assert slept == [300, 300, 300]


def test_poll_never_sleeps_past_its_deadline(tmp_path, monkeypatch):
    """An interval longer than what is left would overshoot the timeout and
    report the pipeline late."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"])
    _, timed_out = asyncio.run(
        forge.poll_ci(fake, repo=tmp_path, branch="b", timeout=0.05, interval=100)
    )
    assert timed_out is True
    assert slept and all(s <= 0.05 for s in slept)


def test_poll_returns_early_on_a_conflict_rather_than_waiting_out_the_pipeline(
    tmp_path, monkeypatch
):
    """A conflict will not resolve itself in thirty minutes."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"], mergeable=False, merge_detail="conflict")

    ci, timed_out = asyncio.run(
        forge.poll_ci(fake, repo=tmp_path, branch="b", timeout=10_000, interval=5)
    )

    assert (ci.state, ci.mergeable, timed_out) == ("pending", False, False)
    assert slept == [], "it waited out a pipeline for a branch that cannot merge"


def test_poll_ci_tolerates_a_short_burst_of_transient_forge_errors(tmp_path):
    """Kraft-x92: a flaky/rate-limited `ci_status` call used to fail the whole
    wait, indistinguishable from a red pipeline. A run of errors under the
    cap must not end the poll."""

    class FlakyThenGreen(forge.FakeForge):
        calls = 0

        async def ci_status(self, *, repo, mr, branch=""):
            self.calls += 1
            if self.calls <= forge.ci._MAX_CONSECUTIVE_POLL_ERRORS:
                raise forge.ForgeError("transient")
            return await super().ci_status(repo=repo, mr=mr, branch=branch)

    fake = FlakyThenGreen(ci_states=["success"])

    ci, timed_out = asyncio.run(
        forge.poll_ci(fake, repo=tmp_path, branch="kraft/w1", timeout=5, interval=0)
    )

    assert timed_out is False
    assert ci.state == "success"


def test_poll_ci_gives_up_after_too_many_consecutive_forge_errors(tmp_path):
    """One more error than the tolerance still ends the wait -- this is not
    an unlimited retry, only a burst allowance."""

    class AlwaysFlaky(forge.FakeForge):
        async def ci_status(self, *, repo, mr, branch=""):
            raise forge.ForgeError("transient")

    fake = AlwaysFlaky()

    with pytest.raises(forge.ForgeError):
        asyncio.run(forge.poll_ci(fake, repo=tmp_path, branch="kraft/w1", timeout=5, interval=0))


def test_fake_forge_ci_status_carries_sha_and_failed_jobs(tmp_path):
    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_shas=["abc123"],
        ci_failed_jobs=[(forge.FailedJob("test", "failed", "script_failure"),)],
    )
    ci = asyncio.run(fake.ci_status(repo=tmp_path, mr=forge.MR(number=1, url="http://x")))
    assert ci.sha == "abc123"
    assert ci.failed_jobs == (forge.FailedJob("test", "failed", "script_failure"),)


def test_fake_forge_retry_jobs_records_the_call(tmp_path):
    fake = forge.FakeForge()
    asyncio.run(fake.retry_jobs(repo=tmp_path, ci=forge.CIStatus(state="failed", url="http://x/1")))
    assert fake.retried == ["http://x/1"]


def test_render_ci_waits_on_a_pending_pipeline_even_when_unmergeable(tmp_path):
    """The exact !171 bug: a transient unmergeable during GitLab's post-push
    recompute window must never fail the node."""
    fake = forge.FakeForge(ci_states=["pending"], mergeable=False, merge_detail="conflict")
    ci_status = asyncio.run(fake.ci_status(repo=tmp_path, mr=forge.MR(1, "http://x")))
    log, verdict = asyncio.run(
        forge.ci.render_ci(ci_status, forge=fake, repo=tmp_path, branch="b", head_sha=None)
    )
    assert verdict == "waiting"


def test_render_ci_waits_on_a_pipeline_not_for_the_branch_head(tmp_path):
    fake = forge.FakeForge(ci_states=["success"], ci_shas=["old-sha"])
    ci_status = asyncio.run(fake.ci_status(repo=tmp_path, mr=forge.MR(1, "http://x")))
    log, verdict = asyncio.run(
        forge.ci.render_ci(ci_status, forge=fake, repo=tmp_path, branch="b", head_sha="new-sha")
    )
    assert verdict == "waiting"


def test_render_ci_fails_a_settled_green_pipeline_only_after_a_re_fetch_confirms_conflict(
    tmp_path,
):
    fake = forge.FakeForge(
        ci_states=["success", "success"], mergeable=False, merge_detail="conflict"
    )
    ci_status = asyncio.run(fake.ci_status(repo=tmp_path, mr=forge.MR(1, "http://x")))
    log, verdict = asyncio.run(
        forge.ci.render_ci(ci_status, forge=fake, repo=tmp_path, branch="b", head_sha=None)
    )
    assert verdict == "conflict"


def test_render_ci_infra_red_vs_code_red(tmp_path):
    infra = forge.CIStatus(
        state="failed",
        url="u",
        failed_jobs=(forge.FailedJob("build", "failed", "runner_system_failure"),),
    )
    code = forge.CIStatus(
        state="failed", url="u", failed_jobs=(forge.FailedJob("test", "failed", "script_failure"),)
    )
    fake = forge.FakeForge()
    _, v1 = asyncio.run(
        forge.ci.render_ci(infra, forge=fake, repo=tmp_path, branch="b", head_sha=None)
    )
    _, v2 = asyncio.run(
        forge.ci.render_ci(code, forge=fake, repo=tmp_path, branch="b", head_sha=None)
    )
    assert (v1, v2) == ("infra", "failed")
