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
from pathlib import Path

import pytest
from support.harness import isolated_bd, make_repo, make_repo_with_submodule, v1_resolved

from kraft import builtins as _builtins
from kraft import db, events, executor, policy, store
from kraft.adapters import forge
from kraft.paths import RunDirs

#: A repo that deliberately needs no preparation. Most tests here are about
#: forge dispatch, not environments.
NO_SETUP = {"setup_command": ""}
#: The same, on a forge. A V1 forge task always runs on `backend: auto`,
#: which reads the forge off the repo entry; `resolve` is patched to the fake.
ON_A_FORGE = {**NO_SETUP, "forge": "github"}


def _forge_node(node_id: str, target: str, **task) -> dict:
    return {
        "id": node_id,
        "kind": "exec",
        "tasks": [{"id": node_id, "kind": "forge", "target": target, **task}],
    }


def test_fake_forge_round_trips_an_mr(tmp_path):
    f = forge.FakeForge(ci_states=["pending", "success"])

    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert mr.number == 1
    assert mr.url.endswith("/1")
    assert asyncio.run(f.ci_status(repo=tmp_path, mr=mr)).state == "pending"
    assert asyncio.run(f.ci_status(repo=tmp_path, mr=mr)).state == "success"


def test_fake_forge_opens_as_draft(tmp_path):
    f = forge.FakeForge()

    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    assert f.opened_draft[mr.number] is True


def test_fake_forge_mark_ready_unsets_draft(tmp_path):
    f = forge.FakeForge()
    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))
    assert f.opened_draft[mr.number] is True

    asyncio.run(f.mark_ready(repo=tmp_path, branch="kraft/abc", mr=mr))

    assert f.opened_draft[mr.number] is False


def test_fake_forge_ci_status_carries_the_block_reason(tmp_path):
    f = forge.FakeForge(ci_states=["success"], block_reason="not_approved")
    mr = asyncio.run(f.open_mr(repo=tmp_path, branch="kraft/abc", title="t", body="b"))

    status = asyncio.run(f.ci_status(repo=tmp_path, mr=mr))

    assert status.block_reason == "not_approved"


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


def test_auto_resolves_to_the_fake_forge_for_a_dev_repo():
    """Ruling 147: `forge: fake` is the dev-only repo value `just dev` uses to
    reach the merge-request half of a chain without a real forge."""
    assert forge.backend_for("auto", "fake") == "fake"
    assert isinstance(forge.resolve(forge.backend_for("auto", "fake")), forge.FakeForge)


def test_a_repo_with_no_forge_is_told_the_remedy_v1_actually_reads():
    """V1 dispatch always passes `backend: auto`, so a registry `backend:` pin
    is never read -- naming it sends an operator to a file that changes
    nothing. The remedy is the repo's own `forge`, and `fake` is for dev."""
    with pytest.raises(forge.ForgeError) as err:
        forge.backend_for("auto", None)
    message = str(err.value)
    assert "registry.yaml" not in message
    assert "`forge: fake`" in message and "dev" in message


def test_the_dev_fake_forge_remembers_an_mr_across_nodes(tmp_path):
    """Ruling 147, round 3: a repo on `forge: fake` resolves afresh at every
    forge node, so the fake must be one instance per process -- otherwise the
    draft MR `open_mr` created is gone by the time `sync_mr` (and later
    `mark_ready`, `merge`) look for it. Nothing is monkeypatched: each
    `run_task` below goes through the real `backend_for`/`resolve`."""
    repo = make_repo(tmp_path)
    branch = f"kraft/{tmp_path.name}"
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            statuses = []
            for handler in ("open_mr", "sync_mr"):
                statuses.append(
                    await forge.run_task(
                        database,
                        rd,
                        session_id=f"s-{handler}",
                        work_item_id="w1",
                        node_id=handler,
                        hook_point=handler,
                        handler=handler,
                        backend="auto",
                        repo_forge="fake",
                        repo=repo,
                        orig_repo=repo,
                        branch=branch,
                        title="t",
                    )
                )
            return statuses
        finally:
            await database.close()

    assert asyncio.run(scenario()) == ["done", "done"]
    fake = forge.resolve(forge.backend_for("auto", "fake"))
    found = asyncio.run(fake.find_mr(repo=repo, branch=branch))
    assert found is not None and found.state == "open"
    assert branch in fake.bodies, "sync_mr's description never reached the MR open_mr made"
    assert fake.opened_draft[found.number] is False


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


def test_ci_poll_treats_an_already_merged_mr_as_done(tmp_path, monkeypatch):
    """Kraft-7itv's twin, caught live on work item b5afe84c: an MR merged
    out-of-band while `ci_poll` was still polling has its source branch
    deleted with it (`force_remove_source_branch`), and the push `ci_poll`
    makes on every entry (Kraft-bxj8) then died `! [rejected] ... (stale
    info)` against a remote ref that no longer existed -- the item never
    reached `sync_mr`'s own guard for the identical race."""
    fake = forge.FakeForge()
    mr = asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
    asyncio.run(fake.merge(repo=tmp_path, branch="kraft/w1", mr=mr))
    fake.pushed.clear()

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "ci_poll", "s11")

    assert (returned, recorded) == ("done", "done")
    assert fake.pushed == [], "it pushed to a branch the forge has already merged and deleted"


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


def test_sync_mr_marks_the_mr_ready_before_it_pushes(tmp_path, monkeypatch):
    """`mr_sync` is the one node every chain shape runs before `merge`,
    gated or not, so it's where the draft comes off (draft-MR workflow
    spec's 'Undraft' decision) -- before the push, so a project that reruns
    checks on ready-for-review sees the head it's about to check."""
    order: list[str] = []

    class Recording(forge.FakeForge):
        async def mark_ready(self, *, repo, branch, mr):
            order.append("mark_ready")
            await super().mark_ready(repo=repo, branch=branch, mr=mr)

        async def push(self, *, repo, branch):
            order.append("push")
            await super().push(repo=repo, branch=branch)

    fake = Recording()
    mr = asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
    assert fake.opened_draft[mr.number] is True

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "sync_mr", "s10")

    assert (returned, recorded) == ("done", "done")
    assert order == ["mark_ready", "push"]
    assert fake.opened_draft[mr.number] is False


