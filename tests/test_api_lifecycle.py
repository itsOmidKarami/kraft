"""The happy path end to end, and abandon/resume/retry."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from support.api import _await_gate, _client, _force_node, _poll_events, _post_default, _set_status
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
        # env_setup, implementation's own agent task, and verify's on.test.run.
        # C1's implementation-time on.test.run gate (Kraft-s7c04.8) was
        # reverted 2026-09-16 -- see test_executor_walk.py's
        # test_run_verify_failure_stops_at_verify.
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


def _post_past_the_still_finishing_walk(client, path, timeout=10, json=None):
    """Retry a POST past a transient 'a walk is already running' 409.

    `work_item_completed` lands on the timeline a few statements before the
    task carrying it actually returns (`asyncio.Task.done()`), so a test that
    polls for that event and then immediately posts to `/resume` or `/retry`
    can race `deps.task_is_live`'s check. Retry past it rather than add a
    sleep tuned to how long those last few statements take."""
    body = json if json is not None else {}
    deadline = time.monotonic() + timeout
    r = None
    while time.monotonic() < deadline:
        r = client.post(path, json=body)
        if r.status_code != 409 or "already running" not in r.json().get("detail", ""):
            return r
        time.sleep(0.1)
    return r


def test_resume_non_conflict_rebase_failure_auto_escalates_when_armed(tmp_path, monkeypatch):
    """A git failure that is NOT a conflict (`RebaseConflict` specifically) --
    still stops and escalates exactly as before Kraft-s7c04.23; only a
    conflict gets the new resolver path. Kraft-h48r: `resume_work_item`'s
    rebase-failure branch used to
    `mark_needs_human` and return without ever giving `auto_escalate_stuck`
    a chance to fire -- `walk.run`/`resuming.resume` call it after every
    step, and this route terminates before either of them runs.

    `quick-task` (no gate on any node) rather than the default chain: a
    `gate_requested` event left open by force-writing status past it reads
    to `gates.pending_gate` as a still-open gate, which makes
    `auto_escalate_stuck` no-op regardless of whether it is armed."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    import kraft.builtins as builtins_mod

    calls = []

    async def fail_refresh(*a, **kw):
        raise RuntimeError("rebase conflict: could not apply")

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")
        _set_status(wid, "paused")

        r = _post_past_the_still_finishing_walk(client, f"/api/work-items/{wid}/resume")

        assert r.status_code == 200, r.text
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
        assert calls == [(wid, True)]


def test_resume_rebase_conflict_does_not_escalate_when_disarmed(tmp_path, monkeypatch):
    """Regression guard: `auto_escalate_stuck: false` must still no-op here
    exactly like it does on the walk-driven path -- converted to raise
    `RebaseConflict` specifically (Kraft-s7c04.23 review finding: the old
    bare-`RuntimeError` version kept passing after this change landed while
    silently no longer covering the conflict path it is named for). The
    steer restore is unconditional and must still fire even disarmed."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    import kraft.builtins as builtins_mod

    calls = []

    async def fail_refresh(*a, **kw):
        raise builtins_mod.RebaseConflict("rebase conflict: could not apply")

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")
        # `implementation`, not wherever the walk left it: that node has an
        # agent task downstream to steer, unlike `verify`'s subprocess-only
        # tasks, which `_steer_reachable` refuses on principle -- a refusal
        # this test does not want to exercise.
        _force_node(wid, "implementation", "paused")
        client.app.state.policy = dataclasses.replace(
            client.app.state.policy, auto_escalate_stuck=False
        )

        r = _post_past_the_still_finishing_walk(
            client, f"/api/work-items/{wid}/resume", json={"steer": "watch the auth module"}
        )

        assert r.status_code == 200, r.text
        # The stop is now recorded inside the spawned conflict-resolution
        # task, not synchronously before the route returns (Kraft-s7c04.23
        # review finding: awaiting it inline blocked the response and hid it
        # from `task_is_live`, reopening Kraft-s7c04.20).
        _poll_events(client, wid, "work_item_needs_human")
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert calls == [], "disarmed must not dispatch the resolver or the escalation"
        assert item["pending_steer_context"] == "watch the auth module"


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


def test_retry_non_conflict_rebase_failure_auto_escalates_when_armed(tmp_path, monkeypatch):
    """A git failure that is NOT a conflict (`RebaseConflict` specifically) --
    still stops and escalates exactly as before Kraft-s7c04.23; only a
    conflict gets the new resolver path. Kraft-h48r: `retry_work_item`'s
    rebase-failure branch used to
    `mark_needs_human` and return without ever giving `auto_escalate_stuck`
    a chance to fire -- `walk.run`/`resuming.resume` call it after every
    step, and this route terminates before either of them runs."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    import kraft.builtins as builtins_mod

    calls = []

    async def fail_refresh(*a, **kw):
        raise RuntimeError("rebase conflict: could not apply")

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")
        _force_node(wid, "verify", "needs_human")

        r = _post_past_the_still_finishing_walk(client, f"/api/work-items/{wid}/retry")

        assert r.status_code == 200, r.text
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
        assert calls == [(wid, True)]


