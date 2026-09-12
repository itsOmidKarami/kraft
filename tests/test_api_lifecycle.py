"""The happy path end to end, and abandon/resume/retry."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from support.api import _client, _force_node, _poll_events, _post_default, _set_status
from support.harness import make_repo


def test_happy_path_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "quick-task",
            },
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")

        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "completed"
        assert len(item["worker_sessions"]) == 3

        run_dir = Path(client.app.state.run_dirs.base)
        assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()

        # 4B: each agent session's summary is ingested and linked to the work item
        impl_session = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
        assert impl_session["session_summary_ref"] == (
            f".engineering/sessions/{impl_session['id']}.md"
        )
        for _ in range(100):
            docs = client.get(f"/api/work-items/{wid}/documents").json()["documents"]
            if docs:
                break
            time.sleep(0.05)
        summaries = [d for d in docs if d["source_kind"] == "session_summary"]
        assert summaries, f"no session summary linked to {wid}"
        assert {d["path"] for d in summaries} >= {impl_session["session_summary_ref"]}
        assert any(d["worker_session_id"] == impl_session["id"] for d in docs)

        # and the summary is searchable, carrying its links inline
        hits = client.get("/api/search", params={"q": "fake-claude session"}).json()["results"]
        assert hits, "session summary not searchable"
        assert any(
            ln["work_item_id"] == wid for h in hits for ln in h["links"] if ln["work_item_id"]
        )

        # log endpoint returns the agent's stdout
        impl = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
        log = client.get(f"/api/worker-sessions/{impl['id']}/log")
        assert log.status_code == 200
        assert "is_error" in log.text


def test_executor_crash_marks_needs_human(tmp_path, monkeypatch):
    """A non-task exception in the spawned run task must not wedge the item in 'active'."""
    repo = make_repo(tmp_path)
    from kraft import executor

    async def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(executor, "run", boom)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post("/api/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.get(f"/api/work-items/{wid}").json()["status"]
            if status == "needs_human":
                break
            time.sleep(0.2)
        assert status == "needs_human"


def test_retry_refuses_explicit_steer_on_a_node_with_no_agent_task(tmp_path, monkeypatch):
    """Kraft-bz9b: `merge` is forge-kind with no fix_loop and is the chain's
    last node, so nothing downstream of it ever calls `Steer.take()`.
    `--steer` used to be accepted and echoed back as if it would reach the
    next launch, when it was silently dropped. (`open_mr`, this test's node
    before Kraft-cbr, no longer qualifies: `mr_checks` right after it now
    carries `fix_loop`, so a steer given at `open_mr` could reach that.)"""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "merge", "needs_human")

        r = client.post(f"/api/work-items/{wid}/retry", json={"steer": "commit the leftover file"})

        assert r.status_code == 409, r.text
        assert "merge" in r.json()["detail"]


def test_retry_without_explicit_steer_still_works_on_a_node_with_no_agent_task(
    tmp_path, monkeypatch
):
    """The guard is for text a caller just typed and expects used, not for
    Kraft's own last-rejection carry-forward (Kraft-ko7j) -- that must keep
    working even on a node with nothing to steer."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "merge", "needs_human")

        r = client.post(f"/api/work-items/{wid}/retry", json={})

        assert r.status_code == 200, r.text


def test_resume_refuses_when_all_slots_are_busy(tmp_path, monkeypatch):
    """A manual start is bounded by the same limit as auto-intake (Kraft-n2d).

    The limit lived only in the intake tick, so `resume` started an item no
    matter how many were already running.
    """
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        busy = _post_default(client, repo)
        _poll_events(client, busy, "gate_requested")
        idle = _post_default(client, repo)
        _poll_events(client, idle, "gate_requested")
        _set_status(busy, "active")
        _set_status(idle, "paused")
        client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)

        r = client.post(f"/api/work-items/{idle}/resume", json={})

        assert r.status_code == 409, r.text
        assert "1" in r.json()["detail"]
        assert client.get(f"/api/work-items/{busy}").json()["status"] == "active"


def test_resume_works_when_a_slot_is_free(tmp_path, monkeypatch):
    """The guard must not wedge the ordinary single-item case shut."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")
        client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)

        r = client.post(f"/api/work-items/{wid}/resume", json={})

        assert r.status_code == 200, r.text


def test_retry_refuses_when_all_slots_are_busy(tmp_path, monkeypatch):
    """The same door resume is bounded by (notes 10): a stopped item's
    /retry must not restart it past the cap either."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        busy = _post_default(client, repo)
        _poll_events(client, busy, "gate_requested")
        stopped = _post_default(client, repo)
        _poll_events(client, stopped, "gate_requested")
        _set_status(busy, "active")
        _force_node(stopped, "verify", "needs_human")
        client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)

        r = client.post(f"/api/work-items/{stopped}/retry", json={})

        assert r.status_code == 409, r.text
        assert "1" in r.json()["detail"]


