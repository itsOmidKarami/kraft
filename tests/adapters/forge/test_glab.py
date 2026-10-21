"""GlabCli: GitLab through `glab`. Everything here runs against a stubbed
`glab` (and sometimes `git`) on PATH; nothing touches the network. What gh
promises too is in test_forge_cli_contract.py."""

from __future__ import annotations

import json
import subprocess

import pytest

from kraft.adapters import forge

from .nodes import FAIL
from .outputs import (
    GLAB_CI_GET_FAILED,
    GLAB_CI_SUCCESS,
    GLAB_CI_TRACE,
    GLAB_MR_LIST_MERGED,
    GLAB_MR_VIEW,
    GLAB_MR_VIEW_CONFLICT,
    glab_merged_results,
)

GLAB_CI_FAILED = (
    '[{"id":2826926700,"iid":142,"status":"failed","ref":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926700"}]'
)

GLAB_CI_RUNNING = (
    '[{"id":2826926701,"iid":143,"status":"running","ref":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926701"}]'
)

# A merge request merged out-of-band -- a person merged it in the GitLab UI
# while mr_checks was still polling. A merged MR carries neither merge-status
# field (glab 1.117.0).
GLAB_MR_VIEW_MERGED = (
    '{"iid":54,"state":"merged","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)

# `glab ci get -F json` for a pipeline read by id -- the same shape `glab ci
# list` returns per row.
GLAB_CI_GET_PINNED_SUCCESS = (
    '{"id":2826926705,"status":"success","sha":"deadbeef",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926705"}'
)

NO_MR = forge.MR(0, "")
#: A GitLab project that does not merge through a merge train.
NO_TRAIN = '{"id":1}'


def _pipeline(sha: str, ref: str = "kraft/abc") -> str:
    """A one-row `glab ci list` answer for a green pipeline on commit `sha`."""
    return f'[{{"id":1,"status":"success","ref":"{ref}","sha":"{sha}","web_url":"http://x/1"}}]'


def _head(repo) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


# --- the pipeline ----------------------------------------------------------------


def _routes(ci_list: str) -> dict:
    """`glab` answering each call `ci_status` makes with that call's own shape,
    and `ci_list` for the pipeline list."""
    return {
        "mr view": GLAB_MR_VIEW,
        "ci list": ci_list,
        "ci get": GLAB_CI_GET_FAILED,
        "ci trace": GLAB_CI_TRACE,
    }


async def test_glab_ci_status_maps_a_failed_pipeline(cli, tmp_path):
    cli.stub("glab", routes=_routes(GLAB_CI_FAILED), default=FAIL)

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1"))

    assert status.state == "failed"
    assert status.url.endswith("/pipelines/2826926700")
    assert status.jobs, "the review brief needs something to show"
    assert status.failed_jobs == (forge.FailedJob("release-impact", "failed", None),)


@pytest.mark.parametrize(
    "ci_list",
    [
        GLAB_CI_RUNNING,
        # An empty list is a pipeline that has not been created, not a green one.
        "[]",
    ],
    ids=["running", "no-pipeline-yet"],
)
async def test_glab_ci_status_is_pending_until_a_pipeline_settles(cli, tmp_path, ci_list):
    cli.stub("glab", routes=_routes(ci_list), default=FAIL)

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1"))

    assert status.state == "pending"


async def test_glab_raises_forge_error_when_the_cli_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no glab anywhere
    with pytest.raises(forge.ForgeError, match="glab"):
        await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1"))


async def test_glab_raises_forge_error_when_the_cli_fails(cli, tmp_path):
    cli.stub("glab", "boom", rc=1)
    with pytest.raises(forge.ForgeError):
        await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1"))


async def test_glab_ci_status_asks_for_this_branch_only(cli, tmp_path):
    """Without --ref, `glab ci list` returns the newest pipeline in the whole
    project. A green pipeline on main would pass the gate for a red branch."""
    cli.stub("glab", routes=_routes(GLAB_CI_SUCCESS), default=FAIL)

    await forge.GlabCli().ci_status(repo=tmp_path, mr=NO_MR, branch="kraft/abc")

    (ci_list,) = [c for c in cli.calls("glab") if c.startswith("ci list")]
    assert "--ref kraft/abc" in ci_list


OLDER = "1" * 40


def _mr_view(head_pipeline: dict | None = None) -> str:
    """`glab mr view -F json` for !262611, the merge request the captured
    merged results pipeline belongs to, with `head_pipeline`."""
    view = {"iid": 262611, "state": "opened", "source_branch": "kraft/abc"}
    return json.dumps({**view, "head_pipeline": head_pipeline})


def _merged(tested: str, *, payload=True, listed="[]", unreadable=False, **fields) -> dict:
    """Routes for a merged results pipeline built from `tested`: as the merge
    request's `head_pipeline` with `listed` the list (`payload`), or as the
    list's top row with no `head_pipeline`. `unreadable` fails the merge
    commit read."""
    pipeline, read_commit, merge_commit = glab_merged_results(tested, **fields)
    return {
        "mr view": _mr_view(pipeline if payload else None),
        "ci list": listed if payload else json.dumps([pipeline]),
        read_commit: FAIL if unreadable else merge_commit,
    }


@pytest.mark.parametrize(
    "routes, expected, says, reads_a_commit",
    [
        # For a few seconds after a push `glab ci list -P 1` still returns the
        # *previous* commit's pipeline, which may be green (Kraft-bxj8).
        (lambda head, checkout: {"ci list": _pipeline(OLDER)}, "pending", "", False),
        (lambda head, checkout: {"ci list": _pipeline(head)}, "success", "", False),
        # An in-place item's checkout may be on another branch: its HEAD is
        # not the head being checked.
        (lambda head, checkout: {"ci list": _pipeline(checkout)}, "pending", "", False),
        # A merged results pipeline runs on a merge commit of the head into the
        # target: that commit's second parent is the head it tested. The list
        # holds only an older branch pipeline, as on an older GitLab whose
        # `?ref=` leaves merge request pipelines out.
        (
            lambda head, checkout: _merged(head, status="success", listed=_pipeline(OLDER)),
            "success",
            "",
            True,
        ),
        # The payload's head pipeline lags a push: the previous head's merge
        # commit, on the very same ref.
        (
            lambda head, checkout: _merged(OLDER, status="success"),
            "pending",
            f"tested {OLDER[:7]}",
            True,
        ),
        (
            lambda head, checkout: _merged(
                head, status="success", ref="refs/merge-requests/999/merge"
            ),
            "pending",
            "",
            False,
        ),
        (
            lambda head, checkout: _merged(head, status="success", unreadable=True),
            "pending",
            "could not read merge commit",
            True,
        ),
        # gitlab.com's `?ref=<branch>` lists the merge request's pipelines too.
        (
            lambda head, checkout: _merged(head, status="success", payload=False),
            "success",
            "",
            True,
        ),
    ],
    ids=[
        "older-commit-is-not-a-result",
        "current-head-accepted",
        "not-the-checkouts-head",
        "merged-results-built-from-the-head",
        "merged-results-built-from-an-older-head",
        "another-merge-request-s-merge-ref",
        "merge-commit-unreadable",
        "merged-results-row-in-the-list",
    ],
)
async def test_glab_ci_status_only_trusts_a_pipeline_for_the_current_head(
    cli, repo, routes, expected, says, reads_a_commit
):
    subprocess.run(["git", "branch", "kraft/abc"], cwd=repo, check=True)
    branch_head = _head(repo)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "theirs"], cwd=repo, check=True)
    cli.stub("glab", routes={"mr view": _mr_view(), **routes(branch_head, _head(repo))})

    status = await forge.GlabCli().ci_status(repo=repo, mr=NO_MR, branch="kraft/abc")

    assert status.state == expected
    assert ("no pipeline for" in status.jobs[0]) is (expected == "pending"), status.jobs
    assert says in "\n".join(status.jobs)
    if expected == "success":
        assert status.sha == branch_head, "reported the merge commit, not the head it tested"
    asked = any("/repository/commits/" in call for call in cli.calls("glab"))
    assert asked is reads_a_commit


