"""What `GlabCli` and `GhCli` both promise, against a stubbed `glab`/`gh`
(and `git`) on PATH: the argv each operation builds and what it parses back.
Behaviour only one CLI has stays in test_glab.py / test_gh.py."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from kraft.adapters import forge
from kraft.adapters.forge import gh as gh_adapter
from kraft.adapters.forge import glab as glab_adapter
from kraft.adapters.forge.gh import _QUEUE_QUERY, _THREADS_QUERY
from kraft.adapters.forge.mr import MRMeta
from kraft.automated_review import AutomatedReview

from . import api_outputs, outputs
from .nodes import Api, FailWith

#: The full sha a merge is asked to land.
HEAD = "a" * 40

GLAB = SimpleNamespace(
    name="glab",
    cls=forge.GlabCli,
    view=outputs.GLAB_MR_VIEW,
    number=54,
    url="https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54",
    body_flag="--description",
    base_flag="--target-branch",
    ready_argv=["mr", "update", "54", "--ready"],
    update_argv=["mr", "update", "kraft/abc", "--description", "fresh"],
    merge_verb="mr merge",
    head_flag="--sha",
    head_moved="409 Branch Merge failed: SHA does not match HEAD of source branch",
    # A merged GitLab merge request's `sha` can be the head it rebased for a fast-forward.
    merged_head="",
    merged_list=outputs.GLAB_MR_LIST_MERGED,
    queued_list=outputs.GLAB_MR_LIST_AUTO_MERGE,
    find_route="mr list",
    find_args={"mr": "list", "--all": None, "--source-branch": "kraft/abc"},
    close_argv=["mr", "close", "54"],
    reopen_argv=["mr", "reopen", "54"],
)
GH = SimpleNamespace(
    name="gh",
    cls=forge.GhCli,
    view=outputs.GH_PR_VIEW,
    number=7,
    url="https://github.com/o/r/pull/7",
    body_flag="--body",
    base_flag="--base",
    ready_argv=["pr", "ready", "7"],
    update_argv=["pr", "edit", "kraft/abc", "--body", "fresh"],
    merge_verb="pr merge",
    head_flag="--match-head-commit",
    head_moved="GraphQL: Head branch was modified. Review and try the merge again.",
    merged_head="c0ffee0",
    merged_list=api_outputs.FIND_MERGED,
    queued_list=api_outputs.FIND_AUTO_MERGE,
    find_route=api_outputs.FIND,
    find_args={"owner={owner}": None, "name={repo}": None, "b=kraft/abc": None},
    close_argv=["pr", "close", "7"],
    reopen_argv=["pr", "reopen", "7"],
)


@pytest.fixture(params=[GLAB, GH], ids=["glab", "gh"])
def be(request, cli):
    """One backend, with its CLI stubbed to answer a merge request view and
    `git` stubbed clean."""
    b = request.param
    cli.stub(b.name, b.view)
    cli.stub("git", "")
    return b


async def test_open_mr_opens_a_draft_titled_with_the_work_item(be, cli, tmp_path):
    """Every MR Kraft opens starts as a draft -- human_review decides when it
    is ready (draft-MR workflow spec). `--fill` titled it from the commits,
    and with more than one commit that is the branch name -- a work item id,
    so every Kraft MR read as a hex string (Kraft-c09h). An empty `--label ""`
    is a real, empty value to the CLI, not an absence."""
    mr = await be.cls().open_mr(
        repo=tmp_path, branch="kraft/abc", base="main", title="Teach it", body="why"
    )

    assert (mr.number, mr.url) == (be.number, be.url)
    argv = cli.argv(be.name)
    assert "--draft" in argv
    assert "--fill" not in argv
    assert argv[argv.index("--title") + 1] == "Teach it"
    assert argv[argv.index(be.body_flag) + 1] == "why"
    assert not {"--label", "--assignee", "--reviewer"} & set(argv)


async def test_open_mr_targets_the_base_it_is_given(be, cli, tmp_path):
    """Kraft-v9gbi: the item's base branch, never the forge's default."""
    await be.cls().open_mr(repo=tmp_path, branch="kraft/abc", base="release", title="T", body="B")

    argv = cli.argv(be.name)
    assert argv[argv.index(be.base_flag) + 1] == "release"


async def test_open_mr_passes_the_authored_metadata(be, cli, tmp_path):
    meta = MRMeta(labels=("release::minor",), assignees=("omid",), reviewers=("ada", "grace"))

    await be.cls().open_mr(
        repo=tmp_path, branch="kraft/abc", base="main", title="T", body="B", meta=meta
    )

    argv = cli.argv(be.name)
    assert argv[argv.index("--label") + 1] == "release::minor"
    assert argv[argv.index("--assignee") + 1] == "omid"
    assert argv[argv.index("--reviewer") + 1] == "ada,grace"


@pytest.mark.parametrize(
    "backend, porcelain, named",
    [
        # Kraft-brq/fhd1, work item 5163dd1b: 18 dirty files, and open_mr pushed
        # a branch carrying only the spec and plan commits.
        (GLAB, " M src/kraft/adapters/forge.py", "forge.py"),
        # Untracked files are not excused: a new file the agent never `git
        # add`ed is exactly what went missing on 5163dd1b.
        (GH, "?? tests/test_new_thing.py", "test_new_thing.py"),
    ],
    ids=["glab-modified", "gh-untracked-only"],
)
async def test_open_mr_refuses_a_dirty_worktree(cli, tmp_path, backend, porcelain, named):
    cli.stub(backend.name, backend.view)
    cli.stub("git", porcelain)

    with pytest.raises(forge.ForgeError, match=named):
        await backend.cls().open_mr(
            repo=tmp_path, branch="kraft/abc", base="main", title="t", body="b"
        )

    assert cli.argv(backend.name) == [], "the CLI ran over an uncommitted worktree"


_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_NO_THREADS = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}

#: Each call that reaches the merge request with no number known.
_BY_BRANCH = {
    "open_mr": lambda f, r: f.open_mr(repo=r, branch="kraft/abc", base="main", title="t", body="b"),
    "update_mr": lambda f, r: f.update_mr(repo=r, branch="kraft/abc", body="b"),
    "mark_ready": lambda f, r: f.mark_ready(repo=r, branch="kraft/abc", mr=forge.MR(0, "")),
    "ci_status": lambda f, r: f.ci_status(repo=r, mr=forge.MR(0, ""), branch="kraft/abc"),
    "merge": lambda f, r: f.merge(repo=r, branch="kraft/abc", mr=forge.MR(0, ""), head="a" * 40),
    "set_labels": lambda f, r: f.set_labels(
        repo=r, branch="kraft/abc", mr=forge.MR(0, ""), labels=("x",)
    ),
    "bot_review": lambda f, r: f.automated_review(
        repo=r, branch="kraft/abc", reviewers=(AutomatedReview(bot="b"),)
    ),
    "check_review": lambda f, r: f.automated_review(
        repo=r, branch="kraft/abc", reviewers=(AutomatedReview(check="c"),)
    ),
    "bot_feedback": lambda f, r: f.reviewer_feedback(
        repo=r, branch="kraft/abc", reviewers=(AutomatedReview(bot="b"),), since=_SINCE
    ),
    "check_feedback": lambda f, r: f.reviewer_feedback(
        repo=r, branch="kraft/abc", reviewers=(AutomatedReview(check="c"),), since=_SINCE
    ),
}


@pytest.mark.parametrize("op", list(_BY_BRANCH))
async def test_every_merge_request_call_names_the_branch(be, cli, tmp_path, op):
    """`in-place-mr-names-its-branch`: an in-place item's checkout may be on
    any branch, so no call resolves the merge request from the current one."""
    pr = {"number": 7, "url": "u", "headRefOid": "abc", "iid": 7, "web_url": "u", "sha": "abc"}
    cli.stub(
        be.name,
        routes={
            api_outputs.READ_BRANCH: api_outputs.pr_by_branch(api_outputs.pull_request()),
            "pr view": json.dumps(pr),
            "mr view": json.dumps(pr),
            "ci list": outputs.GLAB_CI_SUCCESS,
            "api graphql": json.dumps(_NO_THREADS),
        },
    )

    await _BY_BRANCH[op](be.cls(), tmp_path)

    # A GraphQL query's own lines are calls of no words, so only the first line
    # of each call is read.
    calls = [c.split() for c in cli.calls(be.name) if c.split()[:1] in (["pr"], ["mr"])]
    argv = cli.argv(be.name)
    assert calls or argv.count("graphql")
    for call in calls:
        assert "kraft/abc" in call, call
    if be is GH:
        # Each GraphQL call carries the branch (`b`) or the number it read from
        # the branch (`n`) as a variable, and none asked git for the current one.
        named = [x for x in argv if x in ("b=kraft/abc", "n=7")]
        assert len(named) == argv.count("graphql"), argv
        assert not [c for c in cli.calls("git") if "symbolic-ref" in c]


async def test_mark_ready_unsets_draft(be, cli, tmp_path):
    await be.cls().mark_ready(repo=tmp_path, branch="kraft/abc", mr=forge.MR(be.number, "u"))

    assert cli.argv(be.name) == be.ready_argv


async def test_close_mr_runs_the_forges_close_command(be, cli, tmp_path):
    """B4: cancel closing the item's merge request, the same shape as
    `merge`'s own runner and timeout."""
    await be.cls().close_mr(repo=tmp_path, mr=forge.MRRef(be.number, be.url, state="open"))

    assert cli.argv(be.name) == be.close_argv


