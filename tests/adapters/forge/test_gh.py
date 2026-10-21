"""GhCli: GitHub through `gh`. Everything here runs against a stubbed `gh`
(and sometimes `git`) on PATH; nothing touches the network. What glab
promises too is in test_forge_cli_contract.py."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from kraft.adapters import forge

from .nodes import FAIL, FailWith
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


def _gh(cli, view: str, routes: dict[str, str] | None = None) -> None:
    """`gh` answering `gh pr view` with `view`, each of `routes` (keyed on the
    subcommand, `{"run list": ...}`) with its own shape, and failing any
    other call."""
    cli.stub("gh", routes={"pr view": view, **(routes or {})}, default=FAIL)


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
                '{"name":"test","conclusion":"CANCELLED","completedAt":"2026-09-18T00:15:33Z"}',
                '{"name":"lint","conclusion":"CANCELLED"}',
                '{"name":"playwright","conclusion":""}',
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
                '{"name":"test","conclusion":"CANCELLED","completedAt":"2026-09-18T00:15:33Z"}',
                '{"name":"lint","conclusion":"CANCELLED","completedAt":"2026-09-18T00:16:00Z"}',
                '{"name":"build","conclusion":"SUCCESS","completedAt":"2026-09-18T00:17:00Z"}',
            ),
            "pending",
            (),
            "2026-09-18T00:16:00Z",
        ),
        # A cancelled sibling does not hide a real failure, and is not one of
        # the failed jobs a fix loop is handed.
        (
            (
                '{"name":"test","conclusion":"CANCELLED","completedAt":"2026-09-18T00:15:33Z"}',
                '{"name":"lint","conclusion":"FAILURE"}',
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
    _gh(cli, _rollup(*checks))

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert status.state == state
    assert tuple(j.name for j in status.failed_jobs) == failed
    assert status.cancelled_at == cancelled_at


@pytest.mark.parametrize(
    "view", [GH_PR_VIEW_CONFLICT, GH_PR_VIEW_NEEDS_APPROVAL], ids=["conflict", "approval"]
)
async def test_gh_ci_status_reads_the_merge_state_from_the_same_pr_view(cli, tmp_path, view):
    """No extra process on GitHub: `mergeable`, `mergeStateStatus` and
    `reviewDecision` -- the field that tells a pending-approval BLOCKED from any
    other -- ride on the `gh pr view` call the node already makes."""
    _gh(cli, view)

    await forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""))

    argv = cli.argv("gh")
    assert argv.count("--json") == 1, "a second round trip for a fact one call already carries"
    fields = argv[argv.index("--json") + 1].split(",")
    assert {"mergeable", "mergeStateStatus", "reviewDecision"} <= set(fields)


async def test_gh_ci_status_names_the_conflict_detail(cli, tmp_path):
    _gh(cli, GH_PR_VIEW_CONFLICT)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""))

    assert "CONFLICTING" in status.merge_detail


async def test_gh_ci_status_flags_a_conflicting_pr_with_no_checks(cli, tmp_path):
    """Kraft-09ze6: GitHub registers no checks on a pull request that conflicts
    with its base, and `render_ci` needs both facts to stop waiting for them."""
    _gh(cli, _rollup().replace('"number":7', '"number":7,"mergeable":"CONFLICTING"'))

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""))

    assert (status.state, status.no_checks, status.mergeable) == ("pending", True, False)


async def test_gh_ci_status_maps_timed_out_to_the_infra_reason(cli, tmp_path):
    _gh(cli, GH_PR_VIEW_TIMED_OUT)

    status = await forge.GhCli().ci_status(repo=tmp_path, mr=PR)

    assert status.failed_jobs[0].failure_reason == "job_execution_timeout"


#: Two checks, each from its own Actions run (111 and 222).
_TWO_RUNS = _rollup(
    '{"name":"build","conclusion":"SUCCESS",'
    '"detailsUrl":"https://github.com/o/r/actions/runs/111/job/1"}',
    '{"name":"lint","conclusion":"SUCCESS",'
    '"detailsUrl":"https://github.com/o/r/actions/runs/222/job/2"}',
)


@pytest.mark.parametrize(
    "view, run_list, sha",
    [
        # `sha = headRefOid` made render_ci's freshness guard compare the branch
        # head to itself, so it could never fire on GitHub.
        (GH_PR_VIEW_TIMED_OUT, '[{"databaseId":123456,"headSha":"oldsha"}]', "oldsha"),
        # `gh run list` lists every recent run of the repository; only the ones
        # the rollup names say what the checks were built from.
        (
            GH_PR_VIEW_TIMED_OUT,
            '[{"databaseId":999,"headSha":"newer"},{"databaseId":123456,"headSha":"oldsha"}]',
            "oldsha",
        ),
        (
            _TWO_RUNS,
            '[{"databaseId":111,"headSha":"s1"},{"databaseId":222,"headSha":"s1"}]',
            "s1",
        ),
        # A rollup mixing two heads has no one sha to hand the freshness guard.
        (
            _TWO_RUNS,
            '[{"databaseId":111,"headSha":"s1"},{"databaseId":222,"headSha":"s2"}]',
            "",
        ),
        # The guard stays inert rather than guess.
        (GH_PR_VIEW_TIMED_OUT, "{}", ""),
    ],
    ids=[
        "the-checks-sha-not-the-pr-head",
        "only-the-rollups-runs-count",
        "several-runs-of-one-sha",
        "empty-when-the-checks-span-shas",
        "empty-when-it-cannot-be-told",
    ],
)
async def test_gh_ci_status_sha_is_the_checks_own(cli, tmp_path, view, run_list, sha):
    _gh(cli, view, {"run list": run_list})

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
        "run list --branch main -L 20 --json status,conclusion,headSha,url,name,createdAt,updatedAt"
    ]


async def test_gh_branch_ci_status_waits_for_a_run_matching_the_given_head(cli, tmp_path):
    """A run list that has not caught up to `head_sha` yet is a wait, not a
    (wrong-commit) result -- same shape as glab's own sha guard."""
    cli.stub("gh", GH_RUN_LIST)

    status = await forge.GhCli().branch_ci_status(
        repo=tmp_path, branch="main", head_sha="other-sha"
    )

    assert status.state == "pending"


