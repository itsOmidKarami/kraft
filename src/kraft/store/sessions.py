from __future__ import annotations

import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store._common import _span_ms
from kraft.usage import Usage


def create_session(
    conn: sqlite3.Connection,
    *,
    id,
    work_item_id,
    node_id,
    hook_point,
    log_path,
    result_path,
    round: int = 0,
    head_sha: str | None = None,
    reuse_if_waiting: bool = False,
) -> tuple[str, str, str]:
    """`round` is the fix-cycle index this session was dispatched in (0 = first pass).

    `head_sha` is the worktree HEAD this session was dispatched against
    (Kraft-lu2) — None for a builtin/agent task that has no meaningful sha of
    its own to report.

    Returns `(id, log_path, result_path)` -- normally the caller's own `id`
    and paths, echoed back. `reuse_if_waiting` (Kraft-ivh1) changes that: a
    session already sitting at `status='waiting'` for this exact (work item,
    node, hook point, round) is the same wait episode, not a new attempt --
    its id/log_path/result_path are returned instead, and no row is
    inserted. `on.ci.poll` is the only caller that sets this; every other
    hook keeps minting a fresh row every dispatch, unchanged.
    """
    if reuse_if_waiting:
        existing = conn.execute(
            "SELECT id, log_path, result_path FROM worker_sessions WHERE work_item_id = ? "
            "AND node_id = ? AND hook_point = ? AND round = ? AND status = 'waiting' "
            "ORDER BY created_at DESC LIMIT 1",
            (work_item_id, node_id, hook_point, round),
        ).fetchone()
        if existing is not None:
            return existing["id"], existing["log_path"], existing["result_path"]
    # The attempt is the count of this (work item, node, hook point)'s sessions,
    # computed in the INSERT rather than passed in: no caller knows better than the
    # table does, and two callers would each re-implement the same query
    # (Kraft-kq8m). Every write goes through Database.write — one connection,
    # serialised — so the count cannot race a concurrent insert.
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, created_at, exited_at, "
        "round, head_sha) "
        "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'pending', "
        "(SELECT COUNT(*) + 1 FROM worker_sessions "
        "WHERE work_item_id = ? AND node_id = ? AND hook_point = ?), ?, NULL, ?, ?)",
        (
            id,
            work_item_id,
            node_id,
            hook_point,
            log_path,
            result_path,
            work_item_id,
            node_id,
            hook_point,
            _now(),
            round,
            head_sha,
        ),
    )
    (attempt,) = conn.execute("SELECT attempt FROM worker_sessions WHERE id = ?", (id,)).fetchone()
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
        {
            "session_id": id,
            "node_id": node_id,
            "hook_point": hook_point,
            "round": round,
            "attempt": attempt,
        },
    )
    return id, log_path, result_path


def sessions_for_round(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, round: int
) -> list[sqlite3.Row]:
    """Every session this node ran in one fix cycle — the round's result files."""
    return list(
        conn.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND round = ? ORDER BY created_at",
            (work_item_id, node_id, round),
        )
    )


def session_running(conn: sqlite3.Connection, session_id, pid, pid_start_time) -> None:
    # `status != 'paused'`: a pause that landed while this session was still
    # pending must not be undone by the launch finishing.
    conn.execute(
        "UPDATE worker_sessions SET status = 'running', pid = ?, pid_start_time = ?, "
        "started_at = ? WHERE id = ? AND status != 'paused'",
        (pid, pid_start_time, _now(), session_id),
    )
    row = conn.execute(
        "SELECT work_item_id, node_id, hook_point, round, attempt FROM worker_sessions "
        "WHERE id = ?",
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
            "round": row["round"],
            "attempt": row["attempt"],
            "pid": pid,
        },
    )


def session_progress(conn: sqlite3.Connection, session_id, usage: Usage) -> None:
    """Live token counts and model for a session that is still running (Kraft-54dk).

    Deliberately no event. One of these lands every few seconds per running
    worker, and an event fans out to the WebSocket, the indexer and the
    notifier for a number nobody subscribes to. `usage_rollup` reads the row,
    not the event stream, so "tokens this node" starts being true the moment
    the row is.

    Cost and wall time stay with `session_exited`, which writes the
    authoritative final figures and corrects any live drift. `status =
    'running'` guards the update: a paused or finished session's numbers are
    settled, and a late progress write must not reopen them.
    """
    conn.execute(
        "UPDATE worker_sessions SET model = ?, tokens_in = ?, tokens_out = ? "
        "WHERE id = ? AND status = 'running'",
        (usage.model, usage.tokens_in, usage.tokens_out, session_id),
    )