def test_mark_ready_undrafts_the_merge_request(tmp_path, monkeypatch):
    """`mr.mark_ready` is V1's publication step, split out of `sync_mr` so a
    chain can put its final gate between describing the MR and publishing it.
    Without a handler the seeded chain stopped here for a human every time
    (Ruling 48's `config_error` arm), so the chain could never reach merge."""
    fake = forge.FakeForge()
    mr = asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
    assert fake.opened_draft[mr.number] is True

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, forge.run.handler_for("mr.mark_ready"), "s-ready"
    )

    assert (returned, recorded) == ("done", "done")
    assert fake.opened_draft[mr.number] is False


def test_mark_ready_treats_an_already_merged_mr_as_done(tmp_path, monkeypatch):
    """Same shortcut `sync_mr` and `merge` take (Kraft-7itv): a merged MR's
    source branch is usually deleted with it, so undrafting it is at best a
    no-op and at worst a CLI error that stops the item one node from the end
    with the work already on main."""

    class Refusing(forge.FakeForge):
        async def mark_ready(self, *, repo, branch, mr):  # pragma: no cover - must not run
            raise AssertionError("it undrafted a merge request the forge has already merged")

    fake = Refusing()
    mr = asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
    asyncio.run(fake.merge(repo=tmp_path, branch="kraft/w1", mr=mr))

    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, forge.run.handler_for("mr.mark_ready"), "s-ready-merged"
    )

    assert (returned, recorded) == ("done", "done")
    assert "nothing to mark ready" in _session_log(tmp_path, "s-ready-merged")


def test_sync_mr_treats_an_already_merged_mr_as_done(tmp_path, monkeypatch):
    """Kraft-7itv, from work item 45b06993: the MR was merged by hand, its
    source branch deleted with it, and `human_review` skipped. mr_sync's
    push then died `! [rejected] ... (stale info)` -- the lease expects the
    sha origin last showed this worktree and origin had no ref at all -- and
    the item stopped one node short with the work already on main."""
    fake = forge.FakeForge()
    mr = asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
    asyncio.run(fake.merge(repo=tmp_path, branch="kraft/w1", mr=mr))
    fake.pushed.clear()

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "sync_mr", "s9")

    assert (returned, recorded) == ("done", "done")
    assert fake.pushed == [], "it pushed to a branch the forge has already merged and deleted"
    assert "nothing to sync" in _session_log(tmp_path, "s9")


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


def test_ci_poll_reuses_the_session_across_a_still_pending_wait(tmp_path, monkeypatch):
    """Two re-entries of on.ci.poll while the pipeline is still pending must
    leave exactly one worker_sessions row, with both polls' log text in it,
    and no attempt-number climb."""
    fake = forge.FakeForge(ci_states=["pending", "pending"])
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
            for sid in ("s1", "s2"):
                await forge.run_task(
                    database,
                    rd,
                    session_id=sid,
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    handler="ci_poll",
                    backend="fake",
                    repo=tmp_path,
                    branch="kraft/w1",
                    title="t",
                )
            rows = database.read(
                lambda c: c.execute(
                    "SELECT id, attempt, status FROM worker_sessions WHERE work_item_id='w1'"
                ).fetchall()
            )
            return rows
        finally:
            await database.close()

    rows = asyncio.run(scenario())
    assert len(rows) == 1
    assert rows[0]["id"] == "s1"
    assert rows[0]["attempt"] == 1
    assert rows[0]["status"] == "waiting"
    log = (tmp_path / "run" / "logs" / "s1.log").read_text()
    assert log.count("pipeline pending") == 2


def test_ci_poll_pins_to_the_pipeline_it_last_saw(tmp_path, monkeypatch):
    """A second poll of the same head sha reads the pinned pipeline id back,
    not "" (unpinned) again."""
    fake = forge.FakeForge(ci_states=["pending", "pending"], ci_pipeline_refs=["555"])
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
            for sid in ("s1", "s2"):
                await forge.run_task(
                    database,
                    rd,
                    session_id=sid,
                    work_item_id="w1",
                    node_id="mr_checks",
                    hook_point="on.ci.poll",
                    handler="ci_poll",
                    backend="fake",
                    repo=tmp_path,
                    branch="kraft/w1",
                    title="t",
                )
        finally:
            await database.close()

    asyncio.run(scenario())
    # git._head_sha(tmp_path) fails (not a real repo) and returns "" both
    # times -- "" == "" -- so the stored ref's sha always matches and the
    # second call is the pinned one.
    assert fake.pipeline_ids_requested == ["", "555"]


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


class _NoMrForge(forge.FakeForge):
    """A fake that behaves like a real backend once the merge request is gone.

    `FakeForge` answers the MR-based `ci_status` happily whether or not an MR
    exists, so every `merge_watch` test passed while the handler still read
    the pipeline through `gh pr view` / `glab mr view` on a just-merged branch
    -- where both real backends raise `ForgeError` and fail the terminal node.
    This double raises there, so a test can hold the branch-based read down.

    `branch_ci_status` deliberately calls `FakeForge.ci_status` unbound rather
    than `self.ci_status`: the base class implements it in terms of the method
    this class overrides, and going through `self` would make the branch read
    raise too.
    """

    async def ci_status(self, *, repo, mr, branch, pipeline_id=""):
        raise forge.ForgeError(f"no open merge request for {branch!r}")

    async def branch_ci_status(self, *, repo, branch, head_sha="", pipeline_id=""):
        return await forge.FakeForge.ci_status(
            self,
            repo=repo,
            mr=forge.MR(number=0, url="http://fake.forge/branch"),
            branch=branch,
            pipeline_id=pipeline_id,
        )


