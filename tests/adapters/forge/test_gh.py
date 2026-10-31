"""GhCli: GitHub through `gh`. Everything here runs against a stubbed `gh`
(and sometimes `git`) on PATH; nothing touches the network. What glab
promises too is in test_forge_cli_contract.py."""

from __future__ import annotations

import pytest

from kraft.adapters import forge

from .api_outputs import (
    CONFLICT_PR,
    NEEDS_APPROVAL_PR,
    READ_BRANCH,
    READ_NUMBER,
    check_run,
    contexts_page,
    pr_by_branch,
    pr_by_number,
    pull_request,
    runs,
    status_context,
    workflow_run,
)
from .nodes import FAIL

PR = forge.MR(7, "http://x/7")

GH_RUN_LIST = runs(workflow_run(123456, name="build"))
_RUNS = "api*repos/{owner}/{repo}/actions/runs -X GET*"


def _gh(cli, pr: dict, routes: dict[str, str] | None = None) -> None:
    """`gh` answering the lookup of `pr` by number or by branch, each of
    `routes` (`{"run list": ...}`) with its own shape, and failing any other call."""
    answers = {READ_NUMBER: pr_by_number(pr), READ_BRANCH: pr_by_branch(pr), **(routes or {})}
    cli.stub("gh", routes=answers, default=FAIL)


def _view(*checks: dict, **fields) -> dict:
    """A pull request whose head commit's rollup holds `checks`."""
    return pull_request(contexts_page(list(checks)), **fields)


@pytest.mark.parametrize(
    "view, state, jobs",
    [
        # One red check is a red rollup: green build, red lint.
        (
            _view(check_run("build", "SUCCESS"), check_run("lint", "FAILURE")),
            "failed",
            ("build: SUCCESS", "lint: FAILURE"),
        ),
        # Kraft-n70: `test.yml` re-triggers on `labeled`, so a label Kraft sets
        # mid-check starts a second run; the superseded run's CANCELLED job
        # must not outvote the new run's later SUCCESS of the same name.
        (
            _view(
                check_run("test", "CANCELLED", startedAt="2026-09-18T00:15:33Z"),
                check_run("test", "SUCCESS", startedAt="2026-09-18T00:17:03Z"),
            ),
            "success",
            ("test: SUCCESS",),
        ),
        # Kraft-4pqnf: a job skipped by its own `if:` (`deploy` only on main)
        # is settled -- without SKIPPED in the success set, one such job pinned
        # every PR carrying it to "pending" forever.
        (
            _view(check_run("test", "SUCCESS"), check_run("deploy", "SKIPPED")),
            "success",
            ("test: SUCCESS", "deploy: SKIPPED"),
        ),
    ],
    ids=["any-failed-check-fails", "latest-run-of-a-relabeled-check-wins", "skipped-is-settled"],
)
async def test_gh_ci_status_reads_the_check_rollup(cli, tmp_path, view, state, jobs):
    _gh(cli, view)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert (status.state, status.jobs) == (state, jobs)


@pytest.mark.parametrize(
    "checks, state, failed, cancelled_at",
    [
        # Kraft-zn8me / Kraft-7g5h4 / Kraft-50bhi: GitHub cancels the superseded
        # run's jobs seconds before it registers the successor's checks, so a
        # poll in that window sees only CANCELLED rows (plus one still running).
        # A cancelled run is never a verdict: that is a wait.
        (
            (
                check_run("test", "CANCELLED", completedAt="2026-09-18T00:15:33Z"),
                check_run("lint", "CANCELLED"),
                check_run("playwright", None),
            ),
            "pending",
            (),
            "",
        ),
        # Every check settled, one cancelled: still a wait, and when the
        # cancel happened, for `render_ci` to tell a successor from none
        # (Kraft-kbqmk). The latest cancel is the one that counts.
        (
            (
                check_run("test", "CANCELLED", completedAt="2026-09-18T00:15:33Z"),
                check_run("lint", "CANCELLED", completedAt="2026-09-18T00:16:00Z"),
                check_run("build", "SUCCESS", completedAt="2026-09-18T00:17:00Z"),
            ),
            "pending",
            (),
            "2026-09-18T00:16:00Z",
        ),
        # A cancelled sibling does not hide a real failure, and is not one of
        # the failed jobs a fix loop is handed.
        (
            (
                check_run("test", "CANCELLED", completedAt="2026-09-18T00:15:33Z"),
                check_run("lint", "FAILURE"),
            ),
            "failed",
            ("lint",),
            "",
        ),
    ],
    ids=["cancelled-beside-running", "only-cancelled", "cancelled-beside-a-failure"],
)
async def test_gh_ci_status_never_reads_a_cancelled_check_as_a_verdict(
    cli, tmp_path, checks, state, failed, cancelled_at
):
    _gh(cli, _view(*checks))

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert status.state == state
    assert tuple(j.name for j in status.failed_jobs) == failed
    assert status.cancelled_at == cancelled_at


