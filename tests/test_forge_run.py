"""`run_task`/`_run_one`: dispatching one forge node -- open_mr, ci_poll,
sync_mr, merge -- against `FakeForge` and, for the back-half chain tests,
through the executor. Also `resolve`/`backend_for`, and the pure-formatting
bits (`mr_title`, `mr_body`) that have no CLI of their own to sit next to."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from support.harness import isolated_bd, make_repo, make_repo_with_submodule

from kraft import builtins as _builtins
from kraft import db, events, executor, policy, store
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
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

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
    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(forge.FailedJob("test", "failed", "script_failure"),)],
    )
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


def _session_log(tmp_path, session_id: str) -> str:
    return (tmp_path / "run" / "logs" / f"{session_id}.log").read_text()


def _events(tmp_path) -> list[tuple[str, dict]]:
    """(type, payload) for every event the run wrote, in order."""
    conn = sqlite3.connect(str(RunDirs(tmp_path / "run").db))
    try:
        rows = conn.execute("SELECT type, payload FROM events ORDER BY seq").fetchall()
    finally:
        conn.close()
    return [(t, json.loads(p)) for t, p in rows]


def test_open_mr_records_an_mr_opened_event(tmp_path, monkeypatch):
    """Kraft-d2sq. The URL went into the session log text and nowhere else:
    not on the work item row, not in an event, not in the session result. Past
    open_mr there was no way to reach the merge request but to open the forge
    and search for the branch."""
    fake = forge.FakeForge()
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "open_mr", "s-mr1")

    assert (returned, recorded) == ("done", "done")
    assert [p for t, p in _events(tmp_path) if t == "mr_opened"] == [
        {"number": 1, "url": "http://fake.forge/1"}
    ]


def test_reused_mr_records_an_mr_opened_event(tmp_path, monkeypatch):
    """The reuse path too: a rejected review walks the item back through
    open_mr, and the item must still carry a current record of its MR."""
    fake = forge.FakeForge(opened={7: "kraft/w1"})
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "open_mr", "s-mr2")

    assert (returned, recorded) == ("done", "done")
    assert [p for t, p in _events(tmp_path) if t == "mr_opened"] == [
        {"number": 7, "url": "http://fake.forge/7"}
    ]


def test_ci_poll_reports_waiting_on_a_pending_pipeline(tmp_path, monkeypatch):
    """One check, then hand the wait back to the scheduler -- no 30-minute
    coroutine (Kraft-ru98). The old behaviour slept here instead."""
    fake = forge.FakeForge(ci_states=["pending"])
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "ci_poll", "s4")
    assert (returned, recorded) == ("waiting", "waiting")
    assert "pipeline pending" in _session_log(tmp_path, "s4")


def test_ci_poll_still_resolves_a_settled_pipeline(tmp_path, monkeypatch):
    """The path that already worked must not change: success -> done,
    failure -> failed, unmergeable -> conflict (once a re-fetch confirms it,
    Kraft-bjjm)."""
    fake = forge.FakeForge(ci_states=["success"])
    returned, recorded = _forge_session(tmp_path / "a", monkeypatch, fake, "ci_poll", "s5")
    assert (returned, recorded) == ("done", "done")

    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(forge.FailedJob("test", "failed", "script_failure"),)],
    )
    returned, recorded = _forge_session(tmp_path / "b", monkeypatch, fake, "ci_poll", "s5b")
    assert (returned, recorded) == ("failed", "failed")

    fake = forge.FakeForge(ci_states=["success"], mergeable=False)
    returned, recorded = _forge_session(tmp_path / "c", monkeypatch, fake, "ci_poll", "s5c")
    assert (returned, recorded) == ("conflict", "conflict"), (
        "Kraft-ejj9: green and unmergeable, confirmed by a re-fetch"
    )


def test_ci_poll_red_pipeline_is_not_reported_as_a_timeout(tmp_path, monkeypatch):
    """The human_review brief has to tell 'finished red' from 'never finished'."""
    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(forge.FailedJob("test", "failed", "script_failure"),)],
    )
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
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
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
    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(forge.FailedJob("test", "failed", "script_failure"),)],
    )

    status = _run_back_half(tmp_path, monkeypatch, fake)

    assert fake.opened, "the merge request should still have been opened"
    assert fake.merged == [], "a red pipeline reached the merge node"
    assert status == "needs_human"


def test_the_executor_forwards_the_registry_poll_keys(tmp_path, monkeypatch):
    """Task 2's whole payoff is one splat in `kraft.executor.dispatch.dispatch_node`. `ci_poll`
    itself no longer reads `poll_timeout` -- a pending pipeline is always a
    single check that hands the wait back to the scheduler (Kraft-ru98) -- but
    the binding still has to reach `run_task` at all, which this pins."""
    fake = forge.FakeForge(ci_states=["pending", "success"])

    status = _run_back_half(tmp_path, monkeypatch, fake, poll_timeout=0)

    assert fake.opened, "the merge request should still have been opened"
    assert fake.merged == [], "a pipeline still pending must not reach the merge node"
    assert status == "waiting"


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


LONG_TITLE = (
    "CLI/UX cleanup batch — Kraft-97e, sws6, 5lpl: worktree-aware probe_repo, "
    "`kraft disconnect` and `kraft retry` verbs, and a good deal more besides."
)


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


def test_fake_forge_find_mr_tracks_its_own_opened_and_merged_lists(tmp_path):
    f = forge.FakeForge()
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/w1")) is None

    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/w1")).state == "open"
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/other")) is None

    asyncio.run(f.merge(repo=tmp_path, branch="kraft/w1", mr=mr))
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/w1")).state == "merged"


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


# Captured against glab 1.117.0 and gh 2.100.0 on 2026-09-09.
GLAB_MR_LIST_MERGED = (
    '[{"iid":54,"state":"merged","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
)


def test_merge_treats_an_already_merged_mr_as_done(tmp_path, monkeypatch):
    """Kraft-xron, from work item af0fb78e: MR !62 was auto-merged when its
    pipeline went green, Kraft's merge node ran eight minutes later and
    recorded `No open merge request available`. The node's stated end state --
    this branch is in main -- was already true."""
    _stub(tmp_path, monkeypatch, "glab", GLAB_MR_LIST_MERGED)

    returned, recorded = _forge_session(tmp_path, monkeypatch, forge.GlabCli(), "merge", "x1")

    assert (returned, recorded) == ("done", "done")
    assert "already merged (!54)" in _session_log(tmp_path, "x1")
    assert "merge" not in _argv(tmp_path, "glab"), "it tried to merge an already-merged MR"


GH_PR_LIST_MERGED = '[{"number":7,"url":"https://github.com/o/r/pull/7","state":"MERGED"}]'


def test_merge_treats_an_already_merged_pr_as_done_on_gh(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_LIST_MERGED)

    returned, recorded = _forge_session(tmp_path, monkeypatch, forge.GhCli(), "merge", "x2")

    assert (returned, recorded) == ("done", "done")
    assert "already merged (!7)" in _session_log(tmp_path, "x2")
    assert "merge" not in _argv(tmp_path, "gh")


def test_merge_still_fails_on_a_closed_mr(tmp_path, monkeypatch):
    """A closed merge request is not a merged one, and nothing about it is
    resolved."""
    _stub(
        tmp_path,
        monkeypatch,
        "glab",
        '[{"iid":54,"state":"closed","web_url":"http://x/54"}]',
    )
    returned, recorded = _forge_session(tmp_path, monkeypatch, forge.GlabCli(), "merge", "x3")

    assert (returned, recorded) == ("failed", "failed")
    assert "closed" in _session_log(tmp_path, "x3")


def test_merge_still_fails_when_the_forge_refuses(tmp_path, monkeypatch):
    """Conflicts and unmet approval rules must still stop the chain: the fix
    must not swallow a real refusal."""

    class Refusing(forge.FakeForge):
        async def merge(self, *, repo, branch="", mr):
            raise forge.ForgeError("merge blocked: 1 approval required")

    fake = Refusing()
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "x4")

    assert (returned, recorded) == ("failed", "failed")
    assert "1 approval required" in _session_log(tmp_path, "x4")


def test_merge_waits_out_a_pipeline_the_review_brief_restarted(tmp_path, monkeypatch):
    """Kraft-266b. human_review commits and pushes a review brief onto the MR
    branch *after* mr_checks already waited its pipeline green, which starts a
    fresh one; `glab mr merge --yes` against a still-running pipeline exits 0
    but merges nothing (Kraft-79x3). merge must re-poll CI itself rather than
    trust mr_checks' now-stale answer."""
    fake = forge.FakeForge(ci_states=["pending", "success"])
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "m4", poll_interval=0)

    assert (returned, recorded) == ("done", "done")
    assert fake.merged == [1]