def _merge_watch_row(database_write, work_item_id, *, base_ref=None, status="active"):
    """Insert or update a `work_items` row with the columns `merge_watch`
    reads: `base_ref` (for the broken-base pause query) and `status`."""
    return database_write(
        lambda c: c.execute(
            "UPDATE work_items SET base_ref = ?, status = ? WHERE id = ?",
            (base_ref, status, work_item_id),
        )
    )


def test_merge_watch_reports_done_on_a_green_target_branch(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            returned = await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="post_merge_watch",
                hook_point="on.merge.watch",
                handler="merge_watch",
                backend="fake",
                repo=repo,
                orig_repo=repo,
                branch="kraft/w1",
                title="t",
            )
            return returned
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "done"
    # A green read never reaches the follow-up-bead/pause-other-items branch
    # at all -- nothing to assert beyond the returned status.


def test_merge_watch_waits_instead_of_crashing_when_upstream_head_is_unknown(tmp_path, monkeypatch):
    """`upstream_head` returns `None` whenever `git rev-parse HEAD` fails or
    hits `config.git_read`'s timeout (same as the other two callers,
    `ensure_worktree` and `refresh_worktree_base`). `merge_watch` must treat
    that the same way they do -- "nothing to report yet" -- rather than hand
    `None` on to `branch_ci_status`, where `GhCli` crashes formatting
    `head_sha[:7]` and `GlabCli` silently reports the previous commit's
    pipeline as this merge's result (code-review)."""
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def _none(repo):
        return None

    monkeypatch.setattr(_builtins, "upstream_head", _none)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            return await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="post_merge_watch",
                hook_point="on.merge.watch",
                handler="merge_watch",
                backend="fake",
                repo=repo,
                orig_repo=repo,
                branch="kraft/w1",
                title="t",
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "waiting"


def test_merge_watch_retries_infra_red_once_then_reports_done_on_green(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(
        ci_states=["failed", "success"],
        ci_failed_jobs=[(), ()],  # empty failed_jobs -> is_infra_red is True
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
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            return await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="post_merge_watch",
                hook_point="on.merge.watch",
                handler="merge_watch",
                backend="fake",
                repo=repo,
                orig_repo=repo,
                branch="kraft/w1",
                title="t",
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "done"
    assert fake.retried  # retry_jobs was called exactly once


def test_merge_watch_infra_retry_never_reads_through_the_merged_away_mr(tmp_path, monkeypatch):
    """Judge finding (rounds 0/2/3): the infra kick's own re-read went through
    the MR-based `ci_status`, which on a branch whose MR is already merged
    raises `ForgeError` on both real backends and fails this terminal node.
    `_NoMrForge` raises there the way they do, so this fails unless the re-read
    goes through `branch_ci_status`."""
    repo = make_repo(tmp_path)
    fake = _NoMrForge(
        ci_states=["failed", "success"],
        ci_failed_jobs=[(), ()],  # empty failed_jobs -> is_infra_red is True
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
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            return await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="post_merge_watch",
                hook_point="on.merge.watch",
                handler="merge_watch",
                backend="fake",
                repo=repo,
                orig_repo=repo,
                branch="kraft/w1",
                title="t",
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "done"
    assert fake.retried


def test_merge_watch_green_path_never_reads_through_the_merged_away_mr(tmp_path, monkeypatch):
    """The same guard on the ordinary settle: no call in this node's happy
    path may resolve a merge request either."""
    repo = make_repo(tmp_path)
    fake = _NoMrForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            return await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="post_merge_watch",
                hook_point="on.merge.watch",
                handler="merge_watch",
                backend="fake",
                repo=repo,
                orig_repo=repo,
                branch="kraft/w1",
                title="t",
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "done"


def test_merge_watch_files_a_follow_up_bead_and_pauses_items_on_the_broken_sha(
    tmp_path, monkeypatch
):
    repo = make_repo(tmp_path)
    tracker = isolated_bd(tmp_path, name="tracker")
    fake = forge.FakeForge(
        ci_states=["failed"],
        ci_failed_jobs=[(forge.FailedJob("build", "failed", "script_failure"),)],
    )
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, bead_cwd, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active',?,'now','now')",
                    (str(repo), str(tracker)),
                )
            )
            # w2 rebased onto exactly the commit that just broke -- must pause.
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, base_ref, created_at, updated_at) VALUES "
                    "('w2','t',?,'default','{}','active',?,'now','now')",
                    (str(repo), head_sha),
                )
            )
            # w3 is active but on a different base -- must not be touched.
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, base_ref, created_at, updated_at) VALUES "
                    "('w3','t',?,'default','{}','active','some-other-sha','now','now')",
                    (str(repo),),
                )
            )
            # w4 is parked on its own pipeline (`waiting`) on the broken
            # commit -- the item most likely to re-discover this break in its
            # own fix loop, so it must be warned too (gate review).
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, base_ref, created_at, updated_at) VALUES "
                    "('w4','t',?,'default','{}','waiting',?,'now','now')",
                    (str(repo), head_sha),
                )
            )
            returned = await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="post_merge_watch",
                hook_point="on.merge.watch",
                handler="merge_watch",
                backend="fake",
                repo=repo,
                orig_repo=repo,
                branch="kraft/w1",
                title="t",
            )
            w2 = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = 'w2'").fetchone()
            )
            w3 = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = 'w3'").fetchone()
            )
            w4 = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = 'w4'").fetchone()
            )
            w2_events = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w2"))]
            return returned, w2["status"], w3["status"], w4["status"], w2_events
        finally:
            await database.close()

    returned, w2_status, w3_status, w4_status, w2_events = asyncio.run(scenario())
    assert returned == "done"
    assert w4_status == "paused"
    assert w2_status == "paused"
    assert "paused_by_broken_base" in w2_events
    assert w3_status == "active"

    bd_show = subprocess.run(
        ["bd", "search", "post-merge", "--json", "--status", "all"],
        cwd=tracker,
        capture_output=True,
        text=True,
    ).stdout
    assert "post-merge pipeline broke" in bd_show


