"""GlabCli: GitLab through `glab`. Everything here runs against a stubbed
`glab` (and sometimes `git`) on PATH; nothing in this file touches the
network."""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest
from support.harness import make_repo

from kraft.adapters import forge
from kraft.adapters.forge.mr import MRMeta

# Real output shapes, captured from glab 1.116.0 and gh 2.100.0 against this
# repo on 2026-09-07. Parsers are written against these, not against recollection.
GLAB_MR_VIEW = (
    '{"iid":54,"target_branch":"main","source_branch":"kraft/abc","state":"opened",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)


GLAB_CI_SUCCESS = (
    '[{"id":2826926699,"iid":141,"status":"success","ref":"main",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926699"}]'
)


GLAB_CI_FAILED = (
    '[{"id":2826926700,"iid":142,"status":"failed","ref":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926700"}]'
)


GLAB_CI_RUNNING = (
    '[{"id":2826926701,"iid":143,"status":"running","ref":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926701"}]'
)


# glab includes the pipeline's commit in every row; the fixtures above predate
# this code caring about it. A sha that is not the worktree's HEAD is the
# few-second window after a push in which `glab ci list` still answers with the
# previous commit's pipeline (Kraft-bxj8).
GLAB_CI_SUCCESS_OTHER_SHA = (
    '[{"id":2826926702,"iid":144,"status":"success","ref":"kraft/abc",'
    '"sha":"1111111111111111111111111111111111111111",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926702"}]'
)


# `glab mr view -F json` for a branch that conflicts with main. Captured
# against glab 1.117.0; `detailed_merge_status` is the field that says *why*,
# `merge_status` the older, coarser one.
GLAB_MR_VIEW_CONFLICT = (
    '{"iid":54,"state":"opened","source_branch":"kraft/abc",'
    '"merge_status":"cannot_be_merged","detailed_merge_status":"conflict",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)


# `glab mr view -F json` for a merge request merged out-of-band -- a person
# merged it in the GitLab UI while mr_checks was still polling. Captured
# against glab 1.117.0; a merged MR carries neither merge-status field.
GLAB_MR_VIEW_MERGED = (
    '{"iid":54,"state":"merged","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}'
)


def _stub(tmp_path, monkeypatch, name: str, stdout: str, rc: int = 0):
    """Put a fake forge CLI first on PATH.

    A stub binary rather than a monkeypatched `subprocess.run`: this exercises
    the real argv building and the real decoding path, so a wrong flag or a
    bytes/str slip still fails the test. Each call appends its argv, one
    argument per line, to `<tmp_path>/<name>.argv`.
    """
    p = tmp_path / name
    argv = tmp_path / f"{name}.argv"
    p.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {argv}; done\n'
        f"cat <<'STUBEOF'\n{stdout}\nSTUBEOF\nexit {rc}\n"
    )
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return p


def _argv(tmp_path, name: str) -> list[str]:
    path = tmp_path / f"{name}.argv"
    return path.read_text().splitlines() if path.exists() else []


def _stub_routed(tmp_path, monkeypatch, name: str, routes: dict[str, str], default: str = "{}"):
    """A stub CLI whose stdout depends on its first two arguments.

    `ci_status` reads the merge request *and* the pipeline list in one node
    now, and the two answer with different JSON shapes — an object and an
    array — so one stdout cannot stand in for both. Keys are `"<verb> <sub>"`,
    e.g. `"mr view"`. Argv is recorded exactly as `_stub` records it.
    """
    argv = tmp_path / f"{name}.argv"
    cases = "".join(f"  '{k}') cat <<'STUBEOF'\n{v}\nSTUBEOF\n  ;;\n" for k, v in routes.items())
    p = tmp_path / name
    p.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {argv}; done\n'
        f"case \"$1 $2\" in\n{cases}  *) cat <<'STUBEOF'\n{default}\nSTUBEOF\n  ;;\nesac\nexit 0\n"
    )
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return p


