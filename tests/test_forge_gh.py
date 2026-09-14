"""GhCli: GitHub through `gh`. Everything here runs against a stubbed `gh`
(and sometimes `git`) on PATH; nothing in this file touches the network."""

from __future__ import annotations

import asyncio
import os

import pytest

from kraft.adapters import forge
from kraft.adapters.forge.mr import MRMeta

GH_PR_VIEW = (
    '{"number":7,"url":"https://github.com/o/r/pull/7",'
    '"statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"},'
    '{"name":"lint","conclusion":"FAILURE"}]}'
)


GH_PR_VIEW_CONFLICT = (
    '{"number":7,"url":"https://github.com/o/r/pull/7","mergeable":"CONFLICTING",'
    '"mergeStateStatus":"DIRTY","statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"}]}'
)


GH_PR_VIEW_NEEDS_APPROVAL = (
    '{"number":7,"url":"https://github.com/o/r/pull/7","mergeable":"UNKNOWN",'
    '"mergeStateStatus":"BLOCKED","reviewDecision":"REVIEW_REQUIRED",'
    '"statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"}]}'
)


def test_gh_open_mr_parses_the_number_and_url(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    mr = asyncio.run(forge.GhCli().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert mr.number == 7
    assert mr.url == "https://github.com/o/r/pull/7"


def test_gh_open_mr_creates_as_draft(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(forge.GhCli().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert "--draft" in _argv(tmp_path, "gh")


def test_gh_mark_ready_unsets_draft(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", "")

    asyncio.run(
        forge.GhCli().mark_ready(repo=tmp_path, branch="kraft/abc", mr=forge.MR(7, "http://x/7"))
    )

    assert _argv(tmp_path, "gh") == ["pr", "ready", "7"]


def test_open_mr_passes_the_authored_metadata(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    meta = MRMeta(labels=("release::minor",), assignees=("omid",), reviewers=("ada", "grace"))
    asyncio.run(
        forge.GhCli().open_mr(repo=tmp_path, branch="kraft/abc", title="T", body="B", meta=meta)
    )

    argv = _argv(tmp_path, "gh")
    assert argv[argv.index("--label") + 1] == "release::minor"
    assert argv[argv.index("--assignee") + 1] == "omid"
    assert argv[argv.index("--reviewer") + 1] == "ada,grace"


def test_open_mr_omits_the_flags_it_has_no_values_for(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(forge.GhCli().open_mr(repo=tmp_path, branch="kraft/abc", title="T", body="B"))

    argv = _argv(tmp_path, "gh")
    assert "--label" not in argv and "--assignee" not in argv and "--reviewer" not in argv


def test_open_mr_refuses_an_untracked_only_worktree(tmp_path, monkeypatch):
    """Untracked files are deliberately not excused: a new source or test file
    the agent never `git add`ed is exactly what went missing on 5163dd1b."""
    _stub(tmp_path, monkeypatch, "git", "?? tests/test_new_thing.py")
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)

    with pytest.raises(forge.ForgeError, match="test_new_thing.py"):
        asyncio.run(forge.GhCli().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert _argv(tmp_path, "gh") == []


def test_gh_ci_status_fails_when_any_check_failed(tmp_path, monkeypatch):
    """One red check is a red rollup: the fixture is green build, red lint."""
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)

    status = asyncio.run(forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(7, "http://x/7")))

    assert status.state == "failed"
    assert any("lint" in j for j in status.jobs)


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


def test_gh_merge_resolves_from_the_branch_when_no_number_is_known(tmp_path, monkeypatch):
    argv_log = _recording_stub(tmp_path, monkeypatch, "gh", "")
    _stub(tmp_path, monkeypatch, "git", "0")

    asyncio.run(
        forge.GhCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""))
    )

    called = argv_log.read_text().strip()
    assert "pr merge" in called
    assert " 0 " not in f" {called} "


def test_gh_open_mr_titles_the_pr_with_the_work_item_not_the_branch(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(
        forge.GhCli().open_mr(
            repo=tmp_path, branch="kraft/abc", title="Teach probe_repo about worktrees", body="why"
        )
    )

    argv = _argv(tmp_path, "gh")
    assert "--fill" not in argv
    assert argv[argv.index("--title") + 1] == "Teach probe_repo about worktrees"
    assert argv[argv.index("--body") + 1] == "why"


def test_gh_update_mr_rewrites_the_body(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)

    asyncio.run(forge.GhCli().update_mr(repo=tmp_path, branch="kraft/abc", body="fresh"))

    assert _argv(tmp_path, "gh") == ["pr", "edit", "--body", "fresh"]


GH_PR_LIST_MERGED = '[{"number":7,"url":"https://github.com/o/r/pull/7","state":"MERGED"}]'


def test_gh_find_mr_reads_the_state_of_an_existing_pull_request(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_LIST_MERGED)

    found = asyncio.run(forge.GhCli().find_mr(repo=tmp_path, branch="kraft/abc"))

    assert found == forge.MRRef(number=7, url="https://github.com/o/r/pull/7", state="merged")
    argv = _argv(tmp_path, "gh")
    assert argv[argv.index("--head") + 1] == "kraft/abc"
    assert argv[argv.index("--state") + 1] == "all"


def test_gh_ci_status_reads_mergeable_from_the_same_pr_view(tmp_path, monkeypatch):
    """No extra process on GitHub: the fields go on the `gh pr view` call the
    node already makes."""
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW_CONFLICT)

    status = asyncio.run(forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, "")))

    argv = _argv(tmp_path, "gh")
    fields = argv[argv.index("--json") + 1]
    assert "mergeable" in fields and "mergeStateStatus" in fields
    assert argv.count("--json") == 1, "a second round trip for a fact one call already carries"
    assert (status.state, status.mergeable) == ("success", False)
    assert "CONFLICTING" in status.merge_detail


def test_gh_ci_status_names_a_missing_approval_distinctly_from_blocked(tmp_path, monkeypatch):
    """`mergeStateStatus: BLOCKED` alone doesn't say why -- `reviewDecision`
    is the field that tells a pending-approval BLOCKED apart from any
    other kind, so it has to ride along on the same `gh pr view` call."""
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW_NEEDS_APPROVAL)

    status = asyncio.run(forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, "")))

    argv = _argv(tmp_path, "gh")
    fields = argv[argv.index("--json") + 1]
    assert "reviewDecision" in fields
    assert status.block_reason == "not_approved"


