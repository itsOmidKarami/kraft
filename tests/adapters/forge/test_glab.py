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
    GLAB_CI_SUCCESS,
    GLAB_CI_TRACE,
    GLAB_MR_VIEW,
    GLAB_MR_VIEW_CONFLICT,
    GLAB_PIPELINE_FAILED,
    PIPELINES,
    glab_merged_results,
    glab_mr,
    glab_pipeline,
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

# `GET projects/:id/pipelines/<id>` for a pipeline read by id -- the same shape
# the pipeline list returns per row.
GLAB_CI_GET_PINNED_SUCCESS = (
    '{"id":2826926705,"status":"success","sha":"deadbeef",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926705"}'
)

NO_MR = forge.MR(0, "")
#: A GitLab project that does not merge through a merge train.
NO_TRAIN = '{"id":1}'


def _pipeline(sha: str, ref: str = "kraft/abc") -> str:
    """A one-row pipeline list answer for a green pipeline on commit `sha`."""
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
        **glab_mr(GLAB_MR_VIEW),
        PIPELINES: ci_list,
        **glab_pipeline(),
    }


async def test_glab_ci_status_maps_a_failed_pipeline(cli, tmp_path):
    cli.stub("glab", routes=_routes(GLAB_CI_FAILED), default=FAIL)

    status = await forge.GlabCli().ci_status(
        repo=tmp_path, mr=forge.MR(1, "http://x/1"), branch="kraft/abc"
    )

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

    status = await forge.GlabCli().ci_status(
        repo=tmp_path, mr=forge.MR(1, "http://x/1"), branch="kraft/abc"
    )

    assert status.state == "pending"


async def test_glab_raises_forge_error_when_the_cli_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no glab anywhere
    with pytest.raises(forge.ForgeError, match="glab"):
        await forge.GlabCli().ci_status(
            repo=tmp_path, mr=forge.MR(1, "http://x/1"), branch="kraft/abc"
        )


async def test_glab_raises_forge_error_when_the_cli_fails(cli, tmp_path):
    cli.stub("glab", "boom", rc=1)
    with pytest.raises(forge.ForgeError):
        await forge.GlabCli().ci_status(
            repo=tmp_path, mr=forge.MR(1, "http://x/1"), branch="kraft/abc"
        )


@pytest.mark.parametrize(
    "branch, fields",
    [
        ("kraft/abc", "-f ref=kraft/abc -F per_page=1 -f order_by=id -f sort=desc"),
        ("", "-F per_page=1 -f order_by=id -f sort=desc"),
    ],
    ids=["this-branch", "no-branch"],
)
async def test_glab_ci_status_asks_for_this_branch_only(cli, tmp_path, branch, fields):
    """Without a ref, the pipeline list returns the newest pipeline in the whole
    project. A green pipeline on main would pass the gate for a red branch."""
    cli.stub("glab", routes=_routes(GLAB_CI_SUCCESS), default=FAIL)
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"}, default="")

    await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(54, "u"), branch=branch)

    (ci_list,) = [c for c in cli.calls("glab") if c.startswith(f"{PIPELINES} ")]
    assert ci_list == f"{PIPELINES} -X GET -i {fields}"


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
        **glab_mr(_mr_view(pipeline if payload else None)),
        PIPELINES: listed if payload else json.dumps([pipeline]),
        read_commit: FAIL if unreadable else merge_commit,
    }


