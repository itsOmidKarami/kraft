"""`sync_mr` re-running the failed jobs after it changes the description."""

from __future__ import annotations

import pytest

from kraft.adapters import forge

RED = (forge.FailedJob("test", "failed", "script_failure"),)


@pytest.mark.parametrize(
    "state, retried",
    [
        # A repair edited the description under a red check: the jobs that read
        # the old one run again (editing a body triggers no workflow).
        ("failed", 1),
        # A sync after new commits sees a pending pipeline, never a red one.
        ("pending", 0),
        ("success", 0),
    ],
    ids=["red-check-body-changed", "pending-body-changed", "green-body-changed"],
)
async def test_sync_mr_reruns_failed_jobs_only_when_the_description_changed(
    run_forge, tmp_path, state, retried
):
    fake = forge.FakeForge(ci_states=[state], ci_failed_jobs=[RED])
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="old")

    await run_forge(fake, "sync_mr", "x9")
    assert len(fake.retried) == retried, "first sync: the live body differs from the built one"

    fake.retried.clear()
    await run_forge(fake, "sync_mr", "x10")
    assert fake.retried == [], "second sync: the body is already what the sync wrote"


async def test_sync_mr_leaves_a_red_check_of_another_commit_alone(run_forge, tmp_path):
    fake = forge.FakeForge(ci_states=["failed"], ci_failed_jobs=[RED], ci_shas=["not-the-head"])
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="old")

    await run_forge(fake, "sync_mr", "x9")

    assert fake.retried == []


async def test_sync_mr_still_describes_when_the_live_body_cannot_be_read(run_forge, tmp_path):
    class _Unreadable(forge.FakeForge):
        async def mr_body(self, *, repo, branch):
            raise forge.ForgeError("cannot read the description")

    fake = _Unreadable(ci_states=["failed"], ci_failed_jobs=[RED])
    await fake.open_mr(repo=tmp_path, branch="kraft/w1", base="main", title="t", body="old")

    assert await run_forge(fake, "sync_mr", "x9") == ("done", "done")

    assert fake.bodies["kraft/w1"] != "old"
    assert fake.retried == []
