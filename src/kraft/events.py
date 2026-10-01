from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


def append(
    conn: sqlite3.Connection,
    work_item_id: str,
    type: str,
    payload: dict,
    *,
    node_id: str | None = None,
) -> int:
    """Append one event. `node_id` defaults to the payload's own `node_id` or
    `node` key (whichever is a string) -- which covers every emitter that
    already names its node in its payload without touching its call. An
    emitter that knows its node but does not name it in the payload passes
    `node_id=` explicitly (Kraft UI v2 · B13)."""
    if node_id is None:
        default = payload.get("node_id", payload.get("node"))
        node_id = default if isinstance(default, str) else None
    cur = conn.execute(
        "INSERT INTO events (work_item_id, type, payload, node_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (work_item_id, type, json.dumps(payload), node_id, _now()),
    )
    return cur.lastrowid


def read_after(
    conn: sqlite3.Connection,
    after_seq: int,
    work_item_id: str | None = None,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Events after `after_seq`, oldest first. `limit`, when given, caps it to
    the first `limit` of them (`GET /work-items/{id}/events`, Kraft UI v2 ·
    B13's paging)."""
    sql = "SELECT seq, work_item_id, type, payload, node_id, created_at FROM events WHERE seq > ?"
    params: tuple = (after_seq,)
    if work_item_id is not None:
        sql += " AND work_item_id = ?"
        params += (work_item_id,)
    sql += " ORDER BY seq ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    return [_row_dict(r) for r in conn.execute(sql, params).fetchall()]


def read_before(
    conn: sqlite3.Connection,
    before_seq: int,
    work_item_id: str,
    *,
    limit: int,
) -> list[dict]:
    """The last `limit` events with `seq < before_seq`, returned oldest first
    (the same row shape `read_after` returns) -- paging backward through a
    work item's timeline."""
    rows = conn.execute(
        "SELECT seq, work_item_id, type, payload, node_id, created_at FROM events "
        "WHERE work_item_id = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
        (work_item_id, before_seq, limit),
    ).fetchall()
    return [_row_dict(r) for r in reversed(rows)]


def _row_dict(r: sqlite3.Row) -> dict:
    return {
        "seq": r["seq"],
        "work_item_id": r["work_item_id"],
        "type": r["type"],
        "payload": json.loads(r["payload"]),
        "node_id": r["node_id"],
        "created_at": r["created_at"],
    }
