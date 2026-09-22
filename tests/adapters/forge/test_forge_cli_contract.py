"""What `GlabCli` and `GhCli` both promise, against a stubbed `glab`/`gh`
(and `git`) on PATH: the argv each operation builds and what it parses back.
Behaviour only one CLI has stays in test_glab.py / test_gh.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kraft.adapters import forge
from kraft.adapters.forge.mr import MRMeta

from . import outputs

GLAB = SimpleNamespace(
    name="glab",
    cls=forge.GlabCli,
    view=outputs.GLAB_MR_VIEW,
    number=54,
    url="https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54",
    body_flag="--description",
    ready_argv=["mr", "update", "54", "--ready"],
    update_argv=["mr", "update", "--description", "fresh"],
    merge_verb="mr merge",
    merged_list=outputs.GLAB_MR_LIST_MERGED,
    queued_list=outputs.GLAB_MR_LIST_AUTO_MERGE,
    find_args={"mr": "list", "--all": None, "--source-branch": "kraft/abc"},
)
GH = SimpleNamespace(
    name="gh",
    cls=forge.GhCli,
    view=outputs.GH_PR_VIEW,
    number=7,
    url="https://github.com/o/r/pull/7",
    body_flag="--body",
    ready_argv=["pr", "ready", "7"],
    update_argv=["pr", "edit", "--body", "fresh"],
    merge_verb="pr merge",
    merged_list=outputs.GH_PR_LIST_MERGED,
    queued_list=outputs.GH_PR_LIST_AUTO_MERGE,
    find_args={"pr": "list", "--state": "all", "--head": "kraft/abc"},
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
    mr = await be.cls().open_mr(repo=tmp_path, branch="kraft/abc", title="Teach it", body="why")

    assert (mr.number, mr.url) == (be.number, be.url)
    argv = cli.argv(be.name)
    assert "--draft" in argv
    assert "--fill" not in argv
    assert argv[argv.index("--title") + 1] == "Teach it"
    assert argv[argv.index(be.body_flag) + 1] == "why"
    assert not {"--label", "--assignee", "--reviewer"} & set(argv)


async def test_open_mr_passes_the_authored_metadata(be, cli, tmp_path):
    meta = MRMeta(labels=("release::minor",), assignees=("omid",), reviewers=("ada", "grace"))

    await be.cls().open_mr(repo=tmp_path, branch="kraft/abc", title="T", body="B", meta=meta)

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
        await backend.cls().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b")

    assert cli.argv(backend.name) == [], "the CLI ran over an uncommitted worktree"


async def test_mark_ready_unsets_draft(be, cli, tmp_path):
    await be.cls().mark_ready(repo=tmp_path, branch="kraft/abc", mr=forge.MR(be.number, "u"))

    assert cli.argv(be.name) == be.ready_argv


async def test_update_mr_rewrites_the_description(be, cli, tmp_path):
    """`open_mr` runs before verify and mr_checks commit, so the description it
    wrote describes a branch that no longer exists (Kraft-c09h)."""
    await be.cls().update_mr(repo=tmp_path, branch="kraft/abc", body="fresh")

    assert cli.argv(be.name) == be.update_argv


async def test_merge_resolves_from_the_branch_when_no_number_is_known(be, cli, tmp_path):
    """`run_task` never threads the MR number between nodes -- it passes 0 and
    lets the CLI resolve from the checked-out branch. `mr merge 0` would target
    a merge request that does not exist."""
    cli.stub(be.name, "")
    cli.stub("git", "0")  # zero commits ahead of origin

    await be.cls().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""))

    (call,) = cli.calls(be.name)
    assert call.startswith(be.merge_verb)
    assert " 0 " not in f" {call} "


async def test_find_mr_reads_the_state_of_an_existing_merge_request(be, cli, tmp_path):
    """Lists every state -- or a merged MR reads as "no MR at all" -- for this
    source branch only -- or it answers about whatever the project merged most
    recently."""
    cli.stub(be.name, be.merged_list)

    found = await be.cls().find_mr(repo=tmp_path, branch="kraft/abc")

    assert found == forge.MRRef(number=be.number, url=be.url, state="merged")
    argv = cli.argv(be.name)
    for flag, value in be.find_args.items():
        assert flag in argv and (value is None or argv[argv.index(flag) + 1] == value), flag


async def test_find_mr_reads_a_merge_the_forge_already_holds_queued(be, cli, tmp_path):
    """Kraft-l98h6: auto-merge (gh) or merge-when-pipeline-succeeds (glab) is
    the forge's own record that its merge was asked for, which a restart of
    Kraft's wait cannot erase."""
    cli.stub(be.name, be.queued_list)

    found = await be.cls().find_mr(repo=tmp_path, branch="kraft/abc")

    assert found == forge.MRRef(number=be.number, url=be.url, state="open", merge_queued=True)


@pytest.mark.parametrize(
    "backend, view, reason, mergeable",
    [
        (GLAB, outputs.GLAB_MR_VIEW_CONFLICT, "conflict", False),
        (GH, outputs.GH_PR_VIEW_CONFLICT, "conflict", False),
        # A conflict is a code problem; a missing approval is a person's to
        # grant on the forge. `merge` has to tell them apart, and mr_checks
        # (pre-gate) must read the approval as undecided, not fail on it.
        (GLAB, outputs.GLAB_MR_VIEW_NOT_APPROVED, "not_approved", None),
        (GH, outputs.GH_PR_VIEW_NEEDS_APPROVAL, "not_approved", None),
    ],
    ids=["glab-conflict", "gh-conflict", "glab-missing-approval", "gh-missing-approval"],
)
async def test_ci_status_names_the_block_reason(cli, tmp_path, backend, view, reason, mergeable):
    cli.stub(
        backend.name, routes={"mr view": view, "ci list": outputs.GLAB_CI_SUCCESS, "pr view": view}
    )

    status = await backend.cls().ci_status(repo=tmp_path, mr=forge.MR(0, ""), branch="kraft/abc")

    assert (status.state, status.block_reason, status.mergeable) == ("success", reason, mergeable)