def _payload_lags(head: str, status: str) -> dict:
    """Routes where the merge request's `head_pipeline` for `head` is still an
    older one in `status`, and the list's top row is a newer, running one."""
    lagging, read_lagging, lagging_commit = glab_merged_results(head, status=status)
    newer = _merged(head, id=2962437759, sha="3" * 40, payload=False)
    return {**newer, "mr view": _mr_view(lagging), read_lagging: lagging_commit}


@pytest.mark.parametrize(
    "routes, expected",
    [
        # GitLab moves `head_pipeline` in a background worker: just after
        # `set_labels` starts a new pipeline, the payload still names the red
        # one for the same head, and the list already has the new one.
        (
            lambda head: _payload_lags(head, "failed"),
            ("pending", "2962437759"),
        ),
        # A cancelled head pipeline whose successor the payload has not caught
        # up with reads the successor, not "cancelled".
        (
            lambda head: _payload_lags(head, "canceled"),
            ("pending", "2962437759"),
        ),
        # An older GitLab running both kinds lists only the branch pipeline;
        # the merge request's newer, red one is the one its merge rule reads.
        (
            lambda head: _merged(head, status="failed", listed=_pipeline(head)),
            ("failed", "2962437758"),
        ),
    ],
    ids=["a-new-pipeline-the-payload-lags", "a-cancelled-one-s-successor", "newer-than-the-list"],
)
async def test_glab_ci_status_reads_the_newest_pipeline_for_the_head(cli, repo, routes, expected):
    subprocess.run(["git", "branch", "kraft/abc"], cwd=repo, check=True)
    cli.stub("glab", routes=routes(_head(repo)))

    status = await forge.GlabCli().ci_status(repo=repo, mr=NO_MR, branch="kraft/abc")

    assert (status.state, status.pipeline_ref) == expected


