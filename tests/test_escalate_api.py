"""POST /work-items/{wid}/escalate (spec:
docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


def _needs_human_item(client, repo, title="KRAFT_FAIL once"):
    """A real item driven to `needs_human` by the fake agent's own KRAFT_FAIL
    marker -- same recipe as
    test_api_retry_open_log.py::test_retry_restarts_a_stopped_node_that_has_no_fix_loop.
    """
    wid = client.post(
        "/api/work-items",
        json={
            "repo": str(repo),
            "title": title,
            "chain_template": "quick-task",
            # Kraft-lpdd: this file drives the manual escalate/stop-escalate
            # routes by hand -- the unrelated auto-escalate trigger would
            # otherwise race it onto the same needs_human stop.
            "node_overrides": {"implementation": {"auto_escalate_stuck": False}},
        },
    ).json()["id"]
    deadline = time.monotonic() + 120
    item = None
    while time.monotonic() < deadline:
        item = client.get(f"/api/work-items/{wid}").json()
        if item["status"] == "needs_human":
            return wid
        time.sleep(0.2)
    raise AssertionError(f"work item never reached needs_human: {item}")


def test_escalate_refuses_an_item_that_is_not_needs_human(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "fine so far", "chain_template": "quick-task"},
        ).json()["id"]
        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "help"})
        assert r.status_code == 409


def test_escalate_requires_a_nonempty_message(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "   "})
        assert r.status_code == 400


def _seed_running_escalation(tmp_path, wid, node_id, session_id="running-turn"):
    """A `pending` escalation session, inserted directly -- the dispatch
    machinery itself is exercised elsewhere; these tests only need the guard
    every door onto the same worktree has to check."""
    conn = sqlite3.connect(tmp_path / "run" / "orchestrator.db")
    conn.execute(
        "INSERT INTO worker_sessions "
        "(id, work_item_id, node_id, hook_point, log_path, result_path, status, created_at) "
        "VALUES (?, ?, ?, 'escalation', 'l', 'r', 'pending', 'now')",
        (session_id, wid, node_id),
    )
    conn.commit()
    conn.close()


def test_escalate_refuses_a_second_call_while_one_is_running(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
        _seed_running_escalation(tmp_path, wid, node_id)

        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "help"})
        assert r.status_code == 409
        assert "running-turn" in r.json()["detail"]


def test_retry_kills_a_running_escalation_turn_and_proceeds(tmp_path, monkeypatch):
    """`retry` dispatches into the same worktree an escalation agent may
    already be committing in -- a stranger's retry (no matching session
    header) now kills that turn first rather than refusing outright
    (Kraft-vyk8; see test_api_lifecycle.py's
    test_retry_kills_a_strangers_running_escalation_and_proceeds for the
    self-retry-vs-stranger distinction this pins from the other side)."""
    repo = make_repo(tmp_path)
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
        _seed_running_escalation(tmp_path, wid, node_id)

        r = client.post(f"/api/work-items/{wid}/retry", json={})
        assert r.status_code == 200, r.text


def test_resume_refuses_while_an_escalation_turn_is_running(tmp_path, monkeypatch):
    """Same race as `retry`, reached through `resume` instead: a `paused`
    item can carry a leftover running escalation row (Kraft-esc: an item can
    leave `needs_human` some other way while an escalation session exists)."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
        _seed_running_escalation(tmp_path, wid, node_id)
        conn = sqlite3.connect(tmp_path / "run" / "orchestrator.db")
        conn.execute("UPDATE work_items SET status = 'paused' WHERE id = ?", (wid,))
        conn.commit()
        conn.close()

        r = client.post(f"/api/work-items/{wid}/resume", json={})
        assert r.status_code == 409
        assert "running-turn" in r.json()["detail"]


def test_stop_escalation_kills_the_turn_without_changing_item_status(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
        _seed_running_escalation(tmp_path, wid, node_id, session_id="turn-1")

        r = client.post(f"/api/work-items/{wid}/escalate/stop")
        assert r.status_code == 200
        assert r.json()["session_id"] == "turn-1"

        conn = sqlite3.connect(tmp_path / "run" / "orchestrator.db")
        status = conn.execute("SELECT status FROM worker_sessions WHERE id = 'turn-1'").fetchone()[
            0
        ]
        assert status == "paused"
        item_status = conn.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()[
            0
        ]
        assert item_status == "needs_human"
        conn.close()


def test_stop_escalation_refuses_when_none_is_running(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        r = client.post(f"/api/work-items/{wid}/escalate/stop")
        assert r.status_code == 409


def _poll_events(client, wid, want, timeout=30):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        seen = client.get(f"/api/work-items/{wid}/events").json()
        if any(e["type"] == want for e in seen):
            return seen
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen; got {[e['type'] for e in seen]}")


def test_escalate_consumes_a_self_retry_left_by_a_human_escalated_agent(tmp_path, monkeypatch):
    """A human escalates via `/escalate`, the agent fixes the problem and
    calls `kraft item retry` on itself mid-turn (lifecycle.py's
    `work_item_self_retry_requested` deferral), and the turn then exits.
    Without a consumer on this manual path the item silently stayed
    `needs_human` forever even though the caller got HTTP 200 (Kraft
    code-review finding); it must now actually retry once the turn ends."""
    import kraft.escalate as escalate_mod
    from kraft import events

    def fake_dispatch(node_id):
        async def _fake(
            database, run_dirs, *, work_item_id, message, launch, auto=False, evts=None
        ):
            await database.write(
                lambda c: events.append(
                    c,
                    work_item_id,
                    "work_item_self_retry_requested",
                    {
                        "session_id": "s1",
                        "node_id": node_id,
                        "key": None,
                        "gate_key": None,
                        "steer": "fixed it",
                    },
                )
            )
            return "done"

        return _fake

    async def fake_refresh(worktree, repo, branch):
        return None

    walk_calls = []

    async def fake_walk_run(database, run_dirs, **kw):
        walk_calls.append(kw)
        return "completed"

    monkeypatch.setattr("kraft.executor.gates._builtins.refresh_worktree_base", fake_refresh)
    monkeypatch.setattr("kraft.executor.walk.run", fake_walk_run)

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _needs_human_item(client, repo)
        node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
        monkeypatch.setattr(escalate_mod, "dispatch", fake_dispatch(node_id))

        r = client.post(f"/api/work-items/{wid}/escalate", json={"message": "please look"})
        assert r.status_code == 200, r.text

        _poll_events(client, wid, "work_item_retried")
        assert walk_calls and walk_calls[0]["work_item_id"] == wid
