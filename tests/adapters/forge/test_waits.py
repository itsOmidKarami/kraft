"""What one observation of each forge wait reads: automated review, external
approval, and the merge node's own waits (its pre-merge pipeline, a missing
approval, and the merge landing). Parking, backoff, timeout and the scheduler
are tests/test_waits.py; `ci_poll` and `merge_watch` reads are test_run.py and
test_merge_watch.py. A forge error ending a wait, and a forge rate limit stopping
the item, are here, as is a cancelled run nobody followed up, at every node that
reads a pipeline."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from support.harness import git, own_files

from kraft import events, inplace, waits
from kraft.adapters import forge
from kraft.templates.environment import WorkItemTarget

from .nodes import back_half


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


@pytest.mark.parametrize(
    "error, status",
    [
        (forge.ForgeError("glab fell over"), "failed"),
        (forge.CliUnavailable("glab is not installed or not on PATH"), "config_error"),
    ],
    ids=["flaky", "cannot-start"],
)
async def test_a_forge_error_mid_poll_fails_the_node_rather_than_escaping(run_forge, error, status):
    """A flaky CLI lands as a failed node, a CLI that never started as a stop for a
    person -- and, since a wait left no record of its end (Kraft-vzq2q), as its `error`."""
    fake = forge.FakeForge()
    fake.ci_status = AsyncMock(side_effect=error)
    assert await run_forge(fake, "ci_poll", "s7") == (status, status)
    assert str(error) in run_forge.log("s7")
    (ended,) = run_forge.events("external_wait_ended")
    assert ended["outcome"] == "error" and str(error) in ended["result"]


async def test_a_forge_rate_limit_stops_the_item_until_the_forge_allows_it(
    run_forge, monkeypatch, run_dirs
):
    """A `Retry-After` the forge sent is when the walk comes back (`rate_limited`
    stops through `stops.stop_for_rate_limit`), not a failed node or a login
    failure; the record the task leaves beside its log says why."""
    moment = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(waits, "now", lambda: moment)
    fake = forge.FakeForge()
    fake.ci_status = AsyncMock(
        side_effect=forge.ForgeError(
            "GET repos: API rate limit exceeded (HTTP 403)",
            status=403,
            reason="rate_limited",
            retry_after=30,
        )
    )

    assert await run_forge(fake, "ci_poll", "s8") == ("rate_limited", "rate_limited")

    (hit,) = run_forge.events("rate_limit_hit")
    assert hit["resets_at_iso"] == (moment + timedelta(seconds=30)).isoformat()
    record = json.loads(own_files(run_dirs, "s8").log.with_suffix(".forge.json").read_text())
    assert (record["status"], record["reason"], record["retry_after"]) == (403, "rate_limited", 30)


async def test_a_forge_rate_limit_without_a_time_waits_a_minute(run_forge, monkeypatch):
    moment = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(waits, "now", lambda: moment)
    fake = forge.FakeForge()
    fake.ci_status = AsyncMock(
        side_effect=forge.ForgeError("429", status=429, reason="rate_limited")
    )

    await run_forge(fake, "ci_poll", "s9")

    (hit,) = run_forge.events("rate_limit_hit")
    assert hit["resets_at_iso"] == (moment + timedelta(seconds=60)).isoformat()


async def test_a_refused_permission_is_a_failed_node_not_a_rate_limit(run_forge, run_dirs):
    fake = forge.FakeForge()
    fake.ci_status = AsyncMock(
        side_effect=forge.ForgeError("denied (HTTP 403)", status=403, reason="forbidden")
    )

    assert await run_forge(fake, "ci_poll", "s10") == ("failed", "failed")

    assert not run_forge.events("rate_limit_hit")
    record = json.loads(own_files(run_dirs, "s10").log.with_suffix(".forge.json").read_text())
    assert record["reason"] == "forbidden"


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

    written = own_files(run_dirs, "r1").result
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


async def test_merge_waits_when_the_rebased_head_still_reads_conflicting(
    run_forge, repo, monkeypatch
):
    """Kraft-09ze6: just after the rebase's push GitHub can still report the
    old conflict and no checks. That is a head to wait for, not a stop."""

    async def forced(*_, **__):
        return "new-head"

    monkeypatch.setattr(forge.run._builtins, "mr_rebase_forced", forced)
    fake = await _opened(
        forge.FakeForge(ci_states=["pending"], ci_no_checks=True, mergeable=False), repo
    )

    assert (await run_forge(fake, "merge", "r1", repo=repo))[0] == "waiting"
    assert fake.merged == []


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

    async def merge(self, *, repo, branch="", mr, head=""):
        self.requests += 1
        await super().merge(repo=repo, branch=branch, mr=mr, head=head)


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
    git(repo, "add", "rebased.txt")
    git(repo, "commit", "-qm", "rebased onto the new base")
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
    fake = forge.FakeForge(ci_states=["pending"], ci_cancelled_at=[an_hour_ago])
    if handler != "merge_watch":  # whose merge request is merged, or unknown to the forge
        await _opened(fake, repo)

    assert await run_forge(fake, handler, "c1", repo=repo, **kw) == ("infra_stop", "infra_stop")

    assert fake.merged == []
    [stop] = run_forge.events("ci_run_abandoned")
    assert "was cancelled" in stop["reason"] and "re-run" in stop["reason"]


@pytest.mark.parametrize("handler", ["ci_poll", "merge"])
async def test_an_in_place_conflict_rebase_that_clashes_stops_the_item_as_checkout(
    run_forge, item_on, repo, monkeypatch, handler
):
    """In place, the forced rebase goes through `inplace.rebase`, and a
    clash it leaves in the person's checkout is a `checkout` stop naming the
    stash, never one more conflict."""
    target = WorkItemTarget.for_repository("target", in_place=True)
    run_forge.item = await item_on(back_half(), repo=repo, target=target)
    asked = []

    async def clash(_run_dirs, _row, **kw):
        asked.append(kw["force"])
        raise inplace.AutostashClash("your edits to f clash", ["f"], "5ca1ab1e")

    monkeypatch.setattr(inplace, "rebase", clash)
    fake = await _opened(
        forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict"), repo
    )

    assert await run_forge(fake, handler, "r1", repo=repo) == ("checkout_refused", "config_error")
    assert asked == [True]
    [stop] = run_forge.events("work_item_needs_human")
    assert (stop["kind"], stop["facts"]) == ("checkout", {"paths": ["f"], "stash": "5ca1ab1e"})


@pytest.mark.parametrize("handler", ["ci_poll", "merge"])
async def test_an_in_place_checkout_elsewhere_does_not_hide_the_branch_s_pipeline(
    run_forge, item_on, repo, handler
):
    """The head a pipeline must be for is the item branch's tip: an in-place
    item's checkout may be on another branch, whose HEAD no pipeline is for."""
    git(repo, "checkout", "-q", "-b", "kraft/w1")
    git(repo, "commit", "-q", "--allow-empty", "-m", "the item's")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "the person's")
    target = WorkItemTarget.for_repository("target", in_place=True)
    run_forge.item = await item_on(back_half(), repo=repo, target=target)
    fake = await _opened(forge.FakeForge(ci_states=["success"], ci_shas=[head]), repo)

    assert (await run_forge(fake, handler, "h1", repo=repo))[0] == "done"
