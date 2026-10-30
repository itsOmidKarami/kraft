"""GhCli's reads for the item-scoped forge routes: the merge request, its runs and
jobs, a job's log, merged history and labels. Against a stubbed `gh` on PATH, as
test_gh_writes.py; each row names the `gh api` argv sent, so the path and every
field are pinned, and the dataclasses returned."""

from __future__ import annotations

import json

import pytest

from kraft.adapters import forge
from kraft.adapters.forge import Job, Label, MergedMR, MRDetails, Pipeline

from .api_outputs import (
    FIND,
    find_result,
    job,
    listed,
    rest_label,
    rest_pull,
    runs,
    workflow_run,
)
from .nodes import rest_calls as _rest

_OPEN = find_result(listed())
_RUNS = "repos/{owner}/{repo}/actions/runs"
_PULLS = "repos/{owner}/{repo}/pulls"
_LABELS = "repos/{owner}/{repo}/labels"
_BRANCH_RUNS = f"api {_RUNS} -X GET -i -f branch=kraft/abc -F per_page=20"
_GROUP_RUNS = f"api {_RUNS} -X GET -i -f event=merge_group -F per_page=100"
_QUEUE = "gh-readonly-queue/main/pr-7-aaa"


_PAGE = [rest_label(f"l{i}") for i in range(100)]


@pytest.mark.parametrize(
    "call, routes, calls, expected",
    [
        pytest.param(
            lambda g, r: g.mr_details(repo=r, branch="kraft/abc"),
            {FIND: _OPEN, f"api*{_PULLS}/7*": json.dumps(rest_pull(body=None, draft=False))},
            [f"api {_PULLS}/7 -X GET -i"],
            MRDetails(
                number=7,
                url="https://github.com/o/r/pull/7",
                state="open",
                title="Teach it",
                body="",
                draft=False,
                labels=("bug",),
                source_branch="kraft/abc",
                target_branch="main",
                head_sha="abc123",
            ),
            id="mr-details",
        ),
        pytest.param(
            lambda g, r: g.pipelines(repo=r, branch="kraft/abc", base="main"),
            {
                FIND: _OPEN,
                "api*event=merge_group*": runs(
                    workflow_run(3, head_branch=_QUEUE, created_at="2026-09-19T07:00:00Z"),
                    workflow_run(4, head_branch="gh-readonly-queue/main/pr-8-bbb"),
                ),
                "api*branch=kraft/abc*": runs(
                    workflow_run(
                        2,
                        head_branch="kraft/abc",
                        head_sha="abc123",
                        status="in_progress",
                        conclusion=None,
                        created_at="2026-09-19T06:00:00Z",
                    ),
                    workflow_run(1, head_branch="kraft/abc", created_at="2026-09-19T05:00:00Z"),
                ),
            },
            [_BRANCH_RUNS, _GROUP_RUNS],
            (
                Pipeline(
                    "3",
                    "merge_group",
                    _QUEUE,
                    "deadbeef",
                    "failure",
                    "https://github.com/o/r/actions/runs/3",
                    "2026-09-19T07:00:00Z",
                ),
                Pipeline(
                    "2",
                    "branch",
                    "kraft/abc",
                    "abc123",
                    "in_progress",
                    "https://github.com/o/r/actions/runs/2",
                    "2026-09-19T06:00:00Z",
                ),
                Pipeline(
                    "1",
                    "branch",
                    "kraft/abc",
                    "deadbeef",
                    "failure",
                    "https://github.com/o/r/actions/runs/1",
                    "2026-09-19T05:00:00Z",
                ),
            ),
            id="pipelines-branch-and-merge-group",
        ),
        pytest.param(
            lambda g, r: g.jobs(repo=r, pipeline_id="555"),
            {
                "api*actions/runs/555/jobs*": json.dumps(
                    {
                        "jobs": [
                            job(20, "sad", "failure"),
                            {**job(21, "ok", None), "status": "in_progress"},
                        ]
                    }
                )
            },
            [f"api {_RUNS}/555/jobs?per_page=100 -X GET -i"],
            (
                Job(
                    "20",
                    "sad",
                    "",
                    "failure",
                    "failure",
                    "https://github.com/o/r/actions/runs/555/job/20",
                ),
                Job(
                    "21",
                    "ok",
                    "",
                    "in_progress",
                    None,
                    "https://github.com/o/r/actions/runs/555/job/21",
                ),
            ),
            id="jobs",
        ),
        pytest.param(
            lambda g, r: g.job_log(repo=r, job_id="20"),
            {"api*actions/jobs/20/logs*": "line 1\nline 2"},
            ["api repos/{owner}/{repo}/actions/jobs/20/logs -X GET -i"],
            "line 1\nline 2\n",
            id="job-log",
        ),
        pytest.param(
            lambda g, r: g.merged_mrs(repo=r, base="main", limit=2),
            {
                f"api*{_PULLS} -X GET*": json.dumps(
                    [
                        rest_pull(1, state="closed", merged_at="2026-09-01T00:00:00Z"),
                        rest_pull(2, state="closed"),
                        rest_pull(3, state="closed", merged_at="2026-09-03T00:00:00Z", labels=[]),
                        rest_pull(4, state="closed", merged_at="2026-09-02T00:00:00Z"),
                    ]
                )
            },
            [
                f"api {_PULLS} -X GET -i -f state=closed -f base=main"
                " -f sort=updated -f direction=desc -F per_page=6"
            ],
            (
                MergedMR(
                    3, "Teach it", (), "https://github.com/o/r/pull/3", "2026-09-03T00:00:00Z"
                ),
                MergedMR(
                    4, "Teach it", ("bug",), "https://github.com/o/r/pull/4", "2026-09-02T00:00:00Z"
                ),
            ),
            id="merged",
        ),
        pytest.param(
            lambda g, r: g.labels(repo=r),
            {
                f"api*{_LABELS}*-F page=1": json.dumps(_PAGE),
                f"api*{_LABELS}*-F page=2": json.dumps([rest_label("last", description=None)]),
            },
            [
                f"api {_LABELS} -X GET -i -F per_page=100 -F page=1",
                f"api {_LABELS} -X GET -i -F per_page=100 -F page=2",
            ],
            tuple(Label(f"l{i}", "", "d73a4a") for i in range(100))
            + (Label("last", "", "d73a4a"),),
            id="labels-paged",
        ),
    ],
)
async def test_gh_reads_send_these_requests(cli, tmp_path, call, routes, calls, expected):
    cli.stub("gh", routes=routes)
    got = await call(forge.GhCli(), tmp_path)
    assert _rest(cli) == calls
    assert got == expected


