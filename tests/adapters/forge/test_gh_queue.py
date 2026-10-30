"""GhCli and the GitHub merge queue: where a pull request stands in its base
branch's queue, why GitHub removed it, and which merge-group run to blame.
Against a stubbed `gh` on PATH, as test_gh.py."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from kraft.adapters import forge

from . import api_outputs
from .nodes import FAIL, Api, FailWith

_PR_URL = "https://github.com/o/r/pull/7"
_RUN_URL = "https://github.com/o/r/actions/runs/555"
_QUEUE_BRANCH = "gh-readonly-queue/main/pr-7-" + "a" * 40
_JOBS = "api repos/{owner}/{repo}/actions/runs/555/jobs?per_page=100"
_RUNS = "api*repos/{owner}/{repo}/actions/runs -X GET*"
_LOGS = "api*repos/{owner}/{repo}/actions/jobs/*/logs*"
_RUNS_CALL = "api repos/{owner}/{repo}/actions/runs -X GET -i -f event=merge_group -F per_page=100"


def _graphql(
    *,
    queue: bool = True,
    state: str = "OPEN",
    entry: str | None = None,
    removal: str | None = None,
    removed_at: str = "2026-10-21T10:00:00Z",
    enqueued_at: str | None = "2026-10-21T09:00:00Z",
    reasonless_removal: bool = False,
) -> str:
    """`gh api graphql`'s answer for the merge queue query: the base's queue
    (or none), and the pull request's state, queue entry and last removal
    (one with a null reason when `reasonless_removal`)."""
    removed = removal or reasonless_removal
    removals = [{"reason": removal, "createdAt": removed_at}] if removed else []
    enqueues = [{"createdAt": enqueued_at}] if enqueued_at else []
    pr = {
        "state": state,
        "mergeQueueEntry": {"state": entry, "position": 1} if entry else None,
        "removals": {"nodes": removals},
        "enqueues": {"nodes": enqueues},
    }
    repository = {"mergeQueue": {"id": "MQ_1"} if queue else None, "pullRequest": pr}
    return json.dumps({"data": {"repository": repository}})


def _merge_group_run(
    branch: str = _QUEUE_BRANCH,
    conclusion: str = "failure",
    id: int = 555,
    created_at: str = "2026-10-21T09:30:00Z",
):
    return api_outputs.workflow_run(
        id, head_branch=branch, conclusion=conclusion, created_at=created_at
    )


#: The merge group's failed run (09:30), and when GitHub rebuilt it after.
_RED = _merge_group_run()
_REBUILT_AT = "2026-10-21T09:40:00Z"
_BY_HAND_CHECKS = "Removed by omid from the merge queue: checks failed on a flake"


def _queue_routes(graphql: str, runs: list[dict] | None = None, **routes: str) -> dict:
    return {
        api_outputs.FIND: api_outputs.find_result(api_outputs.listed()),
        "api graphql": graphql,
        _RUNS: api_outputs.runs(*(runs or [])),
        _JOBS: json.dumps(
            {
                "jobs": [
                    {"id": 1, "name": "lint", "conclusion": "success"},
                    {"id": 2, "name": "test", "conclusion": "failure", "html_url": _RUN_URL},
                ]
            }
        ),
        _LOGS: "2026-10-21T10:00:00.0000000Z FAILED tests/test_x.py::test_y",
        **routes,
    }


def _queue(url: str = _PR_URL, **fields) -> forge.QueueRead:
    return forge.QueueRead(kind="queue", url=url, **fields)


@pytest.mark.parametrize(
    "routes, expected, failure_state",
    [
        pytest.param(
            _queue_routes(_graphql(queue=False, entry="QUEUED")),
            forge.QueueRead(),
            None,
            id="no-queue",
        ),
        pytest.param(
            _queue_routes(_graphql(entry="QUEUED")), _queue(state="queued"), None, id="queued"
        ),
        pytest.param(
            _queue_routes(_graphql(entry="AWAITING_CHECKS")),
            _queue(state="queued"),
            None,
            id="awaiting-checks",
        ),
        pytest.param(
            _queue_routes(_graphql(entry="LOCKED")), _queue(state="queued"), None, id="locked"
        ),
        pytest.param(
            _queue_routes(_graphql(entry="MERGEABLE")),
            _queue(state="merging"),
            None,
            id="mergeable",
        ),
        pytest.param(
            _queue_routes(_graphql(entry="UNMERGEABLE")),
            _queue(state="queued"),
            None,
            id="unmergeable-is-about-to-be-removed",
        ),
        pytest.param(
            _queue_routes(_graphql(state="MERGED")), _queue(state="merged"), None, id="merged"
        ),
        pytest.param(
            _queue_routes(_graphql(state="CLOSED", removal="Closed")),
            _queue(),
            None,
            id="closed-is-not-dropped",
        ),
        pytest.param(
            _queue_routes(_graphql(removal="Required status checks failed"), [_merge_group_run()]),
            _queue(
                state="dropped", cause="code", reason="Required status checks failed", url=_RUN_URL
            ),
            "failed",
            id="dropped-ci-failed",
        ),
        pytest.param(
            _queue_routes(
                _graphql(removal="Merge conflict with the base branch"), [_merge_group_run()]
            ),
            _queue(
                state="dropped",
                cause="conflict",
                reason="Merge conflict with the base branch",
                url=_RUN_URL,
            ),
            "failed",
            id="dropped-conflict",
        ),
        pytest.param(
            _queue_routes(_graphql(removal="Removed by omid from the merge queue")),
            _queue(state="dropped", cause="other", reason="Removed by omid from the merge queue"),
            None,
            id="dropped-by-person",
        ),
        # No removal event yet (or the pull request is about to show
        # merged): no reason, which the merge node reads again next poll.
        pytest.param(
            _queue_routes(_graphql()),
            _queue(state="dropped", cause="other", reason=""),
            None,
            id="dropped-no-removal-event-yet",
        ),
        # Drop 1 was a red merge group (run 555, 09:30). Kraft repaired and
        # queued again at 11:00; this entry was taken out by a person. Run 555
        # is the newest failed run, but it is the earlier entry's.
        pytest.param(
            _queue_routes(
                _graphql(
                    removal="Removed by omid from the merge queue",
                    removed_at="2026-10-21T11:30:00Z",
                    enqueued_at="2026-10-21T11:00:00Z",
                ),
                [_merge_group_run()],
            ),
            _queue(state="dropped", cause="other", reason="Removed by omid from the merge queue"),
            None,
            id="stale-failed-run-from-an-earlier-drop",
        ),
        # GitHub has not yet written this entry's removal: the one on the
        # timeline is the earlier entry's, and is no reason for this drop.
        pytest.param(
            _queue_routes(
                _graphql(
                    removal="Required status checks failed",
                    removed_at="2026-10-21T10:00:00Z",
                    enqueued_at="2026-10-21T11:00:00Z",
                ),
                [_merge_group_run()],
            ),
            _queue(state="dropped", cause="other", reason=""),
            None,
            id="stale-removal-from-an-earlier-drop",
        ),
        pytest.param(
            _queue_routes(
                _graphql(
                    removal="Required status checks failed",
                    removed_at="2026-10-21T11:40:00Z",
                    enqueued_at="2026-10-21T11:00:00Z",
                ),
                [_merge_group_run(id=556, created_at="2026-10-21T11:10:00Z"), _merge_group_run()],
            ),
            _queue(
                state="dropped",
                cause="code",
                reason="Required status checks failed",
                url="https://github.com/o/r/actions/runs/556",
            ),
            "failed",
            id="the-failed-run-of-the-latest-entry",
        ),
        # The group failed for a pull request ahead (09:30) and GitHub rebuilt
        # it green (09:40); then a person took this one out. Their removal,
        # not the earlier red run: never a repair and re-queue against them.
        pytest.param(
            _queue_routes(
                _graphql(removal="Removed by omid from the merge queue"),
                [_merge_group_run(conclusion="success", id=556, created_at=_REBUILT_AT), _RED],
            ),
            _queue(state="dropped", cause="other", reason="Removed by omid from the merge queue"),
            None,
            id="failed-run-rebuilt-green-then-removed-by-hand",
        ),
        # The same with no removal written yet: nothing failed last, so a wait.
        pytest.param(
            _queue_routes(
                _graphql(),
                [_merge_group_run(conclusion="success", id=556, created_at=_REBUILT_AT), _RED],
            ),
            _queue(state="dropped", cause="other", reason=""),
            None,
            id="failed-run-rebuilt-green-no-removal-yet",
        ),
        # A person's removal is theirs even when the newest run failed.
        pytest.param(
            _queue_routes(_graphql(removal="Removed by omid from the merge queue"), [_RED]),
            _queue(
                state="dropped",
                cause="other",
                reason="Removed by omid from the merge queue",
                url=_RUN_URL,
            ),
            "failed",
            id="removed-by-hand-whatever-the-run-says",
        ),
        pytest.param(
            _queue_routes(_graphql(removal=_BY_HAND_CHECKS), [_RED]),
            _queue(state="dropped", cause="other", reason=_BY_HAND_CHECKS, url=_RUN_URL),
            "failed",
            id="removed-by-hand-even-naming-failed-checks",
        ),
        pytest.param(
            _queue_routes(_graphql(removal="REMOVED_BY_USER: FAILED_CHECKS"), [_RED]),
            _queue(
                state="dropped",
                cause="other",
                reason="REMOVED_BY_USER: FAILED_CHECKS",
                url=_RUN_URL,
            ),
            "failed",
            id="removed-by-hand-in-enum-words",
        ),
        # A reason that names neither checks nor a conflict is GitHub's word
        # that something else took it out: the run is evidence only when the
        # reason names the checks or says nothing.
        pytest.param(
            _queue_routes(_graphql(removal="The base branch was force-pushed"), [_RED]),
            _queue(
                state="dropped",
                cause="other",
                reason="The base branch was force-pushed",
                url=_RUN_URL,
            ),
            "failed",
            id="another-reason-whatever-the-run-says",
        ),
        # A removal with no reason in it is a removal, not "none yet": a
        # person's to look at, unless the merge group's newest run failed.
        pytest.param(
            _queue_routes(_graphql(reasonless_removal=True)),
            _queue(
                state="dropped",
                cause="other",
                reason="removed from the merge queue; GitHub gave no reason",
            ),
            None,
            id="removal-with-no-reason",
        ),
        pytest.param(
            _queue_routes(_graphql(reasonless_removal=True), [_RED]),
            _queue(
                state="dropped",
                cause="code",
                reason="dropped from the merge queue; GitHub gave no reason",
                url=_RUN_URL,
            ),
            "failed",
            id="removal-with-no-reason-and-a-failed-run",
        ),
        # A person dequeued it while GitHub rebuilt the group: the rebuild is
        # cancelled, and the failed run before it is no verdict on them.
        pytest.param(
            _queue_routes(
                _graphql(reasonless_removal=True),
                [_merge_group_run(conclusion="cancelled", id=556, created_at=_REBUILT_AT), _RED],
            ),
            _queue(
                state="dropped",
                cause="other",
                reason="removed from the merge queue; GitHub gave no reason",
            ),
            None,
            id="removal-with-no-reason-after-a-cancelled-rebuild",
        ),
        # Checks GitHub Actions does not run (an external CI, a required
        # status): no merge-group run to read, so GitHub's own words naming
        # the failed checks are the finding.
        *(
            pytest.param(
                _queue_routes(_graphql(removal=reason)),
                _queue(state="dropped", cause="code", reason=reason),
                None,
                id=f"no-actions-run-{id}",
            )
            for reason, id in [
                ("Required status checks failed", "checks-failed"),
                ("The merge commit failed CI", "ci-failed"),
                ("Timed out waiting for status checks", "timed-out"),
                ("CHECK TIMEOUT", "timeout"),
                ("FAILED_CHECKS", "enum-failed-checks"),
                ("CHECKS_TIMED_OUT", "enum-checks-timed-out"),
            ]
        ),
        *(
            pytest.param(
                _queue_routes(_graphql(removal=reason)),
                _queue(state="dropped", cause="other", reason=reason),
                None,
                id=f"not-a-checks-reason-{id}",
            )
            for reason, id in [
                ("Removed by omid from the merge queue", "by-hand"),
                ("Dequeued: the decision was circumstantial", "words-containing-ci"),
                ("Statuses of the team changed", "statuses-of-something-else"),
                ("Removed by ci-bot from the merge queue", "by-a-bot"),
                ("ci-bot dequeued it", "a-bot-named-ci"),
                ("Dequeued to check something", "to-check-something"),
                ("Failed to merge", "failed-to-merge"),
            ]
        ),
    ],
)
async def test_merge_queue_reads_the_queue(cli, tmp_path, routes, expected, failure_state):
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert (got.failure is not None) == (failure_state is not None)
    assert got.failure is None or got.failure.state == failure_state
    assert replace(got, failure=None) == expected
    if expected.kind == "none":
        assert _RUNS_CALL not in cli.calls("gh"), "asked a repo with no queue"


def _run_for(branch: str, conclusion: str = "failure", id: int = 900) -> dict:
    return _merge_group_run(branch, conclusion, id)


@pytest.mark.parametrize(
    "runs, run_id",
    [
        pytest.param([_merge_group_run(id=555)], 555, id="the-failed-run"),
        pytest.param(
            [_run_for(_QUEUE_BRANCH, id=555), _run_for(_QUEUE_BRANCH, id=400)], 555, id="newest"
        ),
        pytest.param(
            [_run_for("gh-readonly-queue/main/pr-70-" + "b" * 40), _run_for(_QUEUE_BRANCH, id=555)],
            555,
            id="not-another-pull-request-sharing-the-prefix",
        ),
        pytest.param(
            [_run_for("gh-readonly-queue/dev/pr-7-" + "b" * 40), _run_for(_QUEUE_BRANCH, id=555)],
            555,
            id="not-another-base",
        ),
        # A rebuild cancelled (a person took it out) or still running is no
        # verdict, and never a reason to blame the failed run before it.
        pytest.param(
            [_merge_group_run(conclusion="cancelled", id=700, created_at=_REBUILT_AT), _RED],
            None,
            id="not-a-cancelled-run",
        ),
        pytest.param([_run_for(_QUEUE_BRANCH, "success", id=700)], None, id="not-a-green-run"),
        pytest.param(
            [_merge_group_run(conclusion="success", id=700, created_at=_REBUILT_AT), _RED],
            None,
            id="not-a-failed-run-since-rebuilt-green",
        ),
        pytest.param(
            [_merge_group_run(conclusion="", id=700, created_at=_REBUILT_AT), _RED],
            None,
            id="not-a-run-still-running",
        ),
        pytest.param([_run_for(_QUEUE_BRANCH, "timed_out", id=555)], 555, id="a-timed-out-run"),
        pytest.param([_run_for("kraft/abc")], None, id="not-a-run-of-the-branch-itself"),
    ],
)
async def test_merge_queue_blames_the_failed_merge_group_run_of_this_pull_request(
    cli, tmp_path, runs, run_id
):
    routes = _queue_routes(_graphql(removal="Required status checks failed"), runs)
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert got.state == "dropped"
    if run_id is None:
        # No run to blame, so GitHub's reason naming the failed checks is all.
        assert (got.failure, got.cause, got.url) == (None, "code", _PR_URL)
    else:
        assert got.failure is not None and got.failure.url.endswith(f"/runs/{run_id}")
        assert got.cause == "code"
    # The runs list is repo-wide: thirty runs is a busy hour on another branch.
    assert cli.calls("gh").count(_RUNS_CALL) == 1


async def test_merge_queue_hands_over_the_failed_jobs_with_two_log_tails_of_fifteen_lines(
    cli, tmp_path
):
    """The failure is what a repair agent is given: every failed job named (the
    rest only by name), the first two with the tail of their log."""
    jobs = {
        "jobs": [
            {"id": i, "name": f"job{i}", "conclusion": "failure", "html_url": f"{_RUN_URL}/job/{i}"}
            for i in (1, 2, 3)
        ]
        + [{"id": 4, "name": "lint", "conclusion": "success"}]
    }
    jobs["jobs"][0]["steps"] = [api_outputs.step(1, "step", "failure", "10:00:00", "10:00:59")]
    log = "\n".join(f"2026-10-21T10:00:{n:02d}.0000000Z line {n}" for n in range(1, 31))
    routes = _queue_routes(
        _graphql(removal="Required status checks failed"),
        [_merge_group_run()],
        **{_JOBS: json.dumps(jobs), _LOGS: log},
    )
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    failure = got.failure
    assert failure is not None and failure.sha == ""
    assert [j.name for j in failure.failed_jobs] == ["job1", "job2", "job3"]
    assert all(j.failure_reason == "script_failure" for j in failure.failed_jobs)
    assert failure.failed_jobs[0].detail_url == f"{_RUN_URL}/job/1"
    logs = [c for c in cli.calls("gh") if "/logs" in c]
    assert logs == [f"api repos/{{owner}}/{{repo}}/actions/jobs/{i}/logs -X GET -i" for i in (1, 2)]
    tails = [line for line in failure.jobs if line.startswith("  ")]
    assert len(tails) == 30
    # job1 has a failed step, job2 none: its whole log goes under UNKNOWN STEP
    assert tails[0] == "  job1\tstep\t2026-10-21T10:00:16.0000000Z line 16"
    assert tails[14].endswith("line 30")
    assert tails[15] == "  job2\tUNKNOWN STEP\t2026-10-21T10:00:16.0000000Z line 16"
    assert not any("line 15" in line for line in failure.jobs)


async def test_merge_queue_cuts_a_job_log_to_its_failed_step_as_gh_does(cli, tmp_path):
    """The log tail and the `jobs` text are what `gh run view --log-failed`
    printed for gh's own sad job (view_test.go:2726-2730)."""
    routes = _queue_routes(
        _graphql(removal="Required status checks failed"),
        [_merge_group_run()],
        **{_JOBS: json.dumps({"jobs": [api_outputs.SAD_JOB]}), _LOGS: api_outputs.SAD_JOB_LOG},
    )
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert got.failure is not None
    assert got.failure.jobs == (
        "run 555: failure",
        "job sad job: failed (script_failure)",
        *(f"  {line}" for line in api_outputs.SAD_JOB_LOG_FAILED.splitlines()),
    )


