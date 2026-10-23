"""A session on a machine, in the work item payload."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from support.api import _seed_session


@pytest.mark.parametrize(
    ("runner", "status", "machine_out_since", "expected"),
    [
        ("gpu", "running", "2026-10-23T10:00:00+00:00", "2026-10-23T10:00:00+00:00"),
        ("gpu", "running", None, None),
        ("gpu", "done", "2026-10-23T10:00:00+00:00", None),
        (None, "running", "2026-10-23T10:00:00+00:00", None),
    ],
    ids=["running-on-a-dropped-machine", "machine-back", "finished-session", "local-session"],
)
def test_a_live_session_on_a_machine_that_is_out_of_reach_says_since_when(
    client, repo, monkeypatch, runner, status, machine_out_since, expected
):
    wid = client.post(
        "/api/work-items",
        json={"autostart": True, "repo": str(repo), "title": "t", "chain_template": "quick-task"},
    ).json()["id"]
    _seed_session(client, wid, session_id="r1", hook_point="verify.main.x", runner=runner)
    db_path = Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE worker_sessions SET status = ? WHERE id = 'r1'", (status,))
    conn.commit()
    conn.close()
    hub = client.app.state.workers_hub
    monkeypatch.setattr(hub, "unreachable_since", lambda name: machine_out_since)

    sessions = client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
    assert next(s for s in sessions if s["id"] == "r1")["runner_unreachable_since"] == expected