async def test_gh_pipelines_keeps_only_this_prs_merge_group_runs(cli, tmp_path):
    cli.stub(
        "gh",
        routes={
            FIND: _OPEN,
            "api*event=merge_group*": runs(
                workflow_run(8, head_branch="gh-readonly-queue/main/pr-8-bbb"),
                workflow_run(17, head_branch="gh-readonly-queue/main/pr-77-ccc"),
            ),
            "api*branch=kraft/abc*": runs(),
        },
    )
    assert await forge.GhCli().pipelines(repo=tmp_path, branch="kraft/abc", base="main") == ()


async def test_gh_pipelines_lists_branch_runs_before_an_mr_exists(cli, tmp_path):
    cli.stub("gh", routes={FIND: find_result(), "api*branch=kraft/abc*": runs(workflow_run(1))})
    got = await forge.GhCli().pipelines(repo=tmp_path, branch="kraft/abc", base="main")
    assert [p.id for p in got] == ["1"]
    assert _rest(cli) == [_BRANCH_RUNS]


async def test_gh_mr_details_is_none_without_an_mr(cli, tmp_path):
    cli.stub("gh", routes={FIND: find_result()})
    assert await forge.GhCli().mr_details(repo=tmp_path, branch="kraft/abc") is None
    assert _rest(cli) == []


async def test_gh_mr_details_says_merged_when_the_pull_has_merged_at(cli, tmp_path):
    pull = rest_pull(state="closed", merged_at="2026-09-01T00:00:00Z")
    cli.stub("gh", routes={FIND: _OPEN, f"api*{_PULLS}/7*": json.dumps(pull)})
    got = await forge.GhCli().mr_details(repo=tmp_path, branch="kraft/abc")
    assert got is not None and got.state == "merged"
