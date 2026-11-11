from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Sequence

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store import chain
from kraft.store._common import write_status
from kraft.store.counters import reset_fix_loops
from kraft.store.review import YOU, threads_for
from kraft.store.work_items import checkout_holder, queue_work_item, set_steer
from kraft.vocab import ChainEvent, GateEvent, ThreadLabel, ThreadState, WorkItemEvent
from kraft.vocab.events import GATE_CLOSED
from kraft.vocab.stop import StopKind


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


#: The events that name a gate in their `payload.gate`, and so are read only
#: with one.
_GATE_EVENTS = (
    GateEvent.REQUESTED,
    GateEvent.APPROVED,
    GateEvent.REJECTED,
    GateEvent.REOPENED,
    GateEvent.AUTO_REVIEW_SKIPPED,
    ChainEvent.NODE_SKIPPED,
)

#: The events `gate_rounds` reads. `GATE_CLOSED` brings the item-level endings
#: (`work_item_completed`, `work_item_abandoned`) along with the rest it lists.
_ROUND_EVENTS = (*_GATE_EVENTS, WorkItemEvent.NEEDS_HUMAN, *GATE_CLOSED)

#: Reasons of a `gate_auto_review_skipped` that are a reviewer's verdict; any
#: other (`unreviewable`, `profile_fallback`, `budget`, `checkout`) is not.
_REVIEW_VERDICT = {"passed": "ready", "undecided": "undecided"}


