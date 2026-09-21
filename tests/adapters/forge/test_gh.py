"""GhCli: GitHub through `gh`. Everything here runs against a stubbed `gh`
(and sometimes `git`) on PATH; nothing touches the network. What glab
promises too is in test_forge_cli_contract.py."""

from __future__ import annotations

import pytest

from kraft.adapters import forge

from .outputs import GH_PR_VIEW, GH_PR_VIEW_CONFLICT, GH_PR_VIEW_NEEDS_APPROVAL

PR = forge.MR(7, "http://x/7")

GH_PR_VIEW_TIMED_OUT = (
    '{"number":7,"url":"https://github.com/o/r/pull/7","headRefOid":"abc123",'
    '"statusCheckRollup":[{"name":"build","conclusion":"TIMED_OUT",'
    '"detailsUrl":"https://github.com/o/r/actions/runs/123456/job/9"}]}'
)

GH_RUN_LIST = (
    '[{"status":"completed","conclusion":"failure","headSha":"deadbeef",'
    '"url":"https://github.com/o/r/actions/runs/123456","name":"build"}]'
)


def _rollup(*checks: str) -> str:
    rollup = ",".join(checks)
    return f'{{"number":7,"url":"https://github.com/o/r/pull/7","statusCheckRollup":[{rollup}]}}'


@pytest.mark.parametrize(
    "view, state, jobs",
    [
        # One red check is a red rollup: green build, red lint.
        (GH_PR_VIEW, "failed", ("build: SUCCESS", "lint: FAILURE")),
        # Kraft-n70: `test.yml` re-triggers on `labeled`, so a label Kraft sets
        # mid-check starts a second run; the superseded run's CANCELLED job
        # must not outvote the new run's later SUCCESS of the same name.
        (
            _rollup(
                '{"name":"test","conclusion":"CANCELLED","startedAt":"2026-09-18T00:15:33Z"}',
                '{"name":"test","conclusion":"SUCCESS","startedAt":"2026-09-18T00:17:03Z"}',
            ),
            "success",
            ("test: SUCCESS",),
        ),
        # Kraft-4pqnf: a job skipped by its own `if:` (`deploy` only on main)
        # is settled -- without SKIPPED in the success set, one such job pinned
        # every PR carrying it to "pending" forever.
        (
            _rollup(
                '{"name":"test","conclusion":"SUCCESS"}', '{"name":"deploy","conclusion":"SKIPPED"}'
            ),
            "success",
            ("test: SUCCESS", "deploy: SKIPPED"),
        ),
    ],
    ids=["any-failed-check-fails", "latest-run-of-a-relabeled-check-wins", "skipped-is-settled"],
)
async def test_gh_ci_status_reads_the_check_rollup(cli, tmp_path, view, state, jobs):
    cli.stub("gh", view)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert (status.state, status.jobs) == (state, jobs)


@pytest.mark.parametrize(
    "view", [GH_PR_VIEW_CONFLICT, GH_PR_VIEW_NEEDS_APPROVAL], ids=["conflict", "approval"]
)
async def test_gh_ci_status_reads_the_merge_state_from_the_same_pr_view(cli, tmp_path, view):
    """No extra process on GitHub: `mergeable`, `mergeStateStatus` and
    `reviewDecision` -- the field that tells a pending-approval BLOCKED from any
    other -- ride on the `gh pr view` call the node already makes."""
    cli.stub("gh", view)

    await forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""))

    argv = cli.argv("gh")
    assert argv.count("--json") == 1, "a second round trip for a fact one call already carries"
    fields = argv[argv.index("--json") + 1].split(",")
    assert {"mergeable", "mergeStateStatus", "reviewDecision"} <= set(fields)


async def test_gh_ci_status_names_the_conflict_detail(cli, tmp_path):
    cli.stub("gh", GH_PR_VIEW_CONFLICT)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""))

    assert "CONFLICTING" in status.merge_detail


async def test_gh_ci_status_maps_timed_out_to_the_infra_reason(cli, tmp_path):
    cli.stub("gh", GH_PR_VIEW_TIMED_OUT)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert status.failed_jobs[0].failure_reason == "job_execution_timeout"


@pytest.mark.parametrize(
    "run_list, sha",
    [
        # `sha = headRefOid` made render_ci's freshness guard compare the branch
        # head to itself, so it could never fire on GitHub.
        ('[{"databaseId":123456,"headSha":"oldsha"}]', "oldsha"),
        # The guard stays inert rather than guess.
        ("{}", ""),
    ],
    ids=["the-checks-sha-not-the-pr-head", "empty-when-it-cannot-be-told"],
)
async def test_gh_ci_status_sha_is_the_checks_own(cli, tmp_path, run_list, sha):
    cli.stub("gh", routes={"run list": run_list}, default=GH_PR_VIEW_TIMED_OUT)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert status.sha == sha  # never the PR's headRefOid, "abc123"


async def test_gh_branch_ci_status_reads_runs_not_a_pull_request(cli, tmp_path):
    """merge_watch's whole reason to call this instead of `ci_status`: the
    checked-out branch has no open PR left post-merge, so this must never
    shell out to `gh pr view` (code-review)."""
    cli.stub("gh", GH_RUN_LIST)

    status = await forge.GhCli().branch_ci_status(repo=tmp_path, branch="main", head_sha="deadbeef")

    assert (status.state, status.sha) == ("failed", "deadbeef")
    assert status.failed_jobs[0].failure_reason == "script_failure"
    assert cli.calls("gh") == [
        "run list --branch main -L 20 --json status,conclusion,headSha,url,name"
    ]


async def test_gh_branch_ci_status_waits_for_a_run_matching_the_given_head(cli, tmp_path):
    """A run list that has not caught up to `head_sha` yet is a wait, not a
    (wrong-commit) result -- same shape as glab's own sha guard."""
    cli.stub("gh", GH_RUN_LIST)

    status = await forge.GhCli().branch_ci_status(
        repo=tmp_path, branch="main", head_sha="other-sha"
    )

    assert status.state == "pending"


async def test_gh_set_labels_edits_the_pull_request(cli, tmp_path):
    """GitHub re-evaluates `pull_request: types: [labeled]` itself, so there is
    no pipeline to re-create here -- only the label to add."""
    cli.stub("gh", "")

    await forge.GhCli().set_labels(
        repo=tmp_path, mr=forge.MR(7, "u"), labels=("release::patch", "bug")
    )

    argv = cli.argv("gh")
    assert argv[:2] == ["pr", "edit"]
    assert argv[argv.index("--add-label") + 1] == "release::patch,bug"


async def test_gh_retry_jobs_reruns_the_actions_run_behind_the_failed_check(cli, tmp_path):
    cli.stub("gh", "")
    job = forge.FailedJob(
        "build",
        "failed",
        "job_execution_timeout",
        "https://github.com/o/r/actions/runs/123456/job/9",
    )

    await forge.GhCli().retry_jobs(
        repo=tmp_path, ci=forge.CIStatus(state="failed", url="u", failed_jobs=(job,))
    )

    assert cli.argv("gh") == ["run", "rerun", "123456", "--failed"]