async def test_reopen_mr_runs_the_forges_reopen_command(be, cli, tmp_path):
    """B8 (E), tested here with the rest of this pair: `reopen-mr` undoing an
    externally-closed merge request."""
    await be.cls().reopen_mr(repo=tmp_path, mr=forge.MRRef(be.number, be.url, state="closed"))

    assert cli.argv(be.name) == be.reopen_argv


async def test_update_mr_rewrites_the_description(be, cli, tmp_path):
    """`open_mr` runs before verify and mr_checks commit, so the description it
    wrote describes a branch that no longer exists (Kraft-c09h)."""
    await be.cls().update_mr(repo=tmp_path, branch="kraft/abc", body="fresh")

    assert cli.argv(be.name) == be.update_argv


async def test_merge_resolves_from_the_branch_when_no_number_is_known(be, cli, tmp_path):
    """`run_task` never threads the MR number between nodes -- it passes 0 and
    the CLI resolves the item's branch. `mr merge 0` would target a merge
    request that does not exist."""
    # A GitLab project with no merge train; gh asks no such thing.
    cli.stub(be.name, routes={"api projects/:id": '{"id":1}'}, default="")
    cli.stub("git", "0")  # zero commits ahead of origin

    await be.cls().merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""), head=HEAD
    )

    # glab also asks the project whether it merges through a train first.
    (call,) = [c for c in cli.calls(be.name) if c.startswith(be.merge_verb)]
    assert " 0 " not in f" {call} "
    assert call.split()[-2:] == [be.head_flag, HEAD], "the forge may merge only this head"