def test_merge_fails_when_the_re_armed_pipeline_goes_red(tmp_path, monkeypatch):
    """The other half: a review-brief pipeline that comes back red must stop
    the node before `glab mr merge` ever runs, same as mr_checks going red."""
    fake = forge.FakeForge(ci_states=["pending", "failed"])
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "m5", poll_interval=0)

    assert (returned, recorded) == ("failed", "failed")
    assert fake.merged == [], "a red pipeline reached glab mr merge"
    assert "pipeline failed" in _session_log(tmp_path, "m5")


def test_open_mr_reuses_an_open_mr_for_the_branch(tmp_path, monkeypatch):
    """Kraft-ko7j's re-entry walks back through this node, and a retry of an
    open_mr that crashed after the create hits the same wall: `mr create` for a
    branch that already has one is an error on both CLIs."""
    fake = forge.FakeForge()
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "open_mr", "x5")

    assert (returned, recorded) == ("done", "done")
    assert list(fake.opened) == [1], "it opened a second merge request for one branch"
    assert fake.pushed == ["kraft/w1"]
    assert fake.bodies["kraft/w1"], "the description was not rewritten from the branch head"
    assert "reusing !1" in _session_log(tmp_path, "x5")


def test_open_mr_still_creates_one_when_the_branch_has_none(tmp_path, monkeypatch):
    fake = forge.FakeForge()

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "open_mr", "x6")

    assert (returned, recorded) == ("done", "done")
    assert fake.opened == {1: "kraft/w1"}
    assert "opened http://fake.forge/1" in _session_log(tmp_path, "x6")