def gate_rounds(
    conn: sqlite3.Connection, work_item_id: str, *, with_contents: bool = True
) -> dict[str, list[dict]]:
    """Every gate's rounds, oldest first, read from the events (one SQL read,
    filtered to the types below: the item detail polls it).

    A round is one time the chain reached a gate and something ended it. It
    opens at a `gate_requested` and ends at the first of a `gate_approved`,
    `gate_rejected` or `node_skipped` that names the gate in `payload.gate`
    (never the event's `node_id` column: a rejection stores its re-entry node
    there; a skip of a node with no gate carries `gate: null` and is no round,
    unless its `node_id` is a gate that already has rounds: after the reject
    cap `pending_gate` is None, so the skip of that gate carries `gate: null`
    too), or an item-level ending (`work_item_completed`, `work_item_abandoned`:
    outcome `closed`, for every round still open). A round with no ending yet is
    `pending`.

    A `gate_requested` while the gate's round is still open (a restart asking
    again) is the same round: `requested_at` is the first ask. An ending with
    no open round is a round of its own, `requested_at` None. Three real paths
    write one: `pass_unchanged_revision` (a chain revision that changes
    nothing, approved by `kraft`), `bounce_on_feedback` (a review-thread bounce
    rejects before the gate is asked), and a skip of a gate whose round already
    ended (after the reject cap left the item on it).

    `gate_reopened` ends and starts nothing: it marks the gate's newest ended
    round `reopened`. A rejection that breached the reject cap sent nothing
    back: the `work_item_needs_human` of `kind` cap on the gate that follows it
    sets `sent_to` None. `reviewer` is the round's own auto-review outcome, as
    `reviewOutcome` reads it in the UI: an ending `by: agent`, or a
    `gate_auto_review_skipped` with reason `passed` or `undecided`; the later
    wins.

    With `with_contents` each round also carries its `review` and `threads`
    (`_attach_round_contents`): what was submitted at the gate in the round's
    window, `(previous round's ended_at, this round's ended_at]`.
    """
    rows = conn.execute(
        "SELECT type, payload, created_at FROM events WHERE work_item_id = ? "
        f"AND type IN ({', '.join('?' * len(_ROUND_EVENTS))}) "
        "AND (type != ? OR json_extract(payload, '$.kind') = ?) ORDER BY seq",
        (
            work_item_id,
            *_ROUND_EVENTS,
            WorkItemEvent.NEEDS_HUMAN,
            StopKind.CAP.value,
        ),
    ).fetchall()
    rounds: dict[str, list[dict]] = {}
    opened: dict[str, dict] = {}  # gate -> its round with no ending yet

    def end(gate: str, at: str, outcome: str, **ending) -> dict:
        r = opened.pop(gate, None)
        if r is None:
            r = _round(len(rounds.setdefault(gate, [])) + 1, None)
            rounds[gate].append(r)
        r.update(ended_at=at, outcome=outcome, **ending)
        return r

    for row in rows:
        kind, at, p = row["type"], row["created_at"], json.loads(row["payload"])
        gate = p.get("gate")
        if kind == ChainEvent.NODE_SKIPPED and not gate and p.get("node_id") in rounds:
            gate = p["node_id"]  # a skip after the reject cap names no gate, only the node
        if not gate and kind in _GATE_EVENTS:
            continue
        if kind == GateEvent.REQUESTED:
            if gate not in opened:
                r = _round(len(rounds.setdefault(gate, [])) + 1, at)
                rounds[gate].append(r)
                opened[gate] = r
        elif kind in (GateEvent.APPROVED, GateEvent.REJECTED):
            rejected = kind == GateEvent.REJECTED
            outcome = (
                ("fixed" if p.get("verdict") == "fixed" else "rejected") if rejected else "approved"
            )
            r = end(
                gate,
                at,
                outcome,
                by=p.get("by"),
                note=p.get("note") if rejected else None,
                sent_to=p.get("node") if rejected else None,
                verdict=p.get("verdict") if rejected else None,
            )
            if p.get("by") == "agent":
                note = p.get("note") or p.get("reason") or p.get("summary") or ""
                r["reviewer"] = {
                    "kind": "reject" if rejected else "approve",
                    "note": str(note).strip(),
                }
        elif kind == ChainEvent.NODE_SKIPPED:
            end(gate, at, "skipped", note=p.get("note"))
        elif kind in GATE_CLOSED:
            for g in list(opened):
                end(g, at, "closed")
        elif kind == GateEvent.REOPENED:
            if rounds.get(gate) and gate not in opened:
                rounds[gate][-1]["reopened"] = True
        elif kind == GateEvent.AUTO_REVIEW_SKIPPED:
            if gate in opened and p.get("reason") in _REVIEW_VERDICT:
                opened[gate]["reviewer"] = {
                    "kind": _REVIEW_VERDICT[p["reason"]],
                    "note": str(p.get("note") or "").strip(),
                }
        elif kind == WorkItemEvent.NEEDS_HUMAN:  # only the cap's, by the query
            newest = (rounds.get(p.get("node_id")) or [None])[-1]
            if newest is not None and newest["outcome"] == "rejected":
                newest["sent_to"] = None
    if with_contents:
        _attach_round_contents(conn, work_item_id, rounds)
    return rounds


def _round(n: int, requested_at: str | None) -> dict:
    return {
        "n": n,
        "requested_at": requested_at,
        "ended_at": None,
        "outcome": "pending",
        "by": None,
        "note": None,
        "sent_to": None,
        "verdict": None,
        "reopened": False,
        "reviewer": None,
        "review": None,
        "threads": _thread_counts([]),
        "first_threads": [],
    }


def _windows(rounds: list[dict]) -> list[tuple[str | None, str | None]]:
    """Each round's window `(after, upto]` over a gate's rounds, oldest first:
    `after` is the previous round's `ended_at` (None for the first), `upto` its
    own (None while pending). The one place the rule lives: a review or thread
    made between two rounds belongs to the later one, and `requested_at` plays
    no part. Timestamps are ISO strings, so they compare as text."""
    return [(rounds[i - 1]["ended_at"] if i else None, r["ended_at"]) for i, r in enumerate(rounds)]


def _in_window(window: tuple[str | None, str | None], at: str) -> bool:
    after, upto = window
    return (after is None or at > after) and (upto is None or at <= upto)


