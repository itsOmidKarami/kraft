from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


def append(conn: sqlite3.Connection, work_item_id: str, type: str, payload: dict) -> int:
    cur = conn.execute(
        "INSERT INTO events (work_item_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
        (work_item_id, type, json.dumps(payload), _now()),
    )
    return cur.lastrowid


def read_after(
    conn: sqlite3.Connection,
    after_seq: int,
    work_item_id: str | None = None,
) -> list[dict]:
    sql = "SELECT seq, work_item_id, type, payload, created_at FROM events WHERE seq > ?"
    params: tuple = (after_seq,)
    if work_item_id is not None:
        sql += " AND work_item_id = ?"
        params += (work_item_id,)
    sql += " ORDER BY seq ASC"
    return [
        {
            "seq": r["seq"],
            "work_item_id": r["work_item_id"],
            "type": r["type"],
            "payload": json.loads(r["payload"]),
            "created_at": r["created_at"],
        }
        for r in conn.execute(sql, params).fetchall()
    ]
