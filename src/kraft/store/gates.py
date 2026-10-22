from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store import chain
from kraft.store._common import write_status
from kraft.store.counters import reset_fix_loops
from kraft.store.work_items import checkout_holder, queue_work_item, set_steer
from kraft.vocab import ChainEvent, GateEvent, WorkItemEvent


def request_gate(conn: sqlite3.Connection, work_item_id, node_id, gate) -> None:
    if not write_status(
        conn,
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    ):
        return
    events.append(conn, work_item_id, GateEvent.REQUESTED, {"gate": gate, "node_id": node_id})


def approve_gate(
    conn: sqlite3.Connection, work_item_id, gate, *, by: str = "human", tips: dict | None = None
) -> bool | str:
    """Clear the gate and set the item running again. True when it is running.

    `tips` is the head the chain's final gate approves (`reviewed_head.pin_for`),
    read before this write: recorded on `gate_approved`, it is what the merge
    may push. None for any other gate.

    While another in-place item works in this item's checkout
    (`checkout_holder`), the approval is still recorded, the item is paused
    and queued to go on from the gate once the checkout frees
    (`hold_for_checkout`), and the holder's id is returned. False, and
    nothing written, for an ended item.

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
    holder = checkout_holder(conn, work_item_id)
    if not write_status(
        conn,
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?"
        if holder is None
        else "UPDATE work_items SET status = 'paused', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    ):
        return False
    payload = {"gate": gate, "by": by, **({"tips": tips} if tips is not None else {})}
    events.append(conn, work_item_id, GateEvent.APPROVED, payload, node_id=gate)
    chain.complete_node(conn, work_item_id, gate)
    if holder is None:
        return True
    # `approved`: its replay refreshes the base first, as a direct approval does.
    hold_for_checkout(conn, work_item_id, holder, {"node": gate, "approved": True})
    return holder


def hold_for_checkout(conn: sqlite3.Connection, work_item_id, holder: str, body: dict) -> None:
    """Queue a paused item's gate decision to go on as Kraft's own walk from
    `body["node"]`, with the item's pending steer (`continue`,
    `kraft.start_queue`), not as a person's resume: the decision is already
    recorded, and its replay is not a new one."""
    queue_work_item(
        conn,
        work_item_id,
        verb="continue",
        body=body,
        headers={},
        from_statuses=["paused"],
        held_by=holder,
    )


def queue_follow_up(conn: sqlite3.Connection, work_item_id, holder: str) -> bool:
    """Queue what Kraft's own agents do at a stop (the gate's reply and
    review, the stop's auto-escalation) while `holder` uses the checkout:
    replayed as Kraft's (`follow_up`, `executor.gates.follow_up`), with no
    person's caller."""
    return queue_work_item(
        conn,
        work_item_id,
        verb="follow_up",
        body={},
        headers={},
        from_statuses=["needs_human"],
        held_by=holder,
    )


def pass_unchanged_revision(conn: sqlite3.Connection, work_item_id, gate, rationale) -> None:
    """Clear a chain revision gate whose proposal changes nothing, without
    asking anyone (Kraft-oydes): the proposal's rationale on
    `chain_revision_unchanged`, then an approval by `kraft` -- neither a person
    nor an agent decided it, so a reader of `by` cannot mistake it for either."""
    events.append(
        conn,
        work_item_id,
        GateEvent.CHAIN_REVISION_UNCHANGED,
        {"gate": gate, "rationale": rationale},
        node_id=gate,
    )
    approve_gate(conn, work_item_id, gate, by="kraft")


#: The events that open or close a gate: the newest one naming a gate is its
#: standing word.
_GATE_WORD = (GateEvent.REQUESTED, GateEvent.APPROVED, GateEvent.REJECTED, GateEvent.REOPENED)


def approval_stands(
    conn: sqlite3.Connection, work_item_id, gate: str, upstream: Collection[str]
) -> bool:
    """Whether `gate`'s approval still stands, so a walk passes over it.

    Per gate: the newest of `gate_requested`, `gate_approved`, `gate_rejected`
    and `gate_reopened` that names `gate` must be its approval -- another
    gate's approval since says nothing about this one. And nothing since may
    have sent the chain back to a node in `upstream`, the ids of the chain's
    nodes up to and including `gate`: that re-run walks back through `gate`
    with new work, so it is asked again. Two motions do that without a
    `gate_reopened` for the gates they span, so their own events are the word:
    a rejection of any gate, re-entering at its `node` (one with none re-entered
    at its own gate), and a person's gateless `request_changes`, rewinding to its
    `target` unless it was cancelled (`review.request_rewind`).
    """
    row = conn.execute(
        "SELECT seq, type FROM events WHERE work_item_id = ? "
        f"AND type IN ({', '.join('?' * len(_GATE_WORD))}) "
        "AND json_extract(payload, '$.gate') = ? ORDER BY seq DESC LIMIT 1",
        (work_item_id, *_GATE_WORD, gate),
    ).fetchone()
    if row is None or row["type"] != GateEvent.APPROVED:
        return False
    later = conn.execute(
        "SELECT type, json_extract(payload, '$.review_id') AS rid, "
        "COALESCE(json_extract(payload, '$.node'), json_extract(payload, '$.gate'), "
        "json_extract(payload, '$.target')) AS entered FROM events "
        "WHERE work_item_id = ? AND type IN (?, ?, ?) AND seq > ? ORDER BY seq DESC",
        (
            work_item_id,
            GateEvent.REJECTED,
            GateEvent.REWIND_REQUESTED,
            GateEvent.REWIND_CANCELLED,
            row["seq"],
        ),
    ).fetchall()
    cancelled = set()
    for e in later:
        if e["type"] == GateEvent.REWIND_CANCELLED:
            cancelled.add(e["rid"])
        elif e["entered"] in upstream and not (
            e["type"] == GateEvent.REWIND_REQUESTED and e["rid"] in cancelled
        ):
            return False
    return True


