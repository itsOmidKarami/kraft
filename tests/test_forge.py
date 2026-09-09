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

from kraft import db, executor, store
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


def test_resolve_returns_each_named_backend():
    assert isinstance(forge.resolve("glab"), forge.GlabCli)
    assert isinstance(forge.resolve("gh"), forge.GhCli)
    assert isinstance(forge.resolve("fake"), forge.FakeForge)


def test_auto_resolves_to_glab_for_a_gitlab_repo():
    """`auto` is still a *name*: it resolves against the forge recorded for the
    repo in repos.yaml, never against which CLI happens to be installed."""
    assert forge.backend_for("auto", "gitlab") == "glab"


def test_auto_resolves_to_gh_for_a_github_repo():
    assert forge.backend_for("auto", "github") == "gh"


def test_an_explicit_backend_ignores_the_repo_forge():
    """A registry that pins a backend wins over the repo entry — that is the
    escape hatch for a self-hosted host `config._FORGES` cannot recognise."""
    assert forge.backend_for("glab", "github") == "glab"
    assert forge.backend_for("fake", None) == "fake"


def _forge_session(
    tmp_path, monkeypatch, fake, handler: str, session_id: str, backend: str = "fake", **extra
) -> tuple[str, str]:
    """Run one forge node against `fake`. Returns (returned status, recorded status).

    `extra` goes straight to `run_task`, which is how the poll tests set a
    zero interval and keep themselves off the clock. `backend` is explicit
    rather than part of `extra` because `run_task` already takes it: the
    `auto` test passes a name `resolve` never sees.
    """
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
                backend=backend,
                repo=tmp_path,
                branch="kraft/w1",
                title="t",
                **extra,
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


def test_sync_mr_pushes_before_it_rewrites_the_description(tmp_path, monkeypatch):
    """Kraft-nh5m. `open_mr` pushed once and nothing after it ever pushed
    again, so every commit verify, mr_checks and the review brief added was
    local only and died with the worktree -- observed on MR !65 and !66, both
    `[ahead 2]` and both pushed by hand before approval. Push first, so the
    description and the branch the reviewer's forge shows describe the same
    head."""
    order: list[str] = []

    class Recording(forge.FakeForge):
        async def push(self, *, repo, branch):
            order.append("push")
            await super().push(repo=repo, branch=branch)

        async def update_mr(self, *, repo, branch, body):
            order.append("update")
            await super().update_mr(repo=repo, branch=branch, body=body)

    fake = Recording()
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "sync_mr", "s8")

    assert (returned, recorded) == ("done", "done")
    assert order == ["push", "update"], "the description was rewritten over an unpushed head"
    assert fake.pushed == ["kraft/w1"]
    assert fake.bodies["kraft/w1"]


def test_push_sets_the_upstream_on_the_work_item_branch(tmp_path, monkeypatch):
    """The line `open_mr` already ran, now reachable on its own so the nodes
    after it can push too."""
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(forge.GlabCli().push(repo=tmp_path, branch="kraft/abc"))

    assert _argv(tmp_path, "git") == ["push", "-u", "origin", "kraft/abc"]


def _session_log(tmp_path, session_id: str) -> str:
    return (tmp_path / "run" / "logs" / f"{session_id}.log").read_text()


def test_ci_poll_waits_out_a_pending_pipeline(tmp_path, monkeypatch):
    """A pipeline that is merely still running must not stop the node."""
    fake = forge.FakeForge(ci_states=["pending", "pending", "success"])
    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "ci_poll", "s4", poll_interval=0
    )
    assert (returned, recorded) == ("done", "done")
    assert fake.ci_states == ["success"], "both pendings should have been consumed"


def test_ci_poll_times_out_while_still_pending(tmp_path, monkeypatch):
    """A pipeline that never settles fails the node, but says why."""
    fake = forge.FakeForge(ci_states=["pending"])
    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "ci_poll", "s5", poll_timeout=0, poll_interval=0
    )
    assert (returned, recorded) == ("failed", "failed")
    assert "timed out" in _session_log(tmp_path, "s5")


def test_ci_poll_red_pipeline_is_not_reported_as_a_timeout(tmp_path, monkeypatch):
    """The human_review brief has to tell 'finished red' from 'never finished'."""
    fake = forge.FakeForge(ci_states=["failed"])
    _forge_session(tmp_path, monkeypatch, fake, "ci_poll", "s6", poll_interval=0)
    log = _session_log(tmp_path, "s6")
    assert "timed out" not in log
    assert "pipeline failed" in log