async def test_a_merge_with_no_head_to_name_is_refused_before_any_cli_call(be, cli, tmp_path):
    with pytest.raises(forge.ForgeError, match="without the head"):
        await be.cls().merge(
            repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""), head=""
        )

    assert cli.calls(be.name) == []


async def test_a_merge_the_forge_refuses_for_a_moved_head_raises_head_moved(be, cli, tmp_path):
    """Each forge's own words for "the head is not the one you named" become
    `HeadMoved`, with the head the forge holds when it can be read: the merge
    path turns that into a hold for another review, not a failed node. Any
    other refusal stays a plain `ForgeError`."""
    cli.stub("git", "0")
    cli.stub(
        be.name,
        routes={
            be.merge_verb: FailWith(be.head_moved),
            "api projects/:id": '{"id":1}',
            be.find_route: be.queued_list,
        },
    )

    with pytest.raises(forge.HeadMoved) as moved:
        await be.cls().merge(
            repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""), head=HEAD
        )

    assert moved.value.current == "f0reign1"
    cli.stub(be.name, routes={be.merge_verb: FailWith("merge conflict"), "api projects/:id": "{}"})
    with pytest.raises(forge.ForgeError, match="merge conflict") as other:
        await be.cls().merge(
            repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""), head=HEAD
        )
    assert not isinstance(other.value, forge.HeadMoved)