def _run(name: str, conclusion: str, created: str) -> str:
    return (
        f'{{"status":"completed","conclusion":"{conclusion}","headSha":"deadbeef",'
        f'"url":"https://github.com/o/r/actions/runs/1","name":"{name}","createdAt":"{created}",'
        f'"updatedAt":"{created.replace(":00Z", ":09Z")}"}}'
    )


@pytest.mark.parametrize(
    "runs, state, cancelled_at",
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
    cli, tmp_path, runs, state, cancelled_at
):
    cli.stub("gh", "[" + ",".join(runs) + "]")

    status = await forge.GhCli().branch_ci_status(repo=tmp_path, branch="main", head_sha="deadbeef")

    assert (status.state, status.cancelled_at) == (state, cancelled_at)
    assert all(j.failure_reason != "cancelled" for j in status.failed_jobs)


async def test_gh_set_labels_edits_the_pull_request(cli, tmp_path):
    """GitHub re-evaluates `pull_request: types: [labeled]` itself, so there is
    no pipeline to re-create here -- only the label to add."""
    cli.stub("gh", routes={"pr view": '{"labels":[]}', "pr edit": ""})

    await forge.GhCli().set_labels(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, "u"), labels=("release::patch", "bug")
    )

    (edit,) = [c for c in cli.calls("gh") if c.startswith("pr edit")]
    assert edit == "pr edit 7 --add-label release::patch,bug"