def test_auto_with_no_recorded_forge_fails_the_node_rather_than_escaping(tmp_path, monkeypatch):
    """The one that pins `resolve` moving inside the `try`.

    Resolution can fail at runtime now. Outside the `try` that exception escapes
    `run_task` past `finish_session`, leaving a started session row with no
    result and no log — pause, abandon and reattach all key off that row. Inside,
    it is an ordinary failed node with a readable line in the log.
    """
    fake = forge.FakeForge()
    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "open_mr", "s-auto", backend="auto"
    )

    assert returned == "failed"
    assert recorded == "failed", "the session row must be finished, not left running"
    log = _session_log(tmp_path, "s-auto")
    assert "repos.yaml" in log, f"the log must name the fix, got: {log!r}"
    assert not fake.opened, "no merge request may be opened with no forge resolved"


def test_poll_backs_off_and_caps_the_interval(tmp_path, monkeypatch):
    """Without a cap, a long pipeline would stretch the gap between checks
    without bound; without doubling, a 30-minute wait costs 360 CLI calls."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(forge.asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"] * 5 + ["success"])
    ci, timed_out = asyncio.run(
        forge._poll_ci(fake, repo=tmp_path, branch="b", timeout=10_000, interval=5)
    )
    assert (ci.state, timed_out) == ("success", False)
    assert slept == [5, 10, 20, 40, forge._MAX_POLL_INTERVAL]


def test_a_configured_interval_longer_than_the_cap_is_not_clamped_down(tmp_path, monkeypatch):
    """The cap bounds the *growth*, not the human's choice. A registry asking
    for 300s between checks -- a rate-limited forge -- must not silently get
    60s and five times the CLI calls. It holds at 300 rather than doubling
    past it: the configured interval is what was asked for."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(forge.asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"] * 3 + ["success"])
    asyncio.run(forge._poll_ci(fake, repo=tmp_path, branch="b", timeout=10_000, interval=300))
    assert slept == [300, 300, 300]