def _stub_glab_mr_view_fails(tmp_path, monkeypatch, mr_list_stdout: str):
    """`glab mr view` exits non-zero -- Kraft-v6ci's reported failure mode --
    and `glab mr list`, `_merge_state`'s fallback, answers `mr_list_stdout`.
    """
    argv = tmp_path / "glab.argv"
    p = tmp_path / "glab"
    p.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {argv}; done\n'
        'case "$1 $2" in\n'
        "  'mr view') echo 'mr view: not found' >&2; exit 1 ;;\n"
        f"  'mr list') cat <<'STUBEOF'\n{mr_list_stdout}\nSTUBEOF\n  ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")


def test_glab_open_mr_parses_the_number_and_url(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    mr = asyncio.run(
        forge.GlabCli().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b")
    )

    assert mr.number == 54
    assert mr.url == "https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"


def test_open_mr_passes_the_authored_metadata(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    meta = MRMeta(labels=("release::minor",), assignees=("omid",), reviewers=("ada", "grace"))
    asyncio.run(
        forge.GlabCli().open_mr(repo=tmp_path, branch="kraft/abc", title="T", body="B", meta=meta)
    )

    argv = _argv(tmp_path, "glab")
    assert argv[argv.index("--label") + 1] == "release::minor"
    assert argv[argv.index("--assignee") + 1] == "omid"
    assert argv[argv.index("--reviewer") + 1] == "ada,grace"


def test_open_mr_omits_the_flags_it_has_no_values_for(tmp_path, monkeypatch):
    # An empty `--label ""` is a real, empty value to glab, not an absence.
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(forge.GlabCli().open_mr(repo=tmp_path, branch="kraft/abc", title="T", body="B"))

    argv = _argv(tmp_path, "glab")
    assert "--label" not in argv and "--assignee" not in argv and "--reviewer" not in argv


def test_glab_ci_status_maps_a_failed_pipeline(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "glab", GLAB_CI_FAILED)

    status = asyncio.run(forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1")))

    assert status.state == "failed"
    assert status.url.endswith("/pipelines/2826926700")
    assert status.jobs, "the review brief needs something to show"


def test_glab_ci_status_maps_a_running_pipeline_to_pending(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "glab", GLAB_CI_RUNNING)
    status = asyncio.run(forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1")))
    assert status.state == "pending"


def test_glab_ci_status_is_pending_when_no_pipeline_exists_yet(tmp_path, monkeypatch):
    """An empty list is a pipeline that has not been created, not a green one."""
    _stub(tmp_path, monkeypatch, "glab", "[]")
    status = asyncio.run(forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1")))
    assert status.state == "pending"


def test_glab_raises_forge_error_when_the_cli_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty: no glab anywhere
    with pytest.raises(forge.ForgeError, match="glab"):
        asyncio.run(forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1")))


def test_glab_raises_forge_error_when_the_cli_fails(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "glab", "boom", rc=1)
    with pytest.raises(forge.ForgeError):
        asyncio.run(forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(1, "http://x/1")))


def test_open_mr_refuses_a_dirty_worktree(tmp_path, monkeypatch):
    """Kraft-brq/Kraft-fhd1. Observed on work item 5163dd1b: the implementation
    node finished with 18 dirty files and 224 uncommitted insertions, and
    open_mr pushed a branch carrying only the spec and plan commits. The node
    must fail and name the files instead of opening a document-only MR."""
    _stub(tmp_path, monkeypatch, "git", " M src/kraft/adapters/forge.py")
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)

    with pytest.raises(forge.ForgeError, match="forge.py"):
        asyncio.run(forge.GlabCli().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert _argv(tmp_path, "glab") == [], "glab ran over an uncommitted worktree"


def _recording_stub(tmp_path, monkeypatch, name: str, stdout: str):
    """A stub that also records the argv it was called with, one call per line."""
    argv_log = tmp_path / f"{name}.argv"
    p = tmp_path / name
    p.write_text(
        f'#!/bin/sh\necho "$@" >> "{argv_log}"\ncat <<\'STUBEOF\'\n{stdout}\nSTUBEOF\nexit 0\n'
    )
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return argv_log


def test_push_sets_the_upstream_on_the_work_item_branch(tmp_path, monkeypatch):
    """The line `open_mr` already ran, now reachable on its own so the nodes
    after it can push too.

    No `origin/kraft/abc` remote-tracking ref exists yet in this stubbed repo
    (the `rev-parse --verify` probe returns nothing), so `_push` has no lease
    to attach and falls back to the plain fast-forward push (Kraft-z6i8)."""
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(forge.GlabCli().push(repo=tmp_path, branch="kraft/abc"))

    assert _argv(tmp_path, "git")[-4:] == ["push", "-u", "origin", "kraft/abc"]


def test_glab_merge_resolves_from_the_branch_when_no_number_is_known(tmp_path, monkeypatch):
    """`run_task` never threads the MR number between nodes — it passes 0 and
    lets the CLI resolve from the checked-out branch. `glab mr merge 0` would
    target a merge request that does not exist."""
    argv_log = _recording_stub(tmp_path, monkeypatch, "glab", "")
    _stub(tmp_path, monkeypatch, "git", "0")

    asyncio.run(
        forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""))
    )

    called = argv_log.read_text().strip()
    assert "mr merge" in called
    assert " 0 " not in f" {called} "


def test_glab_merge_uses_the_number_when_one_is_known(tmp_path, monkeypatch):
    argv_log = _recording_stub(tmp_path, monkeypatch, "glab", "")
    _stub(tmp_path, monkeypatch, "git", "0")
    asyncio.run(
        forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=54, url=""))
    )
    assert "54" in argv_log.read_text()


def test_merge_refuses_a_branch_ahead_of_its_remote(tmp_path, monkeypatch):
    """Kraft-nh5m. Merging a head the forge has never seen merges code CI never
    ran. A stop a human reads beats a green merge of untested code."""
    _stub(tmp_path, monkeypatch, "git", "2")
    _stub(tmp_path, monkeypatch, "glab", "")

    with pytest.raises(forge.ForgeError, match="ahead of origin/kraft/abc by 2"):
        asyncio.run(forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(0, "")))

    assert _argv(tmp_path, "glab") == [], "glab merged a branch the forge has never seen"