def test_ci_poll_pushes_before_it_polls(tmp_path, monkeypatch):
    """Kraft-bxj8. The worker commits in the worktree and is told not to push
    (adapters/agent.py:45); only open_mr and sync_mr ever pushed, and sync_mr
    runs *after* human review. So a commit made after open_mr stayed local and
    ci_poll polled the previous head's pipeline — the same pipeline id on every
    retry, forever. Work items b63d95be and 1c039982 both hit this in one
    session and both needed a manual `git push`."""
    order: list[str] = []

    class Recording(forge.FakeForge):
        async def push(self, *, repo, branch):
            order.append("push")
            await super().push(repo=repo, branch=branch)

        async def ci_status(self, *, repo, mr, branch=""):
            order.append("ci")
            return await super().ci_status(repo=repo, mr=mr, branch=branch)

    fake = Recording(ci_states=["success"])
    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "ci_poll", "b1", poll_interval=0
    )

    assert (returned, recorded) == ("done", "done")
    assert order[:2] == ["push", "ci"], "the pipeline was polled over an unpushed head"
    assert fake.pushed == ["kraft/w1"]


def test_merge_pushes_before_it_merges(tmp_path, monkeypatch):
    """The mirror symptom: `_assert_pushed` correctly refuses a head the remote
    has never seen, but it failed the node three times rather than pushing, and
    a retry of `merge` alone never re-runs `mr_sync`, so it could never clear
    itself."""
    order: list[str] = []

    class Recording(forge.FakeForge):
        async def push(self, *, repo, branch):
            order.append("push")
            await super().push(repo=repo, branch=branch)

        async def merge(self, *, repo, branch="", mr):
            order.append("merge")
            await super().merge(repo=repo, branch=branch, mr=mr)

    fake = Recording()
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "b2")

    assert (returned, recorded) == ("done", "done")
    assert order == ["push", "merge"], "it merged a head that was never pushed"


def test_ci_poll_fails_when_the_mr_cannot_be_merged(tmp_path, monkeypatch):
    """Green pipeline, unmergeable branch: the node reports 'conflict' once a
    re-fetch confirms it, and the log names the state, so the human_review
    brief has something to act on."""
    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "ci_poll", "e1", poll_interval=0
    )

    assert (returned, recorded) == ("conflict", "conflict")
    assert "not mergeable: conflict" in _session_log(tmp_path, "e1")