def session_exited(
    conn: sqlite3.Connection,
    session_id,
    status,
    summary_ref=None,
    usage: Usage | None = None,
    *,
    concerns: str | None = None,
    question: str | None = None,
) -> None:
    """`concerns` (`done_with_concerns`) and `question` (`needs_context`) are
    free text with no column of their own (Task 1's migration deliberately adds
    none) — this event is the only place they are recorded, so a human-review
    gate or a needs_context stop can read them back without a per-request file
    read (`adapters.subprocess.read_concerns`/`read_question` off disk)."""
    now = _now()
    row = conn.execute(
        "SELECT work_item_id, started_at, created_at, status FROM worker_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    # Pausing SIGTERMs the child, so the adapter — or reattach, adopting a child
    # that dies after a restart — arrives here a moment later with 'failed'. A
    # human's interruption is not a task failure, and the row and the event have to
    # agree: return before either is written.
    if row is None or row["status"] == "paused":
        return
    # COALESCE: a None ref must not erase one an earlier resolution already stored.
    conn.execute(
        "UPDATE worker_sessions SET status = ?, exited_at = ?, "
        "session_summary_ref = COALESCE(?, session_summary_ref) WHERE id = ?",
        (status, now, summary_ref, session_id),
    )
    # Wall time runs from the moment the process started, not from row creation:
    # a session that waited behind a lock did not spend that time working.
    wall_ms = _span_ms(row["started_at"] or row["created_at"], now)
    payload = {"session_id": session_id, "status": status, "wall_ms": wall_ms}
    if concerns:
        payload["concerns"] = concerns
    if question:
        payload["question"] = question
    if usage is not None:
        conn.execute(
            "UPDATE worker_sessions SET model = ?, tokens_in = ?, tokens_out = ?, "
            "cost_usd = ?, wall_ms = ? WHERE id = ?",
            (usage.model, usage.tokens_in, usage.tokens_out, usage.cost_usd, wall_ms, session_id),
        )
        payload |= {
            "model": usage.model,
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "cost_usd": usage.cost_usd,
        }
    else:
        conn.execute("UPDATE worker_sessions SET wall_ms = ? WHERE id = ?", (wall_ms, session_id))
    # The design calls this event worker_session_completed; this codebase has
    # always called the same moment worker_session_exited, so the usage rides
    # that rather than a second event meaning the same thing.
    events.append(conn, row["work_item_id"], "worker_session_exited", payload)


def stop_escalation_session(conn: sqlite3.Connection, work_item_id: str, session_id: str) -> None:
    """Kill a running escalation turn without touching the item's own status
    (06 'Stop agent') -- the item was, and remains, needs_human; only the turn
    stops. Mirrors the per-session half of `pause_work_item`, minus the
    work_items UPDATE that function also does."""
    now = _now()
    conn.execute(
        "UPDATE worker_sessions SET status = 'paused', exited_at = ? WHERE id = ?",
        (now, session_id),
    )
    events.append(conn, work_item_id, "worker_session_paused", {"session_id": session_id})


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


def session_status(conn: sqlite3.Connection, session_id: str) -> str | None:
    row = conn.execute("SELECT status FROM worker_sessions WHERE id = ?", (session_id,)).fetchone()
    return row["status"] if row else None


def recent_sessions_for_hook(
    conn: sqlite3.Connection, hook_point: str, limit: int = 5
) -> list[dict]:
    """The Plugins binding detail's "LAST RUNS" (design 28): the most recent
    sessions dispatched for one hook, across every work item — not filtered
    to one item, the same way the binding itself isn't."""
    rows = conn.execute(
        "SELECT work_item_id, node_id, round, status, wall_ms, created_at "
        "FROM worker_sessions WHERE hook_point = ? ORDER BY created_at DESC LIMIT ?",
        (hook_point, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def running_sessions_for_node(conn: sqlite3.Connection, work_item_id: str) -> list[sqlite3.Row]:
    """Every running session on the item's current node — all of them on a
    concurrent node like `verify`."""
    # 'pending' too: a row is inserted before Popen returns, and a pause landing in
    # that window would otherwise neither signal the child nor mark the row — the
    # task would run to completion under an item that reads paused.
    return conn.execute(
        "SELECT s.id, s.pid FROM worker_sessions s JOIN work_items w ON w.id = s.work_item_id "
        "WHERE s.work_item_id = ? AND s.node_id = w.current_node_id "
        "AND s.status IN ('running', 'pending')",
        (work_item_id,),
    ).fetchall()
