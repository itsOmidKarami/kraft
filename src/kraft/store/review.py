"""Review flow storage (docs/superpowers/specs/2026-09-27-review-flow-backend-design.md).

`node_runs` pins the commit each node run started and ended on, so any two
attempts stay diffable. Threads, comments and reviews are added in later
sections of this module.
"""

from __future__ import annotations

import sqlite3

from kraft.store import _now as _now  # test seam for wall-clock checks

__all__ = [
    "finish_run",
    "gate_attempts",
    "node_run_rows",
    "pin_gate",
    "start_run",
]


def _newest(conn: sqlite3.Connection, wid: str, node_id: str):
    return conn.execute(
        "SELECT attempt, end_sha FROM node_runs WHERE work_item_id = ? AND node_id = ? "
        "ORDER BY attempt DESC LIMIT 1",
        (wid, node_id),
    ).fetchone()


def start_run(conn, wid, node_id, *, start_sha, base_sha) -> int | None:
    last = _newest(conn, wid, node_id)
    if last is not None and last["end_sha"] is None:
        # A resume, or a fix loop re-measuring: the same run, not a new attempt.
        return None
    attempt = 1 if last is None else last["attempt"] + 1
    conn.execute(
        "INSERT INTO node_runs (work_item_id, node_id, attempt, start_sha, base_sha, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (wid, node_id, attempt, start_sha, base_sha, _now()),
    )
    return attempt


def finish_run(conn, wid, node_id, *, end_sha, dirty) -> int | None:
    last = _newest(conn, wid, node_id)
    if last is None or last["end_sha"] is not None:
        return None
    conn.execute(
        "UPDATE node_runs SET end_sha = ?, dirty = ? "
        "WHERE work_item_id = ? AND node_id = ? AND attempt = ?",
        (end_sha, int(dirty), wid, node_id, last["attempt"]),
    )
    return last["attempt"]


def pin_gate(conn, wid, gate, *, sha, base_sha, dirty) -> int | None:
    last = _newest(conn, wid, gate)
    if last is not None and last["end_sha"] == sha:
        # The same gate re-requested at the same commit (a restart): nothing new to review.
        return None
    attempt = 1 if last is None else last["attempt"] + 1
    conn.execute(
        "INSERT INTO node_runs (work_item_id, node_id, attempt, start_sha, end_sha, base_sha, "
        "dirty, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (wid, gate, attempt, sha, sha, base_sha, int(dirty), _now()),
    )
    return attempt


def node_run_rows(conn, wid) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM node_runs WHERE work_item_id = ? ORDER BY created_at, attempt", (wid,)
    ).fetchall()


def gate_attempts(conn, wid, gate) -> list[dict]:
    rows = conn.execute(
        "SELECT attempt, end_sha, base_sha, created_at FROM node_runs "
        "WHERE work_item_id = ? AND node_id = ? AND end_sha IS NOT NULL ORDER BY attempt",
        (wid, gate),
    ).fetchall()
    return [
        {"n": r["attempt"], "sha": r["end_sha"], "base_sha": r["base_sha"], "at": r["created_at"]}
        for r in rows
    ]