def test_ci_poll_does_not_fail_on_an_undecided_merge_state(tmp_path, monkeypatch):
    """`mergeable is None` is not `mergeable is False`. An unapproved MR with a
    green pipeline is exactly what this node is supposed to hand to the gate."""
    fake = forge.FakeForge(ci_states=["success"], mergeable=None, merge_detail="not_approved")

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "ci_poll", "e2", poll_interval=0
    )

    assert (returned, recorded) == ("done", "done")


def test_merge_is_done_only_when_the_forge_reports_merged(tmp_path, monkeypatch):
    """The good path, now decided by a read rather than by an exit code."""
    fake = forge.FakeForge()
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "m1")

    assert (returned, recorded) == ("done", "done")
    assert fake.merged == [1]
    assert "merged !1" in _session_log(tmp_path, "m1")


def test_merge_fails_when_the_mr_is_still_open_afterwards(tmp_path, monkeypatch):
    """Kraft-79x3, from work item 0853ea31 / MR !76. GitLab treats `mr merge`
    as async: with the head's pipeline still running it enables "merge when all
    merge checks pass", exits 0, and merges nothing. Kraft logged `merged`,
    marked the node done and completed the work item; the pipeline then failed,
    the auto-merge never fired, and the MR is still open and conflicted."""

    class AutoMergeScheduled(forge.FakeForge):
        async def merge(self, *, repo, branch="", mr):
            return None  # exit 0, merged nothing

    fake = AutoMergeScheduled()
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "merge", "m2", merge_timeout=0, merge_interval=0
    )

    assert (returned, recorded) == ("failed", "failed")
    log = _session_log(tmp_path, "m2")
    assert "still open" in log, log
    assert "auto-merge" in log, "the human is not told where to look"
    assert fake.merged == [], "the fake did not merge, and the node must not claim it did"


def test_merge_fails_when_the_mr_was_closed_rather_than_merged(tmp_path, monkeypatch):
    """Closed is not merged: nothing landed, and the branch is gone."""

    class ClosedAfterwards(forge.FakeForge):
        # The first read is run_task's own `find_mr`, which has to say open or
        # the node never reaches the merge at all; every read after it is the
        # verification poll.
        reads = 0

        async def merge(self, *, repo, branch="", mr):
            return None

        async def find_mr(self, *, repo, branch):
            ref = await super().find_mr(repo=repo, branch=branch)
            if ref is None:
                return None
            self.reads += 1
            state = "open" if self.reads == 1 else "closed"
            return forge.MRRef(number=ref.number, url=ref.url, state=state)

    fake = ClosedAfterwards()
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "merge", "m3", merge_timeout=0, merge_interval=0
    )

    assert (returned, recorded) == ("failed", "failed")
    assert "closed" in _session_log(tmp_path, "m3")


def test_merge_waits_out_a_pipeline_recreated_after_mr_checks(tmp_path, monkeypatch):
    """Kraft-x10m. `mr_sync`'s push after human_review can land a commit on a
    head mr_checks never validated, re-arming a required-pipeline rule for a
    pipeline that takes as long to finish as any other -- not the 300s window
    `_poll_merged` gives the forge's own merge machinery. `merge` must wait
    this pipeline out itself before calling `forge.merge()`."""
    fake = forge.FakeForge(ci_states=["pending", "pending", "success"])
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "g1", poll_interval=0)

    assert (returned, recorded) == ("done", "done")
    assert fake.merged == [1]


def test_merge_fails_fast_on_an_unmergeable_head_without_calling_merge(tmp_path, monkeypatch):
    """Kraft-266b. A conflict (or any other code-change-needed state) is an
    answer, not something `forge.merge()` should ever be asked to resolve."""
    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "g2", poll_interval=0)

    assert (returned, recorded) == ("failed", "failed")
    assert "not mergeable: conflict" in _session_log(tmp_path, "g2")
    assert fake.merged == [], "merge must not be called against an unmergeable head"


def test_merge_times_out_on_the_pipeline_wait_rather_than_the_merge_wait(tmp_path, monkeypatch):
    """The new CI gate uses `poll_timeout`, not `merge_timeout`: a pipeline
    that never settles is a different wait than the forge's own merge
    machinery being slow, and each needs its own name in the log
    (Kraft-x10m)."""
    fake = forge.FakeForge(ci_states=["pending"])
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "merge", "g3", poll_timeout=0, poll_interval=0
    )

    assert (returned, recorded) == ("failed", "failed")
    assert "timed out" in _session_log(tmp_path, "g3")
    assert fake.merged == []


