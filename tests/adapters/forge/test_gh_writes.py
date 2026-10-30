"""GhCli's writes over the REST and GraphQL API: opening, readying, editing,
labelling, closing and re-running. Against a stubbed `gh` on PATH, as test_gh.py;
each row names the requests sent as `gh api` argv, so the method, the path and
every field are pinned."""

from __future__ import annotations

import json

import pytest

from kraft.adapters import forge
from kraft.adapters.forge.mr import MRMeta

from .api_outputs import (
    READ_BRANCH,
    READ_NUMBER,
    URL,
    pr_by_branch,
    pr_by_number,
    pull_request,
)
from .nodes import Api

PR = forge.MR(7, "http://x/7")
_PULLS = "repos/{owner}/{repo}/pulls"
_ISSUES = "repos/{owner}/{repo}/issues/7"
_MADE = json.dumps({"number": 7, "html_url": URL})
_CREATE = f"api {_PULLS} -X POST -i -f head=kraft/abc -f base=main -f title=Teach it -f body=why"


def _rest(cli) -> list[str]:
    """Every REST call made, as `api <path> -X <method> -i <fields>`; the
    GraphQL ones span lines and are read from `cli.argv`."""
    return [c for c in cli.calls("gh") if c.startswith("api repos/")]


def _label(name: str) -> str:
    return f"api repos/{{owner}}/{{repo}}/labels/{name} -X GET -i"


def _stub(cli, pr: dict | None = None, **routes) -> None:
    """`gh` creating pull request 7, finding `pr` by number or branch, and
    answering any other call with an empty object."""
    pr = pr or pull_request()
    cli.stub(
        "gh",
        routes={
            f"api*{_PULLS} -X POST*": _MADE,
            READ_NUMBER: pr_by_number(pr),
            READ_BRANCH: pr_by_branch(pr),
            **routes,
        },
    )


async def _open(tmp_path, **kw):
    return await forge.GhCli().open_mr(
        repo=tmp_path, branch="kraft/abc", base="main", title="Teach it", body="why", **kw
    )


@pytest.mark.parametrize(
    "meta, calls",
    [
        # Every pull request starts as a draft; human_review decides when it is ready.
        pytest.param(None, [f"{_CREATE} -F draft=true"], id="a-draft-with-no-metadata"),
        pytest.param(
            MRMeta(
                labels=("release::minor", "bug", "area/ui"),
                assignees=("omid",),
                reviewers=("ada", "core/x"),
            ),
            [
                _label("release%3A%3Aminor"),
                _label("bug"),
                _label("area%2Fui"),
                f"{_CREATE} -F draft=true",
                f"api {_ISSUES}/labels -X POST -i"
                " -f labels[]=release::minor -f labels[]=bug -f labels[]=area/ui",
                f"api {_ISSUES}/assignees -X POST -i -f assignees[]=omid",
                f"api {_PULLS}/7/requested_reviewers -X POST -i"
                " -f reviewers[]=ada -f team_reviewers[]=x",
            ],
            id="labels-checked-first-then-the-follow-ups",
        ),
        pytest.param(
            MRMeta(reviewers=("ada", "grace")),
            [
                f"{_CREATE} -F draft=true",
                f"api {_PULLS}/7/requested_reviewers -X POST -i"
                " -f reviewers[]=ada -f reviewers[]=grace",
            ],
            id="people-only-sends-no-team-field",
        ),
    ],
)
async def test_gh_open_mr_creates_a_draft_then_adds_the_metadata(cli, tmp_path, meta, calls):
    _stub(cli)
    cli.stub("git", "")

    mr = await _open(tmp_path, meta=meta)

    assert (mr.number, mr.url) == (7, URL)
    assert _rest(cli) == calls


