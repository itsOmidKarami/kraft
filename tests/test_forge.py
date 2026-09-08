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


def _forge_session(
    tmp_path, monkeypatch, fake, handler: str, session_id: str, **extra
) -> tuple[str, str]:
    """Run one forge node against `fake`. Returns (returned status, recorded status).

    `extra` goes straight to `run_task`, which is how the poll tests set a
    zero interval and keep themselves off the clock.
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
                backend="fake",
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


def _forge_registry(**poll) -> Registry:
    return Registry(
        hooks={
            "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
            "on.mr.open": {"kind": "forge", "handler": "open_mr", "backend": "fake"},
            "on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": "fake", **poll},
            "on.merge": {"kind": "forge", "handler": "merge", "backend": "fake"},
        }
    )


def _run_back_half(tmp_path, monkeypatch, fake, **poll):
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
                registry=_forge_registry(**poll),
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