def test_merge_proceeds_when_the_branch_is_pushed(tmp_path, monkeypatch):
    """Zero commits ahead is the ordinary path after the sync node pushes."""
    _stub(tmp_path, monkeypatch, "git", "0")
    _stub(tmp_path, monkeypatch, "glab", "")

    asyncio.run(forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(0, "")))

    assert _argv(tmp_path, "glab")[:2] == ["mr", "merge"]


def test_glab_ci_status_asks_for_this_branch_only(tmp_path, monkeypatch):
    """Without --ref, `glab ci list` returns the newest pipeline in the whole
    project. A green pipeline on main would pass the gate for a red branch."""
    argv_log = _recording_stub(tmp_path, monkeypatch, "glab", GLAB_CI_SUCCESS)

    asyncio.run(forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""), branch="kraft/abc"))

    assert "--ref kraft/abc" in argv_log.read_text()


LONG_TITLE = (
    "CLI/UX cleanup batch — Kraft-97e, sws6, 5lpl: worktree-aware probe_repo, "
    "`kraft disconnect` and `kraft retry` verbs, and a good deal more besides."
)


def test_glab_open_mr_titles_the_mr_with_the_work_item_not_the_branch(tmp_path, monkeypatch):
    """`--fill` made glab title the MR from the commits, and with more than one
    commit it falls back to the branch name — always a work item id here, so
    every Kraft MR read as a hex string (Kraft-c09h)."""
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(
        forge.GlabCli().open_mr(
            repo=tmp_path, branch="kraft/abc", title="Teach probe_repo about worktrees", body="why"
        )
    )

    argv = _argv(tmp_path, "glab")
    assert "--fill" not in argv
    assert argv[argv.index("--title") + 1] == "Teach probe_repo about worktrees"
    assert argv[argv.index("--description") + 1] == "why"


def test_glab_update_mr_rewrites_the_description(tmp_path, monkeypatch):
    """`open_mr` runs before verify and mr_checks commit, so the description it
    wrote describes a branch that no longer exists (Kraft-c09h)."""
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)

    asyncio.run(forge.GlabCli().update_mr(repo=tmp_path, branch="kraft/abc", body="fresh"))

    argv = _argv(tmp_path, "glab")
    assert argv[:3] == ["mr", "update", "--description"]
    assert argv[3] == "fresh"


# Captured against glab 1.117.0 and gh 2.100.0 on 2026-09-09.
GLAB_MR_LIST_MERGED = (
    '[{"iid":54,"state":"merged","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
)