@pytest.mark.parametrize("view", [CONFLICT_PR, NEEDS_APPROVAL_PR], ids=["conflict", "approval"])
async def test_gh_ci_status_reads_the_merge_state_from_the_same_pr_view(cli, tmp_path, view):
    """No extra process on GitHub: `mergeable`, `mergeStateStatus` and
    `reviewDecision` -- the field that tells a pending-approval BLOCKED from any
    other -- ride on the one GraphQL call the node already makes."""
    _gh(cli, view)

    await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    argv = cli.argv("gh")
    assert argv.count("graphql") == 1, "a second round trip for a fact one call already carries"
    query = "\n".join(argv)
    assert all(f in query for f in ("mergeable", "mergeStateStatus", "reviewDecision"))


async def test_gh_ci_status_names_the_conflict_detail(cli, tmp_path):
    _gh(cli, CONFLICT_PR)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert "CONFLICTING" in status.merge_detail


async def test_gh_ci_status_flags_a_conflicting_pr_with_no_checks(cli, tmp_path):
    """Kraft-09ze6: GitHub registers no checks on a pull request that conflicts
    with its base, and `render_ci` needs both facts to stop waiting for them."""
    _gh(cli, _view(mergeable="CONFLICTING"))

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert (status.state, status.no_checks, status.mergeable) == ("pending", True, False)


async def test_gh_ci_status_maps_timed_out_to_the_infra_reason(cli, tmp_path):
    _gh(cli, _view(check_run("build", "TIMED_OUT", run=123456)))

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert status.failed_jobs[0].failure_reason == "job_execution_timeout"


@pytest.mark.parametrize(
    "view, sha",
    [
        # `sha = headRefOid` made render_ci's freshness guard compare the branch
        # head to itself, so it could never fire on GitHub: it is the commit the
        # rollup was read from.
        (
            _view(check_run("build", "TIMED_OUT", run=123456), oid="oldsha", headRefOid="newer"),
            "oldsha",
        ),
        (
            _view(check_run("build", "SUCCESS", run=111), check_run("lint", "SUCCESS", run=222)),
            "abc123",
        ),
        # No Actions run behind the checks (another app's check run) names no sha.
        (_view(check_run("build", "SUCCESS")), ""),
        # Nor does a rollup of commit statuses alone, or one with nothing in it.
        (_view(status_context("ci/x", "SUCCESS")), ""),
        (_view(), ""),
    ],
    ids=[
        "the-checks-sha-not-the-pr-head",
        "several-runs-of-one-sha",
        "empty-when-it-cannot-be-told",
        "empty-with-only-commit-statuses",
        "empty-without-checks",
    ],
)
async def test_gh_ci_status_sha_is_the_checks_own(cli, tmp_path, view, sha):
    _gh(cli, view)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert status.sha == sha  # never the PR's headRefOid, "newer"


async def test_gh_branch_ci_status_reads_runs_not_a_pull_request(cli, tmp_path):
    """merge_watch's whole reason to call this instead of `ci_status`: the
    checked-out branch has no open PR left post-merge, so this must never
    shell out to `gh pr view` (code-review)."""
    cli.stub("gh", GH_RUN_LIST)

    status = await forge.GhCli().branch_ci_status(repo=tmp_path, branch="main", head_sha="deadbeef")

    assert (status.state, status.sha) == ("failed", "deadbeef")
    assert status.failed_jobs[0].failure_reason == "script_failure"
    assert status.failed_jobs[0].detail_url == "https://github.com/o/r/actions/runs/123456"
    assert cli.calls("gh") == [
        "api repos/{owner}/{repo}/actions/runs -X GET -i "
        "-f branch=main -f head_sha=deadbeef -F per_page=20"
    ]