def test_fake_forge_records_labels(tmp_path):
    """The fake is what every chain-level test runs against, so a capability it
    does not have is a capability no node can be tested through."""
    f = forge.FakeForge(ci_states=["success"])
    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    asyncio.run(f.set_labels(repo=tmp_path, mr=mr, labels=("release::patch",)))

    assert f.labels == ["release::patch"]


def test_run_task_opens_a_merge_request_per_repo_deepest_first(tmp_path, monkeypatch):
    """Root goes through the ordinary loop too, deepest submodule first, when
    it has changes of its own to review (Task 7 exempts only a root with
    nothing of its own -- see test_root_with_no_changes_of_its_own... below)."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    root, _sub = make_repo_with_submodule(tmp_path)
    # `_commits_on` (Task 7's root_has_changes) diffs against origin/main; a
    # fixture with no origin always reads as "no changes", so root would be
    # dropped from the loop regardless of what actually changed.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(root), str(origin)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=root, check=True)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(root),
                template=_back_half_template(),
                bd_cwd=str(tracker),
                submodules=["repos/pkg"],
                root_merge_policy="bump",
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id=wid
            )
            subprocess.run(["git", "fetch", "-q", "origin"], cwd=worktree, check=True)
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            branch = store.branch_for(row)
            # A root-level change of its own, or Task 7's "nothing of its own
            # to review" exemption drops it from the loop regardless of policy.
            (worktree / "root-change.txt").write_text("x\n")
            subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "root change"], cwd=worktree, check=True)
            status = await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id=wid,
                node_id="open_mr",
                hook_point="on.mr.open",
                handler="open_mr",
                backend="fake",
                repo=worktree,
                branch=branch,
                title="t",
            )
            repos = database.read(lambda c: store.repos_for(c, wid))
            return status, repos
        finally:
            await database.close()

    status, repos = asyncio.run(scenario())
    assert status == "done"
    assert len(fake.opened) == 2  # submodule, then root
    assert [r["role"] for r in repos] == ["submodule", "root"]
    assert repos[0]["state"] == "open"


def test_run_task_is_unchanged_for_a_single_repo_item(tmp_path, monkeypatch):
    """The regression this whole task is not allowed to cause."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_back_half_template(),
                bd_cwd=str(tracker),
            )
            status = await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id=wid,
                node_id="open_mr",
                hook_point="on.mr.open",
                handler="open_mr",
                backend="fake",
                repo=repo,
                branch="kraft/w1",
                title="t",
            )
            return status
        finally:
            await database.close()

    status = asyncio.run(scenario())
    assert status == "done"
    assert len(fake.opened) == 1


def test_root_with_no_changes_of_its_own_never_opens_a_merge_request(tmp_path, monkeypatch):
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    root, _sub = make_repo_with_submodule(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(root),
                template=_back_half_template(),
                bd_cwd=str(tracker),
                submodules=["repos/pkg"],
                root_merge_policy="bump_no_mr",
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id=wid
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            branch = store.branch_for(row)
            # the agent's half: a commit inside the submodule only
            sub = worktree / "repos" / "pkg"
            (sub / "new.txt").write_text("x\n")
            subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "m"],
                cwd=sub,
                check=True,
            )
            await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id=wid,
                node_id="open_mr",
                hook_point="on.mr.open",
                handler="open_mr",
                backend="fake",
                repo=worktree,
                branch=branch,
                title="t",
            )
            return database.read(lambda c: store.repos_for(c, wid))
        finally:
            await database.close()

    repos = asyncio.run(scenario())
    assert len(fake.opened) == 1  # the submodule only
    root_row = next(r for r in repos if r["role"] == "root")
    assert root_row["state"] == "pending"  # never touched by open_mr


