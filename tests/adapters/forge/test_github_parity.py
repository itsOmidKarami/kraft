"""`GitHubApi` and `GhCli` run one operation against the same `gh` stub routes and must return
the same result and send the same requests (`GhOverHttp` renders the HTTP the API backend sent
as the argv gh would have had). The body is `parity.assert_alike`, shared with test_forge_parity.py,
which also enforces that each of the two tables, on its own, has a row for every `Forge`
method but `push`."""

from __future__ import annotations

import json
import time

import pytest

from kraft.adapters import forge

from .api_outputs import (
    AUTO_MERGE_ENABLED,
    FIND,
    FIND_MERGED,
    HEAD_MODIFIED,
    MERGED,
    NEEDS_APPROVAL_PR,
    READ_BRANCH,
    READ_MORE,
    READ_NUMBER,
    URL,
    check_run,
    checks_page,
    contexts_page,
    find_result,
    gql,
    job,
    listed,
    merge_state,
    pr_by_branch,
    pr_by_number,
    pull_request,
    rest_label,
    rest_pull,
    runs,
    workflow_run,
)
from .nodes import Api
from .parity import BR, Row, assert_alike, feedback, review
from .test_forge_cli_contract import GH_API, HEAD, _contract_token  # noqa: F401

MR7 = forge.MR(7, URL)
QUEUED = forge.MRRef(7, URL, state="open", merge_queued=True)
_PULLS = "repos/{owner}/{repo}/pulls"
_ISSUES = "repos/{owner}/{repo}/issues"
_RUNS = "repos/{owner}/{repo}/actions/runs"
_QUEUE_BRANCH = "gh-readonly-queue/main/pr-7-" + "a" * 40
_RUN_URL = "https://github.com/o/r/actions/runs/555"
_MERGE_READ = "api*graphql*isInMergeQueue*"
_MUTATION = "api*graphql*mutation*"
_BRANCH_PR = {READ_BRANCH: pr_by_branch(pull_request())}
_REVIEW = {"id": 51, "user": {"login": "bot[bot]"}, "commit_id": "abc123", "body": "x"}
_CHECK_RUNS = "api*commits/abc123/check-runs*check_name=c*"
_FAILED_CHECK = json.dumps(
    {"check_runs": [{"status": "completed", "conclusion": "failure", "output": {"title": "t"}}]}
)
_THREAD = {
    "id": "T1",
    "isResolved": False,
    "comments": {
        "nodes": [
            {
                "author": {"login": "bot"},
                "body": "unused x",
                "path": "calc.py",
                "line": None,
                "originalLine": 3,
                "updatedAt": "2026-02-01T00:00:00Z",
            }
        ]
    },
}
_THREADS = gql({"repository": {"pullRequest": {"reviewThreads": {"nodes": [_THREAD]}}}})
_CI_RED = forge.CIStatus(
    state="failed",
    url="",
    failed_jobs=(forge.FailedJob("lint", "failure", "script_failure", f"{_RUN_URL}/job/9"),),
)


def _call(method: str, **kw):
    return lambda f, r: getattr(f, method)(repo=r, **kw)


def _merge(f, r):
    return f.merge(repo=r, branch=BR, mr=MR7, head=HEAD)


def _cancel(held: dict):
    pull = {"id": "PR_1", "autoMergeRequest": None, "mergeQueueEntry": None, **held}
    return Row(
        "cancel_merge",
        lambda f, r: f.cancel_merge(repo=r, branch=BR, mr=QUEUED),
        {"api graphql": gql({"repository": {"pullRequest": pull}})},
    )


def _queue_read(**pull) -> str:
    pull = {"state": "OPEN", "mergeQueueEntry": None, **pull}
    return gql({"repository": {"mergeQueue": {"id": "MQ_1"}, "pullRequest": pull}})


_DROPPED = _queue_read(
    removals={"nodes": [{"reason": "Required status checks failed", "createdAt": "t2"}]},
    enqueues={"nodes": [{"createdAt": "t1"}]},
)
_QUEUE = {
    FIND: find_result(listed()),
    "api graphql": _queue_read(mergeQueueEntry={"state": "QUEUED"}),
}
_FIND = lambda f, r: f.find_mr(repo=r, branch=BR)  # noqa: E731
_PAGE = [rest_label(f"l{i}") for i in range(100)]
_RATE_LIMITED = Api(
    403,
    '{"message": "API rate limit exceeded"}',
    {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 3600)},
)

