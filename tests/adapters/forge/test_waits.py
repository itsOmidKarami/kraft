"""What one observation of each forge wait reads: automated review, external
approval, and the merge node's own waits (its pre-merge pipeline, a missing
approval, and the merge landing). Parking, backoff, timeout and the scheduler
are tests/test_waits.py; `ci_poll` and `merge_watch` reads are test_run.py
and test_merge_watch.py."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft.adapters import forge


async def _opened(fake, repo):
    await fake.open_mr(repo=repo, branch="kraft/w1", title="t", body="b")
    return fake


def _trail(run_forge, task="on.ci.poll") -> list[tuple[str, str | None, str | None]]:
    """(type, state or outcome, condition) of every wait event for `task`."""
    return [
        (t, p.get("state") or p.get("outcome"), p.get("condition"))
        for t in ("external_wait_started", "external_wait_observed", "external_wait_ended")
        for p in run_forge.events(t)
        if p["task"] == task
    ]


def _types(run_forge) -> list[str]:
    evts = run_forge.database.read(
        lambda c: c.execute(
            "SELECT type FROM events WHERE work_item_id = 'w1' AND type LIKE 'external_wait_%' "
            "ORDER BY seq"
        ).fetchall()
    )
    return [e["type"] for e in evts]


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
        # The reviewer itself broke: an ordinary task failure, with nothing
        # for a fix loop to treat as review feedback.
        (forge.ReviewResult("error", detail="reviewer bot crashed"), "failed", None),
    ],
    ids=["pending", "clean", "actionable", "error"],
)
async def test_automated_review_reports_ordinary_task_results(
    run_forge, run_dirs, tmp_path, result, expected, findings
):
    """`automated-review-task-uses-ordinary-task-results`: pending waits,
    clean passes, actionable fails *with its feedback as findings* (what the
    node's recovery and fix loop read), error fails without any."""
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


@pytest.mark.parametrize("cls", [forge.GhCli, forge.GlabCli], ids=["gh", "glab"])
async def test_a_forge_with_no_automated_review_probe_stops_for_a_human_naming_it(
    run_forge, cli, cls
):
    """Which reviewer a real forge's automated review is -- which bot, which
    check -- is not settled yet, and a guess would wait on a reviewer that
    never comes. A configuration stop naming the target, never `failed`: a
    fix loop cannot install a reviewer."""
    assert await run_forge(cls(), "automated_review", "u1") == ("config_error",) * 2

    assert "mr.automated_review" in run_forge.log("u1")
    assert _trail(run_forge)[-1][:2] == ("external_wait_ended", "error")


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


async def test_a_merge_that_has_not_landed_is_observed_again_never_requested_again(
    run_forge, tmp_path, no_sleeping
):
    """`glab mr merge` exits 0 for "merge when checks pass" and merges nothing
    (Kraft-79x3): the landing is read off the forge. A second observation
    reads it again; it does not ask for a second merge."""

    class Counting(forge.FakeForge):
        requests = 0

        async def merge(self, *, repo, branch="", mr):
            self.requests += 1
            await super().merge(repo=repo, branch=branch, mr=mr)

    fake = await _opened(Counting(merge_delay=1), tmp_path)

    first = await run_forge(fake, "merge", "m1")
    second = await run_forge(fake, "merge", "m2")

    assert (first[0], second[0]) == ("waiting", "done")
    assert fake.requests == 1
    assert _trail(run_forge) == [
        ("external_wait_started", None, None),
        ("external_wait_observed", "pending", "merge"),
        ("external_wait_observed", "settled", "merge"),
        ("external_wait_ended", "settled", None),
    ]


async def test_a_forge_error_mid_wait_ends_the_wait_with_a_trace(run_forge):
    """Kraft-vzq2q: a wait left no record of how it ended. Whatever ends one --
    here the forge CLI failing -- is written as its outcome."""

    class Exploding(forge.FakeForge):
        async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
            raise forge.ForgeError("glab fell over")

    assert await run_forge(Exploding(), "ci_poll", "e1") == ("failed", "failed")

    assert _types(run_forge) == ["external_wait_started", "external_wait_ended"]
    (ended,) = run_forge.events("external_wait_ended")
    assert ended["outcome"] == "error" and "glab fell over" in ended["result"]