GLAB_MR_LIST_OPEN = (
    '[{"iid":62,"state":"opened","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/62"}]'
)


def test_glab_find_mr_reads_the_state_of_an_existing_merge_request(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_LIST_MERGED)

    found = asyncio.run(forge.GlabCli().find_mr(repo=tmp_path, branch="kraft/abc"))

    assert found == forge.MRRef(
        number=54,
        url="https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54",
        state="merged",
    )
    # --all, or a merged MR reads as "no MR at all"; --source-branch, or this
    # answers about whatever the project merged most recently.
    argv = _argv(tmp_path, "glab")
    assert argv[:2] == ["mr", "list"]
    assert "--all" in argv
    assert argv[argv.index("--source-branch") + 1] == "kraft/abc"


def test_find_mr_is_none_when_the_branch_has_no_merge_request(tmp_path, monkeypatch):
    """A list, not a view: an empty list is an unambiguous 'no MR', where
    `view`'s non-zero exit would force the caller to swallow real errors to
    read the same fact."""
    _stub(tmp_path, monkeypatch, "glab", "[]")
    assert asyncio.run(forge.GlabCli().find_mr(repo=tmp_path, branch="kraft/abc")) is None


def test_find_mr_prefers_the_open_merge_request(tmp_path, monkeypatch):
    """One branch can carry a closed MR and an open one. The open one is the
    one every caller means."""
    _stub(
        tmp_path,
        monkeypatch,
        "glab",
        '[{"iid":54,"state":"closed","web_url":"http://x/54"},'
        '{"iid":62,"state":"opened","web_url":"http://x/62"}]',
    )
    found = asyncio.run(forge.GlabCli().find_mr(repo=tmp_path, branch="kraft/abc"))
    assert (found.number, found.state) == (62, "open")


def test_glab_ci_status_ignores_a_pipeline_for_an_older_commit(tmp_path, monkeypatch):
    """Now that ci_poll pushes, the gap between the push and GitLab creating the
    pipeline is on the hot path: for a few seconds `glab ci list -P 1` still
    returns the *previous* commit's pipeline, which may be green. A pipeline
    that is not for this head is not a result."""
    repo = make_repo(tmp_path)
    _stub(tmp_path, monkeypatch, "glab", GLAB_CI_SUCCESS_OTHER_SHA)

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=repo, mr=forge.MR(0, ""), branch="kraft/abc")
    )

    assert status.state == "pending", "a green pipeline for another commit passed the check"
    assert "no pipeline for" in status.jobs[0]


def test_glab_ci_status_accepts_a_pipeline_for_the_current_head(tmp_path, monkeypatch):
    """The other half: the sha check must not reject the ordinary case."""
    repo = make_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _stub(
        tmp_path,
        monkeypatch,
        "glab",
        f'[{{"id":1,"status":"success","ref":"kraft/abc","sha":"{head}","web_url":"http://x/1"}}]',
    )

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=repo, mr=forge.MR(0, ""), branch="kraft/abc")
    )

    assert status.state == "success"


def test_glab_ci_status_reads_the_mr_merge_state(tmp_path, monkeypatch):
    """Kraft-ejj9. A branch with a green pipeline and a real conflict against
    main passed mr_checks as done, walked through human_review, and only met
    the conflict at the merge node — which reported success anyway."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {"mr view": GLAB_MR_VIEW_CONFLICT, "ci list": GLAB_CI_SUCCESS},
    )

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""), branch="kraft/abc")
    )

    assert status.state == "success", "the pipeline really is green — that is the whole bug"
    assert status.mergeable is False
    assert status.merge_detail == "conflict"


@pytest.mark.parametrize(
    "detail",
    ["not_approved", "ci_still_running", "discussions_not_resolved", "draft_status", "checking"],
)
def test_glab_ci_status_leaves_a_state_that_needs_a_person_undecided(tmp_path, monkeypatch, detail):
    """Deliberately the opposite of `_GLAB_STATES`' unknown-is-failure rule:
    mr_checks runs *before* the human_review gate, so these are the ordinary
    states of a healthy merge request here. Failing on them would fail the node
    on every repo with an approval rule."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {
            "mr view": '{"iid":54,"detailed_merge_status":"' + detail + '"}',
            "ci list": GLAB_CI_SUCCESS,
        },
    )

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""), branch="kraft/abc")
    )

    assert status.mergeable is None