async def test_glab_branch_ci_status_never_resolves_a_merge_request(cli, repo):
    """merge_watch's whole reason to call this instead of `ci_status`: the
    checked-out branch's MR is already merged, so this must never shell out to
    `glab mr view` at all (code-review)."""
    head = _head(repo)
    cli.stub("glab", _pipeline(head, ref="main"))

    status = await forge.GlabCli().branch_ci_status(repo=repo, branch="main", head_sha=head)

    assert (status.state, status.mergeable) == ("success", None)
    assert all("mr view" not in call for call in cli.calls("glab"))


@pytest.mark.parametrize(
    "listed",
    [
        lambda repo: _pipeline(_head(repo), ref="main"),
        # A target branch has no merge request: no merge ref ever answers for
        # it, whoever's head its merge commit merged in.
        lambda repo: json.dumps([glab_merged_results("not-pulled-yet", status="success")[0]]),
    ],
    ids=["not-the-local-head", "never-a-merge-request-s-merge-ref"],
)
async def test_glab_branch_ci_status_guards_against_the_caller_s_head_not_the_checkout_s(
    cli, repo, listed
):
    """The sha guard compares against `head_sha` -- the caller's freshly-fetched
    upstream head -- not the local checkout's own HEAD, which post-merge may not
    be pulled at all."""
    cli.stub("glab", listed(repo))

    status = await forge.GlabCli().branch_ci_status(
        repo=repo, branch="main", head_sha="not-pulled-yet"
    )

    assert status.state == "pending", "matched a head that is not the caller's"
    assert not any("/repository/commits/" in call for call in cli.calls("glab"))


