from __future__ import annotations

import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks


def request_gate(conn: sqlite3.Connection, work_item_id, node_id, gate) -> None:
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "gate_requested", {"gate": gate, "node_id": node_id})


def approve_gate(conn: sqlite3.Connection, work_item_id, gate, *, by: str = "human") -> None:
    conn.execute(
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(conn, work_item_id, "gate_approved", {"gate": gate, "by": by})


def reject_gate(
    conn: sqlite3.Connection,
    work_item_id,
    gate,
    note,
    *,
    reopen: bool,
    node: str | None = None,
    by: str = "human",
) -> None:
    """Record the rejection. `reopen` flips the item back to active for the
    backward-motion re-run (02 §7.2); a rejection that breached the gate's
    reject loop leaves it needs_human.

    `node` is the chain node the re-run enters at (Kraft-ko7j). It rides the
    event rather than a column: the events table is already append-only and
    already holds the note, and `store.last_rejection` reads both back.

    `by` records who decided (Kraft-zr3s) -- a gate cleared by an agent and a
    gate cleared by a person have to be tellable apart in the timeline forever
    after.
    """
    status = "'active'" if reopen else "status"
    conn.execute(
        f"UPDATE work_items SET status = {status}, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
    events.append(
        conn, work_item_id, "gate_rejected", {"gate": gate, "note": note, "node": node, "by": by}
    )


#: Events that mean the newest rejection has already been acted on, so its note
#: is spent. Newest-wins, the same shape of boundary `kraft.api.routes.board._stop_reason` uses:
#: without one, an old addressed rejection would steer an unrelated retry many
#: nodes later.
_REJECTION_SPENT = ("node_started", "work_item_retried", "gate_requested", "gate_approved")


def last_rejection(conn: sqlite3.Connection, work_item_id: str) -> dict | None:
    """The `gate_rejected` payload the item is currently sitting on, or None.

    Read back out of the append-only events table rather than stored a second
    time on the row: the note is already durable there, it just had no reader
    (Kraft-ko7j).
    """
    for e in reversed(events.read_after(conn, 0, work_item_id)):
        if e["type"] == "gate_rejected":
            return e["payload"]
        if e["type"] in _REJECTION_SPENT:
            return None
    return None