def test_glab_ci_status_reads_an_out_of_band_merge_without_erroring(tmp_path, monkeypatch):
    """Kraft-v6ci. A person merges the MR in the GitLab UI while mr_checks is
    still polling; `state: "merged"` carries neither merge-status field, and
    must not fall through `_mergeable` as undecided or raise."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {"mr view": GLAB_MR_VIEW_MERGED, "ci list": GLAB_CI_SUCCESS},
    )

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""), branch="kraft/abc")
    )

    assert (status.state, status.mergeable) == ("success", True)
    assert status.merge_detail == "merged"
    assert status.jobs == ("merge request already merged",)
    argv = _argv(tmp_path, "glab")
    assert "list" not in argv, "a merged MR's pipeline list is not a fact this node needs"


def test_merge_state_falls_back_to_find_mr_when_mr_view_fails(tmp_path, monkeypatch):
    """Kraft-v6ci's reported failure mode: `glab mr view` errors when the
    branch resolves to nothing because the state changed under it. The same
    `--all` lookup merge's own "already merged" shortcut trusts (Kraft-xron)
    still finds it."""
    _stub_glab_mr_view_fails(tmp_path, monkeypatch, GLAB_MR_LIST_MERGED)

    state = asyncio.run(forge.GlabCli()._merge_state(tmp_path, branch="kraft/abc"))

    assert state == "merged"


def test_merge_state_still_raises_when_find_mr_has_no_answer(tmp_path, monkeypatch):
    """The fallback must not turn a genuine outage or auth failure into a
    false "merged": it only fires when `find_mr` itself confirms the merge."""
    _stub_glab_mr_view_fails(tmp_path, monkeypatch, "[]")

    with pytest.raises(forge.ForgeError):
        asyncio.run(forge.GlabCli()._merge_state(tmp_path, branch="kraft/abc"))


# `glab ci get -F json` for the pipeline in GLAB_CI_FAILED, trimmed to the
# fields this code reads. Captured against glab 1.117.0 on 2026-09-09: the
# pipeline object carries its jobs inline, so one call names every job.
GLAB_CI_GET_FAILED = (
    '{"id":2826926700,"status":"failed","jobs":['
    '{"id":16392037101,"name":"lint-and-test","status":"success"},'
    '{"id":16392037104,"name":"release-impact","status":"failed"}]}'
)


# `glab ci get -F json` for a pipeline read by id -- same shape `glab ci
# list` returns per-row, since both proxy GitLab's pipeline object.
GLAB_CI_GET_PINNED_SUCCESS = (
    '{"id":2826926705,"status":"success","sha":"deadbeef",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/pipelines/2826926705"}'
)


def test_glab_ci_status_reads_the_pinned_pipeline_directly(tmp_path, monkeypatch):
    """A `pipeline_id` skips `glab ci list` entirely -- the caller already
    knows which pipeline this head has, no need to re-resolve "latest"."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {"mr view": GLAB_MR_VIEW, "ci get": GLAB_CI_GET_PINNED_SUCCESS},
    )
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(
            repo=tmp_path,
            mr=forge.MR(1, "http://x/1"),
            branch="kraft/abc",
            pipeline_id="2826926705",
        )
    )

    assert status.state == "success"
    assert status.pipeline_ref == "2826926705"
    assert status.sha == "deadbeef"
    assert "ci list" not in "\n".join(_argv(tmp_path, "glab"))


def test_glab_ci_status_falls_back_when_the_pinned_pipeline_is_unreadable(tmp_path, monkeypatch):
    """A deleted/unreadable pinned pipeline must not fail the whole poll --
    fall back to resolving "latest on branch" the way an unpinned poll does."""
    p = tmp_path / "glab"
    argv = tmp_path / "glab.argv"
    p.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {argv}; done\n'
        'case "$1 $2" in\n'
        f"  'mr view') cat <<'STUBEOF'\n{GLAB_MR_VIEW}\nSTUBEOF\n  ;;\n"
        "  'ci get') echo 'ci get: pipeline not found' >&2; exit 1 ;;\n"
        f"  'ci list') cat <<'STUBEOF'\n{GLAB_CI_SUCCESS}\nSTUBEOF\n  ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(
            repo=tmp_path,
            mr=forge.MR(1, "http://x/1"),
            branch="kraft/abc",
            pipeline_id="999999",
        )
    )

    assert status.state == "success"
    assert status.pipeline_ref == "2826926699"


