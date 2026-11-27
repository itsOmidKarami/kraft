"""An `mr.automated_review` task that sets `watch_ci` also pushes and reads the
pipeline at every check, and ends on a red one as its node's CI wait's finding.
Walked through the executor and `forge.run_task` against a `FakeForge`."""

# ruff: noqa: F811 -- `rig` is an imported fixture, taken by name.

from __future__ import annotations

from pathlib import Path

import pytest
from support.harness import own_files
from support.review_rig import A as _A
from support.review_rig import feedback as _feedback
from support.review_rig import review_node as _review_node
from support.review_rig import rig  # noqa: F401 -- the fixture

from kraft import events, store
from kraft.adapters import forge
from kraft.adapters.forge import ForgeError
from kraft.adapters.forge.models import FailedJob
from kraft.executor.round_prompts import CI_LOG_NOTE

from .test_run_chain import RED, _run_task
from .test_run_push import on_branch  # noqa: F401 -- the fixture
from .test_run_review import _both_halves_changed

_WAIT, _CI = "feedback.settle.review", "feedback.ci.await_ci"


async def test_a_red_pipeline_the_review_wait_reads_is_the_ci_wait_s_finding(rig):
    """One owner for CI: the same red, read first by the watching wait and then
    by the CI wait, is one finding: same source, one fingerprint, the CI-log
    marking in the fix prompt, and the stuck check sees it unchanged."""
    node = _review_node(10, reads=False, reviewers=(), loop=True, watch_ci=True)
    del node["on_failure"]
    node["fix_loop"]["max_attempts"] = 3
    # Reads: the review wait's red; after the fix, the review wait's pending,
    # then the CI wait's red.
    fake = forge.FakeForge(
        ci_states=["failed", "pending", "failed"], ci_failed_jobs=[*RED, (), *RED]
    )
    await rig(node, fake)

    await rig.walk()

    measured = [e["payload"] for e in rig.item.events("findings_measured")]
    assert [m["fingerprints"] for m in measured] == [measured[0]["fingerprints"]] * 2
    assert measured[0]["fingerprints"]
    assert {f["source_plugin"] for m in measured for f in m["findings"]} == {_CI}
    failed = [s["hook_point"] for s in rig.item.sessions("feedback") if s["status"] == "failed"]
    assert failed == [_WAIT, _CI], "two readers, one finding"
    assert CI_LOG_NOTE in rig.instructions[0] and f"({_CI})" in rig.instructions[0]
    assert rig.item.events("work_item_needs_human")[-1]["payload"]["reason"].startswith("stuck:")
    wait_log = Path(rig.item.sessions("feedback")[0]["log_path"]).read_text()
    assert any(line.startswith(forge.run.CI_RED_LINE) for line in wait_log.splitlines())


async def test_a_red_pipeline_the_review_wait_reads_goes_to_on_failure_first(rig):
    await rig(
        _review_node(10, reads=False, reviewers=(), loop=True, watch_ci=True),
        forge.FakeForge(ci_states=["failed"], ci_failed_jobs=RED),
    )
    await rig.walk()
    assert rig.triaged[0] == "feedback.on_failure.checks.repair_checks"


def _watching(repo, **kw):
    """`run_forge` kwargs for the review wait on its collect path, watching CI."""
    return {
        "repo": repo,
        "orig_repo": repo,
        "node_id": "feedback",
        "hook_point": _WAIT,
        "reviewers": (_A,),
        "reads_feedback": True,
        "watch_ci": True,
        "ci_task": _CI,
        "wait": forge.WaitBounds.from_seconds(timeout=600, initial=30, maximum=120),
        **kw,
    }


_RATE_LIMITED = ForgeError("slow", status=429, reason="rate_limited", retry_after=30)
_INFRA = (FailedJob("test", "failed", "runner_system_failure"),)
_STALE = {"ci_shas": ["0" * 40]}