async def test_gh_branch_ci_status_finds_a_commits_runs_on_a_busy_branch(cli, tmp_path):
    """Kraft-h6wi2: the branch's newest 20 runs are all newer commits', so the
    watched commit is found by asking for its own runs, not by filtering those."""
    busy = runs(*(workflow_run(n, head_sha=f"newer{n}") for n in range(20)))
    watched = runs(workflow_run(99, head_sha="deadbeef", conclusion="success"))
    cli.stub("gh", routes={"api*head_sha=deadbeef*": watched}, default=busy)

    status = await forge.GhCli().branch_ci_status(repo=tmp_path, branch="main", head_sha="deadbeef")

    assert (status.state, status.sha, status.no_checks) == ("success", "deadbeef", False)


_CHECK_RUNS = "api repos/{owner}/{repo}/commits/other-sha/check-runs"
_STATUS = "api repos/{owner}/{repo}/commits/other-sha/status"


@pytest.mark.parametrize(
    "api, no_checks",
    [
        # No run and nothing else reported for this head (Kraft-rbjo3):
        # `merge_watch` gives up after a grace.
        ({_CHECK_RUNS: '{"check_runs":[]}', _STATUS: '{"statuses":[]}'}, True),
        # CI from another app (Kraft-5c1tm.19) is a wait, never "no CI".
        ({_CHECK_RUNS: '{"check_runs":[{"name":"buildkite"}]}'}, False),
        (
            {_CHECK_RUNS: '{"check_runs":[]}', _STATUS: '{"statuses":[{"context":"ci/x"}]}'},
            False,
        ),
        # An unreadable answer is a reason to wait, not to pass.
        ({}, False),
    ],
    ids=["nothing-reported", "external-check-run", "commit-status", "api-fails"],
)
async def test_gh_branch_ci_status_waits_for_a_run_matching_the_given_head(
    cli, tmp_path, api, no_checks
):
    """A run list that has not caught up to `head_sha` yet is a wait, not a
    (wrong-commit) result -- same shape as glab's own sha guard."""
    cli.stub("gh", routes={_RUNS: GH_RUN_LIST, **api}, default=FAIL)

    status = await forge.GhCli().branch_ci_status(
        repo=tmp_path, branch="main", head_sha="other-sha"
    )

    assert (status.state, status.no_checks) == ("pending", no_checks)


def _run(name: str, conclusion: str, created: str) -> dict:
    return workflow_run(
        1,
        name=name,
        conclusion=conclusion,
        created_at=created,
        updated_at=created.replace(":00Z", ":09Z"),
    )


@pytest.mark.parametrize(
    "rows, state, cancelled_at",
    [
        # The same invariant post-merge (Kraft-zn8me): a concurrency-cancelled
        # run on the target branch is a wait for its successor, not red --
        # and says when it was cancelled (Kraft-kbqmk).
        ((_run("test", "cancelled", "2026-09-19T05:51:00Z"),), "pending", "2026-09-19T05:51:09Z"),
        # Once the successor for the same head exists, it is the verdict.
        (
            (
                _run("test", "success", "2026-09-19T05:52:00Z"),
                _run("test", "cancelled", "2026-09-19T05:51:00Z"),
            ),
            "success",
            "",
        ),
        (
            (
                _run("test", "cancelled", "2026-09-19T05:51:00Z"),
                _run("test", "failure", "2026-09-19T05:52:00Z"),
            ),
            "failed",
            "",
        ),
    ],
    ids=["only-cancelled", "successor-green", "successor-red"],
)
async def test_gh_branch_ci_status_judges_the_latest_run_never_a_cancelled_one(
    cli, tmp_path, rows, state, cancelled_at
):
    cli.stub("gh", runs(*rows))

    status = await forge.GhCli().branch_ci_status(repo=tmp_path, branch="main", head_sha="deadbeef")

    assert (status.state, status.cancelled_at) == (state, cancelled_at)
    assert all(j.failure_reason != "cancelled" for j in status.failed_jobs)
    assert not status.no_checks, "a head with a run has checks, even a pending one"


async def test_gh_mr_body_reads_the_live_description(cli, tmp_path):
    cli.stub("gh", routes={READ_BRANCH: pr_by_branch(pull_request(body="the live body"))})

    assert await forge.GhCli().mr_body(repo=tmp_path, branch="b") == "the live body"
    argv = cli.argv("gh")
    assert "b=b" in argv and "statusCheckRollup" not in "\n".join(argv)
