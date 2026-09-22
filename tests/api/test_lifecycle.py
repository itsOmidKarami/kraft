"""The happy path end to end, and abandon/resume/retry."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from support.api import _force_node, _poll_events, _post_default, _set_status


def test_happy_path_via_api(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
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
    # implementation's own agent task and verify's changed-test-scope task.
    # V1 has no `env_setup` node, so no session stands in for one. C1's
    # implementation-time test gate (Kraft-s7c04.8) was reverted
    # 2026-09-16 -- see test_walk.py's test_run_verify_failure_stops_at_verify.
    assert len(item["worker_sessions"]) == 2

    run_dir = Path(client.app.state.run_dirs.base)
    assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()

    # 4B: each agent session's summary is ingested and linked to the work item
    impl_session = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
    assert impl_session["session_summary_ref"] == (f".engineering/sessions/{impl_session['id']}.md")
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
    assert any(ln["work_item_id"] == wid for h in hits for ln in h["links"] if ln["work_item_id"])

    # log endpoint returns the agent's stdout
    impl = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
    log = client.get(f"/api/worker-sessions/{impl['id']}/log")
    assert log.status_code == 200
    assert "is_error" in log.text


def test_executor_crash_marks_needs_human(client, repo, monkeypatch):
    """A non-task exception in the spawned run task must not wedge the item in 'active'."""
    from kraft import executor

    async def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(executor, "run", boom)
    wid = client.post("/api/work-items", json={"title": "x", "repo": str(repo)}).json()["id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = client.get(f"/api/work-items/{wid}").json()["status"]
        if status == "needs_human":
            break
        time.sleep(0.2)
    assert status == "needs_human"


def test_retry_refuses_explicit_steer_on_a_node_with_no_agent_task(client, repo):
    """Kraft-bz9b: `merge` is forge-kind with no fix_loop and is the chain's
    last node, so nothing downstream of it ever calls `Steer.take()`.
    `--steer` used to be accepted and echoed back as if it would reach the
    next launch, when it was silently dropped. (`open_mr`, this test's node
    before Kraft-cbr, no longer qualifies: `mr_checks` right after it now
    carries `fix_loop`, so a steer given at `open_mr` could reach that.)"""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "merge", "needs_human")

    r = client.post(f"/api/work-items/{wid}/retry", json={"steer": "commit the leftover file"})

    assert r.status_code == 409, r.text
    assert "merge" in r.json()["detail"]


def test_retry_without_explicit_steer_still_works_on_a_node_with_no_agent_task(client, repo):
    """The guard is for text a caller just typed and expects used, not for
    Kraft's own last-rejection carry-forward (Kraft-ko7j) -- that must keep
    working even on a node with nothing to steer."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "merge", "needs_human")

    r = client.post(f"/api/work-items/{wid}/retry", json={})

    assert r.status_code == 200, r.text


#: How each door's item is stopped: `/resume` takes a paused item, `/retry`
#: a stopped one.
_STOPPED = {
    "resume": lambda wid: _set_status(wid, "paused"),
    "retry": lambda wid: _force_node(wid, "implementation", "needs_human"),
}


@pytest.mark.parametrize("verb", ["resume", "retry"])
@pytest.mark.parametrize("busy", [True, False], ids=["all-slots-busy", "a-slot-free"])
def test_a_manual_start_is_bounded_by_the_slot_limit(client, repo, verb, busy):
    """A manual start is bounded by the same limit as auto-intake (Kraft-n2d):
    the limit lived only in the intake tick, so `resume` started an item no
    matter how many were already running, and `/retry` is the same door
    (notes 10). With a slot free the guard must not wedge the ordinary
    single-item case shut."""
    if busy:
        other = _post_default(client, repo)
        _poll_events(client, other, "gate_requested")
        _set_status(other, "active")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _STOPPED[verb](wid)
    client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)

    r = client.post(f"/api/work-items/{wid}/{verb}", json={})

    if busy:
        assert r.status_code == 409, r.text
        assert "1" in r.json()["detail"]
        assert client.get(f"/api/work-items/{other}").json()["status"] == "active"
    else:
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