@pytest.mark.parametrize(
    "routes, expected, says, reads_a_commit",
    [
        # For a few seconds after a push the one-row pipeline list still returns the
        # *previous* commit's pipeline, which may be green (Kraft-bxj8).
        (lambda head, checkout: {PIPELINES: _pipeline(OLDER)}, "pending", "", False),
        (lambda head, checkout: {PIPELINES: _pipeline(head)}, "success", "", False),
        # An in-place item's checkout may be on another branch: its HEAD is
        # not the head being checked.
        (lambda head, checkout: {PIPELINES: _pipeline(checkout)}, "pending", "", False),
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
    cli.stub("glab", routes={**glab_mr(_mr_view()), **routes(branch_head, _head(repo))})

    status = await forge.GlabCli().ci_status(repo=repo, mr=NO_MR, branch="kraft/abc")

    assert status.state == expected
    assert ("no pipeline for" in status.jobs[0]) is (expected == "pending"), status.jobs
    assert not status.no_checks, "before the merge a missing pipeline is a plain wait"
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
    return {**newer, **glab_mr(_mr_view(lagging)), read_lagging: lagging_commit}


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


@pytest.mark.parametrize(
    "raw, state", [("success", "success"), ("running", "pending")], ids=["green", "running"]
)
async def test_glab_branch_ci_status_never_resolves_a_merge_request(cli, repo, raw, state):
    """merge_watch's whole reason to call this instead of `ci_status`: the
    checked-out branch's MR is already merged, so this must never shell out to
    `glab mr view` at all (code-review)."""
    head = _head(repo)
    cli.stub("glab", _pipeline(head, ref="main").replace('"success"', f'"{raw}"'))

    status = await forge.GlabCli().branch_ci_status(repo=repo, branch="main", head_sha=head)

    assert (status.state, status.mergeable) == (state, None)
    assert not status.no_checks, "a head with a pipeline has checks, even a running one"
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
    # Nothing for this head at all (Kraft-rbjo3): `merge_watch` gives up after a grace.
    assert status.no_checks
    assert not any("/repository/commits/" in call for call in cli.calls("glab"))


def _pinned_merged_results(tested: str) -> dict:
    """Routes for a pinned merged results pipeline built from `tested`."""
    pipeline, read_commit, merge_commit = glab_merged_results(tested, status="success")
    return {f"{PIPELINES}/{pipeline['id']}": json.dumps(pipeline), read_commit: merge_commit}


@pytest.mark.parametrize(
    "routes, pinned",
    [
        ({f"{PIPELINES}/2826926705": GLAB_CI_GET_PINNED_SUCCESS}, "2826926705"),
        # Its `sha` is a merge commit; the head it tested is what it answers for.
        (_pinned_merged_results("deadbeef"), "2962437758"),
    ],
    ids=["on-the-head", "merged-results"],
)
async def test_glab_ci_status_reads_the_pinned_pipeline_directly(cli, tmp_path, routes, pinned):
    """A `pipeline_id` skips the pipeline list entirely -- the caller already knows
    which pipeline this head has."""
    cli.stub("glab", routes={**glab_mr(_mr_view()), **routes})
    cli.stub("git", "deadbeef")

    status = await forge.GlabCli().ci_status(
        repo=tmp_path, mr=forge.MR(1, "u"), branch="kraft/abc", pipeline_id=pinned
    )

    assert (status.state, status.pipeline_ref, status.sha) == ("success", pinned, "deadbeef")
    assert not any(call.startswith(f"{PIPELINES} ") for call in cli.calls("glab"))


@pytest.mark.parametrize(
    "pinned",
    [
        {f"{PIPELINES}/2962437758": FAIL},
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
        routes={**glab_mr(_mr_view()), PIPELINES: GLAB_CI_SUCCESS, **pinned},
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
    cli.stub("glab", routes=glab_mr(GLAB_MR_VIEW), default=GLAB_CI_CANCELED)

    status = await forge.GlabCli().ci_status(
        repo=tmp_path, mr=forge.MR(1, "http://x/1"), branch="kraft/abc"
    )

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
            **glab_mr(GLAB_MR_VIEW),
            f"{PIPELINES}/2826926702": '{"id":2826926702,"status":"canceled","sha":"deadbeef"}',
            PIPELINES: GLAB_CI_SUCCESS,
        },
    )
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"}, default="")

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
            **glab_mr(GLAB_MR_VIEW),
            PIPELINES: GLAB_CI_FAILED,
            **glab_pipeline(),
        },
    )
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"}, default="")

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(54, "u"), branch="")

    assert status.state == "failed"
    joined = "\n".join(status.jobs)
    assert "release-impact" in joined
    assert "no release:: label" in joined, "the trace tail never reached the caller"
    assert "lint-and-test" not in joined, "a passing job is not a diagnosis"
    assert [c for c in cli.calls("glab") if "/jobs" in c or "/pipelines/" in c] == [
        f"{PIPELINES}/2826926700 -X GET -i",
        f"{PIPELINES}/2826926700/jobs -X GET -i -F per_page=100 -F page=1",
        "api projects/:id/jobs/16392037104/trace -X GET -i",
    ]


