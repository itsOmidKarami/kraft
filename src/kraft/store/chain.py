from __future__ import annotations

import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks


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


def splice_chain(conn: sqlite3.Connection, work_item_id, chain_definition: str) -> None:
    """Replace the item's `chain_definition` with a chain-review revision
    (Kraft-hm0), already validated and already the complete tail. Its own
    event, separate from `gate_approved`, so the timeline shows the row
    changed underneath the approval rather than folding it into a payload
    nothing reads.
    """
    conn.execute(
        "UPDATE work_items SET chain_definition = ?, updated_at = ? WHERE id = ?",
        (chain_definition, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "chain_spliced", {})


def set_chain_template(
    conn: sqlite3.Connection, work_item_id, template_id: str, chain_definition: str
) -> None:
    """Switch a not-yet-started item onto a different chain template
    (Kraft-gwn6): the caller has already 404'd an unknown template and 409'd a
    started item, and already recomputed `chain_definition` by calling
    `templates.materialize` the same way `executor.intake` would have, so this
    is just the write. Its own event type, not folded into `chain_spliced`:
    that event means the chain-review splice path touched the row; this means
    intake's own materialization ran again against a different template,
    which is a different question to answer from the timeline.
    """
    old_template_id = conn.execute(
        "SELECT chain_template FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE work_items SET chain_template = ?, chain_definition = ?, updated_at = ? "
        "WHERE id = ?",
        (template_id, chain_definition, _now(), work_item_id),
    )
    events.append(
        conn, work_item_id, "chain_template_changed", {"from": old_template_id, "to": template_id}
    )


def skip_node(
    conn: sqlite3.Connection,
    work_item_id: str,
    node_id: str,
    gate: str | None,
    note: str | None,
    *,
    session_ids: list[str] | None = None,
) -> None:
    """Advance past `node_id` (its own gate `gate`, if it has one and that is
    what is being bypassed) without running or approving it.

    Sets the item back to `active` the same way `approve_gate` and
    `retry_after_cap` do — the caller spawns `executor.run` right after this
    write, same as every other door onto the chain. `retry_at` is cleared for
    the same reason `pause_work_item` clears it: a `ci_wait`-due item skipped
    out from under the poller must not wake back up under the old wait.

    `session_ids` carries the node's own running sessions when the skip
    interrupts a live attempt — marked `paused` here, *before* the caller's
    `_terminate` signals them, so the adapter's death handler reads a session
    it expected to stop rather than one that just failed (`pause_work_item`'s
    ordering, same race).
    """
    now = _now()
    conn.execute(
        "UPDATE work_items SET status = 'active', retry_at = NULL, updated_at = ? WHERE id = ?",
        (now, work_item_id),
    )
    events.append(
        conn, work_item_id, "node_skipped", {"node_id": node_id, "gate": gate, "note": note}
    )
    for sid in session_ids or []:
        conn.execute(
            "UPDATE worker_sessions SET status = 'paused', exited_at = ? WHERE id = ?",
            (now, sid),
        )
        events.append(conn, work_item_id, "worker_session_paused", {"session_id": sid})
