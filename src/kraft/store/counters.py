from __future__ import annotations

import sqlite3

from kraft import events
from kraft.policy import Cap
from kraft.store import _now as _now  # test seam for wall-clock checks


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
    # `escalate_after` comes from the freshly-resolved cap, not the row: it is a
    # routing hint for the next launch, not a limit the item was admitted under,
    # so there is nothing to hold steady across an edited policy.yaml.
    return (
        new_count,
        row["started_at"],
        Cap(row["cap_attempts"], row["cap_wall_s"], escalate_after=cap.escalate_after),
    )


def retry_after_cap(
    conn: sqlite3.Connection,
    work_item_id: str,
    node_id: str,
    key: str | None,
    steer,
    *,
    gate_key: str | None = None,
    escalated: bool = False,
):
    """Clear a breached loop cap so the node can run again (handoff spec §8, 4b).

    The counter row is deleted rather than zeroed: `bump_counter` snapshots the
    cap and the wall-clock start on first fire, and a retry is a fresh budget,
    not a continuation of the exhausted one. The capped-out sessions stay as
    they are — they are the record of what was tried.

    `key` is None for a node with no fix loop: there is no counter to clear, but
    the item still has to be put back to work. A plain task failure strands an
    item exactly as hard as a breached cap does (Kraft-bzwi).

    `gate_key` is the `<gate>_reject_loop` of the node's own gate, when it has
    one. Retry is the human's override of the reject cap too (Kraft-ko7j §A4);
    without clearing it, the retried node re-opens its gate onto a spent
    counter and every rejection after that is refused forever.

    Also clears `ci_wait:<node_id>` and `ci_infra:<node_id>` unconditionally,
    built here from `node_id` rather than threaded in by the caller. A node
    can have been parked mid-poll, or mid-infra-retry (the persisted
    `ci_infra:<node_id>` counter Task 4 added), regardless of whether it has
    a fix loop at all, and a retry is the human's explicit "give this a fresh
    budget" for both of those too (Kraft-cs4s) -- not just for `key`, which is
    None for a node with no fix loop.

    Duplicates `ci_wait.py`'s private `_key()` format string (`f"ci_wait:
    {node_id}"`) and Task 4's `ci_infra:{node_id}` format, the same way
    `templates._FORGE_BACKENDS`/`adapters.forge.resolve` already duplicate
    each other's backend name list, with the same "edit both together"
    comment -- the duplication is contained to this one function and
    `ci_wait.py`, not spread to every caller that wants a retry.

    `escalated` is True only for a retry `gates.auto_escalate_stuck` performed
    on the escalated agent's own behalf, after its escalation session exited.
    Tagged on the `work_item_retried` event so `gates._auto_dispatch_count`
    can tell it apart from a human's own retry: a human retrying resets the
    auto-escalate cap fairly (a person looked at it), but a retry the
    auto-escalation caused must not reset the very cap meant to bound it, or
    an unfixable stop (e.g. a budget breach a retry cannot clear) escalates
    forever.
    """
    counters = (key, gate_key, f"ci_wait:{node_id}", f"ci_infra:{node_id}")
    for counter in counters:
        if counter is not None:
            conn.execute(
                "DELETE FROM retry_counters WHERE work_item_id = ? AND key = ?",
                (work_item_id, counter),
            )
    conn.execute(
        "UPDATE work_items SET status = 'active', retry_at = NULL, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(
        conn,
        work_item_id,
        "work_item_retried",
        {"node_id": node_id, "loop": key, "steer": steer, "escalated": escalated},
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
