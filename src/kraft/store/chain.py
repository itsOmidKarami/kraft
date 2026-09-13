from __future__ import annotations

import json
import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks

#: Node fields a per-item override may touch (UI v2 · 04, point 1). Anything
#: else in a `node_overrides` patch is rejected by the route before it gets
#: here -- keep this list and `templates.validate_agent_overrides`-style
#: validation in the route in sync.
OVERRIDABLE_NODE_FIELDS = frozenset(
    {
        "auto_escalate",
        "auto_escalate_stuck",
        "auto_escalate_delay_s",
        "attempts",
        "wall_clock_s",
        "model",
        "escalate_model",
        "effort",
    }
)


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


def node_overrides_of(row) -> dict:
    """`row["node_overrides"]` decoded, `{}` when there are none."""
    raw = row["node_overrides"] if "node_overrides" in row.keys() else None
    return json.loads(raw) if raw else {}


def effective_chain(chain_definition: dict, node_overrides: dict) -> dict:
    """`chain_definition` with `node_overrides` folded over each node.

    A read-time view, not a write: `chain_definition` stays exactly what
    `templates.materialize` produced at intake (or the last `chain_template`
    switch), and `node_overrides` is a separate, always-small delta layer on
    top of it. Anything that decides node behaviour at run time (auto-gate
    review, the Config tab, the effective-chain YAML) must call this instead
    of reading `chain_definition["nodes"]` directly, or it sees stale config
    on an overridden node.
    """
    if not node_overrides:
        return chain_definition
    nodes = [
        {**node, **node_overrides[node["id"]]} if node["id"] in node_overrides else node
        for node in chain_definition["nodes"]
    ]
    return {**chain_definition, "nodes": nodes}


def effective_auto_escalate_stuck(row, default: bool) -> bool:
    """Per-item node override -> chain node value -> `default` (mirrors
    `store/budget.py:effective_budget`'s "override beats node beats
    caller-supplied fallback" shape).

    `default` is the caller's already-resolved policy value --
    `policy.auto_escalate_stuck`, or a conservative `False` when policy
    failed to load entirely -- the same way `effective_budget` takes
    `policy_budget` already picked out of the whole `Policy` rather than
    the `Policy` itself.

    Reads the node the item is *currently* stopped on
    (`row["current_node_id"]`): the field on any other node in the chain
    has no bearing on whether *this* stop escalates.

    The guard is on the raw `chain_definition`, before `effective_chain`
    is ever called -- not on its result. `effective_chain` indexes
    `chain_definition["nodes"]` directly whenever `node_overrides` is
    non-empty (`store/chain.py:146-149`), so a work item seeded with a
    bare `"{}"` chain_definition (as several existing tests do) plus any
    node override at all raises `KeyError` *inside* `effective_chain`,
    before a `chain.get("nodes", [])` on its return value would ever run.
    Checking `"nodes" in chain_definition` first and returning `default`
    when it's missing -- the same "no chain reads as no node value"
    fallback, just placed where it actually has to sit -- skips the call
    that would crash instead of trying to catch its result afterwards.
    """
    chain_definition = json.loads(row["chain_definition"])
    if "nodes" not in chain_definition:
        return default
    chain = effective_chain(chain_definition, node_overrides_of(row))
    node = next((n for n in chain["nodes"] if n["id"] == row["current_node_id"]), None)
    value = node.get("auto_escalate_stuck") if node else None
    return default if value is None else value


def effective_auto_escalate_delay_s(row, default: int) -> int:
    """Per-item node override -> chain node value -> `default` -- the same
    override chain `effective_auto_escalate_stuck` resolves, for the seconds
    a delayed `auto_escalate`/`auto_escalate_stuck` waits after its
    triggering event before firing (Kraft-vyk8).

    `default` is the caller's already-resolved `policy.auto_escalate_delay_s`,
    or 0 when policy failed to load entirely -- 0 is also this feature's own
    "immediate, unchanged" default, so a missing policy degrades to exactly
    today's behaviour rather than a more conservative one (unlike
    `effective_auto_escalate_stuck`'s conservative `False` fallback, which
    exists because *unset* there means "don't auto-act at all").

    Reads the node the item is *currently* stopped on
    (`row["current_node_id"]`), same reasoning as
    `effective_auto_escalate_stuck`: while an item sits `awaiting_gate` or
    `needs_human`, that is still the node the gate/stop belongs to.
    """
    chain_definition = json.loads(row["chain_definition"])
    if "nodes" not in chain_definition:
        return default
    chain = effective_chain(chain_definition, node_overrides_of(row))
    node = next((n for n in chain["nodes"] if n["id"] == row["current_node_id"]), None)
    value = node.get("auto_escalate_delay_s") if node else None
    return default if value is None else value


def node_started(conn: sqlite3.Connection, work_item_id: str, node_id: str) -> bool:
    """Whether `node_id` has ever been entered for this item.

    `node_started` is written by `enter_node`, in the same transaction that
    sets `current_node_id` -- so a node that is current, or that the chain has
    already moved past (including a fix-loop node re-entered more than once),
    both show up here. This is the "a node's config is locked once it starts"
    check for overrides and reset-to-template (UI v2 · 04, point 1/2).
    """
    row = conn.execute(
        "SELECT 1 FROM events WHERE work_item_id = ? AND type = 'node_started' "
        "AND json_extract(payload, '$.node_id') = ? LIMIT 1",
        (work_item_id, node_id),
    ).fetchone()
    return row is not None


def set_node_overrides(conn: sqlite3.Connection, work_item_id: str, patch: dict[str, dict]) -> dict:
    """Merge `patch` into the item's stored `node_overrides` and return the new
    whole object.

    `patch == {}` (the top-level object itself, not a node inside it) clears
    every override -- "Reset to template" (point 2). A non-empty `patch` is
    per-node: `{node_id: {}}` drops just that node's overrides, `{node_id:
    {field: value}}` sets fields on it. The caller (the PATCH route) has
    already validated node ids, field names and `node_started` locking.
    """
    row = conn.execute(
        "SELECT node_overrides FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()
    current = json.loads(row["node_overrides"]) if row and row["node_overrides"] else {}
    if not patch:
        new = {}
    else:
        new = dict(current)
        for node_id, fields in patch.items():
            if fields:
                new[node_id] = {**new.get(node_id, {}), **fields}
            else:
                new.pop(node_id, None)
    conn.execute(
        "UPDATE work_items SET node_overrides = ?, updated_at = ? WHERE id = ?",
        (json.dumps(new) if new else None, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "node_overrides_changed", {"overrides": new})
    return new
