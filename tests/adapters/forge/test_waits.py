"""What one observation of each forge wait reads: automated review, external
approval, and the merge node's own waits (its pre-merge pipeline, a missing
approval, and the merge landing). Parking, backoff, timeout and the scheduler
are tests/test_waits.py; `ci_poll` and `merge_watch` reads, and a forge error
ending a wait, are test_run.py and test_merge_watch.py. A cancelled run nobody
followed up is here, at every node that reads a pipeline."""

from __future__ import annotations

import asyncio
import json

import pytest
from support.harness import _git

from kraft import events
from kraft.adapters import forge


async def _opened(fake, repo):
    await fake.open_mr(repo=repo, branch="kraft/w1", base="main", title="t", body="b")
    return fake


def _trail(run_forge, task="on.ci.poll") -> list[tuple[str, str | None, str | None]]:
    """(type, state or outcome, condition) of every wait event for `task`."""
    return [
        (t, p.get("state") or p.get("outcome"), p.get("condition"))
        for t in ("external_wait_started", "external_wait_observed", "external_wait_ended")
        for p in run_forge.events(t)
        if p["task"] == task
    ]


# --- automated review ----------------------------------------------------------


@pytest.mark.parametrize(
    "result, expected, findings",
    [
        (forge.ReviewResult("pending"), "waiting", None),
        (forge.ReviewResult("clean"), "done", None),
        (
            forge.ReviewResult("actionable", findings=("rename `x` to `total`",)),
            "failed",
            ["rename `x` to `total`"],
        ),
        # The reviewer itself broke: not feedback, so no finding and no
        # failure a repair would spend on -- a stop for a person (Ruling 170).
        (forge.ReviewResult("error", detail="reviewer bot crashed"), "infra_stop", None),
    ],
    ids=["pending", "clean", "actionable", "error"],
)
async def test_automated_review_reports_ordinary_task_results(
    run_forge, run_dirs, tmp_path, result, expected, findings
):
    """`automated-review-task-uses-ordinary-task-results`: pending waits,
    clean passes, actionable fails *with its feedback as findings* (what the
    node's recovery and fix loop read), and error stops without any."""
    fake = await _opened(forge.FakeForge(review_results=[result]), tmp_path)

    assert await run_forge(fake, "automated_review", "r1") == (expected, expected)

    written = run_dirs.results / "r1.json"
    got = (
        [f["message"] for f in json.loads(written.read_text())["findings"]]
        if written.exists()
        else None
    )
    assert got == findings
    if result.state == "error":
        assert "reviewer bot crashed" in run_forge.log("r1")


# --- external approval ---------------------------------------------------------


@pytest.mark.parametrize(
    "states, expected",
    [(["pending"], "waiting"), (["approved"], "done")],
    ids=["missing-approval-waits", "approved"],
)
async def test_missing_external_approval_is_pending_not_a_failure(
    run_forge, tmp_path, states, expected
):
    """`missing-external-approval-is-normal-pending-state`."""
    fake = await _opened(forge.FakeForge(approval_states=states), tmp_path)

    assert await run_forge(fake, "external_approval", "a1") == (expected, expected)


@pytest.mark.parametrize(
    "block_reason, expected",
    [("not_approved", "pending"), (None, "approved"), ("conflict", "approved")],
    ids=["not-approved", "nothing-blocking", "a-conflict-is-merge-s-problem"],
)
@pytest.mark.parametrize("cls", [forge.GhCli, forge.GlabCli], ids=["gh", "glab"])
async def test_a_real_forge_reads_approval_off_its_merge_request(
    tmp_path, cls, block_reason, expected
):
    """Both CLIs already classify `reviewDecision`/`detailed_merge_status`
    into `block_reason` (`mr.classify_block_reason`); the approval wait reads
    that one answer rather than a second parse of the same fields."""

    class Answering(cls):
        async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
            return forge.CIStatus("success", "u", block_reason=block_reason)

    assert await Answering().approval_state(repo=tmp_path, branch="kraft/w1") == expected


# --- merge: its pre-merge pipeline, a missing approval, the landing -----------


@pytest.fixture
def no_sleeping(monkeypatch):
    """Kraft-7jja/Kraft-vzq2q: the merge node used to sleep in-process on its
    pipeline and on the merge landing. Any sleep now is a regression."""

    async def refuse(seconds):
        raise AssertionError(f"slept {seconds}s in-process instead of handing the wait back")

    monkeypatch.setattr(asyncio, "sleep", refuse)


async def test_merge_hands_a_pending_pipeline_back_to_the_scheduler(
    run_forge, tmp_path, no_sleeping
):
    fake = await _opened(forge.FakeForge(ci_states=["pending"]), tmp_path)

    assert await run_forge(fake, "merge", "m1") == ("waiting", "waiting")

    assert fake.merged == [], "merged over a pipeline nobody saw finish"
    assert _trail(run_forge) == [
        ("external_wait_started", None, None),
        ("external_wait_observed", "pending", "ci"),
    ]