def test_retry_rebase_conflict_does_not_escalate_when_disarmed(tmp_path, monkeypatch):
    """Regression guard: `auto_escalate_stuck: false` must still no-op here
    exactly like it does on the walk-driven path -- converted to raise
    `RebaseConflict` specifically, the same review finding as its `/resume`
    sibling. `/retry` never persists a steer ahead of the rebase, so this is
    the first assertion that the helper writes it at all."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    import kraft.builtins as builtins_mod

    calls = []

    async def fail_refresh(*a, **kw):
        raise builtins_mod.RebaseConflict("rebase conflict: could not apply")

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"title": "x", "repo": str(repo), "chain_template": "quick-task"},
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")
        # `implementation`, not `verify`: that node has an agent task
        # downstream to steer, unlike `verify`'s subprocess-only tasks.
        _force_node(wid, "implementation", "needs_human")
        client.app.state.policy = dataclasses.replace(
            client.app.state.policy, auto_escalate_stuck=False
        )

        r = _post_past_the_still_finishing_walk(
            client, f"/api/work-items/{wid}/retry", json={"steer": "watch the auth module"}
        )

        assert r.status_code == 200, r.text
        # See the /resume sibling's comment: the stop is recorded inside the
        # spawned task now, not before the route returns.
        _poll_events(client, wid, "work_item_needs_human")
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert calls == [], "disarmed must not dispatch the resolver or the escalation"
        assert item["pending_steer_context"] == "watch the auth module"


def test_retry_rebase_conflict_restarts_at_its_own_node_when_before_verify(tmp_path, monkeypatch):
    """The `min()` assertion (design §4.6): an item stopped before `verify`
    (here, `plan`) must restart at its own node on a resolving verdict, not
    jump forward to `verify` -- a plain "jump to verify" would skip
    implementation entirely, and passes every other resolver test, which all
    stop at or after `verify`."""
    import kraft.builtins as builtins_mod
    from kraft.executor import walk

    starts = []

    async def fail_refresh(*a, **kw):
        raise builtins_mod.RebaseConflict("conflict")

    async def fake_resolve(*a, **kw):
        return "ok", None, "deadbeef"

    async def fake_run(database, run_dirs, *, work_item_id, registry, start_index=0, **kw):
        starts.append(start_index)
        return "completed"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr(walk, "resolve_rebase_conflict", fake_resolve)
    monkeypatch.setattr("kraft.api.routes.lifecycle.executor.run", fake_run)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _force_node(wid, "plan", "needs_human")

        r = _post_past_the_still_finishing_walk(client, f"/api/work-items/{wid}/retry")

        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 5
        while not starts and time.monotonic() < deadline:
            time.sleep(0.05)
    assert starts[-1] == 1, "restarted at verify's index (6) instead of plan's own (1)"


def test_retry_rebase_conflict_bounces_to_verify_and_clears_the_span(tmp_path, monkeypatch):
    """An item stopped *after* `verify` (here, `mr_checks`) restarts at
    `verify` on a resolving verdict -- and every node in that span has its
    loop counters cleared, the coupling with .25 the design calls out (§5)."""
    import kraft.builtins as builtins_mod
    from kraft import store as kraft_store
    from kraft.executor import walk
    from kraft.policy import Cap

    starts = []

    async def fail_refresh(*a, **kw):
        raise builtins_mod.RebaseConflict("conflict")

    async def fake_resolve(*a, **kw):
        return "ok", None, "deadbeef"

    async def fake_run(database, run_dirs, *, work_item_id, registry, start_index=0, **kw):
        starts.append(start_index)
        return "completed"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr(walk, "resolve_rebase_conflict", fake_resolve)
    monkeypatch.setattr("kraft.api.routes.lifecycle.executor.run", fake_run)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _force_node(wid, "mr_checks", "needs_human")
        conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
        try:
            cap = Cap(attempts=9, wall_clock_s=3600)
            kraft_store.bump_counter(conn, wid, "verify_fix_loop", cap)
            kraft_store.bump_counter(conn, wid, "ci_fix_loop", cap)
            conn.commit()
        finally:
            conn.close()

        r = _post_past_the_still_finishing_walk(client, f"/api/work-items/{wid}/retry")

        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 5
        while not starts and time.monotonic() < deadline:
            time.sleep(0.05)
        assert starts[-1] == 6, "did not bounce back to verify's index"
        conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
        try:
            rows = conn.execute(
                "SELECT key FROM retry_counters WHERE work_item_id = ?", (wid,)
            ).fetchall()
        finally:
            conn.close()
    assert rows == [], "the bounce span's loop counters survived the resolver's restart"


def test_retry_rebase_conflict_resolver_success_actually_advances_the_walk(tmp_path, monkeypatch):
    """Kraft-s7c04.23 review findings 1 & 2. Awaiting the resolver inline in
    the route handler (rather than as the item's own spawned task) meant a
    successful resolve never went anywhere: nothing put the item back to
    `active` before `executor.run` started, so `run_once`'s own
    `status != "active"` check at its first node immediately returned
    "paused" and did nothing. Unlike the restart-index tests above, this one
    does NOT monkeypatch `executor.run` -- the walk must actually run,
    through the real (noop-bound) nodes between `verify` and `human_review`,
    for this to pass."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    import kraft.builtins as builtins_mod
    from kraft.executor import walk

    calls = {"n": 0}

    async def fail_refresh_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise builtins_mod.RebaseConflict("conflict")
        return None  # pre_mr_rebase's own later attempt: no movement

    async def fake_resolve(*a, **kw):
        return "ok", None, "deadbeef"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh_once)
    monkeypatch.setattr(walk, "resolve_rebase_conflict", fake_resolve)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _force_node(wid, "verify", "needs_human")

        r = _post_past_the_still_finishing_walk(client, f"/api/work-items/{wid}/retry")

        assert r.status_code == 200, r.text
        # Under the bug this never becomes pending -- the walk dies at its
        # very first status check with nothing dispatched past `verify`.
        item = _await_gate(client, wid, "human_review_approval", timeout=20)
        assert item["pending_gate"] == "human_review_approval"


