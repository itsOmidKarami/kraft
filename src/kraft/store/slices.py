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
from kraft.vocab import ENDED, HOLDS_SLOT, WorkItemEvent, WorkItemStatus
from kraft.vocab.sql import in_list

__all__ = [
    "ChildRow",
    "GroupState",
    "behind_group",
    "children_of",
    "family_ids",
    "file_children",
    "group_state",
    "group_unjoined",
    "is_child",
    "mark_waiting_on_slices",
    "never_queued",
    "running_siblings",
    "pin_head",
    "set_docs_commit",
    "set_group_state",
    "share_cap",
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
    #: The prelude's body has run and passed: a re-entry (a storage refusal at the
    #: fork, a restart) goes straight to the fork instead of building it again.
    prelude_done: bool = False
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
    #: The parent's own cap, so the family shares one (`budget_spend` sums it).
    budget_set: bool = False
    budget_usd: float | None = None
    #: Inherited, frozen: the harness and node overrides the parent holds now, and
    #: the cross-repo paths it was filed with (`submodules`, `root_merge_policy`).
    agent_overrides: str | None = None
    submodules: list[str] | None = None
    root_merge_policy: str | None = None


def is_child(row) -> bool:
    """Whether `row` is a slice's child item. A row read with a column list that
    leaves `parent_id` out is no child's."""
    try:
        return row["parent_id"] is not None
    except (IndexError, KeyError):
        return False


def group_unjoined(row) -> bool:
    """Whether `row` is a parent whose slice group has forked and not joined, and
    whose cursor stands at or past the group. A rewind behind the group (a retry
    from the plan, once every slice was dropped) leaves the old phase on the row
    until chain revision resets it, but nothing is unjoined there: the parent is
    being re-planned and every door but the group's own is open."""
    try:
        text = row["group_state"]
    except (IndexError, KeyError):
        return False
    if not text:
        return False
    state = GroupState.from_json(text)
    return state.phase in ("forked", "joining") and not behind_group(row, state)


def behind_group(row, state: GroupState) -> bool:
    """Whether `row`'s cursor stands before the node of `state.group`. False when
    either is unknown (a narrow row, a chain with no such node): unknown reads as
    at the group, the stricter answer."""
    from kraft.store.chain import node_index  # deferred: chain imports the store package

    try:
        here = node_index(row, row["current_node_id"])
        group = node_index(row, state.group)
    except (IndexError, KeyError):
        return False
    return here is not None and group is not None and here < group


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
            submodules=child.submodules,
            root_merge_policy=child.root_merge_policy,
        )
        conn.execute(
            "UPDATE work_items SET parent_id = ?, slice_generation = ?, slice_key = ?, "
            "base_ref = ?, budget_set = ?, budget_usd = ?, agent_overrides = ? WHERE id = ?",
            (
                parent_id,
                state.generation,
                child.slice_key,
                child.base_ref,
                1 if child.budget_set else 0,
                child.budget_usd,
                child.agent_overrides,
                child.id,
            ),
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
    conn: sqlite3.Connection,
    parent_id: str,
    generation: int | None = None,
    *,
    every_generation: bool = False,
) -> list[dict]:
    """A parent's children in slice order. `generation` None: the current one
    in the parent's group state; every generation when the parent has none, or
    when `every_generation` asks (archive and restore move them all)."""
    if generation is None and not every_generation:
        state = group_state(conn, parent_id)
        generation = state.generation if state is not None else None
    sql = "SELECT * FROM work_items WHERE parent_id = ?"
    params: tuple = (parent_id,)
    if generation is not None:
        sql += " AND slice_generation = ?"
        params = (parent_id, generation)
    rows = conn.execute(sql + " ORDER BY slice_generation, slice_key", params).fetchall()
    return [dict(r) for r in rows]


def share_cap(conn: sqlite3.Connection, parent_id: str) -> None:
    """Write the parent's spend caps (its own `budget_usd`, and the dollar and
    token caps of its policy override) to every child that has not ended, so a
    slice's launch check reads the cap the family shares, however a person set
    it. A child keeps the rest of its own override."""
    from kraft.policy import BUDGET_FIELDS

    parent = conn.execute(
        "SELECT budget_set, budget_usd, policy_override FROM work_items WHERE id = ?", (parent_id,)
    ).fetchone()
    if parent is None:
        return
    stored = json.loads(parent["policy_override"] or "null") or {}
    shared = {k: stored[k] for k in BUDGET_FIELDS if k in stored}
    for child in conn.execute(
        "SELECT id, policy_override FROM work_items WHERE parent_id = ? "
        f"AND status NOT IN ({in_list(ENDED)})",
        (parent_id,),
    ).fetchall():
        own = json.loads(child["policy_override"] or "null") or {}
        merged = {**{k: v for k, v in own.items() if k not in BUDGET_FIELDS}, **shared}
        conn.execute(
            "UPDATE work_items SET budget_set = ?, budget_usd = ?, policy_override = ? "
            "WHERE id = ?",
            (
                parent["budget_set"],
                parent["budget_usd"],
                json.dumps(merged) if merged else None,
                child["id"],
            ),
        )


def never_queued(conn: sqlite3.Connection, wid: str) -> bool:
    """Whether the start queue has never held `wid`: a child the fork filed
    `paused` and a crash kept from queueing, as against one a person paused after
    it queued and before it started."""
    return (
        conn.execute(
            "SELECT 1 FROM events WHERE work_item_id = ? AND type = ? LIMIT 1",
            (wid, WorkItemEvent.QUEUED),
        ).fetchone()
        is None
    )


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


def running_siblings(conn: sqlite3.Connection, wid: str) -> int:
    """How many of `wid`'s siblings (same parent and generation) run right now.
    0 for an item that is no child's."""
    row = conn.execute(
        "SELECT parent_id, slice_generation FROM work_items WHERE id = ?", (wid,)
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    return conn.execute(
        f"SELECT COUNT(*) FROM work_items WHERE parent_id = ? AND slice_generation = ? "
        f"AND id != ? AND status IN ({in_list(HOLDS_SLOT)})",
        (row[0], row[1], wid),
    ).fetchone()[0]


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