@pytest.mark.parametrize(
    "current, edit",
    [
        (
            '[{"name":"release::patch"},{"name":"bug"},{"name":"area::ui"}]',
            "pr edit 7 --add-label release::minor --remove-label release::patch",
        ),
        # Setting the label the PR already has removes nothing.
        ('[{"name":"release::minor"}]', "pr edit 7 --add-label release::minor"),
    ],
    ids=["other-scoped-label-dropped", "same-label-kept"],
)
async def test_gh_set_labels_replaces_an_existing_same_scope_label(cli, tmp_path, current, edit):
    """Kraft-o9xh1, as glab does (Kraft-zfdu8): `--add-label` only adds, so a
    repair pass that moves release::patch to release::minor left both on the PR
    and `next_tag.py` raises on more than one. The label sharing a new label's
    `scope::` prefix is removed in the same edit; other labels stay."""
    cli.stub("gh", routes={"pr view": f'{{"labels":{current}}}', "pr edit": ""})

    await forge.GhCli().set_labels(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, "u"), labels=("release::minor",)
    )

    assert cli.calls("gh") == ["pr view 7 --json labels", edit]


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


async def test_gh_rerun_after_description_change_reruns_without_a_new_pipeline(cli, tmp_path):
    cli.stub("gh", "")
    job = forge.FailedJob("build", "failed", None, "https://github.com/o/r/actions/runs/77/job/9")

    made_new = await forge.GhCli().rerun_after_description_change(
        repo=tmp_path,
        branch="b",
        ci=forge.CIStatus(state="failed", url="u", failed_jobs=(job,)),
    )

    assert made_new is False
    assert cli.argv("gh") == ["run", "rerun", "77", "--failed"]


async def test_gh_mr_body_reads_the_live_description(cli, tmp_path):
    cli.stub("gh", routes={"pr view": '{"body":"the live body"}'})

    assert await forge.GhCli().mr_body(repo=tmp_path, branch="b") == "the live body"
    assert cli.argv("gh") == ["pr", "view", "b", "--json", "body"]


_PR_URL = "https://github.com/o/r/pull/7"
_RUN_URL = "https://github.com/o/r/actions/runs/555"
_QUEUE_BRANCH = "gh-readonly-queue/main/pr-7-" + "a" * 40
_JOBS = "api repos/{owner}/{repo}/actions/runs/555/jobs?per_page=100"


def _graphql(
    *,
    queue: bool = True,
    state: str = "OPEN",
    entry: str | None = None,
    removal: str | None = None,
    removed_at: str = "2026-10-21T10:00:00Z",
    enqueued_at: str | None = "2026-10-21T09:00:00Z",
) -> str:
    """`gh api graphql`'s answer for the merge queue query: the base's queue
    (or none), and the pull request's state, queue entry and last removal."""
    removals = [{"reason": removal, "createdAt": removed_at}] if removal else []
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
    return {
        "createdAt": created_at,
        "databaseId": id,
        "headBranch": branch,
        "conclusion": conclusion,
        "url": f"https://github.com/o/r/actions/runs/{id}",
    }


