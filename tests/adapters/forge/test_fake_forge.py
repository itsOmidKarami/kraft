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
        await forge.FakeForge().merge(
            repo="/r", mr=forge.MR(number=99, url="http://x/99"), head="a" * 40
        )


async def test_fake_forge_find_mr_tracks_its_own_opened_and_merged_lists():
    f = forge.FakeForge()
    assert await f.find_mr(repo="/r", branch="kraft/w1") is None

    mr = await _opened(f, "kraft/w1")
    assert (await f.find_mr(repo="/r", branch="kraft/w1")).state == "open"
    assert await f.find_mr(repo="/r", branch="kraft/other") is None

    await f.merge(repo="/r", branch="kraft/w1", mr=mr, head="a" * 40)
    assert (await f.find_mr(repo="/r", branch="kraft/w1")).state == "merged"


async def test_fake_forge_close_mr_flips_the_ref_state():
    f = forge.FakeForge()
    mr = await _opened(f)

    await f.close_mr(repo="/r", mr=mr)

    found = await f.find_mr(repo="/r", branch="kraft/abc")
    assert found.state == "closed"
    assert f.closed == [mr.number]


async def test_fake_forge_reopen_mr_flips_a_closed_ref_back_open():
    f = forge.FakeForge()
    mr = await _opened(f)
    await f.close_mr(repo="/r", mr=mr)

    await f.reopen_mr(repo="/r", mr=mr)

    found = await f.find_mr(repo="/r", branch="kraft/abc")
    assert found.state == "open"
    assert f.reopened == [mr.number]


async def test_fake_forge_records_labels_and_retries():
    f = forge.FakeForge()
    mr = await _opened(f)

    await f.set_labels(repo="/r", branch="b", mr=mr, labels=("release::patch",))
    await f.retry_jobs(repo="/r", ci=forge.CIStatus(state="failed", url="http://x/1"))

    assert (f.labels, f.retried) == (["release::patch"], ["http://x/1"])


_DROP = forge.QueueRead(kind="train", state="dropped", cause="code", reason="pipeline failed")


@pytest.mark.parametrize(
    ("kind", "drops", "state", "cause"),
    [
        pytest.param("train", [_DROP], "dropped", "code", id="train-drop"),
        pytest.param("none", [], "absent", None, id="no-queue"),
    ],
)
async def test_a_queue_drop_leaves_the_mr_open_and_unqueued(kind, drops, state, cause):
    f = forge.FakeForge(queue_kind=kind, queue_drops=list(drops), merge_delay=2)
    mr = await _opened(f)

    await f.merge(repo="/r", branch="kraft/abc", mr=mr, head="a" * 40)

    read = await f.merge_queue(repo="/r", branch="kraft/abc", base="main")
    found = await f.find_mr(repo="/r", branch="kraft/abc")
    assert (read.state, read.cause) == (state, cause)
    assert f.merge_calls == [mr.number]
    if kind == "none":
        assert read == forge.QueueRead()
        assert found.merge_queued is True
    else:
        assert (found.state, found.merge_queued, f.merged) == ("open", False, [])
        assert read == _DROP


async def test_a_second_merge_clears_the_drop_and_consumes_the_next():
    again = forge.QueueRead(kind="train", state="dropped", cause="conflict")
    f = forge.FakeForge(queue_kind="train", queue_drops=[_DROP, again])
    mr = await _opened(f)

    await f.merge(repo="/r", branch="kraft/abc", mr=mr, head="a" * 40)
    await f.merge(repo="/r", branch="kraft/abc", mr=mr, head="a" * 40)
    assert (await f.merge_queue(repo="/r", branch="kraft/abc", base="main")).cause == "conflict"

    await f.merge(repo="/r", branch="kraft/abc", mr=mr, head="a" * 40)

    found = await f.find_mr(repo="/r", branch="kraft/abc")
    assert (found.state, f.merge_calls) == ("merged", [1, 1, 1])
    assert (await f.merge_queue(repo="/r", branch="kraft/abc", base="main")).state == "merged"


async def test_the_fake_refuses_a_head_the_forge_does_not_hold_as_the_real_forges_do():
    """`forge_head` is the head the forge holds. A merge naming another is
    refused with `HeadMoved` (and recorded as asked), so a walk test reaches
    the refusal; `find_mr` reports the head it was asked for, or the forged one."""
    f = forge.FakeForge(forge_head="f" * 40)
    mr = await _opened(f)

    with pytest.raises(forge.HeadMoved) as moved:
        await f.merge(repo="/r", branch="kraft/abc", mr=mr, head="a" * 40)

    assert (moved.value.current, f.merge_heads, f.merged) == ("f" * 40, ["a" * 40], [])
    assert (await f.find_mr(repo="/r", branch="kraft/abc")).head_sha == "f" * 40
    await f.merge(repo="/r", branch="kraft/abc", mr=mr, head="f" * 40)
    assert f.merged == [mr.number]


async def test_a_cancelled_merge_never_lands_and_a_late_forge_commit_moves_the_head():
    f = forge.FakeForge(merge_delay=2, head_after_merge="b" * 40)
    mr = await _opened(f)
    await f.merge(repo="/r", branch="kraft/abc", mr=mr, head="a" * 40)
    queued = await f.find_mr(repo="/r", branch="kraft/abc")
    assert (queued.merge_queued, queued.head_sha) == (True, "b" * 40)

    await f.cancel_merge(repo="/r", branch="kraft/abc", mr=queued)

    after = await f.find_mr(repo="/r", branch="kraft/abc")
    assert (f.cancelled, after.state, after.merge_queued) == ([mr.number], "open", False)


async def test_the_fake_refuses_a_push_the_forge_has_commits_for():
    f = forge.FakeForge(push_refused="c" * 40)

    with pytest.raises(forge.ForeignCommits) as refused:
        await f.push(repo="/r", branch="kraft/abc")

    assert (refused.value.remote, f.pushed) == ("c" * 40, [])