async def test_gh_open_mr_targets_the_base_it_is_given(cli, tmp_path):
    """Kraft-v9gbi: the item's base branch, never the forge's default."""
    _stub(cli)
    cli.stub("git", "")

    await forge.GhCli().open_mr(
        repo=tmp_path, branch="kraft/abc", base="release", title="T", body="B"
    )

    assert _rest(cli) == [
        f"api {_PULLS} -X POST -i -f head=kraft/abc -f base=release -f title=T -f body=B"
        " -F draft=true"
    ]


@pytest.mark.parametrize(
    "answer, match",
    [
        pytest.param(
            Api(404, '{"message": "Not Found"}'), "could not add label: 'nope' not found", id="404"
        ),
        pytest.param(
            Api(500, '{"message": "boom"}'), "500", id="a-failed-lookup-is-not-a-missing-label"
        ),
    ],
)
async def test_gh_open_mr_with_an_unknown_label_creates_nothing(cli, tmp_path, answer, match):
    """gh resolves labels before it creates (params.go:106-125): no pull request
    is left behind for a label the repository does not have."""
    _stub(cli, **{"api*labels/nope*": answer})
    cli.stub("git", "")

    with pytest.raises(forge.ForgeError, match=match):
        await _open(tmp_path, meta=MRMeta(labels=("nope",)))

    assert not [c for c in _rest(cli) if "-X POST" in c]


@pytest.mark.parametrize(
    "answer",
    [_MADE.replace("html_url", "url"), "[]"],
    ids=["no-html-url", "not-an-object"],
)
async def test_gh_open_mr_that_is_answered_without_the_pull_request_raises(cli, tmp_path, answer):
    _stub(cli, **{f"api*{_PULLS} -X POST*": answer})
    cli.stub("git", "")

    with pytest.raises(forge.ForgeError, match="without the pull request"):
        await _open(tmp_path)


@pytest.mark.parametrize(
    "call, requests",
    [
        pytest.param(
            lambda g, r: g.update_mr(repo=r, branch="kraft/abc", body="fresh"),
            [f"api {_PULLS}/7 -X PATCH -i -f body=fresh"],
            id="update-the-body",
        ),
        pytest.param(
            lambda g, r: g.close_mr(repo=r, mr=forge.MRRef(7, URL, state="open")),
            [f"api {_PULLS}/7 -X PATCH -i -f state=closed"],
            id="close",
        ),
        pytest.param(
            lambda g, r: g.reopen_mr(repo=r, mr=forge.MRRef(7, URL, state="closed")),
            [f"api {_PULLS}/7 -X PATCH -i -f state=open"],
            id="reopen",
        ),
    ],
)
async def test_gh_edits_the_pull_request_over_rest(cli, tmp_path, call, requests):
    """`open_mr` runs before verify and mr_checks commit, so the description it
    wrote describes a branch that no longer exists (Kraft-c09h)."""
    _stub(cli, pull_request(number=7))

    await call(forge.GhCli(), tmp_path)

    assert _rest(cli) == requests


@pytest.mark.parametrize(
    "mr, draft, read, mutated",
    [
        pytest.param(PR, True, "n=7", True, id="a-draft-is-marked-ready"),
        pytest.param(forge.MR(0, ""), True, "b=kraft/abc", True, id="found-by-its-branch"),
        pytest.param(PR, False, "n=7", False, id="a-ready-one-is-left-alone"),
    ],
)
async def test_gh_mark_ready_unsets_draft_only_on_a_draft(cli, tmp_path, mr, draft, read, mutated):
    _stub(cli, pull_request(isDraft=draft))

    await forge.GhCli().mark_ready(repo=tmp_path, branch="kraft/abc", mr=mr)

    argv = cli.argv("gh")
    assert read in argv and argv.count("graphql") == 1 + mutated
    assert (
        "markPullRequestReadyForReview(input: {pullRequestId: $id})" in "\n".join(argv)
    ) is mutated
    assert ("id=PR_kwDO7" in argv) is mutated
    assert _rest(cli) == []


def _labelled(*names: str) -> dict:
    return pull_request(labels={"nodes": [{"name": n} for n in names]})