@pytest.mark.parametrize(
    "fake_kw, raises, kw, status, pushed, ci_read, bots_read",
    [
        ({"ci_states": ["pending"]}, None, {}, "waiting", True, True, True),
        ({"ci_states": ["success"]}, None, {}, "waiting", True, True, True),
        ({"ci_states": ["pending"], "ci_no_checks": True}, None, {}, "waiting", True, True, True),
        (
            {"ci_states": ["failed"], "ci_failed_jobs": RED, **_STALE},
            None,
            {},
            "waiting",
            True,
            True,
            True,
        ),
        (
            {"ci_states": ["failed"], "ci_failed_jobs": [_INFRA]},
            None,
            {},
            "waiting",
            True,
            True,
            True,
        ),
        (
            {"ci_states": ["pending"], "ci_cancelled_at": ["2020-01-01T00:00:00+00:00"]},
            None,
            {},
            "waiting",
            True,
            True,
            True,
        ),
        ({}, ForgeError("boom"), {}, "waiting", True, True, True),
        (
            {"ci_states": ["failed"], "ci_failed_jobs": RED},
            None,
            {"ci_checks": False},
            "waiting",
            True,
            False,
            True,
        ),
        (
            {"ci_states": ["success"], "mergeable": False, "merge_detail": "conflict"},
            None,
            {},
            "done",
            True,
            True,
            False,
        ),
        ({"opened": {1: "kraft/w1"}, "merged": [1]}, None, {}, "done", False, False, False),
        ({"push_refused": "f" * 40}, None, {}, "failed", False, False, False),
        ({}, _RATE_LIMITED, {}, "rate_limited", True, True, False),
        (
            {"ci_states": ["failed"], "ci_failed_jobs": RED},
            None,
            {"watch_ci": False},
            "waiting",
            False,
            False,
            True,
        ),
    ],
    ids=[
        "pending",
        "green",
        "no-pipeline-yet",
        "red-for-an-older-head",
        "infra-red",
        "abandoned",
        "read-error",
        "ci-checks-off",
        "conflict",
        "merged",
        "push-refused",
        "rate-limited",
        "watch-ci-off",
    ],
)
async def test_the_watching_wait_acts_only_on_a_settled_red_or_a_conflict(
    run_forge,
    on_branch,
    run_dirs,
    monkeypatch,
    fake_kw,
    raises,
    kw,
    status,
    pushed,
    ci_read,
    bots_read,
):
    """The review wait that watches CI pushes first and reads the pipeline, but
    only a settled red or a confirmed conflict ends it; everything else is the
    CI wait's, and it never rebases, retries, pins or records a missing CI."""
    # Reads: one `ci_status` per check (a conflict's confirming re-read is the
    # same script).
    fake = forge.FakeForge(**fake_kw)
    reads = []
    if raises is not None:

        async def failing(**kwargs):
            reads.append(kwargs)
            raise raises

        monkeypatch.setattr(fake, "ci_status", failing)
    got, _ = await run_forge(fake, "automated_review", "s1", **_watching(on_branch, **kw))

    assert got == status
    assert bool(run_forge.events("branch_pushed")) is pushed
    assert bool(fake.pipeline_ids_requested or reads) is ci_read
    assert bool(fake.reviewers_asked) is bots_read
    # None of the CI wait's side effects.
    assert fake.retried == []
    assert (
        run_forge.database.read(lambda c: store.read_counter(c, "w1", "ci_infra:feedback")) is None
    )
    assert run_forge.events("ci_not_configured") == []
    pin = run_forge.database.read(
        lambda c: c.execute("SELECT ci_pipeline_ref FROM work_items WHERE id = 'w1'").fetchone()
    )
    assert pin is None or pin["ci_pipeline_ref"] is None
    assert set(fake.pipeline_ids_requested) <= {""}
    assert not (own_files(run_dirs, "s1").folder / "bot_feedback.json").exists()
    if raises is not None and raises.reason is None:
        assert "not read" in run_forge.log("s1")


@pytest.mark.parametrize(
    "fake_kw, ended",
    [
        ({"ci_states": ["pending", "failed"], "ci_failed_jobs": [(), *RED]}, "failed"),
        (
            {"ci_states": ["pending", "success"], "mergeable": False, "merge_detail": "conflict"},
            "done",
        ),
    ],
    ids=["red", "conflict"],
)
async def test_a_wait_cut_short_by_ci_records_no_heard_or_silent_bots(
    run_forge, on_branch, monkeypatch, wait_clock, fake_kw, ended
):
    # The push is stamped on the wait's clock.
    monkeypatch.setattr(events, "_now", lambda: wait_clock.now.isoformat())
    fake = forge.FakeForge(**fake_kw)
    assert (await run_forge(fake, "automated_review", "s1", **_watching(on_branch)))[0] == "waiting"
    wait_clock.advance(601)  # past the limit: read, the silent bot would settle as silent now
    assert (await run_forge(fake, "automated_review", "s2", **_watching(on_branch)))[0] == ended
    assert run_forge.events("automated_review_settled") == []
    # Ending closes the episode: no wait is left open.
    assert [
        e["outcome"] for e in run_forge.events("external_wait_ended") if e["task"] == _WAIT
    ] == ["settled"]


def _pipeline(state, *, red=False, conflict=False):
    return forge.CIStatus(
        state=state,
        url="http://fake.forge/pipelines",
        jobs=(f"fake-job: {state}",),
        failed_jobs=RED[0] if red else (),
        mergeable=False if conflict else None,
        merge_detail="conflict" if conflict else "",
    )


