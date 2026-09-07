"""The forge adapter: opening a merge request, reading CI, merging.

The whole back half of `default.yaml` was `builtin:noop` before this — a chain
ended at a local diff (Kraft-33j). Everything here runs against `FakeForge` or a
stubbed CLI on PATH; nothing in this file touches the network.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from support.harness import isolated_bd, make_repo

from kraft import db, executor
from kraft.adapters import forge
from kraft.paths import RunDirs
from kraft.templates import Registry, Template


def test_fake_forge_round_trips_an_mr(tmp_path):
    f = forge.FakeForge(ci_states=["pending", "success"])

    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert mr.number == 1
    assert mr.url.endswith("/1")
    assert asyncio.run(f.ci_status(repo=tmp_path, mr=mr)).state == "pending"
    assert asyncio.run(f.ci_status(repo=tmp_path, mr=mr)).state == "success"


def test_fake_forge_refuses_to_merge_an_unopened_mr(tmp_path):
    f = forge.FakeForge(ci_states=["success"])
    with pytest.raises(forge.ForgeError):
        asyncio.run(f.merge(repo=tmp_path, mr=forge.MR(number=99, url="http://x/99")))


def test_resolve_rejects_an_unknown_backend():
    """Named, never probed: an unusable backend must fail loudly rather than
    silently taking a different path than the one configured."""
    with pytest.raises(forge.ForgeError, match="bitbucket"):
        forge.resolve("bitbucket")


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
GH_PR_VIEW = (
    '{"number":7,"url":"https://github.com/o/r/pull/7",'
    '"statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"},'
    '{"name":"lint","conclusion":"FAILURE"}]}'
)


def _stub(tmp_path, monkeypatch, name: str, stdout: str, rc: int = 0):
    """Put a fake forge CLI first on PATH.

    A stub binary rather than a monkeypatched `subprocess.run`: this exercises
    the real argv building and the real decoding path, so a wrong flag or a
    bytes/str slip still fails the test.
    """
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\ncat <<'STUBEOF'\n{stdout}\nSTUBEOF\nexit {rc}\n")
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return p


def test_glab_open_mr_parses_the_number_and_url(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    mr = asyncio.run(
        forge.GlabCli().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b")
    )

    assert mr.number == 54
    assert mr.url == "https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"


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


def test_gh_open_mr_parses_the_number_and_url(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)
    _stub(tmp_path, monkeypatch, "git", "")

    mr = asyncio.run(forge.GhCli().open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert mr.number == 7
    assert mr.url == "https://github.com/o/r/pull/7"


def test_gh_ci_status_fails_when_any_check_failed(tmp_path, monkeypatch):
    """One red check is a red rollup: the fixture is green build, red lint."""
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)

    status = asyncio.run(forge.GhCli().ci_status(repo=tmp_path, mr=forge.MR(7, "http://x/7")))

    assert status.state == "failed"
    assert any("lint" in j for j in status.jobs)


def test_resolve_returns_each_named_backend():
    assert isinstance(forge.resolve("glab"), forge.GlabCli)
    assert isinstance(forge.resolve("gh"), forge.GhCli)
    assert isinstance(forge.resolve("fake"), forge.FakeForge)


def _forge_session(tmp_path, monkeypatch, fake, handler: str, session_id: str) -> tuple[str, str]:
    """Run one forge node against `fake`. Returns (returned status, recorded status)."""
    monkeypatch.setattr(forge, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t','/r','default','{}','active','now','now')"
                )
            )
            returned = await forge.run_task(
                database,
                rd,
                session_id=session_id,
                work_item_id="w1",
                node_id="mr_checks",
                hook_point="on.ci.poll",
                handler=handler,
                backend="fake",
                repo=tmp_path,
                branch="kraft/w1",
                title="t",
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions WHERE id = ?", (session_id,)
                ).fetchone()
            )
            return returned, row["status"]
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_ci_poll_records_failed_when_the_pipeline_is_red(tmp_path, monkeypatch):
    """Red CI must not read as a done node — the next node is merge."""
    fake = forge.FakeForge(ci_states=["failed"])
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "ci_poll", "s1")
    assert returned == "failed"
    assert recorded == "failed"


def test_ci_poll_records_done_when_the_pipeline_is_green(tmp_path, monkeypatch):
    fake = forge.FakeForge(ci_states=["success"])
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "ci_poll", "s2")
    assert returned == "done"
    assert recorded == "done"


def test_a_forge_error_fails_the_node_rather_than_escaping(tmp_path, monkeypatch):
    """merge on an unopened MR raises ForgeError; the node has to record that
    as a failure, not propagate a traceback out of the executor."""
    fake = forge.FakeForge(ci_states=["success"])
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "s3")
    assert returned == "failed"
    assert recorded == "failed"


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


def test_glab_merge_resolves_from_the_branch_when_no_number_is_known(tmp_path, monkeypatch):
    """`run_task` never threads the MR number between nodes — it passes 0 and
    lets the CLI resolve from the checked-out branch. `glab mr merge 0` would
    target a merge request that does not exist."""
    argv_log = _recording_stub(tmp_path, monkeypatch, "glab", "")

    asyncio.run(forge.GlabCli().merge(repo=tmp_path, mr=forge.MR(number=0, url="")))

    called = argv_log.read_text().strip()
    assert "mr merge" in called
    assert " 0 " not in f" {called} "


def test_gh_merge_resolves_from_the_branch_when_no_number_is_known(tmp_path, monkeypatch):
    argv_log = _recording_stub(tmp_path, monkeypatch, "gh", "")

    asyncio.run(forge.GhCli().merge(repo=tmp_path, mr=forge.MR(number=0, url="")))

    called = argv_log.read_text().strip()
    assert "pr merge" in called
    assert " 0 " not in f" {called} "


def test_glab_merge_uses_the_number_when_one_is_known(tmp_path, monkeypatch):
    argv_log = _recording_stub(tmp_path, monkeypatch, "glab", "")
    asyncio.run(forge.GlabCli().merge(repo=tmp_path, mr=forge.MR(number=54, url="")))
    assert "54" in argv_log.read_text()


def _back_half_template() -> Template:
    """env_setup, then the three forge nodes in chain order. The front half
    (spec, plan, implementation) is what the e2e suite covers with a real agent;
    what was noop until now is everything after verify."""
    return Template(
        id="forge-back-half",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {"id": "open_mr", "tasks": ["on.mr.open"], "gate_after": None, "fix_loop": None},
            {"id": "mr_checks", "tasks": ["on.ci.poll"], "gate_after": None, "fix_loop": None},
            {"id": "merge", "tasks": ["on.merge"], "gate_after": None, "fix_loop": None},
        ],
    )


def _forge_registry() -> Registry:
    return Registry(
        hooks={
            "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
            "on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": "fake"},
            "on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": "fake"},
            "on.merge": {"kind": "forge", "handler": "merge", "backend": "fake"},
        }
    )


def _run_back_half(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="forge back half",
                repo=str(repo),
                template=_back_half_template(),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=_forge_registry(),
                bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            return row["status"]
        finally:
            await database.close()

    return asyncio.run(scenario())


def test_back_half_runs_through_to_merge_with_a_green_pipeline(tmp_path, monkeypatch):
    """open_mr -> ci_poll -> merge. This whole stretch was builtin:noop."""
    fake = forge.FakeForge(ci_states=["success"])

    _run_back_half(tmp_path, monkeypatch, fake)

    assert fake.opened, "no merge request was opened"
    assert fake.merged == [1], "the merge node did not run"


def test_red_pipeline_stops_before_the_merge_node(tmp_path, monkeypatch):
    """The one that matters: a failed pipeline must never reach merge."""
    fake = forge.FakeForge(ci_states=["failed"])

    status = _run_back_half(tmp_path, monkeypatch, fake)

    assert fake.opened, "the merge request should still have been opened"
    assert fake.merged == [], "a red pipeline reached the merge node"
    assert status == "needs_human"


def test_forge_nodes_run_in_the_worktree_not_the_repo(tmp_path, monkeypatch):
    """`glab mr create --fill` uses the *current branch* of its cwd. Run from
    the main repo, that is whatever the human has checked out — not
    kraft/<id> — so the MR would be opened from the wrong branch."""
    seen: list = []

    class RecordingForge(forge.FakeForge):
        async def open_mr(self, *, repo, branch, title, body):
            seen.append(repo)
            return await super().open_mr(repo=repo, branch=branch, title=title, body=body)

    fake = RecordingForge(ci_states=["success"])
    _run_back_half(tmp_path, monkeypatch, fake)

    assert seen, "open_mr never ran"
    assert seen[0].name != "", seen
    assert "worktrees" in str(seen[0]), f"forge node ran in {seen[0]}, not the worktree"


def test_glab_ci_status_asks_for_this_branch_only(tmp_path, monkeypatch):
    """Without --ref, `glab ci list` returns the newest pipeline in the whole
    project. A green pipeline on main would pass the gate for a red branch."""
    argv_log = _recording_stub(tmp_path, monkeypatch, "glab", GLAB_CI_SUCCESS)

    asyncio.run(forge.GlabCli().ci_status(repo=tmp_path, mr=forge.MR(0, ""), branch="kraft/abc"))

    assert "--ref kraft/abc" in argv_log.read_text()