def test_merge_watch_pins_to_the_pipeline_it_last_saw(tmp_path, monkeypatch):
    """Same pin `ci_poll` already relies on (Kraft-ivh1), against the target
    branch instead of this item's own: survives a second item merging before
    this one's own pipeline settles."""
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(ci_states=["pending", "pending"], ci_pipeline_refs=["777"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            for sid in ("s1", "s2"):
                await forge.run_task(
                    database,
                    rd,
                    session_id=sid,
                    work_item_id="w1",
                    node_id="post_merge_watch",
                    hook_point="on.merge.watch",
                    handler="merge_watch",
                    backend="fake",
                    repo=repo,
                    orig_repo=repo,
                    branch="kraft/w1",
                    title="t",
                )
        finally:
            await database.close()

    asyncio.run(scenario())
    assert fake.pipeline_ids_requested == ["", "777"]


def test_merge_watch_does_not_pin_a_pipeline_read_for_a_different_commit(tmp_path, monkeypatch):
    """Plan-review finding 2: right after a merge, the target branch's
    "latest pipeline" is usually still a previous, unrelated commit's, until
    this head's own pipeline exists. Pinning that wrong id to head_sha would
    have every later re-entry poll it forever -- it never moves, and
    `render_ci` never gets a chance to see the real one."""
    repo = make_repo(tmp_path)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    fake = forge.FakeForge(
        ci_states=["success", "success"],
        # First read: settled green, but for a different commit entirely --
        # the shape "latest on branch" takes right after a merge, before this
        # head's own pipeline exists yet. Second read: this head's own,
        # matching pipeline.
        ci_shas=["deadbeefdeadbeef", head_sha],
        ci_pipeline_refs=["111", "222"],
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
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            results = []
            for sid in ("s1", "s2"):
                results.append(
                    await forge.run_task(
                        database,
                        rd,
                        session_id=sid,
                        work_item_id="w1",
                        node_id="post_merge_watch",
                        hook_point="on.merge.watch",
                        handler="merge_watch",
                        backend="fake",
                        repo=repo,
                        orig_repo=repo,
                        branch="kraft/w1",
                        title="t",
                    )
                )
            stored = database.read(
                lambda c: c.execute(
                    "SELECT ci_pipeline_ref FROM work_items WHERE id = 'w1'"
                ).fetchone()
            )
            return results, stored["ci_pipeline_ref"]
        finally:
            await database.close()

    results, stored_ref = asyncio.run(scenario())
    # The wrong-sha read (state "success", but not this head) is caught by
    # `render_ci`'s own sha guard -- neither entry sees a false "done" -- but
    # only the first entry's read is wrong-sha; that one must never be pinned.
    assert results == ["waiting", "done"]
    assert stored_ref == f"{head_sha}:222"


def test_merge_watch_caps_infra_retries_across_separate_entries_then_stops(tmp_path, monkeypatch):
    """Plan-review finding 3, first half: `retry_infra_once` only starts the
    job again, so a persistently infra-red target-branch pipeline must not
    get kicked once per `ci_wait` re-entry forever. Same idiom, same
    sequence, as `ci_poll`'s own
    `test_ci_poll_retries_infra_red_across_separate_entries_then_stops_with_the_reason`
    (`tests/test_forge_run.py:1333`) -- three separate calls standing in for
    three real ~30s-apart re-entries; each entry's own post-kick re-read
    sees "pending" (`"waiting"`), exactly like a real forge would the
    instant after `retry_jobs` returns."""
    repo = make_repo(tmp_path)
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
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            results = []
            for sid in ("s1", "s2", "s3"):
                results.append(
                    await forge.run_task(
                        database,
                        rd,
                        session_id=sid,
                        work_item_id="w1",
                        node_id="post_merge_watch",
                        hook_point="on.merge.watch",
                        handler="merge_watch",
                        backend="fake",
                        repo=repo,
                        orig_repo=repo,
                        branch="kraft/w1",
                        title="t",
                    )
                )
            evts = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
            return results, evts
        finally:
            await database.close()

    (first, second, third), evts = asyncio.run(scenario())
    assert first == "waiting"
    assert second == "waiting"
    assert third == "infra_stop"
    # Two kicks (entries one and two); the third entry's count (3) breaches
    # the cap before a third kick is made -- no follow-up bead, this is a
    # forge/runner problem, not this item's own code.
    assert len(fake.retried) == 2
    assert "ci_infra_exhausted" in evts


def test_merge_watch_gives_up_watching_a_pipeline_that_never_settles(tmp_path, monkeypatch):
    """Plan-review finding 3, second half: `ci_wait.py`'s shared cap
    (1800s/60 attempts) has no notion of which handler is behind a waiting
    node, so left alone a merely-slow (never infra, never red) target-branch
    pipeline would eventually turn into `needs_human` after this item's own
    work is already merged, and -- since `post_merge_watch` is the chain's
    terminal node -- its tracking bead would never close either.
    `_POST_MERGE_WAIT_CAP` bounds this node's own patience first, and reports
    "done" rather than paging anyone."""
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(ci_states=["pending"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            results = []
            # One more entry than `_POST_MERGE_WAIT_CAP` (40): the cap
            # breaches on attempt 41, well before any wall-clock check could.
            for i in range(41):
                results.append(
                    await forge.run_task(
                        database,
                        rd,
                        session_id=f"s{i}",
                        work_item_id="w1",
                        node_id="post_merge_watch",
                        hook_point="on.merge.watch",
                        handler="merge_watch",
                        backend="fake",
                        repo=repo,
                        orig_repo=repo,
                        branch="kraft/w1",
                        title="t",
                    )
                )
            return results
        finally:
            await database.close()

    results = asyncio.run(scenario())
    assert results[:-1] == ["waiting"] * 40
    assert results[-1] == "done"


def test_merge_watch_reuses_the_session_across_a_still_pending_wait(tmp_path, monkeypatch):
    """Same reuse `ci_poll` already relies on (Kraft-41b/Kraft-7xt): two
    re-entries while the target branch's pipeline is still pending must
    leave exactly one `worker_sessions` row, not one per poll."""
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(ci_states=["pending", "pending"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            for sid in ("s1", "s2"):
                await forge.run_task(
                    database,
                    rd,
                    session_id=sid,
                    work_item_id="w1",
                    node_id="post_merge_watch",
                    hook_point="on.merge.watch",
                    handler="merge_watch",
                    backend="fake",
                    repo=repo,
                    orig_repo=repo,
                    branch="kraft/w1",
                    title="t",
                )
            return database.read(
                lambda c: c.execute(
                    "SELECT id, attempt, status FROM worker_sessions WHERE work_item_id='w1'"
                ).fetchall()
            )
        finally:
            await database.close()

    rows = asyncio.run(scenario())
    assert len(rows) == 1
    assert rows[0]["id"] == "s1"
    assert rows[0]["status"] == "waiting"


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
        async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
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


def _back_half_template(**ci_task):
    """The three forge nodes in chain order. The front half (spec, plan,
    implementation) is what the e2e suite covers with a real agent; what was
    noop until now is everything after verify. No `env_setup` node: V1
    prepares the worktree before the first node."""
    return v1_resolved(
        [
            _forge_node("open_mr", "mr.open_draft"),
            _forge_node("mr_checks", "mr.ci", **ci_task),
            _forge_node("merge", "mr.merge"),
        ]
    )


def _run_back_half(tmp_path, monkeypatch, fake, *, launch=None, **ci_task):
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
                chain=_back_half_template(**ci_task),
                bd_cwd=str(tracker),
            )
            await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=launch or executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
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
    the task's `wait:` still has to reach `run_task` at all, which this pins.
    V1: the poll keys come from the task's own `wait`, not a registry binding,
    and are read off the adapter call rather than inferred from the status."""
    fake = forge.FakeForge(ci_states=["pending", "success"])
    seen: list[dict] = []
    real = forge.run_task

    async def spy(*a, **kw):
        seen.append(kw)
        return await real(*a, **kw)

    monkeypatch.setattr("kraft.executor.dispatch._forge.run_task", spy)

    status = _run_back_half(
        tmp_path,
        monkeypatch,
        fake,
        wait={"timeout": "5m", "polling": {"initial_interval": "30s"}},
    )

    assert fake.opened, "the merge request should still have been opened"
    assert fake.merged == [], "a pipeline still pending must not reach the merge node"
    assert status == "waiting"
    ci = next(kw for kw in seen if kw["handler"] == "ci_poll")
    assert (ci["poll_timeout"], ci["poll_interval"]) == (300.0, 30.0)


def test_the_executor_passes_the_repo_forge_to_a_forge_node(tmp_path, monkeypatch):
    """Companion to test_the_executor_forwards_the_registry_poll_keys: one more
    argument in `_dispatch`'s forge branch, and without it every node of a
    `backend: auto` chain fails on a repo whose forge is recorded perfectly well.
    """
    fake = forge.FakeForge(ci_states=["success"])
    launch = executor.LaunchContext(
        repo_entry={"forge": "gitlab", "setup_command": ""}, steering_dir=None
    )

    _run_back_half(tmp_path, monkeypatch, fake, launch=launch)

    assert fake.opened, "open_mr did not run: the repo's forge never reached the node"
    assert fake.merged == [1], "the chain did not reach merge"


def test_a_forge_node_fails_when_auto_has_no_repo_entry(tmp_path, monkeypatch):
    """The other half: `launch=None` is a repo Kraft holds no entry for, and
    `auto` must fail the node rather than guess a CLI."""
    fake = forge.FakeForge(ci_states=["success"])
    launch = executor.LaunchContext(repo_entry=NO_SETUP, steering_dir=None)

    status = _run_back_half(tmp_path, monkeypatch, fake, launch=launch)

    assert not fake.opened, "a merge request was opened with no forge resolved"
    assert status == "needs_human"


def test_forge_nodes_run_in_the_worktree_not_the_repo(tmp_path, monkeypatch):
    """`glab mr create --fill` uses the *current branch* of its cwd. Run from
    the main repo, that is whatever the human has checked out — not
    kraft/<id> — so the MR would be opened from the wrong branch."""
    seen: list = []

    class RecordingForge(forge.FakeForge):
        async def open_mr(self, *, repo, branch, title, body, meta=None):
            seen.append(repo)
            return await super().open_mr(
                repo=repo, branch=branch, title=title, body=body, meta=meta
            )

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


def test_with_no_meta_the_body_is_exactly_todays(tmp_path):
    """Spec §5's 'done when': a missing artifact must produce today's body,
    unchanged down to the byte — the one path `open_mr` must never fail on."""
    with_default = forge.mr_body("af0fb78e", "kraft/af0fb78e", ("the work",), forge.MRMeta())
    without_the_arg = forge.mr_body("af0fb78e", "kraft/af0fb78e", ("the work",))
    assert with_default == without_the_arg


def test_the_commit_list_is_demoted_below_the_description():
    body = forge.mr_body(
        "af0fb78e", "kraft/af0fb78e", ("the work",), forge.MRMeta(description="did the thing")
    )
    assert body.index("did the thing") < body.index("<details>")
    assert "- the work" in body
    assert "Branch `kraft/af0fb78e`" in body


def test_a_body_over_the_cap_is_truncated_with_a_visible_marker():
    huge = "x" * (forge.MR_BODY_MAX_CHARS + 5_000)
    body = forge.mr_body("af0fb78e", "kraft/af0fb78e", (), forge.MRMeta(description=huge))
    assert len(body) <= forge.MR_BODY_MAX_CHARS
    assert "truncated" in body


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


def test_open_mr_uses_the_authored_title_labels_and_description(tmp_path, monkeypatch):
    """`open_mr` reads `.engineering/mr_metas/<wid>.md` out of the item's own
    worktree and hands its title, labels and description through to the
    forge -- a reviewer sees what the agent decided, not a stitched diary."""
    fake = forge.FakeForge()
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
            meta_dir = tmp_path / ".engineering" / "mr_metas"
            meta_dir.mkdir(parents=True)
            (meta_dir / "w1.md").write_text(
                "---\nwork_item_ids: [w1]\ntitle: Authored title\n"
                "labels: [release::minor]\n---\n## What this introduces\n\nA door.\n"
            )
            await forge.run_task(
                database,
                rd,
                session_id="x7",
                work_item_id="w1",
                node_id="mr_checks",
                hook_point="on.ci.poll",
                handler="open_mr",
                backend="fake",
                repo=tmp_path,
                branch="kraft/w1",
                title="t",
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    (number,) = fake.opened
    assert fake.opened_titles[number] == "Authored title"
    assert fake.opened_meta[number].labels == ("release::minor",)
    assert fake.opened_bodies[number].startswith("## What this introduces")


def test_open_mr_without_the_artifact_opens_the_default_body(tmp_path, monkeypatch):
    """Spec §5: no artifact at all -- an install that never ran `on.mr.describe`,
    or one that failed to write it -- must open exactly today's MR, not fail
    the node."""
    fake = forge.FakeForge()
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
                session_id="x8",
                work_item_id="w1",
                node_id="mr_checks",
                hook_point="on.ci.poll",
                handler="open_mr",
                backend="fake",
                repo=tmp_path,
                branch="kraft/w1",
                title="the work item title",
            )
            return returned
        finally:
            await database.close()

    returned = asyncio.run(scenario())

    assert returned == "done"
    (number,) = fake.opened
    assert fake.opened_titles[number] == "the work item title"
    assert fake.opened_meta[number] == forge.MRMeta()
    assert fake.opened_bodies[number] == forge.mr_body("w1", "kraft/w1", ())


def test_sync_mr_republishes_the_authored_description(tmp_path, monkeypatch):
    fake = forge.FakeForge()
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))
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
            meta_dir = tmp_path / ".engineering" / "mr_metas"
            meta_dir.mkdir(parents=True)
            (meta_dir / "w1.md").write_text(
                "---\nwork_item_ids: [w1]\n---\n## What this introduces\n\nA door.\n"
            )
            await forge.run_task(
                database,
                rd,
                session_id="x9",
                work_item_id="w1",
                node_id="mr_checks",
                hook_point="on.ci.poll",
                handler="sync_mr",
                backend="fake",
                repo=tmp_path,
                branch="kraft/w1",
                title="t",
            )
        finally:
            await database.close()

    asyncio.run(scenario())

    assert fake.bodies["kraft/w1"].startswith("## What this introduces")


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

        async def ci_status(self, *, repo, mr, branch="", pipeline_id=""):
            order.append("ci")
            return await super().ci_status(repo=repo, mr=mr, branch=branch, pipeline_id=pipeline_id)

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
    answer, not something `forge.merge()` should ever be asked to resolve.

    Status is "conflict", not a blanket "failed" -- Task 5 threads the real
    status through instead of collapsing it (draft-MR workflow spec), same
    as `ci_poll` already reports for the identical unresolved-conflict case
    (`test_ci_poll_fails_when_the_mr_cannot_be_merged`)."""
    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "g2", poll_interval=0)

    assert (returned, recorded) == ("conflict", "conflict")
    assert "not mergeable: conflict" in _session_log(tmp_path, "g2")
    assert fake.merged == [], "merge must not be called against an unmergeable head"


def test_merge_rebases_a_conflict_away_before_calling_merge(tmp_path, monkeypatch):
    """The same rebase-and-bounce `mr_checks` already has for a conflict
    (Kraft-9h7v), now on `merge` too: `mr_sync`'s push after `human_review`
    can turn up a conflict only `merge` ever sees (draft-MR workflow
    spec). Against a real repo, a forced rebase onto an unmoved default
    branch is a no-op fast-forward -- `new_head` comes back falsy, so the
    node still reports the pre-existing conflict rather than a fabricated
    'done'."""
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(ci_states=["success"], mergeable=False, merge_detail="conflict")
    asyncio.run(fake.open_mr(repo=repo, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(repo, monkeypatch, fake, "merge", "r1", poll_interval=0)

    assert (returned, recorded) == ("conflict", "conflict")
    assert fake.merged == [], "an unresolved conflict must never reach forge.merge"


def test_merge_completes_the_merge_after_a_rebase_when_no_bounce_is_configured(
    tmp_path, monkeypatch
):
    """The forced rebase at `merge` only reports a bare "done" -- without
    calling `forge.merge` -- when *this node's own* `chain_definition` entry
    carries `rebase_bounce_to`, so a later re-verify at `verify` is
    guaranteed before anything lands
    (`test_merge_rebases_a_conflict_away_before_calling_merge` covers that
    guaranteed case). A chain frozen before that field existed, or an
    installed `templates/default.yaml` seeded before it shipped, has no such
    node -- nothing would ever call `forge.merge` for it, and the walk would
    sail on to `post_merge_watch`/`mark_completed`/`close_beads` with the
    branch never merged (code-review). This one must still call
    `forge.merge` once the rebased head is confirmed green."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    _gitignore_engineering(repo)

    push_calls: list[str] = []

    class ResolvesAfterRebase(forge.FakeForge):
        async def push(self, *, repo, branch):
            push_calls.append(branch)
            await super().push(repo=repo, branch=branch)
            if len(push_calls) >= 2:
                # The conflict-rebase's own push is the second one -- a real
                # forge would see the rebased head as mergeable again by the
                # time anything re-reads it; flip the fake the same way.
                self.mergeable = True

    fake = ResolvesAfterRebase(ci_states=["success"], mergeable=False, merge_detail="conflict")
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    # V1 has no `rebase_bounce_to` to configure at all -- the point of this test.
    template = v1_resolved(
        [_forge_node("open_mr", "mr.open_draft"), _forge_node("merge", "mr.merge")]
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="merge without a bounce",
                repo=str(repo),
                chain=template,
                bd_cwd=str(tracker),
            )
            await _builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
            )

            # Origin moves in a way the branch does not touch, so the forced
            # rebase this triggers is clean.
            (repo / "moved.txt").write_text("moved on\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "moved on upstream"], cwd=repo, check=True)

            return await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
            )
        finally:
            await database.close()

    result = asyncio.run(scenario())

    assert result == "completed"
    assert fake.merged == [1], "a rebase with no configured bounce must still land the merge"


def test_merge_does_not_treat_a_rebased_submodule_as_landed_in_a_multi_repo_item(
    tmp_path, monkeypatch
):
    """code-review: `_run_one`'s conflict-rebase shortcut reports "rebased",
    not "merged", for a target it only rebased and never actually merged
    (guaranteed a re-verify via `has_rebase_bounce`). `run_task`'s per-target
    loop must stop right there -- not record `merge_state='merged'` for that
    row, not walk on to later targets, and not run the root's own no-MR
    pointer bump (which, unpatched, would push root pointing at a submodule
    branch nothing has actually merged -- and here would also crash, since
    neither repo has an `origin` remote to push to)."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    root, _sub = make_repo_with_submodule(tmp_path)

    calls: list[Path] = []

    async def fake_run_one(*args, **kwargs):
        calls.append(kwargs["repo"])
        return "rebased away, not merged\n", "rebased", None

    monkeypatch.setattr(forge.run, "_run_one", fake_run_one)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(root),
                chain=_back_half_template(),
                bd_cwd=str(tracker),
                submodules=["repos/pkg"],
                root_merge_policy="bump",
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id=wid, repo_entry=NO_SETUP
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            branch = store.branch_for(row)
            status = await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id=wid,
                node_id="merge",
                hook_point="on.merge",
                handler="merge",
                backend="fake",
                repo=worktree,
                branch=branch,
                title="t",
                has_rebase_bounce=True,
            )
            repos = database.read(lambda c: store.repos_for(c, wid))
            return status, repos
        finally:
            await database.close()

    status, repos = asyncio.run(scenario())

    assert status == "done", "a guaranteed bounce still reports 'done' to the walk"
    assert len(calls) == 1, "must stop right after the rebased target, never reach root"
    assert all(r["state"] != "merged" for r in repos), "a rebased target is not a merged one"
    assert fake.merged == [], "an unmerged target must never let the root land its pointer bump"


def test_ci_poll_conflict_handling_is_unchanged_by_the_extraction(tmp_path, monkeypatch):
    """Task 5 only moves this code into a shared helper -- it must not
    change ci_poll's own behavior. Same case `test_ci_poll_still_resolves_
    a_settled_pipeline` already covers; kept here as a named regression
    for the extraction itself."""
    fake = forge.FakeForge(ci_states=["success"], mergeable=False)
    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "ci_poll", "r2")
    assert (returned, recorded) == ("conflict", "conflict")


def test_merge_fails_fast_on_a_missing_approval_without_calling_merge(tmp_path, monkeypatch):
    """A green, non-conflicting pipeline can still be missing a required
    approval -- `mergeable` alone reads this as undecided (mr_checks must
    not fail pre-gate on it, per test_ci_poll_does_not_fail_on_an_
    undecided_merge_state), so it survives unnoticed all the way to
    merge. One more read, now that the gate is supposed to be done, is
    what catches it before forge.merge() ever runs (draft-MR workflow
    spec)."""
    fake = forge.FakeForge(ci_states=["success"], block_reason="not_approved")
    asyncio.run(fake.open_mr(repo=tmp_path, branch="kraft/w1", title="t", body="b"))

    returned, recorded = _forge_session(tmp_path, monkeypatch, fake, "merge", "n1", poll_interval=0)

    assert (returned, recorded) == ("failed", "failed")
    assert "needs approval" in _session_log(tmp_path, "n1")
    assert fake.merged == [], "merge must not be called against an unapproved head"


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
                chain=_back_half_template(),
                bd_cwd=str(tracker),
                submodules=["repos/pkg"],
                root_merge_policy="bump",
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id=wid, repo_entry=NO_SETUP
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
                chain=_back_half_template(),
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
                chain=_back_half_template(),
                bd_cwd=str(tracker),
                submodules=["repos/pkg"],
                root_merge_policy="bump_no_mr",
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id=wid, repo_entry=NO_SETUP
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
                chain=_back_half_template(),
                bd_cwd=str(tracker),
                submodules=["repos/packages"],
                root_merge_policy="skip",
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(root), work_item_id=wid, repo_entry=NO_SETUP
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
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
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


def _ci_fixloop_template():
    """`mr_checks` with both `fix_loop` and `on_failure` (Task 6/7's shape),
    isolated from open_mr/merge so this test only exercises the repair's own
    re-measure. The repair is a metadata-only stand-in (`true`): what matters
    is that it "succeeds" and touches nothing the CI wait reads. The fix
    loop's own task is never dispatched here."""
    node = _forge_node("mr_checks", "mr.ci")
    node["on_failure"] = {"tasks": [{"id": "repair", "kind": "subprocess", "command": "true"}]}
    node["fix_loop"] = {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]}
    return v1_resolved([node])


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
        "loops:\n  mr_checks.fix_loop: { attempts: 3, wall_clock_s: 3600 }\n"
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
                chain=_ci_fixloop_template(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
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


def _mr_checks_template():
    """`verify` then `mr_checks`. The legacy version gave `mr_checks` a
    `rebase_bounce_to: verify`; V1 has none (the restart span a base change
    re-enters is Task 7's), so the confirmed-conflict bounce test is gone and
    this one pins only the real-conflict stop."""
    return v1_resolved(
        [
            {
                "id": "verify",
                "kind": "exec",
                "tasks": [{"id": "suite", "kind": "subprocess", "command": "true"}],
            },
            _forge_node("mr_checks", "mr.ci"),
        ]
    )


def _gitignore_engineering(repo):
    """`refresh_worktree_base`'s dirty check has no way to know
    `.engineering/sessions/*.md` is Kraft's own bookkeeping rather than an
    agent's leftover work -- same fixture step
    `test_a_moved_base_bounces_back_to_verify_with_a_drift_note` uses."""
    (repo / ".gitignore").write_text(".engineering/\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "gitignore .engineering"], cwd=repo, check=True)


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
                chain=_mr_checks_template(),
                bd_cwd=str(tracker),
            )
            worktree = await _builtins.ensure_worktree(
                database, rd, repo=str(repo), work_item_id=wid, repo_entry=NO_SETUP
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
                registry=None,
                bd_cwd=str(tracker),
                policy=pol,
                launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
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
                    "AND node_id = 'mr_checks' AND hook_point = 'mr_checks.main.mr_checks' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (wid,),
                ).fetchone()
            )
            log_text = Path(session_row["log_path"]).read_text()
            assert "rebase" in log_text.lower() and "failed" in log_text.lower()
        finally:
            await database.close()

    asyncio.run(scenario())


def _bd_status(repo, bead_id):
    out = subprocess.run(
        ["bd", "show", bead_id, "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)[0]["status"]


def test_post_merge_watch_delays_completion_and_bead_close_until_it_runs(tmp_path, monkeypatch):
    """Kraft-43kw: open_mr -> mr_checks -> merge -> post_merge_watch, driven
    through a real `executor.run` walk. `work_item_completed` and the bead
    close must land after `post_merge_watch`'s own `node_completed`, not
    after `merge`'s."""
    fake = forge.FakeForge(ci_states=["success"])
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    template = v1_resolved(
        [
            _forge_node("open_mr", "mr.open_draft"),
            _forge_node("mr_checks", "mr.ci"),
            _forge_node("merge", "mr.merge"),
            _forge_node("post_merge_watch", "mr.post_merge_ci"),
        ]
    )

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            wid = await executor.intake(
                database,
                rd,
                title="watch after merge",
                repo=str(repo),
                chain=template,
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database,
                rd,
                work_item_id=wid,
                registry=None,
                bd_cwd=str(tracker),
                launch=executor.LaunchContext(repo_entry=ON_A_FORGE, steering_dir=None),
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, bead_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            evts = database.read(lambda c: events.read_after(c, 0, wid))
            return result, row["status"], row["bead_id"], evts
        finally:
            await database.close()

    result, status, bead_id, evts = asyncio.run(scenario())
    assert result == "completed"
    assert status == "completed"
    node_order = [e["payload"]["node_id"] for e in evts if e["type"] == "node_completed"]
    assert node_order == ["open_mr", "mr_checks", "merge", "post_merge_watch"]
    assert evts[-1]["type"] == "work_item_completed"
    assert _bd_status(tracker, bead_id) == "closed"


def test_merge_watch_runs_once_for_a_multi_repo_item(tmp_path, monkeypatch):
    """`merge_watch` ignores the per-target repo -- it reads `orig_repo`'s own
    default branch. Without collapsing `targets`, a multi-repo item would poll
    that one branch once per submodule and file that many identical follow-up
    beads for a single break."""
    repo = make_repo(tmp_path)
    fake = forge.FakeForge(ci_states=["success"])
    reads: list[str] = []
    original = fake.branch_ci_status

    async def counting(**kwargs):
        reads.append(kwargs["branch"])
        return await original(**kwargs)

    monkeypatch.setattr(fake, "branch_ci_status", counting)
    monkeypatch.setattr(forge.run, "resolve", lambda name: fake)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','t',?,'default','{}','active','now','now')",
                    (str(repo),),
                )
            )
            for path, role, rank in (
                (repo / "a", "submodule", 1),
                (repo / "b", "submodule", 2),
                (repo, "root", 3),
            ):
                await database.write(
                    lambda c, p=path, r=role, k=rank: c.execute(
                        "INSERT INTO work_item_repos (work_item_id, repo_path, role, "
                        "submodule_path, merge_rank, created_at, updated_at) VALUES "
                        "('w1', ?, ?, ?, ?, 'now', 'now')",
                        (str(p), r, p.name if r == "submodule" else None, k),
                    )
                )
            return await forge.run_task(
                database,
                rd,
                session_id="s1",
                work_item_id="w1",
                node_id="post_merge_watch",
                hook_point="on.merge.watch",
                handler="merge_watch",
                backend="fake",
                repo=repo,
                orig_repo=repo,
                branch="kraft/w1",
                title="t",
            )
        finally:
            await database.close()

    assert asyncio.run(scenario()) == "done"
    assert len(reads) == 1, reads


def test_a_declared_but_unimplemented_forge_target_stops_for_a_human(tmp_path, monkeypatch):
    """Ruling 48. `mr.automated_review` and `mr.external_approval` are
    `ForgeAction` members with no handler, and the seeded V1 chain names both --
    so `failed` told an operator their pipeline had broken and burned the node's
    fix loop finding out. A declared-but-unimplemented action is a configuration
    limit: `config_error`, which is terminal at every tier, so `walk_node` stops
    for a person with the target and its owning task named.

    (Retargeted from `mr.mark_ready` when Task 5a implemented that one -- the
    assertion is about the *unimplemented* arm, so it has to name a target that
    is still unimplemented or it stops testing anything.)
    """
    fake = forge.FakeForge()
    returned, recorded = _forge_session(
        tmp_path, monkeypatch, fake, forge.run.handler_for("mr.automated_review"), "s-unimpl"
    )
    assert returned == "config_error"
    assert recorded == "config_error"
    log = (RunDirs(tmp_path / "run").logs / "s-unimpl.log").read_text()
    assert "mr.automated_review" in log and "Task 9" in log


def test_every_v1_forge_target_either_maps_or_names_its_owner():
    """The two tables must not drift: a target with neither a handler nor an
    owner would reach the stop above with "a later task" and tell the human
    nothing."""
    from kraft.templates.models import ForgeAction

    for action in ForgeAction:
        assert (
            action.value in forge.run.V1_HANDLERS
            or action.value in forge.run._UNIMPLEMENTED_TARGETS
        ), action