def _pinned_merged_results(tested: str) -> dict:
    """Routes for a pinned merged results pipeline built from `tested`."""
    pipeline, read_commit, merge_commit = glab_merged_results(tested, status="success")
    return {"ci get": json.dumps(pipeline), read_commit: merge_commit}


@pytest.mark.parametrize(
    "routes, pinned",
    [
        ({"ci get": GLAB_CI_GET_PINNED_SUCCESS}, "2826926705"),
        # Its `sha` is a merge commit; the head it tested is what it answers for.
        (_pinned_merged_results("deadbeef"), "2962437758"),
    ],
    ids=["on-the-head", "merged-results"],
)
async def test_glab_ci_status_reads_the_pinned_pipeline_directly(cli, tmp_path, routes, pinned):
    """A `pipeline_id` skips `glab ci list` entirely -- the caller already knows
    which pipeline this head has."""
    cli.stub("glab", routes={"mr view": _mr_view(), **routes})
    cli.stub("git", "deadbeef")

    status = await forge.GlabCli().ci_status(
        repo=tmp_path, mr=forge.MR(1, "u"), branch="kraft/abc", pipeline_id=pinned
    )

    assert (status.state, status.pipeline_ref, status.sha) == ("success", pinned, "deadbeef")
    assert not any(call.startswith("ci list") for call in cli.calls("glab"))


@pytest.mark.parametrize(
    "pinned",
    [
        {"ci get": FAIL},
        # One this head no longer answers for: returned, `ci_poll` would pin it
        # again on every poll.
        _pinned_merged_results(OLDER),
    ],
    ids=["unreadable", "built-from-another-head"],
)
async def test_glab_ci_status_falls_back_when_the_pinned_pipeline_is_unreadable(
    cli, tmp_path, pinned
):
    """A deleted/unreadable pinned pipeline must not fail the whole poll: fall
    back to resolving "latest on branch" the way an unpinned poll does."""
    cli.stub(
        "glab",
        routes={"mr view": _mr_view(), "ci list": GLAB_CI_SUCCESS, **pinned},
        default=FAIL,
    )
    cli.stub("git", "deadbeef")

    status = await forge.GlabCli().ci_status(
        repo=tmp_path, mr=forge.MR(1, "u"), branch="kraft/abc", pipeline_id="2962437758"
    )

    assert (status.state, status.pipeline_ref) == ("success", "2826926699")


GLAB_CI_CANCELED = (
    '[{"id":2826926702,"iid":144,"status":"canceled","ref":"kraft/abc",'
    '"updated_at":"2026-09-18T00:15:33.000Z",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926702"}]'
)


async def test_glab_ci_status_never_reads_a_canceled_pipeline_as_a_verdict(cli, tmp_path):
    """Kraft-zn8me on GitLab: `set_labels` re-creates the MR pipeline, and
    auto-cancel of redundant pipelines cancels the old one -- a wait for its
    successor, never red. Nor is it pinned, or every re-entry would read the
    same cancelled pipeline forever."""
    cli.stub("glab", GLAB_CI_CANCELED)

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1"))

    assert (status.state, status.pipeline_ref, status.failed_jobs) == ("pending", "", ())
    # When it was cancelled, for `render_ci` to tell a successor from none
    # (Kraft-kbqmk).
    assert status.cancelled_at == "2026-09-18T00:15:33.000Z"


async def test_glab_ci_status_moves_off_a_pinned_pipeline_that_was_canceled(cli, tmp_path):
    """The pin names the pipeline the last poll of this head saw. Once that one
    is cancelled its successor is the one to read -- the latest on the branch."""
    cli.stub(
        "glab",
        routes={
            "mr view": GLAB_MR_VIEW,
            "ci get": '{"id":2826926702,"status":"canceled","sha":"deadbeef"}',
            "ci list": GLAB_CI_SUCCESS,
        },
    )
    cli.stub("git", "")

    status = await forge.GlabCli().ci_status(
        repo=tmp_path, mr=forge.MR(1, "u"), branch="kraft/abc", pipeline_id="2826926702"
    )

    assert (status.state, status.pipeline_ref) == ("success", "2826926699")