def _since_requested(conn: sqlite3.Connection, work_item_id, gate, type: str) -> list[dict]:
    """`type` events about `gate` since it was last requested, oldest first."""
    found = []
    for e in reversed(events.read_after(conn, 0, work_item_id)):
        if e["payload"].get("gate") != gate:
            continue
        if e["type"] == GateEvent.REQUESTED:
            break
        if e["type"] == type:
            found.insert(0, e["payload"])
    return found


def shown_revision(conn: sqlite3.Connection, work_item_id, gate) -> str | None:
    """The digest of the chain revision `gate` last showed a person since it
    was requested (`revision.digest`), or None if nobody has looked."""
    shown = _since_requested(conn, work_item_id, gate, GateEvent.CHAIN_REVISION_SHOWN)
    return shown[-1]["digest"] if shown else None


def show_revision(conn: sqlite3.Connection, work_item_id, gate, digest: str) -> None:
    """Record that `gate` showed the revision `digest` (Kraft-ze1yj): what an
    approval of it must still apply. Once per distinct result, so a board
    polling the document writes nothing new."""
    if shown_revision(conn, work_item_id, gate) != digest:
        events.append(
            conn,
            work_item_id,
            GateEvent.CHAIN_REVISION_SHOWN,
            {"gate": gate, "digest": digest},
            node_id=gate,
        )


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
    rerun: Sequence[str] = (),
) -> str | None:
    """Record the rejection. `reopen` flips the item back to active for the
    backward-motion re-run (02 §7.2); a rejection that breached the gate's
    reject loop leaves it needs_human.

    A reopen while another in-place item works in this item's checkout
    (`checkout_holder`) pauses the item instead and queues the re-run from
    `node`, with the note as its steer (`hold_for_checkout`); the holder's id
    is returned. None otherwise.

    `rerun` is the nodes with a fix loop that the re-run measures again: a
    rejection that reopens gives them their rounds back
    (`counters.reset_fix_loops`), here so that it is one write with the
    rejection, and an item that has ended (which records none) resets nothing.

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
    holder = checkout_holder(conn, work_item_id) if reopen else None
    status = "status" if not reopen else "'active'" if holder is None else "'paused'"
    if not write_status(
        conn,
        f"UPDATE work_items SET status = {status}, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    ):
        return None
    events.append(
        conn,
        work_item_id,
        GateEvent.REJECTED,
        {"gate": gate, "note": note, "node": node, "by": by, "verdict": verdict},
    )
    if reopen:
        reset_fix_loops(conn, work_item_id, list(rerun), by)
    if holder is not None:
        # The re-run is kept on the row, not only in the queued request, so a
        # person who pauses the held item and resumes it by hand gets it too:
        # the cursor at its target, the note as the next steer.
        if node is not None:
            conn.execute(
                "UPDATE work_items SET current_node_id = ?, current_step = 0 WHERE id = ?",
                (node, work_item_id),
            )
        if note:
            set_steer(conn, work_item_id, note)
        # An agent's note is not a person's steer (`walk.run`'s `steer_source`).
        source = {"steer_source": "gate_review"} if by == "agent" else {}
        hold_for_checkout(conn, work_item_id, holder, {"node": node, **source})
    return holder


#: Events that mean the newest rejection has already been acted on, so its note
#: is spent. Newest-wins, the same shape of boundary `kraft.api.routes.board._stop_reason` uses:
#: without one, an old addressed rejection would steer an unrelated retry many
#: nodes later.
_REJECTION_SPENT = (
    ChainEvent.NODE_STARTED,
    WorkItemEvent.RETRIED,
    GateEvent.REQUESTED,
    GateEvent.APPROVED,
)


def steer_source(conn: sqlite3.Connection, work_item_id: str, steer: str | None) -> str:
    """Who wrote `steer`, the item's pending steer (`walk.run`'s
    `steer_source`): `gate_review` when it is the note of an agent's rejection
    the item still sits on (a re-run `reject_gate` held for the checkout),
    `human` otherwise. Read off the rejection itself, so a person who pauses
    the held item and resumes it does not make the agent's note theirs."""
    rejected = last_rejection(conn, work_item_id)
    by_agent = rejected is not None and rejected.get("by") == "agent"
    return "gate_review" if steer and by_agent and rejected.get("note") == steer else "human"


def last_rejection(conn: sqlite3.Connection, work_item_id: str) -> dict | None:
    """The `gate_rejected` payload the item is currently sitting on, or None.

    Read back out of the append-only events table rather than stored a second
    time on the row: the note is already durable there, it just had no reader
    (Kraft-ko7j).
    """
    for e in reversed(events.read_after(conn, 0, work_item_id)):
        if e["type"] == GateEvent.REJECTED:
            return e["payload"]
        if e["type"] in _REJECTION_SPENT:
            return None
    return None
