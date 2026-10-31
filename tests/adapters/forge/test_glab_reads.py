"""GlabCli's item-scoped reads (MR details, pipelines, jobs, job log, merged
history, labels): the request each makes, field by field, and what is parsed
back. Against a stubbed `glab` on PATH, as test_glab_mr.py; `cli.calls("glab")`
is one line per call. Shapes are in outputs.py with their GitLab docs source."""

from __future__ import annotations

import json

import pytest

from kraft.adapters import forge

from .nodes import Api
from .outputs import (
    GLAB_BRANCH_PIPELINES,
    GLAB_MERGED_LIST,
    GLAB_MR_DETAILS,
    GLAB_MR_PIPELINES,
    GLAB_PIPELINE_JOBS,
)

MRS = "api projects/:id/merge_requests"
#: How `find_mr` looks the branch's merge request up.
FIND = f"{MRS} -X GET -i -f source_branch=kraft/abc -f state=all -F per_page=5"
FOUND = json.dumps([{"iid": 54, "state": "opened", "web_url": "u"}])
_MERGED = (
    f"{MRS} -X GET -i -f state=merged -f target_branch=main -f order_by=merged_at -f sort=desc"
)
_LABELS = "api projects/:id/labels -X GET -i -F per_page=100 -F include_ancestor_groups=true"


_JOBS = "api projects/:id/pipelines/901/jobs -X GET -i -F per_page=100 -F include_retried=false"


def _jobs(n: int) -> str:
    return json.dumps(
        [{"id": i, "name": "j", "stage": "s", "status": "success", "web_url": ""} for i in range(n)]
    )


def _labels(n: int) -> str:
    return json.dumps([{"name": f"l{i}", "description": None, "color": "#fff"} for i in range(n)])