async def test_glab_ci_status_names_the_failed_job_and_why(cli, tmp_path):
    """Kraft-xh0q. 'pipeline 2826926700: failed' tells a reader nothing they can
    act on. The failed job's name and its trace tail say the blocker is a
    missing label rather than the code."""
    cli.stub(
        "glab",
        routes={
            "mr view": GLAB_MR_VIEW,
            "ci list": GLAB_CI_FAILED,
            "ci get": GLAB_CI_GET_FAILED,
            "ci trace": GLAB_CI_TRACE,
        },
    )
    cli.stub("git", "")

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(54, "u"), branch="")

    assert status.state == "failed"
    joined = "\n".join(status.jobs)
    assert "release-impact" in joined
    assert "no release:: label" in joined, "the trace tail never reached the caller"
    assert "lint-and-test" not in joined, "a passing job is not a diagnosis"


async def test_glab_ci_status_does_not_chase_a_green_pipeline(cli, tmp_path):
    """Diagnosis costs two extra round trips per job; a pipeline that passed
    has nothing to diagnose."""
    cli.stub(
        "glab",
        routes={"mr view": GLAB_MR_VIEW, "ci list": GLAB_CI_SUCCESS, "ci get": GLAB_CI_GET_FAILED},
    )
    cli.stub("git", "")

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(54, "u"), branch="")

    assert status.state == "success"
    argv = cli.argv("glab")
    assert "trace" not in argv and "get" not in argv


@pytest.mark.parametrize(
    "ci_get, failed_jobs, infra",
    [
        # A job carrying its own `failure_reason` (Kraft-ddxn).
        (
            '{"id":2826926700,"status":"failed","jobs":[{"id":16392037104,"name":"test",'
            '"status":"failed","failure_reason":"script_failure"}]}',
            (forge.FailedJob("test", "failed", "script_failure"),),
            False,
        ),
        # No jobs at all: a config error stopped anything being created, so
        # `glab ci get` reports the pipeline's own `yaml_errors`.
        (
            '{"id":2826926700,"status":"failed","jobs":[],'
            '"yaml_errors":"jobs config should contain at least one visible job"}',
            (forge.FailedJob("(pipeline)", "failed", "config_error"),),
            False,
        ),
        # A read confirming no job ran at all, and no yaml_errors: infra-shaped.
        ('{"id":2826926700,"status":"failed","jobs":[]}', (), True),
        # The detail fetch itself failed: a genuine code failure must never
        # read as infra just because its own detail could not be read.
        (FAIL, forge.glab._UNREADABLE_JOBS, False),
        # Only the first two failed jobs get a trace, but every failed job is
        # classified: a code failure third in line keeps the pipeline code-red.
        (
            '{"id":2826926700,"status":"failed","jobs":['
            '{"id":1,"name":"a","status":"failed","failure_reason":"runner_system_failure"},'
            '{"id":2,"name":"b","status":"success"},'
            '{"id":3,"name":"c","status":"failed","failure_reason":"stuck_or_timeout_failure"},'
            '{"id":4,"name":"d","status":"failed","failure_reason":"script_failure"}]}',
            (
                forge.FailedJob("a", "failed", "runner_system_failure"),
                forge.FailedJob("c", "failed", "stuck_or_timeout_failure"),
                forge.FailedJob("d", "failed", "script_failure"),
            ),
            False,
        ),
    ],
    ids=[
        "job-with-reason",
        "zero-jobs-yaml-errors",
        "zero-jobs-confirmed",
        "detail-unreadable",
        "a-third-failed-job-counts",
    ],
)
async def test_glab_ci_status_reads_the_failed_jobs(cli, tmp_path, ci_get, failed_jobs, infra):
    cli.stub(
        "glab",
        routes={
            "mr view": GLAB_MR_VIEW,
            "ci list": GLAB_CI_FAILED,
            "ci get": ci_get,
            "ci trace": GLAB_CI_TRACE,
        },
    )
    cli.stub("git", "")

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(54, "u"), branch="")

    assert status.failed_jobs == failed_jobs
    assert status.pipeline_ref == "2826926700"
    assert forge.ci.is_infra_red(status) is infra