async def test_find_mr_reads_the_state_of_an_existing_merge_request(be, cli, tmp_path):
    """Lists every state -- or a merged MR reads as "no MR at all" -- for this
    source branch only -- or it answers about whatever the project merged most
    recently. A merged one names the revision its merge landed."""
    cli.stub(be.name, be.merged_list)

    found = await be.cls().find_mr(repo=tmp_path, branch="kraft/abc")

    assert found == forge.MRRef(
        number=be.number,
        url=be.url,
        state="merged",
        merged_sha="c0ffee1",
        head_sha=be.merged_head,
    ), "with the revision its merge landed (Kraft-n60oh)"
    argv = cli.argv(be.name)
    for flag, value in be.find_args.items():
        assert flag in argv and (value is None or argv[argv.index(flag) + 1] == value), flag


async def test_find_mr_reads_a_merge_the_forge_already_holds_queued(be, cli, tmp_path):
    """Kraft-l98h6: auto-merge (gh) or merge-when-pipeline-succeeds (glab) is
    the forge's own record that its merge was asked for, which a restart of
    Kraft's wait cannot erase."""
    cli.stub(be.name, be.queued_list)

    found = await be.cls().find_mr(repo=tmp_path, branch="kraft/abc")

    assert found == forge.MRRef(
        number=be.number, url=be.url, state="open", merge_queued=True, head_sha="f0reign1"
    ), "with the head the forge holds, which a merge Kraft asked for is compared against"


@pytest.mark.parametrize(
    ("held", "disabled", "dequeued"),
    [
        (("auto", "queue"), True, True),
        (("auto",), True, False),
        ((), False, False),
    ],
    ids=["auto-merge-and-queue", "auto-merge", "neither"],
)
async def test_gh_cancel_merge_undoes_only_what_holds_the_pull_request(
    cli, tmp_path, held, disabled, dequeued
):
    """Auto-merge is disabled and a merge queue entry dequeued, each read first:
    a pull request that is neither costs no call that could fail."""
    pull = {
        "id": "PR_1",
        "autoMergeRequest": {"enabledAt": "t"} if "auto" in held else None,
        "mergeQueueEntry": {"id": "Q_1"} if "queue" in held else None,
    }
    read = json.dumps({"data": {"repository": {"pullRequest": pull}}})
    cli.stub("gh", routes={"api graphql": read}, default="")

    await forge.GhCli().cancel_merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MRRef(7, "u", state="open", merge_queued=True)
    )

    argv = cli.argv("gh")
    assert ("--disable-auto" in argv) is disabled
    assert ("id=PR_1" in argv) is dequeued


@pytest.mark.parametrize(
    ("project", "argv"),
    [
        (
            '{"id":1}',
            [
                "api",
                "projects/:id/merge_requests/54/cancel_merge_when_pipeline_succeeds",
                "-X",
                "POST",
                "-i",
            ],
        ),
        (
            '{"merge_trains_enabled":true,"merge_pipelines_enabled":true}',
            [
                "api",
                "projects/:id/merge_requests/54/cancel_merge_when_pipeline_succeeds",
                "-X",
                "POST",
                "-i",
            ],
        ),
    ],
    ids=["merge-when-pipeline-succeeds", "merge-train"],
)
async def test_glab_cancel_merge_cancels_the_merge_when_pipeline_succeeds_or_the_train_entry(
    cli, tmp_path, project, argv
):
    cli.stub("glab", routes={"api projects/:id": project}, default="{}")

    await forge.GlabCli().cancel_merge(
        repo=tmp_path, branch="kraft/abc", mr=forge.MRRef(54, "u", state="open", merge_queued=True)
    )

    assert cli.calls("glab")[-1].split() == argv