@pytest.mark.parametrize(
    "act, routes, sent, want",
    [
        pytest.param(
            lambda g, r: g.mr_details(repo=r, branch="kraft/abc"),
            {f"{MRS}/54": GLAB_MR_DETAILS, f"{MRS}*": FOUND},
            [FIND, f"{MRS}/54 -X GET -i"],
            forge.MRDetails(
                number=54,
                url="https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54",
                state="open",
                title="Draft: Teach it",
                body="Why and what.",
                draft=True,
                labels=("release::minor", "bug"),
                source_branch="kraft/abc",
                target_branch="main",
                head_sha="c0ffee9",
            ),
            id="mr-details",
        ),
        pytest.param(
            lambda g, r: g.mr_details(repo=r, branch="kraft/abc"),
            {
                f"{MRS}/54": json.dumps(
                    {"iid": 54, "state": "locked", "title": "t", "work_in_progress": True}
                ),
                f"{MRS}*": FOUND,
            },
            [FIND, f"{MRS}/54 -X GET -i"],
            forge.MRDetails(54, "u", "open", "t", "", True, (), "", "", ""),
            id="mr-details-on-an-old-server",
        ),
        pytest.param(
            lambda g, r: g.mr_details(repo=r, branch="kraft/abc"),
            {f"{MRS}*": "[]"},
            [FIND],
            None,
            id="mr-details-none-before-an-mr",
        ),
        pytest.param(
            lambda g, r: g.pipelines(repo=r, branch="kraft/abc", base="main"),
            {
                "api projects/:id/merge_requests/54/pipelines": GLAB_MR_PIPELINES,
                f"{MRS}*": FOUND,
            },
            [FIND, f"{MRS}/54/pipelines -X GET -i -F per_page=20"],
            (
                forge.Pipeline(
                    "903",
                    "train",
                    "refs/merge-requests/54/train",
                    "t1",
                    "running",
                    "https://gitlab.com/g/p/-/pipelines/903",
                    "2026-10-30T10:00:00.000Z",
                ),
                forge.Pipeline(
                    "902",
                    "merge_request",
                    "refs/merge-requests/54/merge",
                    "m1",
                    "success",
                    "https://gitlab.com/g/p/-/pipelines/902",
                    "2026-10-30T09:00:00.000Z",
                ),
                forge.Pipeline(
                    "901",
                    "merge_request",
                    "refs/merge-requests/54/head",
                    "h1",
                    "failed",
                    "https://gitlab.com/g/p/-/pipelines/901",
                    "2026-10-30T08:00:00.000Z",
                ),
                forge.Pipeline(
                    "900",
                    "branch",
                    "kraft/abc",
                    "b1",
                    "success",
                    "https://gitlab.com/g/p/-/pipelines/900",
                    "2026-10-30T07:00:00.000Z",
                ),
            ),
            id="pipelines-of-the-mr",
        ),
        pytest.param(
            lambda g, r: g.pipelines(repo=r, branch="kraft/abc", base="main"),
            {
                "api projects/:id/pipelines": GLAB_BRANCH_PIPELINES,
                f"{MRS}*": "[]",
            },
            [FIND, "api projects/:id/pipelines -X GET -i -f ref=kraft/abc -F per_page=20"],
            (
                forge.Pipeline(
                    "900",
                    "branch",
                    "kraft/abc",
                    "b1",
                    "success",
                    "https://gitlab.com/g/p/-/pipelines/900",
                    "2026-10-30T07:00:00.000Z",
                ),
            ),
            id="pipelines-of-the-branch-before-an-mr",
        ),
        pytest.param(
            lambda g, r: g.jobs(repo=r, pipeline_id="901"),
            {"api projects/:id/pipelines/901/jobs": GLAB_PIPELINE_JOBS},
            [
                "api projects/:id/pipelines/901/jobs -X GET -i"
                " -F per_page=100 -F include_retried=false -F page=1"
            ],
            (
                forge.Job(
                    "7001", "lint", "test", "success", None, "https://gitlab.com/g/p/-/jobs/7001"
                ),
                forge.Job(
                    "7002",
                    "unit",
                    "test",
                    "failed",
                    "script_failure",
                    "https://gitlab.com/g/p/-/jobs/7002",
                ),
            ),
            id="jobs",
        ),
        pytest.param(
            lambda g, r: g.job_log(repo=r, job_id="7002"),
            {"api projects/:id/jobs/7002/trace": "line 1\nline 2"},
            ["api projects/:id/jobs/7002/trace -X GET -i"],
            "line 1\nline 2\n",
            id="job-log",
        ),
        pytest.param(
            lambda g, r: g.merged_mrs(repo=r, base="main", limit=20),
            {f"{MRS}*": GLAB_MERGED_LIST},
            [f"{_MERGED} -F per_page=20"],
            (
                forge.MergedMR(
                    53,
                    "Fix it",
                    ("release::patch",),
                    "https://gitlab.com/g/p/-/merge_requests/53",
                    "2026-10-29T12:00:00.000Z",
                ),
                forge.MergedMR(52, "Add it", (), "https://gitlab.com/g/p/-/merge_requests/52", ""),
            ),
            id="merged",
        ),
        pytest.param(
            lambda g, r: g.merged_mrs(repo=r, base="main", limit=1),
            {
                f"{MRS}*order_by=merged_at*": Api(
                    400, '{"message": "order_by does not have a valid value"}'
                ),
                f"{MRS}*order_by=updated_at*": json.dumps(
                    [
                        {"iid": 52, "merged_at": "2026-10-28T12:00:00.000Z"},
                        {"iid": 53, "merged_at": "2026-10-29T12:00:00.000Z", "title": "Fix it"},
                    ]
                ),
            },
            [
                f"{_MERGED} -F per_page=1",
                f"{_MERGED.replace('merged_at', 'updated_at')} -F per_page=1",
            ],
            (forge.MergedMR(53, "Fix it", (), "", "2026-10-29T12:00:00.000Z"),),
            id="merged-on-an-old-server",
        ),
        pytest.param(
            lambda g, r: g.jobs(repo=r, pipeline_id="901"),
            {"api*page=1": _jobs(100), "api*page=2": _jobs(3)},
            [f"{_JOBS} -F page=1", f"{_JOBS} -F page=2"],
            tuple(forge.Job(str(i), "j", "s", "success", None, "") for i in (*range(100), 0, 1, 2)),
            id="jobs-two-pages",
        ),
        pytest.param(
            lambda g, r: g.labels(repo=r),
            {"api*page=1": _labels(100), "api*page=2": _labels(3)},
            [f"{_LABELS} -F page=1", f"{_LABELS} -F page=2"],
            tuple(forge.Label(f"l{i}", "", "#fff") for i in (*range(100), 0, 1, 2)),
            id="labels-paged",
        ),
    ],
)
async def test_glab_reads_send_these_requests(cli, tmp_path, act, routes, sent, want):
    cli.stub("glab", routes=routes, default="[]")

    got = await act(forge.GlabCli(), tmp_path)

    assert got == want
    assert cli.calls("glab") == sent


async def test_glab_pipelines_names_the_train_pipeline(cli, tmp_path):
    """Only the MR's own train ref is a train; another MR's is a plain ref."""
    other = json.dumps([{"id": 5, "ref": "refs/merge-requests/55/train", "status": "x"}])
    cli.stub(
        "glab",
        routes={"api projects/:id/merge_requests/54/pipelines": other, f"{MRS}*": FOUND},
    )

    got = await forge.GlabCli().pipelines(repo=tmp_path, branch="kraft/abc", base="main")

    assert [p.kind for p in got] == ["branch"]