def test_retry_works_when_a_slot_is_free(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "verify", "needs_human")
        client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)

        r = client.post(f"/api/work-items/{wid}/retry", json={})

        assert r.status_code == 200, r.text


def _create_escalation_session(
    wid: str, session_id: str, node_id: str, *, pid: int | None = None
) -> None:
    """A live `worker_sessions` row for `wid` (`hook_point='escalation'`,
    `status='running'`) -- the shape `retry`'s self-retry check reads,
    without actually dispatching an agent."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
            "log_path, result_path, status, created_at) VALUES (?, ?, ?, 'escalation', ?, "
            "'x', 'x', 'running', datetime('now'))",
            (session_id, wid, node_id, pid),
        )
        conn.commit()
    finally:
        conn.close()


def test_retry_defers_instead_of_racing_its_own_still_running_escalation_session(
    tmp_path, monkeypatch
):
    """The escalation agent calling `kraft item retry` on itself must not run
    the rebase/spawn inline: its own session is still `running` (it is
    mid-tool-call, blocked on this very response), so racing a `git rebase`
    and a fresh spawn into the worktree it is still live in is exactly the
    collision `escalation_running` exists to prevent everywhere else."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "verify", "needs_human")
        _create_escalation_session(wid, "s1", "verify")

        r = client.post(
            f"/api/work-items/{wid}/retry",
            json={},
            headers={"x-kraft-session-id": "s1"},
        )

        assert r.status_code == 200, r.text
        # Deferred, not run: the item is still parked at needs_human, and no
        # `work_item_retried` (the rebase+spawn path) has fired yet.
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
        events_seen = client.get(f"/api/work-items/{wid}/events").json()
        types = [e["type"] for e in events_seen]
        assert "work_item_self_retry_requested" in types
        assert "work_item_retried" not in types


def test_retry_kills_a_strangers_running_escalation_and_proceeds(tmp_path, monkeypatch):
    """A caller that is *not* the live escalation session (no header, or a
    different one) now gets through: that session is killed first, then the
    retry proceeds the same way it would against a plain needs_human stop --
    distinct from the self-retry deferral path above, which stays a defer,
    not a kill."""
    repo = make_repo(tmp_path)
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "verify", "needs_human")
        _create_escalation_session(wid, "s1", "verify", pid=4242)

        r = client.post(f"/api/work-items/{wid}/retry", json={})

        assert r.status_code == 200, r.text
        assert terminated == [4242]
        types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
        assert "work_item_retried" in types
        # The turn is stopped, not the item: `retry` claims the item out of
        # `needs_human` itself a few lines later, which a `pause_requested`
        # (status -> paused) would have made impossible.
        assert "worker_session_paused" in types
        assert "pause_requested" not in types


def test_retry_refuses_and_writes_nothing_when_a_walk_is_still_live(tmp_path, monkeypatch):
    """A `needs_human` item can still have a live walk task behind it -- a
    pending gate under auto_escalate review, or the brief window while the
    walk that just called request_gate/mark_needs_human is still unwinding.
    `/retry` must refuse before it claims and rebases, not claim, rebase, and
    clear the fix-loop cap only for `spawn` to 409 on top of those writes."""
    from kraft.api import deps

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "verify", "needs_human")

        async def _never_returning():
            await asyncio.Event().wait()

        async def inject():
            deps.spawn(client.app, wid, _never_returning())

        client.portal.call(inject)

        r = client.post(f"/api/work-items/{wid}/retry", json={})

        assert r.status_code == 409, r.text
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"

        async def cleanup():
            task = client.app.state.tasks.pop(wid)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        client.portal.call(cleanup)


def test_resume_refuses_and_writes_nothing_when_a_walk_is_still_live(tmp_path, monkeypatch):
    """Same race as retry, from `paused`: `/resume` must not claim, rebase,
    and `resume_work_item` an item that still has a live walk task behind it."""
    from kraft.api import deps

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")

        async def _never_returning():
            await asyncio.Event().wait()

        async def inject():
            deps.spawn(client.app, wid, _never_returning())

        client.portal.call(inject)

        r = client.post(f"/api/work-items/{wid}/resume", json={})

        assert r.status_code == 409, r.text
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "paused"

        async def cleanup():
            task = client.app.state.tasks.pop(wid)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        client.portal.call(cleanup)


