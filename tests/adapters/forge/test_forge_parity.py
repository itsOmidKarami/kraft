"""`GitLabApi` and `GlabCli` run one operation against the same `glab` stub routes and must
return the same result and send the same requests (`GlabOverHttp` renders the HTTP the API
backend sent as the argv glab would have had). One row per behaviour; every `Forge` method
but `push` has one, which `test_every_forge_method_has_a_parity_row` enforces."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from kraft.adapters import forge
from kraft.adapters.forge.glab import GlabCli
from kraft.adapters.forge.models import Forge
from kraft.automated_review import AutomatedReview

from . import outputs
from .nodes import Api
from .test_forge_cli_contract import GIT_ROUTES, GLAB_API, HEAD, _contract_token  # noqa: F401

BR = "kraft/abc"
MR54 = forge.MR(54, "u")
QUEUED = forge.MRRef(54, "u", state="open", merge_queued=True)
SINCE = datetime(2026, 1, 1, tzinfo=UTC)
VIEW = outputs.glab_mr(outputs.GLAB_MR_VIEW)
RUNNING = json.dumps({"iid": 54, "head_pipeline": {"status": "running"}})
TRAIN_PROJECT = json.dumps({"merge_trains_enabled": True, "merge_pipelines_enabled": True})
#: A view with a sha, labels and a discussion-bearing iid, for the review reads.
REVIEWED = json.dumps({"iid": 54, "sha": "c0ffee9", "labels": ["release::minor", "bug"]})
NOTE = {
    "system": False,
    "author": {"username": "bot"},
    "body": "hi",
    "updated_at": "2026-02-01T00:00:00Z",
}
THREAD = {"id": "d1", "notes": [{**NOTE, "resolvable": True, "resolved": False}]}
STATUS = json.dumps([{"id": 1, "name": "c", "status": "failed", "description": "boom"}])
_OLD_CANCEL = "api projects/:id/merge_requests/54/cancel_merge_when_pipeline_succeeds"
MERGE_REF = "refs/merge-requests/54/merge"
PICK = {"api projects/:id/merge_requests": outputs.GLAB_MR_LIST_AUTO_MERGE}


def _version(v: str) -> dict:
    return {"api version": outputs.GITLAB_VERSIONS[v]}


@dataclass
class Row:
    method: str
    call: Any  # (forge, repo) -> awaitable
    routes: dict = field(default_factory=dict)
    git: dict = field(default_factory=dict)


def _merge(f, r):
    return f.merge(repo=r, branch=BR, mr=MR54, head=HEAD)


def _cancel(f, r):
    return f.cancel_merge(repo=r, branch=BR, mr=QUEUED)


def _ci(f, r):
    return f.ci_status(repo=r, mr=forge.MR(0, ""), branch=BR)


def _review(**kw):
    return lambda f, r: f.automated_review(repo=r, branch=BR, reviewers=(AutomatedReview(**kw),))


def _feedback(**kw):
    return lambda f, r: f.reviewer_feedback(
        repo=r, branch=BR, reviewers=(AutomatedReview(**kw),), since=SINCE
    )


_merged_pipeline, _merge_commit_route, _merge_commit = outputs.glab_merged_results(HEAD)
_MERGED_RESULTS_VIEW = json.dumps(
    {
        "iid": 54,
        "state": "opened",
        "detailed_merge_status": "mergeable",
        "head_pipeline": {**_merged_pipeline, "ref": MERGE_REF, "status": "success"},
    }
)
_DROPPED = {
    "api projects/:id": TRAIN_PROJECT,
    **outputs.glab_mr(json.dumps({"iid": 54, "state": "opened", "web_url": "w"})),
    "api projects/:id/merge_trains/merge_requests/54": Api(404, '{"message":"404 Not found"}'),
    "api projects/:id/merge_requests/54/notes": json.dumps(
        [
            {
                "system": True,
                "body": "removed this merge request from the merge train because conflict",
            }
        ]
    ),
}
_MERGE_OK = {**VIEW, "api projects/:id/merge_requests/54": RUNNING}

ROWS: dict[str, Row] = {
    "open_mr": Row(
        "open_mr",
        lambda f, r: f.open_mr(repo=r, branch=BR, base="main", title="t", body="b"),
        {"api projects/:id/merge_requests": '{"iid":54,"web_url":"w"}'},
    ),
    "mark_ready": Row(
        "mark_ready",
        lambda f, r: f.mark_ready(repo=r, branch=BR, mr=MR54),
        {"api projects/:id/merge_requests/54": outputs.GLAB_MR_VIEW},
    ),
    "update_mr": Row("update_mr", lambda f, r: f.update_mr(repo=r, branch=BR, body="b"), VIEW),
    "mr_body": Row(
        "mr_body",
        lambda f, r: f.mr_body(repo=r, branch=BR),
        outputs.glab_mr(outputs.GLAB_MR_DETAILS),
    ),
    "ci_status": Row("ci_status", _ci, {**VIEW, outputs.PIPELINES: outputs.GLAB_CI_SUCCESS}),
    "ci_status-merged-results": Row(
        "ci_status",
        _ci,
        {
            **outputs.glab_mr(_MERGED_RESULTS_VIEW),
            _merge_commit_route: _merge_commit,
            outputs.PIPELINES: "[]",
        },
        {"*rev-parse*": HEAD},
    ),
    "branch_ci_status": Row(
        "branch_ci_status",
        lambda f, r: f.branch_ci_status(repo=r, branch="main", head_sha=HEAD),
        {outputs.PIPELINES: outputs.GLAB_CI_SUCCESS},
    ),
    "merge-17.11": Row("merge", _merge, {**_MERGE_OK, **_version("17.11.0")}),
    "merge-17.10-old-flag": Row("merge", _merge, {**_MERGE_OK, **_version("17.10.4")}),
    "merge-train": Row(
        "merge",
        _merge,
        {
            "api projects/:id": TRAIN_PROJECT,
            "api projects/:id/merge_trains/merge_requests/54": "{}",
        },
    ),
    "cancel_merge-19.5": Row(
        "cancel_merge",
        _cancel,
        {**_version("19.5.0"), "api projects/:id/merge_requests/54/cancel_auto_merge": "{}"},
    ),
    "cancel_merge-19.4-old-endpoint": Row("cancel_merge", _cancel, _version("19.4.2")),
    "cancel_merge-old-201-status-error": Row(
        "cancel_merge",
        _cancel,
        {
            **_version("19.4.2"),
            _OLD_CANCEL: '{"status":"error","message":"no"}',
        },
    ),
    "close_mr": Row("close_mr", lambda f, r: f.close_mr(repo=r, mr=QUEUED), {}),
    "reopen_mr": Row("reopen_mr", lambda f, r: f.reopen_mr(repo=r, mr=QUEUED), {}),
    "set_labels": Row(
        "set_labels",
        lambda f, r: f.set_labels(repo=r, branch=BR, mr=MR54, labels=("release::patch",)),
        {"api projects/:id/merge_requests/54": REVIEWED},
    ),
    "find_mr": Row(
        "find_mr",
        lambda f, r: f.find_mr(repo=r, branch=BR),
        {"api projects/:id/merge_requests": outputs.GLAB_MR_LIST_MERGED},
    ),
    "merge_queue": Row(
        "merge_queue",
        lambda f, r: f.merge_queue(repo=r, branch=BR, base="main"),
        {
            "api projects/:id": TRAIN_PROJECT,
            **VIEW,
            "api projects/:id/merge_trains/merge_requests/54": '{"status":"idle"}',
        },
    ),
    "retry_jobs": Row(
        "retry_jobs",
        lambda f, r: f.retry_jobs(
            repo=r, ci=forge.CIStatus(state="failed", url="", pipeline_ref="901")
        ),
        {},
    ),
    "rerun_after_description_change": Row(
        "rerun_after_description_change",
        lambda f, r: f.rerun_after_description_change(
            repo=r, branch=BR, ci=forge.CIStatus(state="failed", url="")
        ),
        VIEW,
    ),
    "approval_state": Row(
        "approval_state",
        lambda f, r: f.approval_state(repo=r, branch=BR),
        {
            **outputs.glab_mr(outputs.GLAB_MR_VIEW_NOT_APPROVED),
            outputs.PIPELINES: outputs.GLAB_CI_SUCCESS,
        },
    ),
    "removal-note": Row(
        "merge_queue", lambda f, r: f.merge_queue(repo=r, branch=BR, base="main"), _DROPPED
    ),
    "mr_details": Row(
        "mr_details",
        lambda f, r: f.mr_details(repo=r, branch=BR),
        {**PICK, "api projects/:id/merge_requests/54": outputs.GLAB_MR_DETAILS},
    ),
    "pipelines": Row(
        "pipelines",
        lambda f, r: f.pipelines(repo=r, branch=BR, base="main"),
        {**PICK, "api projects/:id/merge_requests/54/pipelines": outputs.GLAB_MR_PIPELINES},
    ),
    "jobs-two-pages": Row(
        "jobs",
        lambda f, r: f.jobs(repo=r, pipeline_id="901"),
        {
            "api projects/:id/pipelines/901/jobs*page=2": outputs.jobs_page(3, 100),
            "api projects/:id/pipelines/901/jobs": outputs.jobs_page(100),
        },
    ),
    "job_log": Row(
        "job_log",
        lambda f, r: f.job_log(repo=r, job_id="7002"),
        {"api projects/:id/jobs/7002/trace": outputs.GLAB_CI_TRACE},
    ),
    "merged-17.2": Row(
        "merged_mrs",
        lambda f, r: f.merged_mrs(repo=r, base="main", limit=5),
        {"api projects/:id/merge_requests": outputs.GLAB_MERGED_LIST},
    ),
    "merged-before-17.2-falls-back": Row(
        "merged_mrs",
        lambda f, r: f.merged_mrs(repo=r, base="main", limit=5),
        {
            "api*order_by=merged_at*": Api(400, '{"error":"order_by does not have a valid value"}'),
            "api projects/:id/merge_requests": outputs.GLAB_MERGED_LIST,
        },
    ),
    "labels-two-pages": Row(
        "labels",
        lambda f, r: f.labels(repo=r),
        {
            "api projects/:id/labels*page=2": outputs.labels_page(2, 100),
            "api projects/:id/labels": outputs.labels_page(100),
        },
    ),
    "bot-review": Row(
        "automated_review",
        _review(bot="bot"),
        {
            **VIEW,
            "api projects/:id/merge_requests/54/discussions": json.dumps([THREAD]),
        },
    ),
    "check-review": Row(
        "automated_review",
        _review(check="c"),
        {
            "api projects/:id/merge_requests/54": REVIEWED,
            **{"api*state=opened*": '[{"iid":54}]'},
            "api projects/:id/repository/commits/c0ffee9/statuses": STATUS,
        },
    ),
    "bot-feedback": Row(
        "reviewer_feedback",
        _feedback(bot="bot"),
        {
            **VIEW,
            "api projects/:id/merge_requests/54/notes": json.dumps([NOTE]),
            "api projects/:id/merge_requests/54/discussions": json.dumps([THREAD]),
        },
    ),
    "check-feedback": Row(
        "reviewer_feedback",
        _feedback(check="c"),
        {
            "api projects/:id/merge_requests/54": REVIEWED,
            "api*state=opened*": '[{"iid":54}]',
            "api projects/:id/repository/commits/c0ffee9/statuses": STATUS,
        },
    ),
}


async def _outcome(call, f, repo):
    """The result, or what an error says about itself (not its text: the CLI and HTTP word it
    alike only where both read the same body)."""
    try:
        return await call(f, repo)
    except forge.ForgeError as exc:
        return (type(exc), exc.status, exc.reason)


@pytest.mark.parametrize("name", list(ROWS))
async def test_every_forge_method_answers_alike(cli, tmp_path, name):
    row = ROWS[name]
    cli.stub("glab", routes=row.routes, default="{}")
    cli.stub("git", routes={**row.git, **GIT_ROUTES}, default="")

    twin = await _outcome(row.call, GlabCli(), tmp_path)
    twin_calls = cli.calls("glab")
    for ext in ("calls", "argv"):
        (cli.bindir / f"glab.{ext}").unlink(missing_ok=True)
    api = await _outcome(row.call, GLAB_API.cls(), tmp_path)

    assert twin_calls, "the twin never reached glab"
    assert api == twin
    assert cli.calls("glab") == twin_calls


def test_every_forge_method_has_a_parity_row():
    """A method added to `Forge` without a row fails: the twin proof must not go stale."""
    methods = {m for m in vars(Forge) if not m.startswith("_")} - {"push"}
    assert methods - {row.method for row in ROWS.values()} == set()