def _rebase_fails(monkeypatch, error):
    """`refresh_worktree_base` raises `error`; escalation dispatches are
    recorded, never run. Returns the recorded `(work_item_id, auto)` calls."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    import kraft.builtins as builtins_mod

    calls = []

    async def fail_refresh(*a, **kw):
        raise error(builtins_mod)("rebase conflict: could not apply")

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr(builtins_mod, "refresh_worktree_base", fail_refresh)
    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    return calls


def _completed_quick_task(client, repo):
    """`quick-task` (no gate on any node) rather than the default chain: a
    `gate_requested` event left open by force-writing status past it reads
    to `gates.pending_gate` as a still-open gate, which makes
    `auto_escalate_stuck` no-op regardless of whether it is armed."""
    wid = client.post(
        "/api/work-items", json={"title": "x", "repo": str(repo), "chain_template": "quick-task"}
    ).json()["id"]
    _poll_events(client, wid, "work_item_completed")
    return wid


_STOP_STATUS = {"resume": "paused", "retry": "needs_human"}


@pytest.mark.parametrize("verb", ["resume", "retry"])
def test_a_non_conflict_rebase_failure_goes_to_a_human_even_when_armed(
    client, repo, monkeypatch, verb
):
    """A git failure that is NOT a conflict (`RebaseConflict` specifically)
    stops for a human naming it; only a conflict gets the resolver path
    (Kraft-s7c04.23). It is not in the stuck set, so no agent is dispatched
    onto it even with `auto_escalate_stuck` armed (Ruling 176)."""
    calls = _rebase_fails(monkeypatch, lambda b: RuntimeError)
    wid = _completed_quick_task(client, repo)
    _force_node(wid, "verify", _STOP_STATUS[verb])

    r = _post_past_the_still_finishing_walk(client, f"/api/work-items/{wid}/{verb}")

    assert r.status_code == 200, r.text
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    stops = [
        e
        for e in _poll_events(client, wid, "work_item_needs_human")
        if e["type"] == "work_item_needs_human"
    ]
    assert stops[-1]["payload"]["reason"] == "rebase conflict: could not apply"
    assert calls == []


@pytest.mark.parametrize("verb", ["resume", "retry"])
def test_a_rebase_conflict_does_not_escalate_when_disarmed(client, repo, monkeypatch, verb):
    """Regression guard: `auto_escalate_stuck: false` must still no-op here
    exactly like it does on the walk-driven path -- converted to raise
    `RebaseConflict` specifically (Kraft-s7c04.23 review finding: the old
    bare-`RuntimeError` version kept passing after this change landed while
    silently no longer covering the conflict path it is named for). The
    steer restore is unconditional and must still fire even disarmed;
    `/retry` never persists a steer ahead of the rebase, so for it this is
    the assertion that the helper writes it at all."""
    calls = _rebase_fails(monkeypatch, lambda b: b.RebaseConflict)
    wid = _completed_quick_task(client, repo)
    # `implementation`, not wherever the walk left it: that node has an agent
    # task downstream to steer, unlike `verify`'s subprocess-only tasks, which
    # `_steer_reachable` refuses on principle.
    _force_node(wid, "implementation", _STOP_STATUS[verb])
    if verb == "resume":
        # As `/pause` leaves it: a paused item's steer needs a paused agent
        # task to reach (Ruling 183).
        conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
        conn.execute("UPDATE worker_sessions SET status = 'paused' WHERE work_item_id = ?", (wid,))
        conn.commit()
        conn.close()
    client.app.state.policy = dataclasses.replace(
        client.app.state.policy, auto_escalate_stuck=False
    )

    r = _post_past_the_still_finishing_walk(
        client, f"/api/work-items/{wid}/{verb}", json={"steer": "watch the auth module"}
    )

    assert r.status_code == 200, r.text
    # The stop is recorded inside the spawned conflict-resolution task, not
    # synchronously before the route returns (Kraft-s7c04.23 review finding:
    # awaiting it inline blocked the response and hid it from `task_is_live`,
    # reopening Kraft-s7c04.20).
    _poll_events(client, wid, "work_item_needs_human")
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    assert calls == [], "disarmed must not dispatch the resolver or the escalation"
    assert item["pending_steer_context"] == "watch the auth module"


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


def test_retry_defers_instead_of_racing_its_own_still_running_escalation_session(client, repo):
    """The escalation agent calling `kraft item retry` on itself must not run
    the rebase/spawn inline: its own session is still `running` (it is
    mid-tool-call, blocked on this very response), so racing a `git rebase`
    and a fresh spawn into the worktree it is still live in is exactly the
    collision `escalation_running` exists to prevent everywhere else."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "implementation", "needs_human")
    _create_escalation_session(wid, "s1", "implementation")

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