# The tail of `glab ci trace release-impact`, which is the only place the
# reason for MR !89's red pipeline was ever written down.
GLAB_CI_TRACE = (
    '$ python3 dev/next_tag.py "" "$CI_MERGE_REQUEST_LABELS" > /dev/null\n'
    "no release:: label; expected one of ('major', 'minor', 'patch', 'none')\n"
    "This MR needs one of: release::major, release::minor, release::patch, release::none\n"
    "ERROR: Job failed: exit code 1\n"
)


def test_glab_ci_status_names_the_failed_job_and_why(tmp_path, monkeypatch):
    """Kraft-xh0q. 'pipeline 2826926700: failed' tells a reader nothing they can
    act on, and tells a remediator less. The failed job's name and the tail of
    its trace are what say the blocker is a missing label rather than the code."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {
            "mr view": GLAB_MR_VIEW,
            "ci list": GLAB_CI_FAILED,
            "ci get": GLAB_CI_GET_FAILED,
            "ci trace": GLAB_CI_TRACE,
        },
    )
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(number=54, url="u"), branch="")
    )

    assert status.state == "failed"
    joined = "\n".join(status.jobs)
    assert "release-impact" in joined
    assert "no release:: label" in joined, "the trace tail never reached the caller"
    assert "lint-and-test" not in joined, "a passing job is not a diagnosis"
    assert "trace" in _argv(tmp_path, "glab")


def test_glab_ci_status_does_not_chase_a_green_pipeline(tmp_path, monkeypatch):
    """Diagnosis costs two extra round trips per job. A pipeline that passed has
    nothing to diagnose, so it must not pay for them."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {"mr view": GLAB_MR_VIEW, "ci list": GLAB_CI_SUCCESS, "ci get": GLAB_CI_GET_FAILED},
    )
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(number=54, url="u"), branch="")
    )

    assert status.state == "success"
    argv = _argv(tmp_path, "glab")
    assert "trace" not in argv and "get" not in argv


def test_glab_set_labels_labels_the_mr_and_starts_a_new_pipeline(tmp_path, monkeypatch):
    """A label added to an MR does not reach the pipeline that already ran:
    CI_MERGE_REQUEST_LABELS is fixed when the pipeline is created, so retrying
    the job re-reads the old value. Labelling without re-creating looks fixed
    and is still red."""
    _stub_routed(tmp_path, monkeypatch, "glab", {"mr update": "", "api": "{}"})

    asyncio.run(
        forge.GlabCli().set_labels(
            repo=tmp_path, mr=forge.MR(number=54, url="u"), labels=("release::patch",)
        )
    )

    argv = _argv(tmp_path, "glab")
    assert argv[:2] == ["mr", "update"]
    assert "release::patch" in argv
    assert "POST" in argv, "the pipeline was never re-created"
    assert any("merge_requests/54/pipelines" in a for a in argv)