@pytest.mark.parametrize(
    "current, labels, calls",
    [
        # GitHub re-evaluates `pull_request: types: [labeled]` itself, so there
        # is no pipeline to re-create here -- only the labels to add.
        pytest.param(
            (),
            ("release::patch", "bug"),
            [
                _label("release%3A%3Apatch"),
                _label("bug"),
                f"api {_ISSUES}/labels -X POST -i -f labels[]=release::patch -f labels[]=bug",
            ],
            id="added",
        ),
        # Kraft-o9xh1, as glab does (Kraft-zfdu8): adding only adds, so a repair
        # pass that moves release::patch to release::minor left both on the PR
        # and `next_tag.py` raises on more than one. The label sharing a new
        # label's `scope::` prefix goes; other labels stay.
        pytest.param(
            ("release::patch", "bug", "area::ui"),
            ("release::minor",),
            [
                _label("release%3A%3Aminor"),
                f"api {_ISSUES}/labels -X POST -i -f labels[]=release::minor",
                f"api {_ISSUES}/labels/release%3A%3Apatch -X DELETE -i",
            ],
            id="other-scoped-label-dropped",
        ),
        pytest.param(
            ("release::minor",),
            ("release::minor",),
            [
                _label("release%3A%3Aminor"),
                f"api {_ISSUES}/labels -X POST -i -f labels[]=release::minor",
            ],
            id="same-label-kept",
        ),
    ],
)
async def test_gh_set_labels_adds_and_drops_over_rest(cli, tmp_path, current, labels, calls):
    _stub(cli, _labelled(*current))

    await forge.GhCli().set_labels(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, "u"), labels=labels
    )

    assert _rest(cli) == calls
    argv = cli.argv("gh")
    assert argv.count("graphql") == 1 and "n=7" in argv, "one read, of the number"
    assert "statusCheckRollup" not in "\n".join(argv), "labels need no checks"


async def test_gh_set_labels_with_an_unknown_label_writes_nothing(cli, tmp_path):
    _stub(cli, _labelled("release::patch"), **{"api*labels/nope*": Api(404, '{"message": "x"}')})

    with pytest.raises(forge.ForgeError, match="could not add label: 'nope' not found"):
        await forge.GhCli().set_labels(
            repo=tmp_path,
            branch="kraft/abc",
            mr=forge.MR(7, "u"),
            labels=("release::minor", "nope"),
        )

    assert not [c for c in _rest(cli) if "-X POST" in c or "-X DELETE" in c]


@pytest.mark.parametrize(
    "url, calls",
    [
        pytest.param(
            "https://github.com/o/r/actions/runs/123456/job/9",
            ["api repos/{owner}/{repo}/actions/runs/123456/rerun-failed-jobs -X POST -i"],
            id="the-run-behind-the-failed-check",
        ),
        pytest.param("sweet link", [], id="a-check-outside-actions-is-skipped"),
    ],
)
async def test_gh_retry_jobs_reruns_the_failed_jobs_of_the_run(cli, tmp_path, url, calls):
    cli.stub("gh", "")
    job = forge.FailedJob("build", "failed", "job_execution_timeout", url)

    await forge.GhCli().retry_jobs(
        repo=tmp_path, ci=forge.CIStatus(state="failed", url="u", failed_jobs=(job,))
    )

    assert _rest(cli) == calls


async def test_gh_rerun_after_description_change_reruns_without_a_new_pipeline(cli, tmp_path):
    cli.stub("gh", "")
    job = forge.FailedJob("build", "failed", None, "https://github.com/o/r/actions/runs/77/job/9")

    made_new = await forge.GhCli().rerun_after_description_change(
        repo=tmp_path,
        branch="b",
        ci=forge.CIStatus(state="failed", url="u", failed_jobs=(job,)),
    )

    assert made_new is False
    assert _rest(cli) == ["api repos/{owner}/{repo}/actions/runs/77/rerun-failed-jobs -X POST -i"]