@pytest.mark.parametrize(
    "pipelines, merged, halves, status, pushed, ci_read, bots_read",
    [
        (
            {"pkg": _pipeline("failed", red=True), "root": _pipeline("pending")},
            False,
            ("pkg", "root"),
            "failed",
            {"pkg", "root"},
            {"pkg"},
            {},
        ),
        (
            {"pkg": _pipeline("pending"), "root": _pipeline("failed", red=True)},
            False,
            ("pkg", "root"),
            "failed",
            {"pkg", "root"},
            {"pkg", "root"},
            {},
        ),
        (
            {"pkg": _pipeline("success", conflict=True), "root": _pipeline("pending")},
            False,
            ("pkg", "root"),
            "done",
            {"pkg", "root"},
            {"pkg"},
            {},
        ),
        (
            {"pkg": _pipeline("pending"), "root": _pipeline("pending")},
            False,
            ("pkg", "root"),
            "waiting",
            {"pkg", "root"},
            {"pkg", "root"},
            {"pkg": 1},
        ),
        (
            {"root": _pipeline("pending")},
            True,
            ("pkg", "root"),
            "waiting",
            {"root"},
            {"root"},
            None,
        ),
        ({"pkg": _pipeline("pending")}, False, ("pkg",), "waiting", {"pkg"}, {"pkg"}, {"pkg": 1}),
    ],
    ids=[
        "a-member-red",
        "the-root-red-behind-a-pending-member",
        "a-member-conflict",
        "every-pipeline-pending",
        "a-merged-member-is-skipped",
        "a-pointer-only-root-is-not-read",
    ],
)
async def test_a_multi_repo_watching_wait_reads_every_repository_before_its_bots(
    item_on,
    database,
    run_dirs,
    tmp_path,
    monkeypatch,
    pipelines,
    merged,
    halves,
    status,
    pushed,
    ci_read,
    bots_read,
):
    """Every repository is pushed and its pipeline read before any bot, so a
    later repository's red does not wait behind an earlier one's pending."""
    it, worktree, branch = await _both_halves_changed(
        item_on, database, run_dirs, tmp_path, halves=halves
    )
    fake = forge.FakeForge()
    if merged:
        number = await fake.open_mr(
            repo=worktree / "repos" / "pkg", branch=branch, base="main", title="t", body="b"
        )
        fake.merged.append(getattr(number, "number", number))
    ci_reads: list[str] = []
    bot_reads: dict[str, int] = {}

    def which(repo):
        return "pkg" if repo.name == "pkg" else "root"

    async def ci_status(*, repo, mr, branch="", pipeline_id=""):
        ci_reads.append(which(repo))
        return pipelines[which(repo)]

    async def feedback(*, repo, branch, reviewers, since):
        bot_reads[which(repo)] = bot_reads.get(which(repo), 0) + 1
        return [_feedback(_A)]

    monkeypatch.setattr(fake, "ci_status", ci_status)
    monkeypatch.setattr(fake, "reviewer_feedback", feedback)
    ask = {
        "handler": "automated_review",
        "reviewers": (_A,),
        "reads_feedback": True,
        "watch_ci": True,
        "ci_task": _CI,
        "wait": forge.WaitBounds.from_seconds(timeout=600, initial=30, maximum=120),
    }

    got = await _run_task(database, run_dirs, it, worktree, branch, fake, monkeypatch, **ask)

    assert got == status
    assert {which(Path(e["payload"]["repo"])) for e in it.events("branch_pushed")} == pushed
    assert set(ci_reads) == ci_read
    if bots_read is not None:
        assert bot_reads == bots_read
    if status == "failed":
        assert not Path(it.sessions()[-1]["result_path"]).exists(), "no findings, as the CI wait's"
        cause = next(
            line
            for line in own_files(run_dirs, "s1").log.read_text().splitlines()
            if line.startswith(forge.run.CI_RED_LINE)
        )
        assert cause.endswith(" in pkg" if ci_read == {"pkg"} else f" in {worktree.name}")
    if status in ("failed", "done"):
        assert it.events("automated_review_settled") == []


@pytest.mark.parametrize(
    "watch_ci, at_601",
    [(True, "done"), (False, "waiting")],
    ids=["from-the-push", "from-the-wait-start"],
)
async def test_the_watching_wait_s_limit_counts_from_the_push(
    run_forge, on_branch, monkeypatch, wait_clock, watch_ci, at_601
):
    """'If it didn't post a review after the push in 10 mins, it's done' --
    only with watch_ci; without it the limit counts from the wait's start."""
    monkeypatch.setattr(events, "_now", lambda: wait_clock.now.isoformat())
    fake = forge.FakeForge(ci_states=["pending"])
    # An earlier push, at 0.
    await run_forge(fake, "ci_poll", "s0", repo=on_branch, orig_repo=on_branch)
    wait_clock.advance(300)
    first = await run_forge(
        fake, "automated_review", "s1", **_watching(on_branch, watch_ci=watch_ci)
    )
    assert first[0] == "waiting"
    wait_clock.advance(301)  # 601s after the push, 301s into the wait
    got = await run_forge(fake, "automated_review", "s2", **_watching(on_branch, watch_ci=watch_ci))
    assert got[0] == at_601
    ended = [e["outcome"] for e in run_forge.events("external_wait_ended") if e["task"] == _WAIT]
    assert ended == (["timed_out"] if watch_ci else [])
