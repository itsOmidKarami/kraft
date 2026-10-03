"""Pause from the states the board offers it in."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from support.api import _poll_events, _post_default


def _rate_limit(wid: str) -> None:
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "UPDATE work_items SET status = 'rate_limited', stop_kind = 'rate_limit', "
            "retry_at = '2999-01-01T00:00:00+00:00' WHERE id = ?",
            (wid,),
        )
        conn.commit()
    finally:
        conn.close()


def _row(wid: str) -> tuple[str, str | None]:
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        return conn.execute(
            "SELECT status, retry_at FROM work_items WHERE id = ?", (wid,)
        ).fetchone()
    finally:
        conn.close()


def test_a_rate_limited_item_pauses_and_resumes(client, repo):
    """R10b-01's follow-up: the board offers Pause on a rate-limited item, and
    /pause answered it 409 "not running", which left it no door at all. It
    pauses now, clearing the poller's `retry_at`, and Resume takes it back."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _rate_limit(wid)

    r = client.post(f"/api/work-items/{wid}/pause")

    assert r.status_code == 200, r.text
    assert _row(wid) == ("paused", None)
    r = client.post(f"/api/work-items/{wid}/resume", json={})
    assert r.status_code == 200, r.text
    _poll_events(client, wid, "gate_requested", count=2)
