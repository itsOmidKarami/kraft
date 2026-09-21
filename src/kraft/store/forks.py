from __future__ import annotations

import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store.chain import materialized_chain_of
from kraft.store.counters import clear_loop_counters


def current_fork(conn: sqlite3.Connection, work_item_id: str):
    """The run fork this item is executing, or None for the intake run."""
    from kraft.templates.forks import RunFork

    row = conn.execute(
        "SELECT * FROM run_forks WHERE work_item_id = ? ORDER BY rowid DESC LIMIT 1",
        (work_item_id,),
    ).fetchone()
    return RunFork.from_row(row) if row is not None else None


def run_forks(conn: sqlite3.Connection, work_item_id: str) -> list:
    """Every fork of this item, oldest first: its whole retry lineage."""
    from kraft.templates.forks import RunFork

    rows = conn.execute(
        "SELECT * FROM run_forks WHERE work_item_id = ? ORDER BY rowid", (work_item_id,)
    ).fetchall()
    return [RunFork.from_row(r) for r in rows]


def fork_run(conn: sqlite3.Connection, work_item_id: str, target, override=None):
    """Record a retry of `target` (a `ChainPath`, or None to restart the whole
    work item) as a new run fork, and invalidate exactly its downstream span.

    Nothing is deleted: sessions, events and findings of every earlier run stay
    as they were, and the fork's `after_seq` marks where they end
    (`retry-creates-an-immutable-run-fork`). What the span loses is its
    standing, not its record:

    * every execution node from the target on gets fresh loop counters -- a
      rerun is a fresh pass, the same as a base-change restart's span;
    * every gate from the target on that was approved is reopened, so the
      rerun stops there again (`retry-reopens-invalidated-gates`). A gate
      before the target is never walked again, so its decision stands.

    The item's `run_chain` becomes the fork's copy of the chain, so every
    reader of the row runs what the fork froze.
    """
    from kraft.templates.forks import RunFork
    from kraft.templates.models import ExecNode

    item = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    (after_seq,) = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM events WHERE work_item_id = ?", (work_item_id,)
    ).fetchone()
    parent = current_fork(conn, work_item_id)
    fork = RunFork.from_retry(
        work_item_id=work_item_id,
        parent=parent,
        chain=materialized_chain_of(item),
        target=target,
        override=override,
        after_seq=after_seq,
    )
    conn.execute(
        "INSERT INTO run_forks (id, work_item_id, parent, scope, path, after_seq, "
        "materialized_chain, override, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*fork.row(), _now()),
    )
    conn.execute(
        "UPDATE work_items SET run_chain = ?, updated_at = ? WHERE id = ?",
        (fork.materialized_chain, _now(), work_item_id),
    )
    start, _ = fork.start
    reopened = []
    for node in fork.chain.chain.nodes[start:]:
        if isinstance(node.node, ExecNode):
            # `walk._loop_key`'s format; the store must not import the executor.
            key = f"{node.id}.fix_loop" if node.fix_loop else None
            clear_loop_counters(conn, work_item_id, node.id, key)
        else:
            # `gates.reject_loop_key`'s format: a reopened gate gets a fresh
            # reject budget, the human's override of that cap as it always was.
            conn.execute(
                "DELETE FROM retry_counters WHERE work_item_id = ? AND key = ?",
                (work_item_id, f"{node.id}_reject_loop"),
            )
            if _approved(conn, work_item_id, node.id):
                reopened.append(node.id)
                events.append(
                    conn, work_item_id, "gate_reopened", {"gate": node.id, "reason": "retry"}
                )
    events.append(
        conn,
        work_item_id,
        "run_forked",
        {
            "fork": fork.id,
            "parent": fork.parent,
            "scope": fork.scope.value,
            "path": fork.path,
            "reopened": reopened,
            "preserved": sorted(fork.preserved),
        },
    )
    return fork


def _approved(conn: sqlite3.Connection, work_item_id: str, gate: str) -> bool:
    """Whether a decision to reopen stands on `gate`: it was approved, and not
    reopened since."""
    row = conn.execute(
        "SELECT type FROM events WHERE work_item_id = ? "
        "AND type IN ('gate_approved', 'gate_reopened') "
        "AND json_extract(payload, '$.gate') = ? ORDER BY seq DESC LIMIT 1",
        (work_item_id, gate),
    ).fetchone()
    return row is not None and row["type"] == "gate_approved"


def fork_boundary(conn: sqlite3.Connection, work_item_id: str) -> int:
    """The event seq the current run starts after; 0 for the intake run."""
    (seq,) = conn.execute(
        "SELECT COALESCE(MAX(after_seq), 0) FROM run_forks WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchone()
    return seq