def test_the_shape_that_broke_on_9d0ab38ff3c9439b90506df0f6966660(tmp_path, monkeypatch):
    """A work item whose entire deliverable is inside a submodule, root told
    not to bump its pointer: the submodule gets its own merge request and the
    root gets none. This is the standing regression test for the real
    occurrence -- it exercises the same structure without depending on that
    one workspace ever existing again (see the spec's Verification section)."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    root, _sub = make_repo_with_submodule(tmp_path, submodule_path="repos/packages")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="six OTEL metric attributes",
                repo=str(root),
                template=_back_half_template(),
                bd_cwd=str(tracker),
                submodules=["repos/packages"],
                root_merge_policy="skip",
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id=wid
            )
            sub = worktree / "repos" / "packages"
            (sub / "metrics.py").write_text("ATTRS = 6\n")
            subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "m"],
                cwd=sub,
                check=True,
            )
            status = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=_forge_registry("fake"),
                bd_cwd=str(tracker),
            )
            repos = database.read(lambda c: store.repos_for(c, wid))
            return status, repos
        finally:
            await database.close()

    status, repos = asyncio.run(scenario())
    assert status == "completed"
    assert fake.merged == [1]  # only the submodule's MR, never a root one
    submodule_row = next(r for r in repos if r["role"] == "submodule")
    assert submodule_row["state"] == "merged"
    root_row = next(r for r in repos if r["role"] == "root")
    assert root_row["state"] == "pending"  # skip: root untouched, as asked


def test_ci_poll_retries_infra_red_across_separate_entries_then_stops_with_the_reason(
    tmp_path, monkeypatch
):
    """Kraft-h81i, and the review's question about `retry_infra`'s immediate
    re-read: the retry budget has to survive across separate ci_poll entries
    (three separate calls here, standing in for three real ~30s-apart
    ci_wait re-entries), not live inside one call's loop. Each entry's own
    re-read after a kick sees "pending" (`"waiting"`), exactly like a real
    forge would the instant after `retry_jobs` returns; only the *next*
    entry's fresh read reflects the settled outcome once real CI time has
    passed."""
    fake = forge.FakeForge(
        ci_states=["failed", "pending", "failed", "pending", "failed"],
        ci_failed_jobs=[(forge.FailedJob("build", "failed", "runner_system_failure"),)] * 5,
    )
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

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
            results = []
            for session_id in ["i1", "i2", "i3"]:
                results.append(
                    await forge.run_task(
                        database,
                        rd,
                        session_id=session_id,
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
                )
            return results
        finally:
            await database.close()

    first, second, third = asyncio.run(scenario())
    assert first == "waiting"
    assert second == "waiting"
    assert third == "infra_stop"
    # Two kicks (entries one and two); the third entry's count (3) breaches
    # the cap before a third kick is made.
    assert len(fake.retried) == 2


def test_job_finding_message_carries_the_trace_tail_when_the_backend_gave_one():
    """Task 3's glab shape: a header line plus indented trace lines."""
    jobs = (
        "pipeline 88: failed",
        "job test: failed (script_failure)",
        "  AssertionError: expected 3, got 4",
        "  at test_foo.py:12",
        "job lint: failed (script_failure)",
        "  ruff: E501 line too long",
    )
    msg = forge.run._job_finding_message(jobs, forge.FailedJob("test", "failed", "script_failure"))
    assert "AssertionError: expected 3, got 4" in msg
    assert "ruff" not in msg  # only this job's own block, not the next one's


def test_job_finding_message_falls_back_to_name_and_reason_with_no_trace():
    """gh's one-liner shape has nothing to extract a block from."""
    jobs = ("test: FAILURE",)
    msg = forge.run._job_finding_message(jobs, forge.FailedJob("test", "failed", "script_failure"))
    assert msg == "job test failed: script_failure"


def test_ci_poll_writes_findings_for_a_code_red_pipeline(tmp_path, monkeypatch):
    """`on.ci.poll` going code-red feeds the fix-loop plumbing the same way
    any other measuring task does: one finding per failed job, result_path
    populated (Kraft-cbr §3)."""
    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(forge.FailedJob("test", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

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
            await forge.run_task(
                database,
                rd,
                session_id="s-findings",
                work_item_id="w1",
                node_id="mr_checks",
                hook_point="on.ci.poll",
                handler="ci_poll",
                backend="fake",
                repo=tmp_path,
                branch="kraft/w1",
                title="t",
            )
            return rd
        finally:
            await database.close()

    rd = asyncio.run(scenario())
    result_path = rd.results / "s-findings.json"
    payload = json.loads(result_path.read_text())
    assert payload["findings"]
    assert "test" in payload["findings"][0]["message"]


def _ci_fixloop_template() -> Template:
    """`mr_checks` with both `fix_loop` and `on_failure` (Task 6/7's shape),
    isolated from open_mr/merge so this test only exercises the repair's own
    re-measure."""
    return Template(
        id="ci-fixloop-waiting",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None, "fix_loop": None},
            {
                "id": "mr_checks",
                "tasks": ["on.ci.poll"],
                "gate_after": None,
                "fix_loop": "ci_fix_loop",
                "on_failure": ["on.mr_checks.repair"],
            },
        ],
    )