def _thread_counts(threads: list[dict]) -> dict:
    by_label = {label.value: 0 for label in ThreadLabel}
    for t in threads:
        if t["label"] in by_label:
            by_label[t["label"]] += 1
    return {
        "total": len(threads),
        "open": sum(t["state"] != ThreadState.RESOLVED for t in threads),
        "by_label": by_label,
    }


def _gate_thread_rows(conn: sqlite3.Connection, work_item_id: str) -> dict[str, list[dict]]:
    """Every gate thread of the item that is no draft, by gate, oldest first:
    one read, the first comment joined in. A draft is `store.review.is_draft_thread`'s."""
    out: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT t.id, t.gate, t.file_path, t.start_line, t.end_line, t.label, t.state, "
        "t.created_at, c.author, c.review_id, c.body FROM review_threads t "
        "JOIN review_comments c ON c.id = (SELECT id FROM review_comments "
        "WHERE thread_id = t.id ORDER BY created_at, rowid LIMIT 1) "
        "WHERE t.work_item_id = ? AND t.gate IS NOT NULL ORDER BY t.created_at, t.rowid",
        (work_item_id,),
    ):
        if r["author"] == YOU and r["review_id"] is None:
            continue  # a draft, as store.review.is_draft_thread says
        out.setdefault(r["gate"], []).append(
            {
                "id": r["id"],
                "file_path": r["file_path"],
                "start_line": r["start_line"],
                "end_line": r["end_line"],
                "label": r["label"],
                "state": r["state"],
                "body": r["body"].split("\n", 1)[0],
                "created_at": r["created_at"],
            }
        )
    return out


def _round_threads(threads: list[dict]) -> tuple[dict, list[dict]]:
    first = sorted(threads, key=lambda t: t["label"] != ThreadLabel.MUST_FIX)[:3]
    shown = [{k: v for k, v in t.items() if k != "created_at"} for t in first]
    return _thread_counts(threads), shown


def _attach_round_contents(
    conn: sqlite3.Connection, work_item_id: str, rounds: dict[str, list[dict]]
) -> None:
    """Give each round its `review` (the newest one submitted at the gate in its
    window) and its `threads` / `first_threads`. Two reads for every gate."""
    reviews: dict[str, list] = {}
    for r in conn.execute(
        "SELECT id, gate, outcome, summary, head_sha, submitted_at FROM reviews "
        "WHERE work_item_id = ? AND gate IS NOT NULL ORDER BY submitted_at, rowid",
        (work_item_id,),
    ):
        reviews.setdefault(r["gate"], []).append(r)
    threads = _gate_thread_rows(conn, work_item_id)
    for gate, rs in rounds.items():
        for r, window in zip(rs, _windows(rs), strict=True):
            made = [x for x in reviews.get(gate, ()) if _in_window(window, x["submitted_at"])]
            if made:
                newest = made[-1]
                r["review"] = {
                    "id": newest["id"],
                    "outcome": newest["outcome"],
                    "summary": newest["summary"] or "",
                    "head_sha": newest["head_sha"],
                }
            r["threads"], r["first_threads"] = _round_threads(
                [t for t in threads.get(gate, ()) if _in_window(window, t["created_at"])]
            )


def threads_in_round(
    conn: sqlite3.Connection, work_item_id: str, gate: str, n: int
) -> list[dict] | None:
    """The non-draft threads round `n` of `gate` counts, in the shape
    `threads_for` gives (comments, quote, state); None when the gate has no
    round `n`."""
    rounds = gate_rounds(conn, work_item_id, with_contents=False).get(gate, [])
    if not 1 <= n <= len(rounds):
        return None
    window = _windows(rounds)[n - 1]
    return [
        t
        for t in threads_for(conn, work_item_id, gate)
        if not t["draft"] and _in_window(window, t["created_at"])
    ]