def test_poll_never_sleeps_past_its_deadline(tmp_path, monkeypatch):
    """An interval longer than what is left would overshoot the timeout and
    report the pipeline late."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(forge.asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"])
    _, timed_out = asyncio.run(
        forge._poll_ci(fake, repo=tmp_path, branch="b", timeout=0.05, interval=100)
    )
    assert timed_out is True
    assert slept and all(s <= 0.05 for s in slept)


def test_a_session_row_exists_for_the_whole_ci_poll_wait(tmp_path, monkeypatch):
    """Kraft-41b: pause and abandon key off `worker_sessions`, and the row used
    to appear only when the node finished -- for the whole wait there was
    nothing to find, so a pause silently no-op'd and the chain walked on into
    merge. The row has to be visible to `running_sessions_for_node` (the same
    lookup `api.pause_work_item` makes) *during* the poll, not just after."""
    seen: list[list[str]] = []
    fake = forge.FakeForge(ci_states=["pending", "success"])
    monkeypatch.setattr(forge, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        db_ = await db.Database.open(rd.db)

        async def fake_sleep(seconds):
            rows = db_.read(lambda c: store.running_sessions_for_node(c, "w1"))
            seen.append([r["id"] for r in rows])

        monkeypatch.setattr(forge.asyncio, "sleep", fake_sleep)
        try:
            await db_.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, current_node_id, created_at, updated_at) "
                    "VALUES ('w1','t','/r','default','{}','active','mr_checks','now','now')"
                )
            )
            await forge.run_task(
                db_,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="mr_checks",
                hook_point="on.ci.poll",
                handler="ci_poll",
                backend="fake",
                repo=tmp_path,
                branch="kraft/w1",
                title="t",
                poll_interval=0,
            )
        finally:
            await db_.close()

    asyncio.run(scenario())
    assert seen, "the poll never slept -- nothing was observed mid-wait"
    assert seen[0] == ["s1"], "pause/abandon would have found nothing during the poll"


def test_a_forge_error_mid_poll_fails_the_node_rather_than_escaping(tmp_path, monkeypatch):
    """The poll widened the window a flaky CLI can raise in from one call to
    the whole timeout; it still has to land as a failed node."""

    class Exploding(forge.FakeForge):
        async def ci_status(self, *, repo, mr, branch=""):
            raise forge.ForgeError("glab fell over")

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, Exploding(), "ci_poll", "s7", poll_interval=0
    )
    assert (returned, recorded) == ("failed", "failed")
    assert "glab fell over" in _session_log(tmp_path, "s7")


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
    _stub(tmp_path, monkeypatch, "git", "0")

    asyncio.run(
        forge.GlabCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""))
    )

    called = argv_log.read_text().strip()
    assert "mr merge" in called
    assert " 0 " not in f" {called} "


def test_gh_merge_resolves_from_the_branch_when_no_number_is_known(tmp_path, monkeypatch):
    argv_log = _recording_stub(tmp_path, monkeypatch, "gh", "")
    _stub(tmp_path, monkeypatch, "git", "0")

    asyncio.run(
        forge.GhCli().merge(repo=tmp_path, branch="kraft/abc", mr=forge.MR(number=0, url=""))
    )

    called = argv_log.read_text().strip()
    assert "pr merge" in called
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


def _forge_registry(backend: str = "fake", **poll) -> Registry:
    return Registry(
        hooks={
            "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
            "on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": backend},
            "on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": backend, **poll},
            "on.merge": {"kind": "forge", "handler": "merge", "backend": backend},
        }
    )


def _run_back_half(tmp_path, monkeypatch, fake, *, backend="fake", launch=None, **poll):
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
                registry=_forge_registry(backend, **poll),
                bd_cwd=str(tracker),
                launch=launch,
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


def test_the_executor_forwards_the_registry_poll_keys(tmp_path, monkeypatch):
    """Task 2's whole payoff is one splat in `executor._dispatch`. Without it
    the binding is ignored, the node waits out the default interval and the
    pipeline goes green -- so this fails loudly rather than silently.

    `poll_timeout: 0` is a single-shot check: the first status is pending, so
    the node times out and the chain must stop before merge.
    """
    fake = forge.FakeForge(ci_states=["pending", "success"])

    status = _run_back_half(tmp_path, monkeypatch, fake, poll_timeout=0)

    assert fake.opened, "the merge request should still have been opened"
    assert fake.merged == [], "a pipeline that never settled reached the merge node"
    assert status == "needs_human"


def test_the_executor_passes_the_repo_forge_to_a_forge_node(tmp_path, monkeypatch):
    """Companion to test_the_executor_forwards_the_registry_poll_keys: one more
    argument in `_dispatch`'s forge branch, and without it every node of a
    `backend: auto` chain fails on a repo whose forge is recorded perfectly well.
    """
    fake = forge.FakeForge(ci_states=["success"])
    launch = executor.LaunchContext(repo_entry={"forge": "gitlab"}, steering_dir=None)

    _run_back_half(tmp_path, monkeypatch, fake, backend="auto", launch=launch)

    assert fake.opened, "open_mr did not run: the repo's forge never reached the node"
    assert fake.merged == [1], "the chain did not reach merge"


def test_a_forge_node_fails_when_auto_has_no_repo_entry(tmp_path, monkeypatch):
    """The other half: `launch=None` is a repo Kraft holds no entry for, and
    `auto` must fail the node rather than guess a CLI."""
    fake = forge.FakeForge(ci_states=["success"])

    status = _run_back_half(tmp_path, monkeypatch, fake, backend="auto")

    assert not fake.opened, "a merge request was opened with no forge resolved"
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


def test_a_long_work_item_title_is_clipped_to_one_headline():
    """A work item title is a paragraph; a forge title is a headline."""
    out = forge.mr_title(LONG_TITLE)

    assert len(out) <= forge.MR_TITLE_MAX
    assert out.startswith("CLI/UX cleanup batch")
    assert out.endswith("…")


def test_a_short_title_is_passed_through_untouched():
    assert forge.mr_title("  Teach probe_repo about worktrees\nand more  ") == (
        "Teach probe_repo about worktrees"
    )


def test_an_empty_title_still_names_something():
    """glab and gh both refuse an empty --title; a stuck node is worse than a
    dull one."""
    assert forge.mr_title("   ") == "Kraft work item"


def test_glab_update_mr_rewrites_the_description(tmp_path, monkeypatch):
    """`open_mr` runs before verify and mr_checks commit, so the description it
    wrote describes a branch that no longer exists (Kraft-c09h)."""
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_VIEW)

    asyncio.run(forge.GlabCli().update_mr(repo=tmp_path, branch="kraft/abc", body="fresh"))

    argv = _argv(tmp_path, "glab")
    assert argv[:3] == ["mr", "update", "--description"]
    assert argv[3] == "fresh"


def test_gh_update_mr_rewrites_the_body(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_VIEW)

    asyncio.run(forge.GhCli().update_mr(repo=tmp_path, branch="kraft/abc", body="fresh"))

    assert _argv(tmp_path, "gh") == ["pr", "edit", "--body", "fresh"]


def test_the_body_lists_the_commits_on_the_branch():
    body = forge.mr_body("af0fb78e", "kraft/af0fb78e", ("spec: batch", "plan: batch", "the work"))

    assert "- spec: batch" in body and "- the work" in body
    assert body.index("- spec: batch") < body.index("- plan: batch"), "oldest first"
    assert "af0fb78e" in body


def test_the_body_survives_a_branch_with_no_commits_yet():
    """`git log` returning nothing must not produce a dangling 'Commits:' header
    — _commits_on swallows a git failure, so this is the shape it hands over."""
    body = forge.mr_body("af0fb78e", "kraft/af0fb78e", ())

    assert "Commits on this branch" not in body
    assert "af0fb78e" in body


def test_commits_on_a_branch_without_origin_main_is_empty_not_an_error(tmp_path):
    """A description is not worth failing a node over."""
    repo = make_repo(tmp_path)

    assert asyncio.run(forge._commits_on(repo, "kraft/nope")) == ()
