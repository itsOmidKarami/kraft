"""Implementation progress through the API: the report route, and the derived
`progress` field on the board and the detail."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from support.harness import _git, make_repo
from test_api import _client, _force_node

from kraft import events
from kraft.config import git_read

PLAN = "# p\n\n## Task 1 — parse\n\n## Task 2 — serve\n\n## Task 3 — render\n"


def _db() -> sqlite3.Connection:
    return sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")


def _paused_item(client, repo) -> str:
    """Created paused: an autostarted chain would race the forced state below."""
    response = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _worktree(wid: str, plan: str | None = PLAN) -> Path:
    """The item's worktree as `ensure_worktree` would leave it, plan artifact included."""
    wt = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    wt.mkdir(parents=True, exist_ok=True)
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    _git(wt, "commit", "-q", "--allow-empty", "-m", "base")
    if plan is not None:
        doc = wt / ".engineering" / "plans" / f"{wid}.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(plan)
    return wt


def _seed_run(wid: str, head_sha: str | None) -> None:
    """What the executor writes on entering the implementation node: node_started,
    then the implementer's session, stamped with the HEAD it was dispatched on."""
    conn = _db()
    try:
        events.append(conn, wid, "node_started", {"node_id": "implementation"})
        events.append(
            conn,
            wid,
            "worker_session_created",
            {
                "session_id": "s1",
                "node_id": "implementation",
                "hook_point": "on.implementation.start",
                "round": 0,
            },
        )
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
            "result_path, status, created_at, head_sha) VALUES ('s1', ?, 'implementation', "
            "'on.implementation.start', 'x.log', 'x.json', 'running', "
            "'2026-09-11T00:00:00+00:00', ?)",
            (wid, head_sha),
        )
        conn.commit()
    finally:
        conn.close()


def _task_progress_events(wid: str) -> list[dict]:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT payload FROM events WHERE work_item_id = ? AND type = 'task_progress' "
            "ORDER BY seq",
            (wid,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(p) for (p,) in rows]


def _board_row(client, wid: str) -> dict:
    return next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)


def test_a_report_moves_progress_on_the_detail_and_the_board(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _paused_item(client, repo)
        _worktree(wid)
        _force_node(wid, "implementation", "active")

        response = client.post(f"/api/work-items/{wid}/progress", json={"task": 2})

        assert response.status_code == 200, response.text
        assert response.json()["progress"]["current"] == 2
        assert _task_progress_events(wid) == [
            {"node_id": "implementation", "task": 2, "total": 3, "title": "serve"}
        ]
        detail = client.get(f"/api/work-items/{wid}").json()["progress"]
        assert (detail["current"], detail["total"], detail["title"]) == (2, 3, "serve")
        assert [t["state"] for t in detail["tasks"]] == ["done", "current", "pending"]
        assert _board_row(client, wid)["progress"] == {"current": 2, "total": 3, "title": "serve"}


def test_commits_naming_a_task_move_progress_without_a_report(tmp_path, monkeypatch):
    """Only commits since the implementer's dispatch HEAD count: an earlier
    run's `Task 3` commit sits below the base and must not."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _paused_item(client, repo)
        wt = _worktree(wid)
        _git(wt, "commit", "-q", "--allow-empty", "-m", "Task 3: from an earlier run")
        _seed_run(wid, head_sha=git_read(wt, "rev-parse", "HEAD"))
        _git(wt, "commit", "-q", "--allow-empty", "-m", "feat: the parser (task 1)")
        _force_node(wid, "implementation", "active")

        assert client.get(f"/api/work-items/{wid}").json()["progress"]["current"] == 2


def test_a_report_from_before_the_latest_node_start_does_not_count(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _paused_item(client, repo)
        _worktree(wid)
        _force_node(wid, "implementation", "active")
        assert client.post(f"/api/work-items/{wid}/progress", json={"task": 3}).status_code == 200
        _seed_run(wid, head_sha=None)  # the node was re-entered

        assert client.get(f"/api/work-items/{wid}").json()["progress"]["current"] == 1


def test_progress_is_null_off_the_implementation_node(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _paused_item(client, repo)
        _worktree(wid)
        _force_node(wid, "verify", "active")

        assert client.get(f"/api/work-items/{wid}").json()["progress"] is None
        assert _board_row(client, wid)["progress"] is None


def test_a_report_off_the_running_implementation_node_is_a_409(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _paused_item(client, repo)
        _worktree(wid)
        _force_node(wid, "verify", "active")
        assert client.post(f"/api/work-items/{wid}/progress", json={"task": 1}).status_code == 409
        _force_node(wid, "implementation", "paused")
        assert client.post(f"/api/work-items/{wid}/progress", json={"task": 1}).status_code == 409
        assert _task_progress_events(wid) == []


def test_a_task_outside_the_plan_is_a_400(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _paused_item(client, repo)
        _worktree(wid)
        _force_node(wid, "implementation", "active")
        for task in (0, 4):
            response = client.post(f"/api/work-items/{wid}/progress", json={"task": task})
            assert response.status_code == 400, task
        assert _task_progress_events(wid) == []


def test_a_plan_without_task_headings_is_a_400_and_no_progress(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _paused_item(client, repo)
        _worktree(wid, plan="# p\n\n## Step one\n")
        _force_node(wid, "implementation", "active")

        assert client.post(f"/api/work-items/{wid}/progress", json={"task": 1}).status_code == 400
        assert client.get(f"/api/work-items/{wid}").json()["progress"] is None
