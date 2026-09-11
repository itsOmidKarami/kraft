"""The forge adapter: opening a merge request, reading CI, merging.

The whole back half of `default.yaml` was `builtin:noop` before this — a chain
ended at a local diff (Kraft-33j). Everything here runs against `FakeForge` or a
stubbed CLI on PATH; nothing in this file touches the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess

import pytest
from support.harness import isolated_bd, make_repo, make_repo_with_submodule

from kraft import builtins as _builtins
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
GH_PR_VIEW_CONFLICT = (
    '{"number":7,"url":"https://github.com/o/r/pull/7","mergeable":"CONFLICTING",'
    '"mergeStateStatus":"DIRTY","statusCheckRollup":[{"name":"build","conclusion":"SUCCESS"}]}'
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


def test_assert_clean_sees_a_submodule_with_ignore_all(tmp_path):
    """`submodule.<path>.ignore = all` is a legitimate thing for a human to
    set on a six-submodule workspace -- it must not blind Kraft's own guard
    to a submodule commit that never left the worktree (the real failure on
    work item 9d0ab38ff3c9439b90506df0f6966660)."""
    root = tmp_path / "root"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("root\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    sub = tmp_path / "sub"
    sub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=sub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=sub, check=True)
    (sub / "f.txt").write_text("1\n")
    subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=sub, check=True)

    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub), "pkg"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "config", "submodule.pkg.ignore", "all"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add submodule"], cwd=root, check=True)

    # The submodule checkout has its own gitdir under root/.git/modules and
    # does not inherit `sub`'s identity, so a runner with no global git
    # config (CI, unlike a dev machine) hits "unable to auto-detect email
    # address" on the commit below without this.
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root / "pkg", check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root / "pkg", check=True)

    # New commit inside the submodule, root pointer left untouched -- exactly
    # what "do not bump the workspace submodule pointer" produces.
    (root / "pkg" / "f.txt").write_text("2\n")
    subprocess.run(["git", "add", "-A"], cwd=root / "pkg", check=True)
    subprocess.run(["git", "commit", "-q", "-m", "metric change"], cwd=root / "pkg", check=True)

    with pytest.raises(forge.ForgeError, match="pkg"):
        asyncio.run(forge._assert_clean(root))


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
    after it can push too.

    No `origin/kraft/abc` remote-tracking ref exists yet in this stubbed repo
    (the `rev-parse --verify` probe returns nothing), so `_push` has no lease
    to attach and falls back to the plain fast-forward push (Kraft-z6i8)."""
    _stub(tmp_path, monkeypatch, "git", "")

    asyncio.run(forge.GlabCli().push(repo=tmp_path, branch="kraft/abc"))

    assert _argv(tmp_path, "git")[-4:] == ["push", "-u", "origin", "kraft/abc"]


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
    failure -> failed, unmergeable -> failed."""
    fake = forge.FakeForge(ci_states=["success"])
    returned, recorded = _forge_session(tmp_path / "a", monkeypatch, fake, "ci_poll", "s5")
    assert (returned, recorded) == ("done", "done")

    fake = forge.FakeForge(ci_states=["failed"])
    returned, recorded = _forge_session(tmp_path / "b", monkeypatch, fake, "ci_poll", "s5b")
    assert (returned, recorded) == ("failed", "failed")

    fake = forge.FakeForge(ci_states=["success"], mergeable=False)
    returned, recorded = _forge_session(tmp_path / "c", monkeypatch, fake, "ci_poll", "s5c")
    assert (returned, recorded) == ("failed", "failed"), "Kraft-ejj9: green and unmergeable"


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
    """Task 2's whole payoff is one splat in `executor._dispatch`. `ci_poll`
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


# Captured against glab 1.117.0 and gh 2.100.0 on 2026-09-09.
GLAB_MR_LIST_MERGED = (
    '[{"iid":54,"state":"merged","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/54"}]'
)
GLAB_MR_LIST_OPEN = (
    '[{"iid":62,"state":"opened","source_branch":"kraft/abc",'
    '"web_url":"https://gitlab.com/itsOmidKarami/kraft/-/merge_requests/62"}]'
)
GH_PR_LIST_MERGED = '[{"number":7,"url":"https://github.com/o/r/pull/7","state":"MERGED"}]'


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


def test_gh_find_mr_reads_the_state_of_an_existing_pull_request(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, "gh", GH_PR_LIST_MERGED)

    found = asyncio.run(forge.GhCli().find_mr(repo=tmp_path, branch="kraft/abc"))

    assert found == forge.MRRef(number=7, url="https://github.com/o/r/pull/7", state="merged")
    argv = _argv(tmp_path, "gh")
    assert argv[argv.index("--head") + 1] == "kraft/abc"
    assert argv[argv.index("--state") + 1] == "all"


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


def test_fake_forge_find_mr_tracks_its_own_opened_and_merged_lists(tmp_path):
    f = forge.FakeForge()
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/w1")) is None

    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/w1")).state == "open"
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/other")) is None

    asyncio.run(f.merge(repo=tmp_path, branch="kraft/w1", mr=mr))
    assert asyncio.run(f.find_mr(repo=tmp_path, branch="kraft/w1")).state == "merged"


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


def test_ci_poll_fails_when_the_mr_cannot_be_merged(tmp_path, monkeypatch):
    """Green pipeline, unmergeable branch: the node fails and the log names the
    state, so the human_review brief has something to act on."""
    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "ci_poll", "e1", poll_interval=0
    )

    assert (returned, recorded) == ("failed", "failed")
    assert "not mergeable: conflict" in _session_log(tmp_path, "e1")


def test_ci_poll_does_not_fail_on_an_undecided_merge_state(tmp_path, monkeypatch):
    """`mergeable is None` is not `mergeable is False`. An unapproved MR with a
    green pipeline is exactly what this node is supposed to hand to the gate."""
    fake = forge.FakeForge(ci_states=["success"], mergeable=None, merge_detail="not_approved")

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, "ci_poll", "e2", poll_interval=0
    )

    assert (returned, recorded) == ("done", "done")


def test_poll_returns_early_on_a_conflict_rather_than_waiting_out_the_pipeline(
    tmp_path, monkeypatch
):
    """A conflict will not resolve itself in thirty minutes."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(forge.asyncio, "sleep", fake_sleep)
    fake = forge.FakeForge(ci_states=["pending"], mergeable=False, merge_detail="conflict")

    ci, timed_out = asyncio.run(
        forge._poll_ci(fake, repo=tmp_path, branch="b", timeout=10_000, interval=5)
    )

    assert (ci.state, ci.mergeable, timed_out) == ("pending", False, False)
    assert slept == [], "it waited out a pipeline for a branch that cannot merge"


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


# `glab ci get -F json` for the pipeline in GLAB_CI_FAILED, trimmed to the
# fields this code reads. Captured against glab 1.117.0 on 2026-09-09: the
# pipeline object carries its jobs inline, so one call names every job.
GLAB_CI_GET_FAILED = (
    '{"id":2826926700,"status":"failed","jobs":['
    '{"id":16392037101,"name":"lint-and-test","status":"success"},'
    '{"id":16392037104,"name":"release-impact","status":"failed"}]}'
)
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
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
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
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
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
    monkeypatch.setattr(forge, "resolve", lambda name: fake)
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