async def test_glab_ci_status_does_not_chase_a_green_pipeline(cli, tmp_path):
    """Diagnosis costs two extra round trips per job; a pipeline that passed
    has nothing to diagnose."""
    cli.stub(
        "glab",
        routes={**glab_mr(GLAB_MR_VIEW), PIPELINES: GLAB_CI_SUCCESS, **glab_pipeline()},
    )
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"}, default="")

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(54, "u"), branch="")

    assert status.state == "success"
    assert not any("/jobs" in call or "/trace" in call for call in cli.calls("glab"))


@pytest.mark.parametrize(
    "pipeline, jobs, failed_jobs, infra",
    [
        # A job carrying its own `failure_reason` (Kraft-ddxn).
        (
            GLAB_PIPELINE_FAILED,
            '[{"id":16392037104,"name":"test","status":"failed","failure_reason":"script_failure"}]',
            (forge.FailedJob("test", "failed", "script_failure"),),
            False,
        ),
        # No jobs at all: a config error stopped anything being created, so
        # the pipeline reports its own `yaml_errors`.
        (
            '{"id":2826926700,"status":"failed",'
            '"yaml_errors":"jobs config should contain at least one visible job"}',
            "[]",
            (forge.FailedJob("(pipeline)", "failed", "config_error"),),
            False,
        ),
        # A read confirming no job ran at all, and no yaml_errors: infra-shaped.
        (GLAB_PIPELINE_FAILED, "[]", (), True),
        # The detail fetch itself failed: a genuine code failure must never
        # read as infra just because its own detail could not be read.
        (FAIL, "[]", forge.glab._UNREADABLE_JOBS, False),
        # The jobs list failed after the pipeline was read: the same.
        (GLAB_PIPELINE_FAILED, FAIL, forge.glab._UNREADABLE_JOBS, False),
        # Only the first two failed jobs get a trace, but every failed job is
        # classified: a code failure third in line keeps the pipeline code-red.
        (
            GLAB_PIPELINE_FAILED,
            '[{"id":1,"name":"a","status":"failed","failure_reason":"runner_system_failure"},'
            '{"id":2,"name":"b","status":"success"},'
            '{"id":3,"name":"c","status":"failed","failure_reason":"stuck_or_timeout_failure"},'
            '{"id":4,"name":"d","status":"failed","failure_reason":"script_failure"}]',
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
        "jobs-unreadable",
        "a-third-failed-job-counts",
    ],
)
async def test_glab_ci_status_reads_the_failed_jobs(
    cli, tmp_path, pipeline, jobs, failed_jobs, infra
):
    cli.stub(
        "glab",
        routes={
            **glab_mr(GLAB_MR_VIEW),
            PIPELINES: GLAB_CI_FAILED,
            **glab_pipeline(pipeline, jobs),
        },
    )
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"}, default="")

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
    assert argv[:4] == ["api", "projects/:id/pipelines/123/retry", "-X", "POST"]


# --- the merge request's own state ---------------------------------------------------


async def test_glab_ci_status_reads_the_mr_merge_state(cli, tmp_path):
    """Kraft-ejj9. A branch with a green pipeline and a real conflict against
    main passed mr_checks as done, walked through human_review, and only met
    the conflict at the merge node -- which reported success anyway."""
    cli.stub("glab", routes={**glab_mr(GLAB_MR_VIEW_CONFLICT), PIPELINES: GLAB_CI_SUCCESS})

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
    cli.stub("glab", routes={**glab_mr(view), PIPELINES: GLAB_CI_SUCCESS})

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=NO_MR, branch="kraft/abc")

    assert status.mergeable is None