async def test_glab_retry_jobs_posts_the_pipeline_retry(cli, tmp_path):
    cli.stub("glab", routes={"api": "{}"})

    await forge.GlabCli().retry_jobs(
        repo=tmp_path, ci=forge.CIStatus(state="failed", url="u", pipeline_ref="123")
    )

    argv = cli.argv("glab")
    assert argv[:3] == ["api", "-X", "POST"]
    assert any("pipelines/123/retry" in a for a in argv)


# --- the merge request's own state ---------------------------------------------------


async def test_glab_ci_status_reads_the_mr_merge_state(cli, tmp_path):
    """Kraft-ejj9. A branch with a green pipeline and a real conflict against
    main passed mr_checks as done, walked through human_review, and only met
    the conflict at the merge node -- which reported success anyway."""
    cli.stub("glab", routes={"mr view": GLAB_MR_VIEW_CONFLICT, "ci list": GLAB_CI_SUCCESS})

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=NO_MR, branch="kraft/abc")

    assert status.state == "success", "the pipeline really is green -- that is the whole bug"
    assert (status.mergeable, status.merge_detail) == (False, "conflict")


@pytest.mark.parametrize(
    "detail",
    ["not_approved", "ci_still_running", "discussions_not_resolved", "draft_status", "checking"],
)
async def test_glab_ci_status_leaves_a_state_that_needs_a_person_undecided(cli, tmp_path, detail):
    """Deliberately the opposite of `_GLAB_STATES`' unknown-is-failure rule:
    mr_checks runs *before* the human_review gate, so these are the ordinary
    states of a healthy merge request here. Failing on them would fail the node
    on every repo with an approval rule."""
    view = '{"iid":54,"detailed_merge_status":"' + detail + '"}'
    cli.stub("glab", routes={"mr view": view, "ci list": GLAB_CI_SUCCESS})

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=NO_MR, branch="kraft/abc")

    assert status.mergeable is None


async def test_glab_ci_status_reads_an_out_of_band_merge_without_erroring(cli, tmp_path):
    """Kraft-v6ci. `state: "merged"` carries neither merge-status field, and
    must not fall through `_mergeable` as undecided or raise."""
    cli.stub("glab", routes={"mr view": GLAB_MR_VIEW_MERGED, "ci list": GLAB_CI_SUCCESS})

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=NO_MR, branch="kraft/abc")

    assert (status.state, status.mergeable, status.merge_detail) == ("success", True, "merged")
    assert status.jobs == ("merge request already merged",)
    assert "list" not in cli.argv("glab"), "a merged MR's pipeline list is not a fact this needs"


async def test_merge_state_falls_back_to_find_mr_when_mr_view_fails(cli, tmp_path):
    """Kraft-v6ci's reported failure mode: `glab mr view` errors when the
    branch resolves to nothing because the state changed under it. The same
    `--all` lookup merge's own "already merged" shortcut trusts (Kraft-xron)
    still finds it."""
    cli.stub("glab", routes={"mr view": FAIL, "mr list": GLAB_MR_LIST_MERGED}, default=FAIL)

    assert await forge.GlabCli()._merge_state(tmp_path, branch="kraft/abc") == "merged"


async def test_merge_state_still_raises_when_find_mr_has_no_answer(cli, tmp_path):
    """The fallback must not turn a genuine outage or auth failure into a false
    "merged": it only fires when `find_mr` itself confirms the merge."""
    cli.stub("glab", routes={"mr view": FAIL, "mr list": "[]"}, default=FAIL)

    with pytest.raises(forge.ForgeError):
        await forge.GlabCli()._merge_state(tmp_path, branch="kraft/abc")


