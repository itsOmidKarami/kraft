"""Parents and children of a slice group (`kraft.executor.slices`).

A parent item holds the group's state as JSON on its row; each child is an
ordinary work item row that also names its parent, the fork generation and its
slice key. Filing the children and writing the `forked` state is one
transaction, so a crash leaves either no children and the old state, or every
child and `forked`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Literal

from kraft import events
from kraft.store import _now as _now
from kraft.store._common import write_status
from kraft.store.work_items import create_work_item
from kraft.vocab import WorkItemEvent, WorkItemStatus

__all__ = [
    "ChildRow",
    "GroupState",
    "children_of",
    "family_ids",
    "file_children",
    "group_state",
    "mark_waiting_on_slices",
    "pin_head",
    "set_docs_commit",
    "set_group_state",
]

Phase = Literal["deciding", "prelude", "forked", "joining", "joined"]
SlicePhase = Literal["idle", "picking", "handed_off"]


@dataclass
class GroupState:
    """Where a parent stands at one group. Per group entry, stored on the parent,
    so a crash, restart, resume or retry re-entering the group knows where it
    was. `generation` only grows: a later fork is generation + 1."""

    group: str
    phase: Phase = "deciding"
    generation: int = 0
    fork_commit: str | None = None
    children: list[str] = field(default_factory=list)
    #: The last slice key the join has recorded as applied (0: none yet).
    joined_through: int = 0
    #: The commit the join had reached before the slice it is on ("X").
    at_commit: str | None = None
    slice_phase: SlicePhase = "idle"
    #: Slice key -> the ordered child commits recorded before picking it.
    recorded: dict[int, list[str]] = field(default_factory=dict)
    #: Slice key -> what that slice skipped, completed by hand or dropped.
    skipped: dict[int, list[str]] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> GroupState:
        data = json.loads(text)
        for name in ("recorded", "skipped"):
            data[name] = {int(k): v for k, v in data.get(name, {}).items()}
        return cls(**data)


@dataclass(frozen=True)
class ChildRow:
    """One child work item as the fork files it."""

    id: str
    title: str
    slice_key: int
    materialized_chain: str
    base_ref: str
    attachments: list[dict]
    repo: str
    chain_definition: str = "{}"
    chain_template: str | None = None
    node_overrides: dict | None = None
    policy_override: dict | None = None
    auto_gate: bool = False


def group_state(conn: sqlite3.Connection, wid: str) -> GroupState | None:
    row = conn.execute("SELECT group_state FROM work_items WHERE id = ?", (wid,)).fetchone()
    return GroupState.from_json(row[0]) if row is not None and row[0] else None


def set_group_state(conn: sqlite3.Connection, wid: str, state: GroupState) -> None:
    conn.execute(
        "UPDATE work_items SET group_state = ?, updated_at = ? WHERE id = ?",
        (state.to_json(), _now(), wid),
    )


def file_children(
    conn: sqlite3.Connection, parent_id: str, state: GroupState, children: list[ChildRow]
) -> None:
    """File every child `paused` (generation `state.generation`) and write
    `state` on the parent with phase `forked`, in this one transaction. A
    duplicate slice key raises, and the caller's rollback files none."""
    for child in children:
        create_work_item(
            conn,
            id=child.id,
            bead_id=None,
            title=child.title,
            repo=child.repo,
            chain_template=child.chain_template,
            chain_definition=child.chain_definition,
            attachments=child.attachments,
            status=WorkItemStatus.PAUSED,
            auto_gate=child.auto_gate,
            node_overrides=child.node_overrides,
            policy_override=child.policy_override,
            materialized_chain=child.materialized_chain,
        )
        conn.execute(
            "UPDATE work_items SET parent_id = ?, slice_generation = ?, slice_key = ?, "
            "base_ref = ? WHERE id = ?",
            (parent_id, state.generation, child.slice_key, child.base_ref, child.id),
        )
    state.phase = "forked"
    state.children = [c.id for c in children]
    set_group_state(conn, parent_id, state)
    events.append(
        conn,
        parent_id,
        WorkItemEvent.SLICES_FORKED,
        {"generation": state.generation, "children": state.children},
    )


def children_of(
    conn: sqlite3.Connection, parent_id: str, generation: int | None = None
) -> list[dict]:
    """A parent's children in slice order. `generation` None: the current one
    in the parent's group state; every generation when the parent has none."""
    if generation is None:
        state = group_state(conn, parent_id)
        generation = state.generation if state is not None else None
    sql = "SELECT * FROM work_items WHERE parent_id = ?"
    params: tuple = (parent_id,)
    if generation is not None:
        sql += " AND slice_generation = ?"
        params = (parent_id, generation)
    rows = conn.execute(sql + " ORDER BY slice_generation, slice_key", params).fetchall()
    return [dict(r) for r in rows]


def family_ids(conn: sqlite3.Connection, wid: str) -> list[str]:
    """`wid`'s family: for a child, its parent and every sibling (itself
    included); for a parent, itself and every child. Just `wid` for an item
    with neither."""
    row = conn.execute("SELECT parent_id FROM work_items WHERE id = ?", (wid,)).fetchone()
    parent = row[0] if row is not None and row[0] else wid
    ids = [parent]
    ids += [
        r[0]
        for r in conn.execute(
            "SELECT id FROM work_items WHERE parent_id = ? ORDER BY slice_generation, slice_key",
            (parent,),
        )
    ]
    return ids if wid in ids else [wid]


def mark_waiting_on_slices(conn: sqlite3.Connection, wid: str, node_id: str) -> None:
    """The parent waits for its children: `waiting` with stop kind `slices`, and
    `retry_at` NULL so `waits.tick` never re-enters it. It holds no slot; a
    start-queue pass claims it when every child has ended."""
    if not write_status(
        conn,
        "UPDATE work_items SET status = 'waiting', stop_kind = 'slices', retry_at = NULL, "
        "updated_at = ? WHERE id = ?",
        (_now(), wid),
    ):
        return
    events.append(conn, wid, WorkItemEvent.WAITING, {"node_id": node_id, "reason": "slices"})


def pin_head(conn: sqlite3.Connection, child_id: str, sha: str) -> None:
    """Record the commit a completed child's review saw: the join replays up to
    exactly this, and refuses a branch that has moved since."""
    conn.execute(
        "UPDATE work_items SET pinned_head = ?, updated_at = ? WHERE id = ?",
        (sha, _now(), child_id),
    )


def set_docs_commit(conn: sqlite3.Connection, child_id: str, sha: str) -> None:
    """Record the child's docs commit: the first commit of its branch, which the
    join's replay starts after."""
    conn.execute(
        "UPDATE work_items SET docs_commit = ?, updated_at = ? WHERE id = ?",
        (sha, _now(), child_id),
    )
