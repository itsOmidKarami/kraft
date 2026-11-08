"""Bookkeeping for the CI re-runs a fix-loop repair asks for (Kraft-zje6v).

A repair that finds a CI job died outside the code (a download 504, a runner
that went away) calls `kraft item rerun-ci`; the route records
`ForgeEvent.CI_RERUN_REQUESTED`. These helpers read that record back: whether a
node is one a re-run makes sense for, whether a fix attempt is open, and which
re-runs a node has made. They read events only, so a crash and resume sees the
same answer as the live run.
"""

from __future__ import annotations

import sqlite3

from kraft import events
from kraft.templates.models import ForgeAction, ForgeTask, ResolvedNode
from kraft.vocab import RESTARTS_RUN, ChainEvent, ForgeEvent, GateEvent

__all__ = [
    "waits_on_mr_ci",
    "open_fix_cycle",
    "reran_last_cycle",
    "reruns",
    "node_run_start",
    "node_of",
]


def waits_on_mr_ci(node: ResolvedNode | None) -> bool:
    """Whether `node` has a task that waits on the merge request's CI. `False`
    for `None`: a node the caller could not find in the item's chain."""
    return node is not None and any(
        isinstance(t.task, ForgeTask) and t.task.target is ForgeAction.MR_CI for t in node.tasks()
    )


def node_of(chain, node_id: str | None) -> ResolvedNode | None:
    """The node called `node_id` in a `MaterializedChain` (or None), or None
    when there is no chain."""
    if chain is None or node_id is None:
        return None
    return next((n for n in chain.chain.nodes if n.id == node_id), None)


def _node_events(conn: sqlite3.Connection, work_item_id: str, node_id: str, *types) -> list[dict]:
    return [
        e
        for e in events.read_after(conn, 0, work_item_id)
        if e["node_id"] == node_id and e["type"] in types
    ]


def open_fix_cycle(conn: sqlite3.Connection, work_item_id: str, node_id: str) -> dict | None:
    """The node's latest `fix_cycle_started` event, provided no finish or
    refund for the node follows it. This is "the fix attempt running now": not
    the cycle number, which a refunded round reuses."""
    cycle = _node_events(
        conn,
        work_item_id,
        node_id,
        ChainEvent.FIX_CYCLE_STARTED,
        ChainEvent.FIX_CYCLE_FINISHED,
        ChainEvent.FIX_CYCLE_REFUNDED,
    )
    return cycle[-1] if cycle and cycle[-1]["type"] == ChainEvent.FIX_CYCLE_STARTED else None


def reran_last_cycle(conn: sqlite3.Connection, work_item_id: str, node_id: str) -> bool:
    """Whether the node's latest fix cycle, finished or not, re-ran CI. Read
    from events, so a crash and resume sees what the live run saw."""
    started = _node_events(conn, work_item_id, node_id, ChainEvent.FIX_CYCLE_STARTED)
    after = started[-1]["seq"] if started else 0
    return bool(reruns(conn, work_item_id, node_id, after_seq=after))


def reruns(
    conn: sqlite3.Connection, work_item_id: str, node_id: str, *, after_seq: int
) -> list[dict]:
    """The node's `ci_rerun_requested` events after `after_seq`, oldest first."""
    return [
        e
        for e in _node_events(conn, work_item_id, node_id, ForgeEvent.CI_RERUN_REQUESTED)
        if e["seq"] > after_seq
    ]


def node_run_start(conn: sqlite3.Connection, work_item_id: str, node_id: str) -> int:
    """The seq where the node's current run began, or 0: after the latest
    restart of the run (a retry, a fork, a base change or merge rejection that
    rewinds it), gate decision, or the node's last completion. Not
    `node_started`, which `dispatch.measure_node` fires once per measurement
    round, after the earlier rounds' re-runs."""
    spent = [
        e["seq"]
        for e in events.read_after(conn, 0, work_item_id)
        if e["type"] in (*RESTARTS_RUN, GateEvent.APPROVED, GateEvent.REJECTED)
        or (e["type"] == ChainEvent.NODE_COMPLETED and e["node_id"] == node_id)
    ]
    return max(spent, default=0)