@pytest.mark.parametrize(
    "mr_list, expected",
    [
        # A list, not a view: an empty list is an unambiguous 'no MR', where
        # `view`'s non-zero exit would force the caller to swallow real errors.
        ("[]", None),
        # One branch can carry a closed MR and an open one; every caller means
        # the open one.
        (
            '[{"iid":54,"state":"closed","web_url":"http://x/54"},'
            '{"iid":62,"state":"opened","web_url":"http://x/62"}]',
            forge.MRRef(number=62, url="http://x/62", state="open"),
        ),
        # Kraft-n60oh: with no merge commit, a squash fast-forwarded onto the
        # target is what landed; with neither, the head itself.
        (
            '[{"iid":54,"state":"merged","web_url":"http://x/54","sha":"c3",'
            '"merge_commit_sha":null,"squash_commit_sha":"c2"}]',
            forge.MRRef(number=54, url="http://x/54", state="merged", merged_sha="c2"),
        ),
        (
            '[{"iid":54,"state":"merged","web_url":"http://x/54","sha":"c3",'
            '"merge_commit_sha":null,"squash_commit_sha":null}]',
            forge.MRRef(number=54, url="http://x/54", state="merged", merged_sha="c3"),
        ),
    ],
    ids=[
        "none-when-the-branch-has-none",
        "prefers-the-open-one",
        "a-fast-forwarded-squash-landed-its-squash",
        "a-fast-forward-landed-its-head",
    ],
)
async def test_glab_find_mr_picks_the_branch_s_merge_request(cli, tmp_path, mr_list, expected):
    cli.stub("glab", mr_list)

    assert await forge.GlabCli().find_mr(repo=tmp_path, branch="kraft/abc") == expected


# --- push / merge / labels ---------------------------------------------------------


async def test_push_names_the_work_item_branch_and_sets_no_upstream(cli, tmp_path):
    """Origin has no `kraft/abc` yet and Kraft never pushed it (every probe
    in this stubbed repo answers nothing), so there is no lease to attach
    and the push is the plain fast-forward one (Kraft-z6i8). No `-u`: it
    would write `branch.kraft/abc.*` into the repository's config, which in
    place is the person's."""
    cli.stub("git", "")

    await forge.GlabCli().push(repo=tmp_path, branch="kraft/abc")

    assert [c for c in cli.calls("git") if c.startswith("push")] == ["push origin kraft/abc"]


async def test_glab_merge_uses_the_number_when_one_is_known(cli, tmp_path):
    cli.stub("glab", routes={"api projects/:id": NO_TRAIN}, default="")
    cli.stub("git", "0")

    await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=54, url=""))

    assert cli.calls("glab")[-1].startswith("mr merge 54")


async def test_merge_refuses_a_branch_ahead_of_its_remote(cli, tmp_path):
    """Kraft-nh5m. Merging a head the forge has never seen merges code CI never
    ran; a stop a human reads beats a green merge of untested code."""
    cli.stub("git", "2")
    cli.stub("glab", "")

    with pytest.raises(forge.ForgeError, match="ahead of origin/kraft/abc by 2"):
        await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=NO_MR)

    assert cli.argv("glab") == [], "glab merged a branch the forge has never seen"


async def test_merge_proceeds_when_the_branch_is_pushed(cli, tmp_path):
    """Zero commits ahead is the ordinary path after the sync node pushes."""
    cli.stub("git", "0")
    cli.stub("glab", routes={"api projects/:id": NO_TRAIN}, default="")

    await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=NO_MR)

    assert cli.calls("glab")[-1].startswith("mr merge")