def test_retry_kills_a_strangers_running_escalation_and_proceeds(client, repo, monkeypatch):
    """A caller that is *not* the live escalation session (no header, or a
    different one) now gets through: that session is killed first, then the
    retry proceeds the same way it would against a plain needs_human stop --
    distinct from the self-retry deferral path above, which stays a defer,
    not a kill."""
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "implementation", "needs_human")
    _create_escalation_session(wid, "s1", "implementation", pid=4242)

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


@pytest.mark.parametrize(
    ("verb", "status", "body"),
    [
        ("retry", "needs_human", {}),
        ("resume", "paused", {}),
        ("escalate", "needs_human", {"message": "help"}),
    ],
    ids=["retry", "resume", "escalate"],
)
def test_a_door_refuses_and_writes_nothing_when_a_walk_is_still_live(
    client, repo, verb, status, body
):
    """A stopped item can still have a live walk task behind it -- a pending
    gate under auto_escalate review, or the brief window while the walk that
    just called request_gate/mark_needs_human is still unwinding. `/retry`
    must refuse before it claims and rebases, not claim, rebase, and clear
    the fix-loop cap only for `spawn` to 409 on top of those writes; `/resume`
    likewise from `paused`. Kraft-s7c04.20: `/escalate` too -- a gate's own
    auto-review is a live walk task under this same `wid`, invisible to
    `escalation_running`'s check, and until this guard existed a human could
    escalate straight into a worktree that live review agent was still
    writing to (43717ee6: two agents, one worktree, both committed)."""
    from kraft.api import deps

    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "verify", status)

    async def _never_returning():
        await asyncio.Event().wait()

    async def inject():
        deps.spawn(client.app, wid, _never_returning())

    client.portal.call(inject)
    before = client.get(f"/api/work-items/{wid}/events").json()

    r = client.post(f"/api/work-items/{wid}/{verb}", json=body)

    assert r.status_code == 409, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == status
    assert client.get(f"/api/work-items/{wid}/events").json() == before

    async def cleanup():
        task = client.app.state.tasks.pop(wid)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    client.portal.call(cleanup)


def test_abandon_sets_terminal_status_and_removes_the_worktree(client, repo):
    """A rejected or dead item stayed on the board forever, worktree and all."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    assert worktree.is_dir(), "fixture never made a worktree; the test would prove nothing"
    _set_status(wid, "paused")

    r = client.post(f"/api/work-items/{wid}/abandon")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "abandoned"
    assert not worktree.exists()


def test_abandon_reclaims_the_attachment_storage(client, repo):
    """The worktree is already reclaimed; the documents that fed it should not
    outlive it in $KRAFT_HOME."""
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


def test_abandon_refuses_an_active_item(client, repo):
    """Pause first. Otherwise this races a running agent's writes."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "active")

    r = client.post(f"/api/work-items/{wid}/abandon")

    assert r.status_code == 409, r.text
    assert "active" in r.json()["detail"]


def test_abandon_kills_a_process_left_running_from_the_worktree(client, repo):
    """Kraft-ugm6: a server (or anything else) an agent started by hand from
    inside the worktree is invisible to `pause`'s session teardown and used to
    outlive the directory it was launched from."""
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


def test_post_triggers_files_a_paused_item(client, repo):
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