def _ci_fixloop_registry(backend: str = "fake") -> Registry:
    return Registry(
        hooks={
            "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
            "on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": backend},
            # A metadata-only repair (Task 6's real on.mr_checks.repair contract) --
            # `noop` stands in for it here since its own logic is out of scope for
            # this test; what matters is that it "succeeds" and touches nothing
            # `on.ci.poll` reads.
            "on.mr_checks.repair": {"kind": "builtin", "handler": "noop"},
        }
    )


def test_ci_fix_loop_stops_for_waiting_on_the_repairs_re_measure(tmp_path, monkeypatch):
    """The human review's second point: `recover_node`'s re-measure calls
    `on.ci.poll` again, and a repaired head's pipeline being merely `pending`
    (WAITING) is the *routine* case, not the exception -- a metadata-only
    repair touches nothing CI runs against, so the fix that would turn this
    green is still to come. Before this task's fix, the fall-through line
    handed WAITING to the ordinary fix-cycle machinery below, which would
    have spent a paid fix cycle on a pipeline that had not even settled yet."""
    fake = forge.FakeForge(
        ci_states=["failed", "pending"],
        ci_failed_jobs=[(forge.FailedJob("test", "failed", "script_failure"),)] * 2,
    )
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  ci_fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="waiting on repair re-measure",
                repo=str(repo),
                template=_ci_fixloop_template(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=_ci_fixloop_registry(),
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "waiting"
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]
            assert "fix_cycle_started" not in types, (
                "the repair's WAITING re-measure must stop the node, not spend a "
                "paid fix cycle on a pipeline that has not settled yet"
            )
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            assert row["status"] == "waiting"
        finally:
            await database.close()

    asyncio.run(scenario())


def _mr_checks_bounce_template() -> Template:
    """`verify` then `mr_checks`, `mr_checks` carrying `rebase_bounce_to`
    (Kraft-9h7v) but neither `fix_loop` nor `on_failure` -- this test is about
    the bounce mechanism itself, not the fix loop Task 6/7 layers on top of
    it, the same isolation `_rebase_chain_template` uses in
    `tests/test_executor_walk.py` for `pre_mr_rebase`'s own version of this
    bounce."""
    return Template(
        id="mr-checks-bounce",
        nodes=[
            {"id": "verify", "tasks": ["on.test.run"], "gate_after": None, "fix_loop": None},
            {
                "id": "mr_checks",
                "tasks": ["on.ci.poll"],
                "gate_after": None,
                "fix_loop": None,
                "rebase_bounce_to": "verify",
            },
        ],
    )


def _mr_checks_bounce_registry(backend: str = "fake") -> Registry:
    return Registry(
        hooks={
            "on.test.run": {"kind": "subprocess", "command": [sys.executable, "-c", "exit(0)"]},
            "on.ci.poll": {"kind": "forge", "handler": "ci_poll", "backend": backend},
        }
    )