async def test_glab_set_labels_labels_the_mr_and_starts_a_new_pipeline(cli, tmp_path):
    """A label added to an MR does not reach the pipeline that already ran:
    CI_MERGE_REQUEST_LABELS is fixed when the pipeline is created, so retrying
    the job re-reads the old value. Labelling without re-creating looks fixed
    and is still red."""
    cli.stub("glab", routes={"mr view": GLAB_MR_VIEW, "mr update": "", "api": "{}"})

    await forge.GlabCli().set_labels(
        repo=tmp_path,
        branch="kraft/abc",
        mr=forge.MR(number=54, url="u"),
        labels=("release::patch",),
    )

    argv = cli.argv("glab")
    assert argv[:2] == ["mr", "view"], "current labels must be read before they are replaced"
    assert argv[argv.index("update") - 1] == "mr"
    assert "release::patch" in argv
    assert "POST" in argv, "the pipeline was never re-created"
    assert any("merge_requests/54/pipelines" in a for a in argv)


async def test_glab_set_labels_replaces_an_existing_same_scope_label(cli, tmp_path):
    """GitLab's free tier does not enforce a scoped label's exclusivity
    server-side (Kraft-zfdu8): `glab mr update --label` only adds, so a second
    repair pass left both `release::minor` and `release::patch`, and
    `next_tag.py` raises on more than one. The existing same-scope label is
    dropped in the same call that adds the new one."""
    view = (
        '{"iid":54,"target_branch":"main","source_branch":"kraft/abc","state":"opened",'
        '"labels":["release::minor","bug"],"web_url":"http://x/54"}'
    )
    cli.stub("glab", routes={"mr view": view, "mr update": "", "api": "{}"})

    await forge.GlabCli().set_labels(
        repo=tmp_path,
        branch="kraft/abc",
        mr=forge.MR(number=54, url="u"),
        labels=("release::patch",),
    )

    argv = cli.argv("glab")
    assert argv[argv.index("--label") + 1] == "release::patch"
    assert argv[argv.index("--unlabel") + 1] == "release::minor"
    assert "bug" not in argv[argv.index("--unlabel") :][:2], "an unscoped label must not be dropped"


async def test_glab_set_labels_re_creates_the_pipeline_for_the_sentinel_number(cli, tmp_path):
    """`run_task` passes number 0 -- "resolve from the checked-out branch" -- to
    every forge handler, so 0 is the number the only real caller supplies.
    Skipping the re-create for it would leave a labelled MR and the same red
    pipeline."""
    cli.stub("glab", routes={"mr update": "", "mr view": GLAB_MR_VIEW, "api": "{}"})

    await forge.GlabCli().set_labels(
        repo=tmp_path, branch="kraft/abc", mr=NO_MR, labels=("release::patch",)
    )

    argv = cli.argv("glab")
    assert "POST" in argv, "the pipeline was never re-created"
    assert any("/pipelines" in a for a in argv)
    assert not any("merge_requests/0/" in a for a in argv), "the sentinel 0 was used as an id"


async def test_glab_rerun_after_description_change_creates_a_new_pipeline(cli, tmp_path):
    """`CI_MERGE_REQUEST_DESCRIPTION` is fixed at pipeline creation: retrying
    the old pipeline's jobs would re-read the old body."""
    cli.stub("glab", routes={"mr view": GLAB_MR_VIEW, "api": ""})

    made_new = await forge.GlabCli().rerun_after_description_change(
        repo=tmp_path, branch="b", ci=forge.CIStatus(state="failed", url="u", pipeline_ref="9")
    )

    assert made_new is True
    argv = cli.argv("glab")
    assert "POST" in argv and any("/merge_requests/54/pipelines" in a for a in argv)
    assert not any("/retry" in a for a in argv)


async def test_glab_mr_body_reads_the_live_description(cli, tmp_path):
    cli.stub("glab", routes={"mr view": '{"description":"the live body"}'})

    assert await forge.GlabCli().mr_body(repo=tmp_path, branch="b") == "the live body"
    assert cli.argv("glab") == ["mr", "view", "b", "-F", "json"]
