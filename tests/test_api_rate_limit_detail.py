"""GET /work-items/{wid} exposes the rate-limit relaunch counter (06's
'N of M relaunches used')."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    from fastapi.testclient import TestClient

    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54322))


def test_rate_limit_field_is_null_off_a_rate_limited_item(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "fine", "chain_template": "quick-task"},
        ).json()["id"]
        assert client.get(f"/api/work-items/{wid}").json()["rate_limit"] is None


def test_rate_limit_field_reports_the_bumped_counter(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/api/work-items",
            json={"repo": str(repo), "title": "rl", "chain_template": "quick-task"},
        ).json()["id"]
        node_id = client.get(f"/api/work-items/{wid}").json()["current_node_id"]
        conn = sqlite3.connect(tmp_path / "run" / "orchestrator.db")
        conn.execute(
            "UPDATE work_items SET status = 'rate_limited', current_node_id = ? WHERE id = ?",
            (node_id, wid),
        )
        conn.execute(
            "INSERT INTO retry_counters (work_item_id, key, count, cap_attempts, cap_wall_s, "
            "started_at, updated_at) VALUES (?, ?, 3, 5, 1000000000, 'now', 'now')",
            (wid, f"rate_limit:{node_id}"),
        )
        conn.commit()
        conn.close()

        body = client.get(f"/api/work-items/{wid}").json()
        assert body["rate_limit"] == {"count": 3, "cap": 5}
