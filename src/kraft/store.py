from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from kraft import events
from kraft.policy import Cap
from kraft.usage import Usage


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _span_ms(start: str | None, end: str) -> int | None:
    """Milliseconds between two ISO timestamps, or None if either is unusable."""
    if not start:
        return None
    try:
        return int(
            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
        )
    except ValueError:
        return None


def create_work_item(
    conn: sqlite3.Connection,
    *,
    id,
    bead_id,
    title,
    repo,
    chain_template,
    chain_definition,
    submodules: list[str] | None = None,
    root_merge_policy: str | None = None,
    attachments: list[dict] | None = None,
    status: str = "active",
) -> None:
    """`submodules` are the cross-repo paths chosen at intake (06, design 1g).

    They are stored as JSON rather than a side table: they are chosen once, never
    queried across items, and belong to this item as much as its chain does.

    `attachments` are the spec/plan documents chosen at intake (Kraft-dgh),
    stored as JSON for the same reason `submodules` is: chosen once, never
    queried across items.
    """
    now = _now()
    conn.execute(
        "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
        "chain_definition, current_node_id, status, created_at, updated_at, "
        "submodules, root_merge_policy, attachments) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
        (
            id,
            bead_id,
            title,
            repo,
            chain_template,
            chain_definition,
            status,
            now,
            now,
            json.dumps(submodules) if submodules else None,
            root_merge_policy if submodules else None,
            json.dumps(attachments) if attachments else None,
        ),
    )
    events.append(
        conn,
        id,
        "work_item_created",
        {"title": title, "repo": repo, "chain_template": chain_template},
    )
    if attachments:
        # Its own event, not a field on work_item_created: the timeline has to
        # explain why this item's chain has no spec node.
        events.append(conn, id, "work_item_attachments", {"attachments": attachments})


#: Root merge policies (design 1g). What happens to the root repo's submodule
#: pointer once the submodule MRs land.
ROOT_MERGE_POLICIES = ("bump", "skip", "bump_no_mr")


def repos_for(row: sqlite3.Row, merged_nodes: set[str]) -> list[dict]:
    """The item's repos, deepest submodule first (design 3a).

    Merge rank is depth: a submodule has to merge before the parent that points
    at it, so the deepest path merges first and the root repo merges last.
    """
    raw = row["submodules"]
    paths = json.loads(raw) if raw else []
    if not paths:
        return []
    ordered = sorted(paths, key=lambda p: (-p.count("/"), p))
    merged = "merge" in merged_nodes
    return [
        *(
            {
                "repo": Path(p).name,
                "path": p,
                "role": "submodule",
                "merge_rank": i + 1,
                "state": "merged" if merged else "pending",
            }
            for i, p in enumerate(ordered)
        ),
        {
            "repo": Path(row["repo"]).name,
            "path": row["repo"],
            "role": "root",
            "merge_rank": len(ordered) + 1,
            "state": "merged" if merged else "pending",
        },
    ]


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


def mark_needs_human(
    conn: sqlite3.Connection, work_item_id, node_id, reason, capped: dict | None = None
) -> None:
    """`capped` carries {cycles, attempts} when a loop cap is what stopped the item.

    The UI shows capped-out as a glyph *plus* the words "capped n/n", and the
    board never fetches sessions per row — so the numbers have to ride the
    event rather than be re-derived client-side.
    """
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    payload = {"node_id": node_id, "reason": reason}
    if capped is not None:
        payload["capped"] = capped
    events.append(conn, work_item_id, "work_item_needs_human", payload)


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


def retry_after_cap(conn: sqlite3.Connection, work_item_id: str, node_id: str, key: str, steer):
    """Clear a breached loop cap so the node can run again (handoff spec §8, 4b).

    The counter row is deleted rather than zeroed: `bump_counter` snapshots the
    cap and the wall-clock start on first fire, and a retry is a fresh budget,
    not a continuation of the exhausted one. The capped-out sessions stay as
    they are — they are the record of what was tried.
    """
    conn.execute(
        "DELETE FROM retry_counters WHERE work_item_id = ? AND key = ?", (work_item_id, key)
    )
    conn.execute(
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(
        conn,
        work_item_id,
        "work_item_retried",
        {"node_id": node_id, "loop": key, "steer": steer},
    )


def read_counter(conn: sqlite3.Connection, work_item_id: str, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM retry_counters WHERE work_item_id = ? AND key = ?",
        (work_item_id, key),
    ).fetchone()


def mark_sessions_capped_out(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, hook_points: list[str]
) -> None:
    # On breach every *measuring* task in the node becomes capped_out (02 §7.2) —
    # including one that ended 'failed' on the final cycle. A 'done' or
    # 'done_with_concerns' co-task (a clean, or clean-with-doubts, noop review)
    # is left as-is: the agent finished, and a cap breach elsewhere in the node
    # is not license to overwrite its status or lose its concerns text. Scoped
    # to the node's measuring hook points so the fix task's own session
    # (on.implementation.start) is not mislabelled as a capped-out measurement.
    placeholders = ",".join("?" * len(hook_points))
    where = (
        f"work_item_id = ? AND node_id = ? AND hook_point IN ({placeholders}) "
        f"AND status NOT IN ('done', 'done_with_concerns', 'capped_out')"
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


def reject_gate(conn: sqlite3.Connection, work_item_id, gate, note, *, reopen: bool) -> None:
    """Record the rejection. `reopen` flips the item back to active for the
    backward-motion re-run (02 §7.2); a terminal reject leaves it needs_human."""
    status = "'active'" if reopen else "status"
    conn.execute(
        f"UPDATE work_items SET status = {status}, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "gate_rejected", {"gate": gate, "note": note})


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
) -> None:
    """`round` is the fix-cycle index this session was dispatched in (0 = first pass)."""
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, created_at, exited_at, round) "
        "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'pending', 1, ?, NULL, ?)",
        (id, work_item_id, node_id, hook_point, log_path, result_path, _now(), round),
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
        {"session_id": id, "node_id": node_id, "hook_point": hook_point, "round": round},
    )


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
        "SELECT work_item_id, node_id, hook_point, round FROM worker_sessions WHERE id = ?",
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
            "pid": pid,
        },
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