def test_retry_clears_the_ci_counter_for_the_current_node(client, repo):
    """A `ci_infra:<node>` counter (one kick short of the cap): after a retry
    the row is gone, so an infra-red poll gets a fresh budget, not an instant
    breach."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "merge_request_feedback", "needs_human")
    _seed_counter(wid, "ci_infra:merge_request_feedback")

    r = client.post(f"/api/work-items/{wid}/retry", json={})

    assert r.status_code == 200, r.text
    assert not _counter_exists(wid, "ci_infra:merge_request_feedback")


def test_retry_with_no_steer_seeds_the_last_measurements_findings(client, repo):
    """Kraft-7sec, second half: a retry after a fix-loop cap breach with no
    explicit steer must seed the agent with the last measurement's unresolved
    findings, not start blind."""
    from kraft import events as kraft_events

    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "implementation", "needs_human")

    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        kraft_events.append(
            conn,
            wid,
            "findings_measured",
            {
                "node_id": "implementation",
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


def _status_of(client, wid: str) -> str:
    return client.get(f"/api/work-items/{wid}").json()["status"]


def test_resume_retry_and_skip_do_not_strand_a_v1_item_claimed(client, repo):
    """H1, and the reason it is high severity rather than a 500.

    `chain_definition` is `"{}"` on a V1 row, and `resume`/`retry`/`skip` read
    the start index from it **after** `resume_work_item`/`retry_after_cap`/
    `claim_for_skip` have already written the row. So the `KeyError: 'nodes'`
    did not merely fail the request: it left the item claimed `active` with no
    walk behind it, which looks running and is not -- the worst shape a failure
    can take here.

    All three now read the index through `store.node_index`, which answers over
    either chain shape and cannot raise. Asserted on the *status afterwards*,
    not only on the response code, because a 200 with a stranded row was never
    the failure mode.
    """
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")

    _set_status(wid, "paused")
    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200, r.text
    _poll_events(client, wid, "gate_requested", count=2)

    _set_status(wid, "needs_human")
    r = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r.status_code == 200, r.text
    _poll_events(client, wid, "gate_requested", count=3)

    # And skip, whose own read runs after `cancel` has taken the walk away.
    r = client.post(f"/api/work-items/{wid}/skip", json={"note": "by hand"})
    assert r.status_code == 200, r.text
    assert (
        _status_of(client, wid) != "active" or client.get(f"/api/work-items/{wid}/events").json()
    ), "skip left the item claimed with nothing behind it"


def test_resume_of_an_item_that_never_reached_a_node_starts_at_the_chain_head(client, repo):
    """`store.node_index(..., default=0)`'s reason for existing: a paused item
    with a null `current_node_id` has no index to find, and the honest fallback
    is node 0 rather than a refusal or a raise."""
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["current_node_id"] is None

    r = client.post(f"/api/work-items/{wid}/resume", json={})

    assert r.status_code == 200, r.text
    evts = _poll_events(client, wid, "node_started")
    first = next(e for e in evts if e["type"] == "node_started")
    assert first["payload"]["node_id"] == "spec"


def _counter(wid: str, key: str) -> int | None:
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        row = conn.execute(
            "SELECT count FROM retry_counters WHERE work_item_id = ? AND key = ?", (wid, key)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_resume_does_not_consume_a_retry_attempt(client, repo, monkeypatch):
    """`resume-does-not-consume-a-retry-attempt`: a human pausing and resuming
    is an interruption, not a failure, so the resume itself spends nothing --
    not the paused node's fix-loop attempts, and not a gate's reject loop.
    The walk the resume hands off to is held back, so what is measured is the
    resume alone."""
    from kraft.api import deps

    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "verification", "paused")
    _seed_counter(wid, "verification.fix_loop", count=2)
    _seed_counter(wid, "spec_approval_reject_loop", count=1)
    handed: list[str] = []

    def held(app, item, coro):
        deps.discard(coro)
        handed.append(item)
        # Stands in for the live walk the claim bracket checks for.
        app.state.tasks[item] = asyncio.get_event_loop().create_future()

    monkeypatch.setattr(deps, "spawn", held)
    r = client.post(f"/api/work-items/{wid}/resume", json={})

    assert r.status_code == 200, r.text
    assert handed == [wid]
    assert _counter(wid, "verification.fix_loop") == 2
    assert _counter(wid, "spec_approval_reject_loop") == 1