async def test_merge_queue_a_failed_run_whose_jobs_cannot_be_read_is_not_infra(cli, tmp_path):
    """No failed job to name must mean GitHub said there is none, never that the
    read failed: `ci.is_infra_red` takes an empty list for a runner problem and
    would retry a code failure instead of repairing it."""
    routes = _queue_routes(
        _graphql(removal="Required status checks failed"),
        [_merge_group_run()],
        **{_JOBS: Api(502, '{"message": "502"}')},
    )
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert got.failure is not None and got.cause == "code"
    assert [j.name for j in got.failure.failed_jobs] == ["(unreadable)"]
    assert not forge.ci.is_infra_red(got.failure)


@pytest.mark.parametrize(
    "broken, answer",
    [
        pytest.param("api graphql", Api(404, '{"message": "Not Found"}'), id="graphql-404"),
        pytest.param("api graphql", Api(502, '{"message": "Bad Gateway"}'), id="graphql-502"),
        pytest.param("api graphql", FAIL, id="graphql-exit-1"),
        pytest.param(api_outputs.FIND, FailWith("gh: HTTP 502 Bad Gateway"), id="pr-list-502"),
        pytest.param(api_outputs.FIND, FAIL, id="pr-list-exit-1"),
        # A payload without the queue is no answer, not an answer of "no queue".
        pytest.param("api graphql", '{"data":{"repository":null}}', id="graphql-no-repository"),
        pytest.param("api graphql", '{"data":null}', id="graphql-no-data"),
        pytest.param("api graphql", "[]", id="graphql-not-an-object"),
    ],
)
async def test_merge_queue_does_not_read_a_failed_call_as_no_queue_or_a_drop(
    cli, tmp_path, broken, answer
):
    """A read that failed says nothing: Kraft would otherwise take a merge
    request still in the queue for one that left it, and ask to merge it twice
    (Kraft-l98h6)."""
    cli.stub("gh", routes={**_queue_routes(_graphql(entry="QUEUED")), broken: answer}, default=FAIL)

    with pytest.raises(forge.ForgeError):
        await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")


async def test_merge_queue_with_no_pull_request_is_no_queue_read(cli, tmp_path):
    cli.stub("gh", routes={api_outputs.FIND: api_outputs.find_result()}, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert got == forge.QueueRead()
    assert cli.argv("gh").count("graphql") == 1, "only the lookup was asked"


async def test_merge_queue_asks_for_the_base_branch_queue_of_this_pull_request(cli, tmp_path):
    cli.stub("gh", routes=_queue_routes(_graphql(entry="QUEUED")), default=FAIL)

    await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="release")

    argv = cli.argv("gh")
    fields = dict(a.split("=", 1) for a in argv if re.match(r"(owner|name|base|n)=", a))
    assert argv[argv.index("graphql") - 1] == "api"
    assert (fields["base"], fields["n"]) == ("release", "7")
    # the branch is a raw string (`-f`), the number typed (`-F`)
    assert argv[argv.index("base=release") - 1] == "-f"
    assert argv[argv.index("n=7") - 1] == "-F"
    assert (fields["owner"], fields["name"]) == ("{owner}", "{repo}")
    assert "mergeQueue(branch: $base)" in "\n".join(argv)