@pytest.mark.parametrize(
    "failure",
    [
        Api(406, '{"message": "406 Not Acceptable"}'),
        # `glab api` exits 0 on this: the endpoint answers 201 with the error in the body.
        '{"status":"error","message":"Cannot cancel the automatic merge","http_status":406}',
    ],
    ids=["http-406", "201-status-error"],
)
async def test_glab_cancel_merge_that_fails_raises_unless_nothing_was_queued(
    cli, tmp_path, failure
):
    """The forge may still merge when a cancel fails, so it fails loudly; the
    refusal GitLab gives a merge request with nothing to cancel is not one."""
    cli.stub("glab", default=failure, routes={})
    glab = forge.GlabCli()

    with pytest.raises(forge.ForgeError, match="cancel|406"):
        await glab.cancel_merge(
            repo=tmp_path, branch="b", mr=forge.MRRef(54, "u", state="open", merge_queued=True)
        )
    await glab.cancel_merge(repo=tmp_path, branch="b", mr=forge.MRRef(54, "u", state="open"))


@pytest.mark.parametrize(
    "backend, view, reason, mergeable",
    [
        (GLAB, outputs.GLAB_MR_VIEW_CONFLICT, "conflict", False),
        (GH, api_outputs.pr_by_branch(api_outputs.CONFLICT_PR), "conflict", False),
        # A conflict is a code problem; a missing approval is a person's to
        # grant on the forge. `merge` has to tell them apart, and mr_checks
        # (pre-gate) must read the approval as undecided, not fail on it.
        (GLAB, outputs.GLAB_MR_VIEW_NOT_APPROVED, "not_approved", None),
        (GH, api_outputs.pr_by_branch(api_outputs.NEEDS_APPROVAL_PR), "not_approved", None),
    ],
    ids=["glab-conflict", "gh-conflict", "glab-missing-approval", "gh-missing-approval"],
)
async def test_ci_status_names_the_block_reason(cli, tmp_path, backend, view, reason, mergeable):
    cli.stub(
        backend.name,
        routes={"mr view": view, "ci list": outputs.GLAB_CI_SUCCESS, api_outputs.READ_BRANCH: view},
    )

    status = await backend.cls().ci_status(repo=tmp_path, mr=forge.MR(0, ""), branch="kraft/abc")

    assert (status.state, status.block_reason, status.mergeable) == ("success", reason, mergeable)


@pytest.mark.e2e("glab")
def test_glab_train_payload_shapes():
    """The merge-train payloads `GlabCli.merge_queue` reads, from a real GitLab:
    the project's `merge_trains_enabled`, and a car's `status`, `pipeline.ref`
    and `merge_request.iid`. Needs a checkout of a project on a merge train
    (`KRAFT_E2E_GLAB_REPO`) and the iid of a merge request on its train
    (`KRAFT_E2E_GLAB_TRAIN_MR`), and skips without them."""
    repo, iid = os.environ.get("KRAFT_E2E_GLAB_REPO"), os.environ.get("KRAFT_E2E_GLAB_TRAIN_MR")
    if not (repo and iid):
        pytest.skip("e2e: set KRAFT_E2E_GLAB_REPO and KRAFT_E2E_GLAB_TRAIN_MR (a train project)")

    def api(path: str) -> dict:
        done = subprocess.run(
            ["glab", "api", path], cwd=repo, capture_output=True, text=True, check=True
        )
        return json.loads(done.stdout)

    assert "merge_trains_enabled" in api("projects/:id")
    car = api(f"projects/:id/merge_trains/merge_requests/{iid}")
    assert car["status"]
    assert car["pipeline"]["ref"]
    assert car["merge_request"]["iid"] == int(iid)