def test_gh_ci_status_names_a_conflict_as_a_conflict(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW_CONFLICT)

    status = asyncio.run(forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(0, "")))

    assert status.block_reason == "conflict"


def test_gh_set_labels_edits_the_pull_request(tmp_path, monkeypatch):
    """GitHub re-evaluates `pull_request: types: [labeled]` itself, so there is
    no pipeline to re-create here — only the label to add."""
    _stub(tmp_path, monkeypatch, "gh", "")

    asyncio.run(
        forge.GhCli().set_labels(
            repo=tmp_path, mr=forge.MR(number=7, url="u"), labels=("release::patch", "bug")
        )
    )

    argv = _argv(tmp_path, "gh")
    assert argv[:2] == ["pr", "edit"]
    assert "--add-label" in argv
    assert "release::patch,bug" in argv


GH_PR_VIEW_TIMED_OUT = (
    '{"number":7,"url":"https://github.com/o/r/pull/7","headRefOid":"abc123",'
    '"statusCheckRollup":[{"name":"build","conclusion":"TIMED_OUT",'
    '"detailsUrl":"https://github.com/o/r/actions/runs/123456/job/9"}]}'
)


def test_gh_ci_status_maps_timed_out_to_the_infra_reason(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW_TIMED_OUT)

    status = asyncio.run(forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(7, "http://x/7")))

    assert status.sha == "abc123"
    assert status.failed_jobs[0].failure_reason == "job_execution_timeout"


GH_RUN_LIST = (
    '[{"status":"completed","conclusion":"failure","headSha":"deadbeef",'
    '"url":"https://github.com/o/r/actions/runs/123456","name":"build"}]'
)


def test_gh_branch_ci_status_reads_runs_not_a_pull_request(tmp_path, monkeypatch):
    """merge_watch's whole reason to call this instead of `ci_status`: the
    checked-out branch has no open PR left post-merge, so this must never
    shell out to `gh pr view` (code-review)."""
    argv_log = _recording_stub(tmp_path, monkeypatch, "gh", GH_RUN_LIST)

    status = asyncio.run(
        forge.GhCli().branch_ci_status(repo=tmp_path, branch="main", head_sha="deadbeef")
    )

    assert status.state == "failed"
    assert status.sha == "deadbeef"
    assert status.failed_jobs[0].failure_reason == "script_failure"
    calls = argv_log.read_text().splitlines()
    assert len(calls) == 1
    assert calls == ["run list --branch main -L 20 --json status,conclusion,headSha,url,name"]


def test_gh_branch_ci_status_waits_for_a_run_matching_the_given_head(tmp_path, monkeypatch):
    """A run list that has not caught up to `head_sha` yet is a wait, not a
    (wrong-commit) result -- same shape as glab's own sha guard."""
    _stub(tmp_path, monkeypatch, "gh", GH_RUN_LIST)

    status = asyncio.run(
        forge.GhCli().branch_ci_status(repo=tmp_path, branch="main", head_sha="other-sha")
    )

    assert status.state == "pending"


def test_gh_retry_jobs_reruns_the_actions_run_behind_the_failed_check(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", "")

    ci = forge.CIStatus(
        state="failed",
        url="u",
        failed_jobs=(
            forge.FailedJob(
                "build",
                "failed",
                "job_execution_timeout",
                "https://github.com/o/r/actions/runs/123456/job/9",
            ),
        ),
    )
    asyncio.run(forge.GhCli().retry_jobs(repo=tmp_path, ci=ci))

    argv = _argv(tmp_path, "gh")
    assert argv == ["run", "rerun", "123456", "--failed"]
