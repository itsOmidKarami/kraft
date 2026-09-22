"""GET /work-items/{wid} exposes the rate-limit relaunch counter (06's
'N of M relaunches used')."""

from __future__ import annotations

import sqlite3


def test_rate_limit_field_is_null_off_a_rate_limited_item(client, repo):
    wid = client.post(
        "/api/work-items",
        json={"repo": str(repo), "title": "fine", "chain_template": "quick-task"},
    ).json()["id"]
    assert client.get(f"/api/work-items/{wid}").json()["rate_limit"] is None


def test_rate_limit_field_reports_the_bumped_counter(client, repo, tmp_path):
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