ROWS: dict[str, Row] = {
    "github-open_mr": Row(
        "open_mr",
        lambda f, r: f.open_mr(repo=r, branch=BR, base="main", title="t", body="b"),
        {f"api*{_PULLS} -X POST*": json.dumps({"number": 7, "html_url": URL})},
    ),
    "github-mark_ready": Row(
        "mark_ready",
        lambda f, r: f.mark_ready(repo=r, branch=BR, mr=MR7),
        {READ_NUMBER: pr_by_number(pull_request(isDraft=True))},
    ),
    "github-update_mr": Row("update_mr", _call("update_mr", branch=BR, body="b"), _BRANCH_PR),
    "github-mr_body": Row(
        "mr_body", _call("mr_body", branch=BR), {READ_BRANCH: pr_by_branch(pull_request(body="b"))}
    ),
    "github-ci_status": Row(
        "ci_status",
        lambda f, r: f.ci_status(repo=r, mr=forge.MR(0, ""), branch=BR),
        {READ_BRANCH: pr_by_branch(pull_request(contexts_page([check_run("build", "SUCCESS")])))},
    ),
    "github-ci_status-two-context-pages": Row(
        "ci_status",
        lambda f, r: f.ci_status(repo=r, mr=forge.MR(0, ""), branch=BR),
        {
            READ_BRANCH: pr_by_branch(
                pull_request(contexts_page([check_run("a", "SUCCESS")], next_cursor="C1"))
            ),
            READ_MORE: checks_page(contexts_page([check_run("b", "FAILURE")])),
        },
    ),
    "github-branch_ci_status": Row(
        "branch_ci_status",
        _call("branch_ci_status", branch="main", head_sha="deadbeef"),
        {
            "api*head_sha=deadbeef*": runs(
                workflow_run(99, head_sha="deadbeef", conclusion="success")
            )
        },
    ),
    "github-merge-off-queue": Row(
        "merge", _merge, {_MERGE_READ: merge_state("CLEAN"), _MUTATION: MERGED}
    ),
    "github-merge-on-queue": Row(
        "merge",
        _merge,
        {_MERGE_READ: merge_state("BLOCKED", queue=True), _MUTATION: AUTO_MERGE_ENABLED},
    ),
    "github-merge-blocked-refused": Row(
        "merge", _merge, {_MERGE_READ: merge_state("BLOCKED"), _MUTATION: MERGED}
    ),
    "github-merge-head-moved": Row(
        "merge",
        _merge,
        {
            _MERGE_READ: merge_state("CLEAN"),
            _MUTATION: HEAD_MODIFIED,
            FIND: find_result(listed(headRefOid="f0reign1")),
        },
    ),
    "github-cancel_merge-auto": _cancel({"autoMergeRequest": {"enabledAt": "t"}}),
    "github-cancel_merge-dequeue": _cancel(
        {"autoMergeRequest": {"enabledAt": "t"}, "mergeQueueEntry": {"id": "Q_1"}}
    ),
    "github-close_mr": Row("close_mr", lambda f, r: f.close_mr(repo=r, mr=QUEUED), {}),
    "github-reopen_mr": Row("reopen_mr", lambda f, r: f.reopen_mr(repo=r, mr=QUEUED), {}),
    "github-set_labels": Row(
        "set_labels",
        lambda f, r: f.set_labels(repo=r, branch=BR, mr=MR7, labels=("release::patch",)),
        {READ_NUMBER: pr_by_number(pull_request(labels={"nodes": [{"name": "release::minor"}]}))},
    ),
    "github-set_labels-unknown-label-refused": Row(
        "set_labels",
        lambda f, r: f.set_labels(repo=r, branch=BR, mr=MR7, labels=("nope",)),
        {
            READ_NUMBER: pr_by_number(pull_request()),
            "api*labels/nope*": Api(404, '{"message": "Not Found"}'),
        },
    ),
    "github-find_mr": Row("find_mr", _FIND, {FIND: FIND_MERGED}),
    "github-merge_queue": Row("merge_queue", _call("merge_queue", branch=BR, base="main"), _QUEUE),
    "github-merge_queue-dropped-reads-the-failed-log": Row(
        "merge_queue",
        _call("merge_queue", branch=BR, base="main"),
        {
            FIND: find_result(listed()),
            "api graphql": _DROPPED,
            f"api*{_RUNS} -X GET*": runs(
                workflow_run(555, head_branch=_QUEUE_BRANCH, created_at="t3", html_url=_RUN_URL)
            ),
            f"api {_RUNS}/555/jobs": json.dumps(
                {"jobs": [job(2, "test", "failure"), job(3, "lint", "success")]}
            ),
            "api*actions/jobs/2/logs*": "2026-10-21T10:00:00.0000000Z FAILED test_y",
        },
    ),
    "github-retry_jobs": Row("retry_jobs", _call("retry_jobs", ci=_CI_RED), {}),
    "github-rerun_after_description_change": Row(
        "rerun_after_description_change",
        _call("rerun_after_description_change", branch=BR, ci=_CI_RED),
        {},
    ),
    "github-approval_state": Row(
        "approval_state",
        _call("approval_state", branch=BR),
        {READ_BRANCH: pr_by_branch(NEEDS_APPROVAL_PR)},
    ),
    "github-bot-review": Row(
        "automated_review",
        review(bot="bot"),
        {
            **_BRANCH_PR,
            f"api {_PULLS}/7/reviews": json.dumps([{**_REVIEW, "state": "CHANGES_REQUESTED"}]),
            f"api {_PULLS}/7/reviews/51/comments": json.dumps(
                [{"path": "calc.py", "line": 3, "body": "rename"}]
            ),
        },
    ),
    "github-check-review": Row(
        "automated_review", review(check="c"), {**_BRANCH_PR, _CHECK_RUNS: _FAILED_CHECK}
    ),
    "github-bot-feedback": Row(
        "reviewer_feedback",
        feedback(bot="bot"),
        {
            **_BRANCH_PR,
            f"api {_ISSUES}/7/comments": json.dumps(
                [
                    {
                        "user": {"login": "bot[bot]"},
                        "body": "hi",
                        "updated_at": "2026-02-01T00:00:00Z",
                    }
                ]
            ),
            f"api {_PULLS}/7/reviews": json.dumps(
                [{**_REVIEW, "state": "COMMENTED", "submitted_at": "2026-02-01T00:00:00Z"}]
            ),
            "api graphql": _THREADS,
        },
    ),
    "github-check-feedback": Row(
        "reviewer_feedback", feedback(check="c"), {**_BRANCH_PR, _CHECK_RUNS: _FAILED_CHECK}
    ),
    "github-mr_details": Row(
        "mr_details",
        _call("mr_details", branch=BR),
        {FIND: find_result(listed()), f"api*{_PULLS}/7*": json.dumps(rest_pull())},
    ),
    "github-pipelines": Row(
        "pipelines",
        _call("pipelines", branch=BR, base="main"),
        {
            FIND: find_result(listed()),
            "api*event=merge_group*": runs(workflow_run(3, head_branch=_QUEUE_BRANCH)),
            "api*branch=kraft/abc*": runs(workflow_run(2, head_branch=BR)),
        },
    ),
    "github-jobs-two-pages": Row(
        "jobs",
        _call("jobs", pipeline_id="555"),
        {
            "api*actions/runs/555/jobs*-F page=1": json.dumps(
                {"jobs": [job(n, f"j{n}", "success") for n in range(100)]}
            ),
            "api*actions/runs/555/jobs*-F page=2": json.dumps({"jobs": [job(100, "j100", None)]}),
        },
    ),
    "github-job_log": Row(
        "job_log",
        _call("job_log", job_id="20"),
        {"api*actions/jobs/20/logs*": "line 1\nline 2"},
    ),
    "github-merged_mrs": Row(
        "merged_mrs",
        _call("merged_mrs", base="main", limit=2),
        {
            f"api*{_PULLS} -X GET*": json.dumps(
                [
                    rest_pull(1, state="closed", merged_at="2026-09-01T00:00:00Z"),
                    rest_pull(2, state="closed"),
                    rest_pull(3, state="closed", merged_at="2026-09-03T00:00:00Z"),
                ]
            )
        },
    ),
    "github-labels-two-pages": Row(
        "labels",
        _call("labels"),
        {
            "api*repos/{owner}/{repo}/labels*-F page=1": json.dumps(_PAGE),
            "api*repos/{owner}/{repo}/labels*-F page=2": json.dumps([rest_label("last")]),
        },
    ),
    "github-graphql-not-found": Row(
        "find_mr",
        _FIND,
        {FIND: json.dumps({"errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}]})},
    ),
    # No sleep: the reset is an hour out, so both backends stop at once with the same reason.
    "github-rate-limited-403": Row(
        "close_mr",
        lambda f, r: f.close_mr(repo=r, mr=QUEUED),
        {f"api*{_PULLS}/7 -X PATCH*": _RATE_LIMITED},
    ),
}


@pytest.mark.parametrize("name", list(ROWS))
async def test_every_github_method_answers_alike(cli, tmp_path, name):
    await assert_alike(cli, tmp_path, ROWS[name], GH_API)