def usage_rollup(conn: sqlite3.Connection, work_item_id: str) -> dict:
    """Per-node and per-item usage for one work item (handoff spec §8).

    Rounds count *distinct* fix cycles seen on the node, not sessions: a node
    that ran three tasks in one pass has run one round, not three.

    `cost_complete` is false when some session spent tokens but reported no cost
    — cost comes only from the agent, so a sum over those is a floor, not a
    total. Treating a missing cost as zero would quietly under-report the bill,
    which is the one thing a cost figure must not do.
    """
    rows = conn.execute(
        "SELECT node_id, round, tokens_in, tokens_out, cost_usd, wall_ms, status "
        "FROM worker_sessions WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchall()
    by_node: dict[str, dict] = {}
    rounds: dict[str, set[int]] = {}
    for r in rows:
        node = by_node.setdefault(
            r["node_id"],
            {
                "node": r["node_id"],
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "wall_ms": 0,
                "sessions": 0,
                "rounds": 0,
                "capped_out": 0,
                "cost_complete": True,
            },
        )
        node["tokens_in"] += r["tokens_in"] or 0
        node["tokens_out"] += r["tokens_out"] or 0
        node["cost_usd"] += r["cost_usd"] or 0.0
        # a session that spent tokens but reported no cost makes the sum a floor
        if r["cost_usd"] is None and (r["tokens_in"] or r["tokens_out"]):
            node["cost_complete"] = False
        node["wall_ms"] += r["wall_ms"] or 0
        node["sessions"] += 1
        node["capped_out"] += 1 if r["status"] == "capped_out" else 0
        rounds.setdefault(r["node_id"], set()).add(r["round"] or 0)
    for node_id, seen in rounds.items():
        by_node[node_id]["rounds"] = len(seen)

    nodes = list(by_node.values())
    total = {
        k: sum(n[k] for n in nodes)
        for k in ("tokens_in", "tokens_out", "cost_usd", "wall_ms", "sessions", "capped_out")
    }
    # An item's rounds is the deepest a single node had to loop, not the sum:
    # summing would read as "this item retried nine times" for nine clean nodes.
    total["rounds"] = max((n["rounds"] for n in nodes), default=0)
    total["cost_complete"] = all(n["cost_complete"] for n in nodes)
    return {"total": total, "by_node": sorted(nodes, key=lambda n: n["node"])}


def session_status(conn: sqlite3.Connection, session_id: str) -> str | None:
    row = conn.execute("SELECT status FROM worker_sessions WHERE id = ?", (session_id,)).fetchone()
    return row["status"] if row else None


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


def pause_work_item(conn: sqlite3.Connection, work_item_id: str, session_ids: list[str]) -> None:
    """Mark the item and its killed sessions paused (02 §10.2).

    The log files are left alone: a paused attempt's output is still the record
    of what it managed to do before a human stopped it.
    """
    now = _now()
    conn.execute(
        "UPDATE work_items SET status = 'paused', updated_at = ? WHERE id = ?",
        (now, work_item_id),
    )
    events.append(conn, work_item_id, "pause_requested", {"sessions": session_ids})
    for sid in session_ids:
        conn.execute(
            "UPDATE worker_sessions SET status = 'paused', exited_at = ? WHERE id = ?",
            (now, sid),
        )
        events.append(conn, work_item_id, "worker_session_paused", {"session_id": sid})


def set_steer(conn: sqlite3.Connection, work_item_id: str, text: str) -> None:
    conn.execute(
        "UPDATE work_items SET pending_steer_context = ?, updated_at = ? WHERE id = ?",
        (text, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "steer_context_set", {"steer": text})


def set_base_ref(conn: sqlite3.Connection, work_item_id: str, sha: str) -> None:
    """Pin the commit a work item's diff is measured against.

    Written once, when the worktree is created. A merge-base recomputed later
    moves when the default branch moves, and a diff that changes under an
    unchanged work item is worse than no diff.
    """
    conn.execute(
        "UPDATE work_items SET base_ref = ?, updated_at = ? WHERE id = ?",
        (sha, _now(), work_item_id),
    )


def take_steer(conn: sqlite3.Connection, work_item_id: str) -> str | None:
    """Read and clear the pending steer — it belongs to one relaunch, not to every
    later one."""
    row = conn.execute(
        "SELECT pending_steer_context FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()
    text = row["pending_steer_context"] if row else None
    if text:
        conn.execute(
            "UPDATE work_items SET pending_steer_context = NULL WHERE id = ?", (work_item_id,)
        )
    return text


def resume_work_item(conn: sqlite3.Connection, work_item_id: str, steer: str | None) -> None:
    """Back to active. Deliberately does NOT touch retry_counters (02 §10.2): a
    human-initiated interruption is not a plugin failure."""
    conn.execute(
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "work_item_resumed", {"steer": steer})
