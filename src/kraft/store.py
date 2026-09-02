from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from kraft import events


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_work_item(
    conn: sqlite3.Connection, *, id, bead_id, title, repo, chain_template, chain_definition
) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
        "chain_definition, current_node_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, 'active', ?, ?)",
        (id, bead_id, title, repo, chain_template, chain_definition, now, now),
    )
    events.append(
        conn,
        id,
        "work_item_created",
        {"title": title, "repo": repo, "chain_template": chain_template},
    )


def load_chain(conn: sqlite3.Connection, work_item_id, first_node_id) -> None:
    conn.execute(
        "UPDATE work_items SET current_node_id = ?, updated_at = ? WHERE id = ?",
        (first_node_id, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "chain_loaded", {})


def enter_node(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    conn.execute(
        "UPDATE work_items SET current_node_id = ?, updated_at = ? WHERE id = ?",
        (node_id, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "node_started", {"node_id": node_id})


def complete_node(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    conn.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (_now(), work_item_id))
    events.append(conn, work_item_id, "node_completed", {"node_id": node_id})


def mark_needs_human(conn: sqlite3.Connection, work_item_id, node_id, reason) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(
        conn, work_item_id, "work_item_needs_human", {"node_id": node_id, "reason": reason}
    )


def mark_completed(conn: sqlite3.Connection, work_item_id) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'completed', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_completed", {})


def create_session(
    conn: sqlite3.Connection, *, id, work_item_id, node_id, hook_point, log_path, result_path
) -> None:
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, created_at, exited_at) "
        "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'pending', 1, ?, NULL)",
        (id, work_item_id, node_id, hook_point, log_path, result_path, _now()),
    )


def session_running(conn: sqlite3.Connection, session_id, pid, pid_start_time) -> None:
    conn.execute(
        "UPDATE worker_sessions SET status = 'running', pid = ?, pid_start_time = ? WHERE id = ?",
        (pid, pid_start_time, session_id),
    )
    row = conn.execute(
        "SELECT work_item_id, node_id, hook_point FROM worker_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    events.append(
        conn,
        row["work_item_id"],
        "worker_session_started",
        {
            "session_id": session_id,
            "node_id": row["node_id"],
            "hook_point": row["hook_point"],
            "pid": pid,
        },
    )


def session_exited(conn: sqlite3.Connection, session_id, status) -> None:
    conn.execute(
        "UPDATE worker_sessions SET status = ?, exited_at = ? WHERE id = ?",
        (status, _now(), session_id),
    )
    row = conn.execute(
        "SELECT work_item_id FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    events.append(
        conn,
        row["work_item_id"],
        "worker_session_exited",
        {"session_id": session_id, "status": status},
    )


def session_unknown(conn: sqlite3.Connection, session_id) -> None:
    conn.execute(
        "UPDATE worker_sessions SET status = 'unknown', exited_at = ? WHERE id = ?",
        (_now(), session_id),
    )
    row = conn.execute(
        "SELECT work_item_id FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    events.append(conn, row["work_item_id"], "session_unknown", {"session_id": session_id})


def session_reattached(conn: sqlite3.Connection, session_id) -> None:
    row = conn.execute(
        "SELECT work_item_id, pid FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    events.append(
        conn,
        row["work_item_id"],
        "session_reattached",
        {"session_id": session_id, "pid": row["pid"]},
    )
