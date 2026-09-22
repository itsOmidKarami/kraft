from __future__ import annotations

import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store import chain
from kraft.store._common import write_status


def request_gate(conn: sqlite3.Connection, work_item_id, node_id, gate) -> None:
    if not write_status(
        conn,
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    ):
        return
    events.append(conn, work_item_id, "gate_requested", {"gate": gate, "node_id": node_id})


def approve_gate(conn: sqlite3.Connection, work_item_id, gate, *, by: str = "human") -> None:
    """Clear the gate and set the item running again.

    Also closes the gate node's own `node_started`/`node_completed` pair. A V1
    gate *is* a node (`gate-is-an-ordered-node`) and `gates.maybe_gate` enters
    it, but nothing ever completed it: the walk resumes at the node *after* the
    gate, so the board rendered an approved gate as a node still running,
    forever (4a's Concern 4). The gate's node id and its name are the same
    string in V1, which is why one argument answers both.

    Written here rather than at each approval door so the human's `POST
    .../approve` and `review_gates`' `approve` verdict cannot drift -- the one
    rule the two doors have to keep. A rejection is deliberately *not* a
    completion: the gate reopens, and `gate_rejected` already says so.
    `complete_node` is idempotent, so re-approving writes one event.
    """
    if not write_status(
        conn,
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    ):
        return
    events.append(conn, work_item_id, "gate_approved", {"gate": gate, "by": by})
    chain.complete_node(conn, work_item_id, gate)


def reject_gate(
    conn: sqlite3.Connection,
    work_item_id,
    gate,
    note,
    *,
    reopen: bool,
    node: str | None = None,
    by: str = "human",
    verdict: str | None = None,
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

    `verdict` records *what* was decided, for the same reason and one level
    down: `by: agent` alone cannot tell a reviewer that rejected from one that
    repaired the worktree and committed (`gate_review.VERDICTS`' `fixed`), and
    those two have very different consequences -- a `fixed` re-enters at the
    gate's own node and regenerates the artifact the gate is about. Without it
    that cycle is invisible in every aggregate and only readable by putting
    session logs in order by hand (Kraft-s7c04.16). Rides the event alongside
    `note` and `node` for the reason those do.
    """
    status = "'active'" if reopen else "status"
    if not write_status(
        conn,
        f"UPDATE work_items SET status = {status}, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    ):
        return
    events.append(
        conn,
        work_item_id,
        "gate_rejected",
        {"gate": gate, "note": note, "node": node, "by": by, "verdict": verdict},
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