def _gitignore_engineering(repo):
    """`refresh_worktree_base`'s dirty check has no way to know
    `.engineering/sessions/*.md` is Kraft's own bookkeeping rather than an
    agent's leftover work -- same fixture step
    `test_a_moved_base_bounces_back_to_verify_with_a_drift_note` uses."""
    (repo / ".gitignore").write_text(".engineering/\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "gitignore .engineering"], cwd=repo, check=True)


def test_ci_poll_rebases_and_bounces_on_a_confirmed_conflict(tmp_path, monkeypatch):
    """A settled green pipeline the forge still reports unmergeable, with a
    clean rebase available, must not just move `base_ref` -- it has to
    actually land the chain back on `verify`, the same way `pre_mr_rebase`'s
    own bounce already does
    (`test_a_moved_base_bounces_back_to_verify_with_a_drift_note`,
    `tests/test_executor_walk.py`)."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)

    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 2, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
        # Kraft-lpdd: this suite is about the rebase bounce's own stop, not
        # the unrelated auto-escalate trigger that `needs_human` would
        # otherwise also fire.
        "auto_escalate_stuck: false\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="bounce on conflict",
                repo=str(repo),
                template=_mr_checks_bounce_template(),
                bd_cwd=str(tracker),
            )
            await _builtins.ensure_worktree(database, rd, repo=str(repo), work_item_id=wid)

            # Origin moves in a way the branch does not touch, so the forced
            # rebase this triggers is clean.
            (repo / "moved.txt").write_text("moved on\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "moved on upstream"], cwd=repo, check=True)
            new_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=_mr_checks_bounce_registry(),
                bd_cwd=str(tracker),
                policy=pol,
            )
            # Not "completed": the second pass through mr_checks hits the
            # same FakeForge-reported "still unmergeable" verdict, but this
            # time the branch is already up to date with `default`, so the
            # forced rebase has nothing left to do (`refresh_worktree_base`'s
            # own "already an ancestor" short-circuit returns None) and
            # `status` stays "conflict" -- a bare node with neither `fix_loop`
            # nor `on_failure` fails straight to needs_human at that point.
            # That second failure is not a bug this test is pinning; it is
            # FakeForge's `mergeable=False` being a constant rather than
            # something a real forge would clear once the branch is current.
            # What matters here is that the *first* pass bounced at all.
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute(
                    "SELECT base_ref FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["base_ref"] == new_head

            starts = [
                e["payload"]["node_id"]
                for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "node_started"
            ]
            assert starts.count("verify") == 2, (
                f"expected the confirmed conflict's forced rebase to bounce the "
                f"chain back to verify a second time, got {starts}"
            )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_ci_poll_stops_for_a_human_on_a_real_rebase_conflict(tmp_path, monkeypatch):
    """The other branch: the worktree's branch and origin each edit the same
    line of `calc.py`, so the forced rebase itself conflicts -- the identical
    fixture shape `test_refresh_worktree_base_raises_and_aborts_on_conflict`
    (`tests/test_builtins.py`) already uses to pin `refresh_worktree_base`
    alone; this test drives the same conflict through the whole `ci_poll`
    node instead. `status` stays `"conflict"`, the node fails, and -- with no
    `fix_loop`/`on_failure` on this bare template -- `walk_node` stops it for
    a human directly; `base_ref` is never touched."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)

    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    pol_path = tmp_path / "policy.yaml"
    pol_path.write_text(
        "loops:\n  rebase_bounce: { attempts: 2, wall_clock_s: 3600 }\n"
        "default: { attempts: 3, wall_clock_s: 3600 }\n"
        # Kraft-lpdd: this suite is about the rebase bounce's own stop, not
        # the unrelated auto-escalate trigger that `needs_human` would
        # otherwise also fire.
        "auto_escalate_stuck: false\n"
    )
    pol = policy.load_policy(pol_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="real conflict",
                repo=str(repo),
                template=_mr_checks_bounce_template(),
                bd_cwd=str(tracker),
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid
            )
            original_base = database.read(
                lambda c: c.execute(
                    "SELECT base_ref FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )["base_ref"]

            # Same line of calc.py, two diverging edits -- exactly
            # test_refresh_worktree_base_raises_and_aborts_on_conflict's setup.
            (worktree / "calc.py").write_text(
                "def add(a, b):\n    return a - b - 1  # bug: should be +\n"
            )
            subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
            subprocess.run(["git", "commit", "-m", "worktree edit"], cwd=worktree, check=True)

            (repo / "calc.py").write_text(
                "def add(a, b):\n    return a - b - 2  # bug: should be +\n"
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", "conflicting edit upstream"], cwd=repo, check=True
            )

            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=_mr_checks_bounce_registry(),
                bd_cwd=str(tracker),
                policy=pol,
            )
            assert result == "needs_human"

            row = database.read(
                lambda c: c.execute(
                    "SELECT base_ref FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["base_ref"] == original_base, "a real conflict must never move base_ref"

            # walk_node's needs_human reason names the failed task's kind, not
            # the detail -- that lives in the ci_poll session's own log, the
            # same place a human reading this stop looks.
            session_row = database.read(
                lambda c: c.execute(
                    "SELECT log_path FROM worker_sessions WHERE work_item_id = ? "
                    "AND node_id = 'mr_checks' AND hook_point = 'on.ci.poll' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (wid,),
                ).fetchone()
            )
            log_text = Path(session_row["log_path"]).read_text()
            assert "rebase" in log_text.lower() and "failed" in log_text.lower()
        finally:
            await database.close()

    asyncio.run(scenario())