@pytest.mark.e2e("gh")
def test_gh_merge_queue_fields_exist(record_property):
    """GitHub's schema accepts the query `GhCli.merge_queue` sends: `mergeQueue`,
    a pull request's `mergeQueueEntry`, and `RemovedFromMergeQueueEvent`'s
    `reason`, `createdAt` and `enqueuer`. It runs against `KRAFT_E2E_GH_REPO`
    (`owner/name`, any repository you can read) at pull request
    `KRAFT_E2E_GH_PR` into `KRAFT_E2E_GH_BASE` (default `main`); a repository
    with no queue answers `mergeQueue: null`, which is the query being valid.

    Whether `gh pr merge --squash` is accepted where a queue is required is
    recorded when `KRAFT_E2E_GH_QUEUE_PR` names a pull request of that
    repository on such a branch, and it is then really asked to merge. It is
    not exercised otherwise, and the test skips saying so."""
    slug, number = os.environ.get("KRAFT_E2E_GH_REPO"), os.environ.get("KRAFT_E2E_GH_PR")
    if not (slug and number):
        pytest.skip("e2e: set KRAFT_E2E_GH_REPO (owner/name) and KRAFT_E2E_GH_PR")
    owner, _, name = slug.partition("/")
    base = os.environ.get("KRAFT_E2E_GH_BASE", "main")
    done = subprocess.run(
        ["gh", "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}"]
        + ["-F", f"base={base}", "-F", f"n={number}", "-f", f"query={_QUEUE_QUERY}"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert "errors" not in data
    repository = data["data"]["repository"]
    assert "mergeQueue" in repository
    pull = repository["pullRequest"]
    assert {"state", "mergeQueueEntry", "timelineItems"} <= pull.keys()

    queue_pr = os.environ.get("KRAFT_E2E_GH_QUEUE_PR")
    if not queue_pr:
        pytest.skip(
            "schema accepted; `gh pr merge --squash` on a queue-required branch not "
            "exercised: set KRAFT_E2E_GH_QUEUE_PR (it will really be asked to merge)"
        )
    merged = subprocess.run(
        ["gh", "pr", "merge", queue_pr, "--squash", "--repo", slug],
        capture_output=True,
        text=True,
    )
    record_property("gh_pr_merge_squash_on_queue_rc", merged.returncode)
    record_property("gh_pr_merge_squash_on_queue_output", (merged.stdout + merged.stderr)[-500:])


@pytest.mark.e2e("gh")
def test_gh_refuses_a_merge_naming_a_head_the_pull_request_does_not_have(record_property):
    """`gh pr merge --match-head-commit` with a sha the pull request is not at is
    refused, in the words `GhCli.merge` reads as `HeadMoved`. Needs a pull request
    of `KRAFT_E2E_GH_REPO` (`owner/name`) at `KRAFT_E2E_GH_PR`. It never merges:
    no pull request has a head of all zeros."""
    slug, number = os.environ.get("KRAFT_E2E_GH_REPO"), os.environ.get("KRAFT_E2E_GH_PR")
    if not (slug and number):
        pytest.skip("e2e: set KRAFT_E2E_GH_REPO (owner/name) and KRAFT_E2E_GH_PR")
    done = subprocess.run(
        ["gh", "pr", "merge", number, "--squash", "--repo", slug]
        + ["--match-head-commit", "0" * 40],
        capture_output=True,
        text=True,
    )
    said = done.stdout + done.stderr
    record_property("gh_match_head_commit_refusal", said[-500:])
    assert done.returncode != 0
    assert gh_adapter._HEAD_MODIFIED in said.lower()


@pytest.mark.e2e("glab")
def test_glab_refuses_a_merge_naming_a_head_the_merge_request_does_not_have(record_property):
    """`glab mr merge --sha` with a sha the source branch is not at is refused
    with a 409, in the words `GlabCli.merge` reads as `HeadMoved`. Needs a checkout
    of a GitLab project (`KRAFT_E2E_GLAB_REPO`) and the iid of one of its open merge
    requests (`KRAFT_E2E_GLAB_MR`). It never merges: no branch is at all zeros."""
    repo, iid = os.environ.get("KRAFT_E2E_GLAB_REPO"), os.environ.get("KRAFT_E2E_GLAB_MR")
    if not (repo and iid):
        pytest.skip("e2e: set KRAFT_E2E_GLAB_REPO and KRAFT_E2E_GLAB_MR")
    done = subprocess.run(
        ["glab", "mr", "merge", iid, "--yes", "--sha", "0" * 40],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    said = done.stdout + done.stderr
    record_property("glab_sha_refusal", said[-500:])
    assert done.returncode != 0
    assert glab_adapter._HEAD_MOVED in said.lower()


@pytest.mark.e2e("gh")
def test_gh_reviewer_feedback_payload_shapes():
    """The payloads `GhCli.reviewer_feedback` reads, from a real GitHub: a pull
    request's issue comments (`user.login`, `updated_at`), its reviews
    (`commit_id`, `submitted_at`), and the review threads query
    (`isResolved`, and each comment's `author`, `path`, `line`, `originalLine`
    and `updatedAt`). Runs against `KRAFT_E2E_GH_REPO` (`owner/name`) at pull
    request `KRAFT_E2E_GH_PR`."""
    slug, number = os.environ.get("KRAFT_E2E_GH_REPO"), os.environ.get("KRAFT_E2E_GH_PR")
    if not (slug and number):
        pytest.skip("e2e: set KRAFT_E2E_GH_REPO (owner/name) and KRAFT_E2E_GH_PR")
    owner, _, name = slug.partition("/")

    def api(*args: str) -> object:
        done = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    for comment in api(f"repos/{slug}/issues/{number}/comments?per_page=100"):
        assert {"user", "body", "updated_at"} <= comment.keys()
        assert "login" in comment["user"]
    for review in api(f"repos/{slug}/pulls/{number}/reviews?per_page=100"):
        assert {"user", "state", "commit_id", "body", "submitted_at"} <= review.keys()
    data = api(
        "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"n={number}",
        "-f", f"query={_THREADS_QUERY}",
    )  # fmt: skip
    assert "errors" not in data
    for thread in data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        assert {"id", "isResolved", "comments"} <= thread.keys()
        for note in thread["comments"]["nodes"]:
            assert {"author", "body", "path", "line", "originalLine", "updatedAt"} <= note.keys()


@pytest.mark.e2e("glab")
def test_glab_reviewer_feedback_payload_shapes():
    """The payloads `GlabCli.reviewer_feedback` reads, from a real GitLab: a
    merge request's notes (`system`, `author.username`, `updated_at`) and its
    discussions (`id`, and each note's `resolvable`, `resolved` and `position`).
    Needs a checkout of a GitLab project (`KRAFT_E2E_GLAB_REPO`) and the iid of
    one of its merge requests (`KRAFT_E2E_GLAB_MR`)."""
    repo, iid = os.environ.get("KRAFT_E2E_GLAB_REPO"), os.environ.get("KRAFT_E2E_GLAB_MR")
    if not (repo and iid):
        pytest.skip("e2e: set KRAFT_E2E_GLAB_REPO and KRAFT_E2E_GLAB_MR")

    def api(path: str) -> list:
        done = subprocess.run(
            ["glab", "api", path], cwd=repo, capture_output=True, text=True, check=True
        )
        return json.loads(done.stdout)

    for note in api(f"projects/:id/merge_requests/{iid}/notes?per_page=100"):
        assert {"system", "author", "body", "updated_at"} <= note.keys()
        assert "username" in note["author"]
    for discussion in api(f"projects/:id/merge_requests/{iid}/discussions?per_page=100"):
        assert {"id", "notes"} <= discussion.keys()
        for note in discussion["notes"]:
            assert {"author", "body", "resolvable", "updated_at"} <= note.keys()
