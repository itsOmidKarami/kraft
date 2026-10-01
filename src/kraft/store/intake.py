from __future__ import annotations

import json
import sqlite3

from kraft.store import _now

__all__ = ["CHECKS_KEPT", "intake_checks", "record_intake_check"]

CHECKS_KEPT = 500


def record_intake_check(
    conn: sqlite3.Connection, *, ready: int, started: list[str], skipped: list[dict]
) -> dict:
    """One poll's row; the oldest beyond `CHECKS_KEPT` go. Not an `events` row:
    a check is about no work item."""
    at = _now()
    cur = conn.execute(
        "INSERT INTO intake_checks (at, ready, started, skipped) VALUES (?, ?, ?, ?)",
        (at, ready, json.dumps(started), json.dumps(skipped)),
    )
    conn.execute("DELETE FROM intake_checks WHERE id <= ?", (cur.lastrowid - CHECKS_KEPT,))
    return {"id": cur.lastrowid, "at": at, "ready": ready, "started": started, "skipped": skipped}


def intake_checks(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT id, at, ready, started, skipped FROM intake_checks ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {**dict(r), "started": json.loads(r["started"]), "skipped": json.loads(r["skipped"])}
        for r in rows
    ]