async def test_glab_ci_status_reads_an_out_of_band_merge_without_erroring(cli, tmp_path):
    """Kraft-v6ci. `state: "merged"` carries neither merge-status field, and
    must not fall through `_mergeable` as undecided or raise."""
    cli.stub(
        "glab", routes={**glab_mr(GLAB_MR_VIEW_MERGED, open=False), PIPELINES: GLAB_CI_SUCCESS}
    )

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=NO_MR, branch="kraft/abc")

    assert (status.state, status.mergeable, status.merge_detail) == ("success", True, "merged")
    assert status.jobs == ("merge request already merged",)
    assert "list" not in cli.argv("glab"), "a merged MR's pipeline list is not a fact this needs"


# --- push / merge / labels ---------------------------------------------------------


async def test_push_names_the_work_item_branch_and_sets_no_upstream(cli, tmp_path):
    """Origin has no `kraft/abc` yet and Kraft never pushed it (every probe
    in this stubbed repo answers nothing), so there is no lease to attach
    and the push is the plain fast-forward one (Kraft-z6i8). No `-u`: it
    would write `branch.kraft/abc.*` into the repository's config, which in
    place is the person's."""
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"}, default="")

    await forge.GlabCli().push(repo=tmp_path, branch="kraft/abc")

    assert [c for c in cli.calls("git") if c.startswith("push")] == ["push origin kraft/abc"]


async def test_glab_merge_uses_the_number_when_one_is_known(cli, tmp_path):
    cli.stub("glab", routes={"api projects/:id": NO_TRAIN}, default=GLAB_MR_VIEW)
    cli.stub("git", "0")

    await forge.GlabCli().merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=54, url=""), head="a" * 40
    )

    assert cli.calls("glab")[-1].startswith("api projects/:id/merge_requests/54/merge -X PUT")


async def test_merge_refuses_a_branch_ahead_of_its_remote(cli, tmp_path):
    """Kraft-nh5m. Merging a head the forge has never seen merges code CI never
    ran; a stop a human reads beats a green merge of untested code."""
    cli.stub("git", "2")
    cli.stub("glab", "")

    with pytest.raises(forge.ForgeError, match="ahead of origin/kraft/abc by 2"):
        await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=NO_MR, head="a" * 40)

    assert cli.argv("glab") == [], "glab merged a branch the forge has never seen"


async def test_merge_proceeds_when_the_branch_is_pushed(cli, tmp_path):
    """Zero commits ahead is the ordinary path after the sync node pushes."""
    cli.stub("git", "0")
    cli.stub("glab", routes={"api projects/:id": NO_TRAIN, **glab_mr(GLAB_MR_VIEW)}, default="")

    await forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=NO_MR, head="a" * 40)

    assert cli.calls("glab")[-1].startswith("api projects/:id/merge_requests/54/merge -X PUT")


async def test_glab_ci_status_pages_through_the_jobs_to_a_failure_on_page_two(cli, tmp_path):
    """A pipeline of 101 jobs: the failed one is the 101st, alone on page two.
    glab follows X-Next-Page without a cap (get.go:117-124); a first page of
    exactly 100 means there may be more."""
    first = json.dumps([{"id": n, "name": f"j{n}", "status": "success"} for n in range(1, 101)])
    last = '[{"id":101,"name":"deploy","status":"failed","failure_reason":"script_failure"}]'
    jobs = f"{PIPELINES}/2826926700/jobs"
    cli.stub(
        "glab",
        routes={
            **glab_mr(GLAB_MR_VIEW),
            PIPELINES: GLAB_CI_FAILED,
            f"{jobs}*page=2": last,
            **glab_pipeline(jobs=first),
            "api projects/:id/jobs/101/trace": GLAB_CI_TRACE,
        },
    )
    cli.stub("git", routes={"symbolic-ref --short": "kraft/abc"}, default="")

    status = await forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(54, "u"), branch="")

    assert status.failed_jobs == (forge.FailedJob("deploy", "failed", "script_failure"),)
    assert "no release:: label" in "\n".join(status.jobs)
    assert [c for c in cli.calls("glab") if "/jobs" in c or "/pipelines/" in c] == [
        f"{PIPELINES}/2826926700 -X GET -i",
        f"{jobs} -X GET -i -F per_page=100 -F page=1",
        f"{jobs} -X GET -i -F per_page=100 -F page=2",
        "api projects/:id/jobs/101/trace -X GET -i",
    ]