def _queue_routes(graphql: str, runs: list[dict] | None = None, **routes: str) -> dict:
    return {
        "pr list": json.dumps([{"number": 7, "url": _PR_URL, "state": "OPEN"}]),
        "api graphql": graphql,
        "run list": json.dumps(runs or []),
        _JOBS: json.dumps(
            {
                "jobs": [
                    {"id": 1, "name": "lint", "conclusion": "success"},
                    {"id": 2, "name": "test", "conclusion": "failure", "html_url": _RUN_URL},
                ]
            }
        ),
        "run view": "test\tRun tests\t2026-10-21T10:00:00Z FAILED tests/test_x.py::test_y",
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
        pytest.param(
            _queue_routes(_graphql()),
            _queue(
                state="dropped",
                cause="other",
                reason="dropped from the merge queue; GitHub gave no reason",
            ),
            None,
            id="dropped-no-reason",
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
            _queue(
                state="dropped",
                cause="other",
                reason="dropped from the merge queue; GitHub gave no reason",
            ),
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
    ],
)
async def test_merge_queue_reads_the_queue(cli, tmp_path, routes, expected, failure_state):
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert (got.failure is not None) == (failure_state is not None)
    assert got.failure is None or got.failure.state == failure_state
    assert replace(got, failure=None) == expected
    if expected.kind == "none":
        assert not any(c.startswith("run ") for c in cli.calls("gh")), "asked a repo with no queue"


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
        pytest.param(
            [_run_for(_QUEUE_BRANCH, "cancelled", id=700), _run_for(_QUEUE_BRANCH, id=555)],
            555,
            id="not-a-cancelled-run",
        ),
        pytest.param([_run_for(_QUEUE_BRANCH, "success", id=700)], None, id="not-a-green-run"),
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
        assert (got.failure, got.cause, got.url) == (None, "other", _PR_URL)
    else:
        assert got.failure is not None and got.failure.url.endswith(f"/runs/{run_id}")
        assert got.cause == "code"
    run_lists = [c for c in cli.calls("gh") if c.startswith("run list")]
    assert len(run_lists) == 1 and "--event merge_group" in run_lists[0]
    assert "createdAt" in run_lists[0]


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
    log = "\n".join(f"job1\tstep\t2026-10-21T10:00:00Z line {n}" for n in range(1, 31))
    routes = _queue_routes(
        _graphql(removal="Required status checks failed"),
        [_merge_group_run()],
        **{_JOBS: json.dumps(jobs), "run view": log},
    )
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    failure = got.failure
    assert failure is not None and failure.sha == ""
    assert [j.name for j in failure.failed_jobs] == ["job1", "job2", "job3"]
    assert all(j.failure_reason == "script_failure" for j in failure.failed_jobs)
    assert failure.failed_jobs[0].detail_url == f"{_RUN_URL}/job/1"
    views = [c for c in cli.calls("gh") if c.startswith("run view")]
    assert views == [f"run view 555 --log-failed --job {i}" for i in (1, 2)]
    tails = [line for line in failure.jobs if line.startswith("  ")]
    assert len(tails) == 30
    assert tails[0].endswith("line 16") and tails[14].endswith("line 30")
    assert not any("line 15" in line for line in failure.jobs)


async def test_merge_queue_a_failed_run_whose_jobs_cannot_be_read_is_not_infra(cli, tmp_path):
    """No failed job to name must mean GitHub said there is none, never that the
    read failed: `ci.is_infra_red` takes an empty list for a runner problem and
    would retry a code failure instead of repairing it."""
    routes = _queue_routes(
        _graphql(removal="Required status checks failed"),
        [_merge_group_run()],
        **{_JOBS: FailWith("gh: HTTP 502")},
    )
    cli.stub("gh", routes=routes, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert got.failure is not None and got.cause == "code"
    assert [j.name for j in got.failure.failed_jobs] == ["(unreadable)"]
    assert not forge.ci.is_infra_red(got.failure)


@pytest.mark.parametrize(
    "broken, answer",
    [
        pytest.param("api graphql", FailWith("gh: HTTP 404 not found"), id="graphql-404"),
        pytest.param("api graphql", FailWith("gh: HTTP 502 Bad Gateway"), id="graphql-502"),
        pytest.param("api graphql", FAIL, id="graphql-exit-1"),
        pytest.param("pr list", FailWith("gh: HTTP 502 Bad Gateway"), id="pr-list-502"),
        pytest.param("pr list", FAIL, id="pr-list-exit-1"),
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
    cli.stub("gh", routes={"pr list": "[]"}, default=FAIL)

    got = await forge.GhCli().merge_queue(repo=tmp_path, branch="kraft/abc", base="main")

    assert got == forge.QueueRead()
    assert [c.split()[:2] for c in cli.calls("gh")] == [["pr", "list"]]


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