async def test_merge_waits_for_a_missing_approval_instead_of_failing(run_forge, tmp_path):
    """`missing-external-approval-is-normal-pending-state`, at the merge
    node too: an approval rule the pipeline cannot see is waited out."""
    fake = await _opened(
        forge.FakeForge(ci_states=["success"], block_reason="not_approved"), tmp_path
    )

    assert await run_forge(fake, "merge", "m1") == ("waiting", "waiting")

    assert fake.merged == []
    assert "approval" in run_forge.log("m1")
    assert _trail(run_forge)[-1] == ("external_wait_observed", "pending", "external_approval")


class _Counting(forge.FakeForge):
    """A `FakeForge` that counts every merge Kraft asks it for."""

    requests = 0

    async def merge(self, *, repo, branch="", mr):
        self.requests += 1
        await super().merge(repo=repo, branch=branch, mr=mr)


async def test_a_merge_that_has_not_landed_is_observed_again_never_requested_again(
    run_forge, tmp_path, no_sleeping
):
    """`glab mr merge` exits 0 for "merge when checks pass" and merges nothing
    (Kraft-79x3): the landing is read off the forge. A second observation
    reads it again; it does not ask for a second merge."""

    fake = await _opened(_Counting(merge_delay=2), tmp_path)

    results = [(await run_forge(fake, "merge", f"m{i}"))[0] for i in range(3)]

    assert results == ["waiting", "waiting", "done"]
    assert fake.requests == 1
    assert _trail(run_forge) == [
        ("external_wait_started", None, None),
        ("external_wait_observed", "pending", "merge"),
        ("external_wait_observed", "pending", "merge"),
        ("external_wait_observed", "settled", "merge"),
        ("external_wait_ended", "settled", None),
    ]


_MERGE = {"hook_point": "merge.main.merge", "node_id": "merge"}


@pytest.mark.parametrize("restart", ["work_item_retried", "run_forked", "base_change_restart"])
async def test_a_restart_mid_merge_asks_the_forge_to_merge_only_once(run_forge, repo, restart):
    """Kraft-l98h6: a restart ends the merge wait, and with it Kraft's memory
    of having asked for the merge. The forge still has the request queued, so
    the restarted merge reads that off the merge request and waits for the
    landing rather than asking a second time."""
    fake = await _opened(_Counting(merge_delay=2), repo)
    assert (await run_forge(fake, "merge", "m1", repo=repo, **_MERGE))[0] == "waiting"
    await run_forge.database.write(lambda c: events.append(c, "w1", restart, {}))

    assert (await run_forge(fake, "merge", "m2", repo=repo, **_MERGE))[0] == "waiting"

    assert fake.requests == 1
    assert (await run_forge(fake, "merge", "m3", repo=repo, **_MERGE))[0] == "done"
    assert fake.requests == 1


async def test_a_new_head_after_a_base_change_asks_for_the_merge_again(run_forge, repo):
    """The merge is asked for once per head: a base change that puts a new
    head on the branch drops the queued merge on the forge, and once the
    restarted span has pushed that head, merge asks for it afresh."""
    fake = await _opened(_Counting(merge_delay=3), repo)
    assert (await run_forge(fake, "merge", "m1", repo=repo, **_MERGE))[0] == "waiting"
    (repo / "rebased.txt").write_text("the new base\n")
    _git(repo, "add", "rebased.txt")
    _git(repo, "commit", "-qm", "rebased onto the new base")
    await run_forge.database.write(lambda c: events.append(c, "w1", "base_change_restart", {}))
    assert (await run_forge(fake, "ci_poll", "c1", repo=repo))[0] == "done"

    assert (await run_forge(fake, "merge", "m2", repo=repo, **_MERGE))[0] == "waiting"

    assert fake.requests == 2


# --- a cancelled run nobody followed up, at every node that reads CI -----------


@pytest.mark.parametrize(
    "handler, kw",
    [("ci_poll", {}), ("merge", _MERGE), ("merge_watch", {})],
    ids=["ci_poll", "merge", "merge_watch"],
)
async def test_a_cancel_with_no_successor_stops_for_a_person_at_once(run_forge, repo, handler, kw):
    """Kraft-kbqmk: a run cancelled an hour ago that is still the latest for
    this head has no successor coming. Stop now, naming it -- not a failure
    for a fix loop, and not the wait's full timeout later. Nothing merged."""
    from datetime import UTC, datetime, timedelta

    an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    fake = await _opened(
        forge.FakeForge(ci_states=["pending"], ci_cancelled_at=[an_hour_ago]), repo
    )

    assert await run_forge(fake, handler, "c1", repo=repo, **kw) == ("infra_stop", "infra_stop")

    assert fake.merged == []
    [stop] = run_forge.events("ci_run_abandoned")
    assert "was cancelled" in stop["reason"] and "re-run" in stop["reason"]