def test_glab_set_labels_re_creates_the_pipeline_for_the_sentinel_number(tmp_path, monkeypatch):
    """`run_task` passes number 0 — "resolve from the checked-out branch" — to
    every forge handler, so 0 is the number the only real caller supplies.
    Skipping the re-create for it would leave that caller with a labelled merge
    request and the same red pipeline."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {"mr update": "", "mr view": GLAB_MR_VIEW, "api": "{}"},
    )

    asyncio.run(
        forge.GlabCli().set_labels(
            repo=tmp_path, mr=forge.MR(number=0, url=""), labels=("release::patch",)
        )
    )

    argv = _argv(tmp_path, "glab")
    assert "POST" in argv, "the pipeline was never re-created"
    assert any("/pipelines" in a for a in argv)
    assert not any("merge_requests/0/" in a for a in argv), (
        "the sentinel 0 was used as a merge request id"
    )


# `glab ci get -F json` for a failed pipeline whose job carries its own
# `failure_reason` (Kraft-ddxn) -- captured shape, glab 1.117.0.
GLAB_CI_GET_FAILED_WITH_REASON = (
    '{"id":2826926700,"status":"failed","jobs":['
    '{"id":16392037104,"name":"test","status":"failed","failure_reason":"script_failure"}]}'
)

# A pipeline with no jobs at all -- a config error stopped anything from being
# created, so `glab ci get` reports the pipeline's own `yaml_errors` instead.
GLAB_CI_GET_ZERO_JOBS_CONFIG_ERROR = (
    '{"id":2826926700,"status":"failed","jobs":[],'
    '"yaml_errors":"jobs config should contain at least one visible job"}'
)

# A pipeline the forge confirms has zero jobs, and no yaml_errors at all --
# distinct from the config-error case above, and from an unreadable read.
GLAB_CI_GET_ZERO_JOBS_NO_ERROR = '{"id":2826926700,"status":"failed","jobs":[]}'


def test_glab_ci_status_carries_the_sha_and_pipeline_ref(tmp_path, monkeypatch):
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {
            "mr view": GLAB_MR_VIEW,
            "ci list": GLAB_CI_FAILED,
            "ci get": GLAB_CI_GET_FAILED_WITH_REASON,
            "ci trace": GLAB_CI_TRACE,
        },
    )
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(number=54, url="u"), branch="")
    )

    assert status.failed_jobs == (forge.FailedJob("test", "failed", "script_failure"),)
    assert status.pipeline_ref == "2826926700"


def test_glab_ci_status_reports_yaml_errors_for_a_zero_job_pipeline(tmp_path, monkeypatch):
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {
            "mr view": GLAB_MR_VIEW,
            "ci list": GLAB_CI_FAILED,
            "ci get": GLAB_CI_GET_ZERO_JOBS_CONFIG_ERROR,
        },
    )
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(number=54, url="u"), branch="")
    )

    assert status.failed_jobs == (forge.FailedJob("(pipeline)", "failed", "config_error"),)


def test_glab_retry_jobs_posts_the_pipeline_retry(tmp_path, monkeypatch):
    _stub_routed(tmp_path, monkeypatch, "glab", {"api": "{}"})

    asyncio.run(
        forge.GlabCli().retry_jobs(
            repo=tmp_path, ci=forge.CIStatus(state="failed", url="u", pipeline_ref="123")
        )
    )

    argv = _argv(tmp_path, "glab")
    assert argv[:3] == ["api", "-X", "POST"]
    assert any("pipelines/123/retry" in a for a in argv)


def test_glab_ci_status_reads_unreadable_detail_as_code_red_not_infra(tmp_path, monkeypatch):
    """The fix for the human review's third point: a genuine code failure must
    never read as infra just because its own detail fetch failed."""
    argv = tmp_path / "glab.argv"
    p = tmp_path / "glab"
    p.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> {argv}; done\n'
        'case "$1 $2" in\n'
        f"  'mr view') cat <<'STUBEOF'\n{GLAB_MR_VIEW}\nSTUBEOF\n  ;;\n"
        f"  'ci list') cat <<'STUBEOF'\n{GLAB_CI_FAILED}\nSTUBEOF\n  ;;\n"
        "  'ci get') echo 'ci get: transient error' >&2; exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(number=54, url="u"), branch="")
    )

    assert status.failed_jobs == forge.glab._UNREADABLE_JOBS
    assert forge.ci.is_infra_red(status) is False


def test_glab_ci_status_confirmed_zero_jobs_reads_as_infra(tmp_path, monkeypatch):
    """The contrasting, correctly-infra case: a successful read confirming no
    job ran at all is still infra-shaped."""
    _stub_routed(
        tmp_path,
        monkeypatch,
        "glab",
        {
            "mr view": GLAB_MR_VIEW,
            "ci list": GLAB_CI_FAILED,
            "ci get": GLAB_CI_GET_ZERO_JOBS_NO_ERROR,
        },
    )
    _stub(tmp_path, monkeypatch, "git", "")

    status = asyncio.run(
        forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(number=54, url="u"), branch="")
    )

    assert status.failed_jobs == ()
    assert forge.ci.is_infra_red(status) is True
