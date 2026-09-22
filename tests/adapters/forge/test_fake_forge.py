"""`FakeForge`, the double every forge handler and chain-level test runs
against: a capability it lacks is a capability no node can be tested through."""

from __future__ import annotations

import pytest

from kraft.adapters import forge


async def _opened(f: forge.FakeForge, branch: str = "kraft/abc") -> forge.MR:
    return await f.open_mr(repo="/r", branch=branch, base="main", title="t", body="b")


async def test_fake_forge_round_trips_an_mr():
    f = forge.FakeForge(ci_states=["pending", "success"])

    mr = await _opened(f)

    assert (mr.number, mr.url.endswith("/1")) == (1, True)
    assert (await f.ci_status(repo="/r", mr=mr)).state == "pending"
    assert (await f.ci_status(repo="/r", mr=mr)).state == "success"


async def test_fake_forge_mark_ready_unsets_draft():
    f = forge.FakeForge()
    mr = await _opened(f)
    assert f.opened_draft[mr.number] is True, "every MR opens as a draft"

    await f.mark_ready(repo="/r", branch="kraft/abc", mr=mr)

    assert f.opened_draft[mr.number] is False


async def test_fake_forge_ci_status_carries_sha_jobs_and_block_reason():
    job = forge.FailedJob("test", "failed", "script_failure")
    f = forge.FakeForge(
        ci_states=["failed"],
        ci_shas=["abc123"],
        ci_failed_jobs=[(job,)],
        block_reason="not_approved",
    )

    ci = await f.ci_status(repo="/r", mr=forge.MR(number=1, url="http://x"))

    assert (ci.sha, ci.failed_jobs, ci.block_reason) == ("abc123", (job,), "not_approved")


async def test_fake_forge_refuses_to_merge_an_unopened_mr():
    with pytest.raises(forge.ForgeError):
        await forge.FakeForge().merge(repo="/r", mr=forge.MR(number=99, url="http://x/99"))


async def test_fake_forge_find_mr_tracks_its_own_opened_and_merged_lists():
    f = forge.FakeForge()
    assert await f.find_mr(repo="/r", branch="kraft/w1") is None

    mr = await _opened(f, "kraft/w1")
    assert (await f.find_mr(repo="/r", branch="kraft/w1")).state == "open"
    assert await f.find_mr(repo="/r", branch="kraft/other") is None

    await f.merge(repo="/r", branch="kraft/w1", mr=mr)
    assert (await f.find_mr(repo="/r", branch="kraft/w1")).state == "merged"


async def test_fake_forge_records_labels_and_retries():
    f = forge.FakeForge()
    mr = await _opened(f)

    await f.set_labels(repo="/r", mr=mr, labels=("release::patch",))
    await f.retry_jobs(repo="/r", ci=forge.CIStatus(state="failed", url="http://x/1"))

    assert (f.labels, f.retried) == (["release::patch"], ["http://x/1"])