def test_abandon_sets_terminal_status_and_removes_the_worktree(tmp_path, monkeypatch):
    """A rejected or dead item stayed on the board forever, worktree and all."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
        assert worktree.is_dir(), "fixture never made a worktree; the test would prove nothing"
        _set_status(wid, "paused")

        r = client.post(f"/api/work-items/{wid}/abandon")

        assert r.status_code == 200, r.text
        assert r.json()["status"] == "abandoned"
        assert not worktree.exists()


def test_abandon_reclaims_the_attachment_storage(tmp_path, monkeypatch):
    """The worktree is already reclaimed; the documents that fed it should not
    outlive it in $KRAFT_HOME."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        doc = repo / ".engineering" / "specs" / "s.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("# s\n")
        wid = client.post(
            "/api/work-items",
            json={
                "title": "x",
                "repo": str(repo),
                "attachments": [{"kind": "spec", "path": ".engineering/specs/s.md"}],
            },
        ).json()["id"]
        stored = client.app.state.run_dirs.attachments / wid
        assert stored.is_dir()
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "paused")

        client.post(f"/api/work-items/{wid}/abandon")

        assert not stored.exists()


def test_abandon_refuses_an_active_item(tmp_path, monkeypatch):
    """Pause first. Otherwise this races a running agent's writes."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _set_status(wid, "active")

        r = client.post(f"/api/work-items/{wid}/abandon")

        assert r.status_code == 409, r.text
        assert "active" in r.json()["detail"]


def test_abandon_kills_a_process_left_running_from_the_worktree(tmp_path, monkeypatch):
    """Kraft-ugm6: a server (or anything else) an agent started by hand from
    inside the worktree is invisible to `pause`'s session teardown and used to
    outlive the directory it was launched from."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
        orphan = subprocess.Popen(["sleep", "60"], cwd=worktree, start_new_session=True)
        try:
            _set_status(wid, "paused")

            r = client.post(f"/api/work-items/{wid}/abandon")

            assert r.status_code == 200, r.text
            for _ in range(50):
                if orphan.poll() is not None:
                    break
                time.sleep(0.1)
            assert orphan.poll() is not None, "orphan process outlived the abandon"
        finally:
            if orphan.poll() is None:
                orphan.kill()
            orphan.wait()


def test_post_triggers_files_a_paused_item(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post(
            "/api/triggers",
            json={"repo": str(repo), "title": "from a trigger", "description": "d"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "paused"
        assert body["title"] == "from a trigger"


def _seed_counter(wid: str, key: str, *, count: int = 2) -> None:
    """A retry_counters row, as if the node had already re-entered a wait or
    an infra retry a couple of times (Kraft-cs4s)."""
    import sqlite3

    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "INSERT INTO retry_counters (work_item_id, key, count, cap_attempts, "
            "cap_wall_s, started_at, updated_at) VALUES (?, ?, ?, 60, 1800, ?, ?)",
            (wid, key, count, "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _counter_exists(wid: str, key: str) -> bool:
    import sqlite3

    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        row = conn.execute(
            "SELECT 1 FROM retry_counters WHERE work_item_id = ? AND key = ?", (wid, key)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def test_retry_clears_the_ci_wait_counter_for_the_current_node(tmp_path, monkeypatch):
    """Seed a ci_wait:<node> counter (as if the item had re-entered a wait
    twice already), stop the item at needs_human, retry it, and assert the
    counter row is gone -- a fresh ci_wait poll after retry starts back at
    count 1, not 3."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "mr_checks", "needs_human")
        _seed_counter(wid, "ci_wait:mr_checks")

        r = client.post(f"/api/work-items/{wid}/retry", json={})

        assert r.status_code == 200, r.text
        assert not _counter_exists(wid, "ci_wait:mr_checks")


def test_retry_clears_the_ci_infra_counter_for_the_current_node(tmp_path, monkeypatch):
    """Same shape, for the persisted infra-retry counter: seed
    ci_infra:<node> at count 2 (one kick short of the cap), retry, and
    assert the counter row is gone -- the retried item's next infra-red
    poll gets a fresh budget, not an instant breach on its first one."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "mr_checks", "needs_human")
        _seed_counter(wid, "ci_infra:mr_checks")

        r = client.post(f"/api/work-items/{wid}/retry", json={})

        assert r.status_code == 200, r.text
        assert not _counter_exists(wid, "ci_infra:mr_checks")
