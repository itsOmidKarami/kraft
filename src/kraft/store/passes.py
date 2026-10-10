from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from kraft.vocab import ChainEvent, GateEvent
from kraft.vocab.sql import in_list

#: What sends a node back to run again, and so starts a new pass of it.
_BOUNDARIES = (GateEvent.REJECTED, ChainEvent.RUN_FORKED, ChainEvent.BASE_CHANGE_RESTART)


def _started_by(event: dict, node: str, order: Sequence[str]) -> dict | None:
    """Why `event` starts a new pass of `node`, or None when it does not re-run it.

    A rejection re-runs from its re-entry node up to its gate; a retry, its
    node and every later one, but a retry of one step or task leaves its own
    node on the pass it is on (that is another attempt); a base-change restart,
    the nodes it lists. A node or gate the chain no longer names cannot be
    placed, and is taken as re-run.
    """
    at = {n: i for i, n in enumerate(order)}
    here = at.get(node)
    payload = event["payload"]

    def before(other: str | None) -> bool:
        return here is None or other not in at or at[other] <= here

    def after(other: str | None) -> bool:
        return here is None or other not in at or here <= at[other]

    if event["type"] == GateEvent.REJECTED:
        covered = before(payload.get("node")) and after(payload.get("gate"))
        return {"reason": "reject", "gate": payload.get("gate")} if covered else None
    if event["type"] == ChainEvent.RUN_FORKED:
        target = (payload.get("path") or "").split(".")[0] or None
        inside = target == node and payload.get("scope") in ("step", "task")
        return {"reason": "retry"} if before(target) and not inside else None
    return {"reason": "base_change"} if node in payload.get("nodes", [node]) else None


def number_passes(
    sessions: Sequence, boundaries: Sequence[dict], order: Sequence[str]
) -> tuple[dict[str, int], dict[str, list[dict]]]:
    """Which pass of its node each session ran in, 1-based, and each node's
    passes with what started them: `{"pass": 2, "reason": "reject", "gate": g}`.

    A pass is one time the chain runs a node; a fix loop's rounds are inside
    it. A new one starts at the first session after something sent the node
    back (`_started_by`), so two of those with nothing run between them are
    one pass, and one before the node ever ran starts nothing. It also starts
    where a measurement's round drops below the one before it: a person's
    retry of one task clears the loop counter the round is seeded from, and
    the node's rounds start over (a retry, when one came before the drop; else
    no reason to give). That was the only sign read before (Kraft-9d8b2.150),
    and a re-run that resumed at the round it left off at was read as one more
    attempt of that round.

    The loop's own repair and judge do not count for the drop (a fix cycle
    that was paused and refunded is measured again at a round below the repair
    it follows), nor does a negative round (`walk._REPAIR_ROUND`). An
    escalation turn (`escalation`, as the UI's `isEscalation` reads it) belongs
    to the node, not to a pass of it, and gets no number; nor does a gate's
    reviewer (`<gate>.auto_review`), whose runs are the attempts of one review.
    A stuck node's own escalation task (`<node>.escalation.<task>`) runs inside
    a pass like any task of the node, and is numbered.

    `sessions` and `boundaries` are in the order they were created; `order` is
    the chain's node ids.
    """
    passes: dict[str, int] = {}
    nodes: dict[str, list[dict]] = {}
    last: dict[str, str] = {}
    top: dict[str, int] = {}
    for s in sessions:
        node, hook = s["node_id"], s["hook_point"]
        if hook == "escalation" or hook.endswith((".escalation", ".auto_review")):
            continue
        started = nodes.setdefault(node, [{"pass": 1}])
        why = retried = None
        if node in last:
            for e in boundaries:
                if last[node] < e["created_at"] <= s["created_at"]:
                    why = _started_by(e, node, order) or why
                    retried = retried or e["type"] == ChainEvent.RUN_FORKED
        counts = s["round"] >= 0 and not hook.startswith(f"{node}.fix_loop.")
        if why is None and counts and s["round"] < top.get(node, -1):
            why = {"reason": "retry"} if retried else {}
        if why is not None:
            started.append({"pass": len(started) + 1, **why})
            top.pop(node, None)
        if counts:
            top[node] = s["round"]
        last[node] = s["created_at"]
        passes[s["id"]] = len(started)
    return passes, nodes


def session_passes(
    conn: sqlite3.Connection, work_item_id: str, order: Sequence[str]
) -> tuple[dict[str, int], dict[str, list[dict]]]:
    """`number_passes` over a work item's sessions and events."""
    sessions = conn.execute(
        "SELECT id, node_id, hook_point, round, created_at FROM worker_sessions "
        "WHERE work_item_id = ? ORDER BY created_at, rowid",
        (work_item_id,),
    ).fetchall()
    events = conn.execute(
        "SELECT type, payload, created_at FROM events WHERE work_item_id = ? "
        f"AND type IN ({in_list(_BOUNDARIES)}) ORDER BY seq",
        (work_item_id,),
    ).fetchall()
    boundaries = [
        {"type": e["type"], "payload": json.loads(e["payload"]), "created_at": e["created_at"]}
        for e in events
    ]
    return number_passes(sessions, boundaries, order)