def test_retry_while_the_resolver_is_running_is_refused(tmp_path, monkeypatch):
    """Finding 2's other half: for as long as the resolver runs, the item
    must not be re-claimable by a concurrent `/retry` -- refused with 409
    rather than starting a second walk in the same worktree (the collision
    Kraft-s7c04.20 closed). In practice the item's own status (still
    `active` from the first call's `claim_for_run`, not reverted to
    `needs_human` until the resolver finishes) refuses the race even before
    `deps.task_is_live` would -- either guard firing proves no second walk
    can start; this asserts on the observable one."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    import asyncio as _asyncio
    import threading

    import kraft.builtins as builtins_mod
    from kraft.executor import walk

    # `threading.Event`, not `asyncio.Event`: `TestClient` runs the app on a
    # background-thread event loop, and this signal crosses from the main
    # (synchronous) test thread into that loop -- an `asyncio.Event.set()`
    # called cross-thread is not the safe way to do that.
    started = threading.Event()
    release = threading.Event()

    async def fail_refresh(*a, **kw):
        raise builtins_mod.RebaseConflict("conflict")

    async def slow_resolve(*a, **kw):
        started.set()
        while not release.is_set():
            await _asyncio.sleep(0.01)
        return "ok", None, "deadbeef"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr(walk, "resolve_rebase_conflict", slow_resolve)
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _force_node(wid, "verify", "needs_human")

        r1 = _post_past_the_still_finishing_walk(client, f"/api/work-items/{wid}/retry")
        assert r1.status_code == 200, r1.text

        assert started.wait(timeout=10), "the resolver never started"

        r2 = client.post(f"/api/work-items/{wid}/retry", json={})
        release.set()

        assert r2.status_code == 409, r2.text
        assert r2.json()["detail"] in (
            "work item is not stopped",
            "a walk is already running for this work item",
        )
        # Confirms *why* it's refused: the item never reverted to a
        # re-claimable state while the resolver was still running.
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "active"


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


def test_escalate_refuses_and_writes_nothing_when_a_walk_is_still_live(tmp_path, monkeypatch):
    """Kraft-s7c04.20: same race as `/retry` and `/resume`, reached through
    `/escalate` -- a gate's own auto-review is a live walk task under this
    same `wid`, invisible to `escalation_running`'s check (that only sees
    another *escalation*), and until this guard existed a human could
    escalate straight into a worktree that live review agent was still
    writing to (43717ee6: two agents, one worktree, both committed)."""
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

        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "help"})

        assert r.status_code == 409, r.text
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"

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


def test_retry_with_no_steer_seeds_the_last_measurements_findings(tmp_path, monkeypatch):
    """Kraft-7sec, second half: a retry after a fix-loop cap breach with no
    explicit steer must seed the agent with the last measurement's unresolved
    findings, not start blind."""
    from kraft import events as kraft_events

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _force_node(wid, "verify", "needs_human")

        conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
        try:
            kraft_events.append(
                conn,
                wid,
                "findings_measured",
                {
                    "node_id": "verify",
                    "cycle": 0,
                    "findings": [
                        {
                            "severity": "important",
                            "message": "missing null check",
                            "file": "a.py",
                            "line": 10,
                            "source_plugin": "reviewer",
                        }
                    ],
                    "fingerprints": ["x"],
                    "noop_hooks": [],
                },
            )
            conn.commit()
        finally:
            conn.close()

        r = client.post(f"/api/work-items/{wid}/retry", json={})
        assert r.status_code == 200, r.text
        assert "missing null check" in r.json()["steer"]

        evts = client.get(f"/api/work-items/{wid}/events").json()
        retried = next(e for e in evts if e["type"] == "work_item_retried")
        assert retried["payload"]["seeded"] is True
