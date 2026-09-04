from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from kraft import events
from kraft.policy import Cap


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
    # Idempotent: resume can re-enter an already-completed node (a reconciled
    # non-fix node, or a fix_loop node re-measured after a crash in the
    # complete_node -> enter_node window) and must not emit a second
    # node_completed. See Kraft-gbt / Kraft-126.
    done = conn.execute(
        "SELECT 1 FROM events WHERE work_item_id = ? AND type = 'node_completed' "
        "AND json_extract(payload, '$.node_id') = ? LIMIT 1",
        (work_item_id, node_id),
    ).fetchone()
    if done:
        return
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


def bump_counter(
    conn: sqlite3.Connection, work_item_id: str, key: str, cap: Cap
) -> tuple[int, str, Cap]:
    """Insert (count=1) or increment. Returns (count, started_at, effective_cap).

    The effective cap is the one snapshotted on the row: `cap` on insert, the
    stored snapshot on increment. Per spec §2.C the cap is written once at first
    fire and not re-resolved per attempt, so callers must `check` against the
    returned cap, not a fresh `resolve_cap` (which would pick up an edited
    policy.yaml across a restart).
    """
    now = _now()
    row = conn.execute(
        "SELECT count, cap_attempts, cap_wall_s, started_at "
        "FROM retry_counters WHERE work_item_id = ? AND key = ?",
        (work_item_id, key),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO retry_counters (work_item_id, key, count, cap_attempts, "
            "cap_wall_s, started_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
            (work_item_id, key, cap.attempts, cap.wall_clock_s, now, now),
        )
        return 1, now, cap
    new_count = row["count"] + 1
    conn.execute(
        "UPDATE retry_counters SET count = ?, updated_at = ? WHERE work_item_id = ? AND key = ?",
        (new_count, now, work_item_id, key),
    )
    return new_count, row["started_at"], Cap(row["cap_attempts"], row["cap_wall_s"])


def read_counter(conn: sqlite3.Connection, work_item_id: str, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM retry_counters WHERE work_item_id = ? AND key = ?",
        (work_item_id, key),
    ).fetchone()


def mark_sessions_capped_out(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, hook_points: list[str]
) -> None:
    # On breach every *measuring* task in the node becomes capped_out (02 §7.2) —
    # including one that ended 'failed' on the final cycle. A 'done' co-task
    # (a clean noop review) is left as-is. Scoped to the node's measuring hook
    # points so the fix task's own session (on.implementation.start) is not
    # mislabelled as a capped-out measurement.
    placeholders = ",".join("?" * len(hook_points))
    where = (
        f"work_item_id = ? AND node_id = ? AND hook_point IN ({placeholders}) "
        f"AND status NOT IN ('done', 'capped_out')"
    )
    args = (work_item_id, node_id, *hook_points)
    capped = conn.execute(f"SELECT id FROM worker_sessions WHERE {where}", args).fetchall()
    conn.execute(
        f"UPDATE worker_sessions SET status = 'capped_out', exited_at = ? WHERE {where}",
        (_now(), *args),
    )
    # The SPA only learns session status from worker_session_* events + hydrate;
    # without this the chip stays on its last live status until the reconcile.
    for row in capped:
        events.append(
            conn,
            work_item_id,
            "worker_session_exited",
            {"session_id": row["id"], "status": "capped_out"},
        )


def request_gate(conn: sqlite3.Connection, work_item_id, node_id, gate) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "gate_requested", {"gate": gate, "node_id": node_id})


def approve_gate(conn: sqlite3.Connection, work_item_id, gate) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "gate_approved", {"gate": gate})


def reject_gate(conn: sqlite3.Connection, work_item_id, gate, note) -> None:
    conn.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (_now(), work_item_id))
    events.append(conn, work_item_id, "gate_rejected", {"gate": gate, "note": note})


def create_session(
    conn: sqlite3.Connection, *, id, work_item_id, node_id, hook_point, log_path, result_path
) -> None:
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, created_at, exited_at) "
        "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'pending', 1, ?, NULL)",
        (id, work_item_id, node_id, hook_point, log_path, result_path, _now()),
    )
    # Announce the session here, where every session is born, rather than in
    # session_running — only the subprocess adapter calls that, so a builtin hook
    # (create_session -> session_exited) never told the SPA the session existed.
    # worker_session_exited carries no node_id/hook_point, so the client could not
    # build the row from it either: it saw no session for the current node, inferred
    # no gate was awaiting, and rendered no Approve button until a reload (Kraft-dce).
    events.append(
        conn,
        work_item_id,
        "worker_session_created",
        {"session_id": id, "node_id": node_id, "hook_point": hook_point},
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


def session_exited(conn: sqlite3.Connection, session_id, status, summary_ref=None) -> None:
    # COALESCE: a None ref must not erase one an earlier resolution already stored.
    conn.execute(
        "UPDATE worker_sessions SET status = ?, exited_at = ?, "
        "session_summary_ref = COALESCE(?, session_summary_ref) WHERE id = ?",
        (status, _now(), summary_ref, session_id),
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
