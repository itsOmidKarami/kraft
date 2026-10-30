from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3

from kraft import events
from kraft.policy import RETIRED_WAIT_TIMEOUT, deprecated
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store._common import write_status
from kraft.templates.one_off import ONE_OFF_TEMPLATE
from kraft.vocab import (
    ENDED,
    HOLDS_SLOT,
    ChainEvent,
    GateEvent,
    SandboxEvent,
    SessionEvent,
    SessionStatus,
    SettingsEvent,
    StopKind,
    WorkItemStatus,
)
from kraft.vocab.sql import in_list

#: Node fields a per-item override may touch (UI v2 · 04, point 1). Anything
#: else in a `node_overrides` patch is rejected by the route before it gets
#: here -- keep this list and `overrides.validate_agent_overrides`-style
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
        "runner",
        "extra_prompt",
        "reviewers",
    }
)


def load_chain(conn: sqlite3.Connection, work_item_id, first_node_id) -> bool:
    """Put the item on its first node. False, writing nothing, when its one-off
    chain changed after it was claimed and awaits approval again: nothing
    unreviewed runs from that window."""
    if (
        conn.execute(
            "UPDATE work_items SET current_node_id = ?, updated_at = ? "
            "WHERE id = ? AND one_off_pending = 0",
            (first_node_id, _now(), work_item_id),
        ).rowcount
        != 1
    ):
        return False
    # The add-on versions the item runs on, so its history shows them: a
    # retried item's `run_chain` first, as `materialized_chain_of` reads it.
    row = conn.execute(
        "SELECT materialized_chain, run_chain FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()
    stored = (row[1] or row[0]) if row else None
    pins = json.loads(stored).get("addons") if stored else None
    events.append(conn, work_item_id, ChainEvent.CHAIN_LOADED, {"addons": pins} if pins else {})
    return True


def enter_node(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    # A node always starts at group 0 unless something resumed it: the cursor
    # survives re-entering the same node and resets when the node changes.
    conn.execute(
        "UPDATE work_items SET "
        "current_step = CASE WHEN current_node_id IS ? THEN current_step ELSE 0 END, "
        "current_node_id = ?, updated_at = ? WHERE id = ?",
        (node_id, node_id, _now(), work_item_id),
    )
    events.append(conn, work_item_id, ChainEvent.NODE_STARTED, {"node_id": node_id})


def stand_at(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    """Put the item's cursor on `node_id`'s first step without entering it:
    the next walk from the cursor -- a resume, a poller, a retry's default
    target -- starts there. For a stop that must not resume where it stood
    (`walk._hold_at_restart`)."""
    conn.execute(
        "UPDATE work_items SET current_node_id = ?, current_step = 0, updated_at = ? WHERE id = ?",
        (node_id, _now(), work_item_id),
    )


def complete_node(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    # Idempotent: resume can re-enter an already-completed node (a reconciled
    # non-fix node, or a fix_loop node re-measured after a crash in the
    # complete_node -> enter_node window) and must not emit a second
    # node_completed. See Kraft-gbt / Kraft-126. Within one run fork only: a
    # retry reruns completed work (`retry-can-target-completed-work`), and the
    # rerun's completion is its own.
    done = conn.execute(
        "SELECT 1 FROM events WHERE work_item_id = ? AND type = ? "
        "AND json_extract(payload, '$.node_id') = ? AND seq > ("
        "  SELECT COALESCE(MAX(after_seq), 0) FROM run_forks WHERE work_item_id = ?"
        ") LIMIT 1",
        (work_item_id, ChainEvent.NODE_COMPLETED, node_id, work_item_id),
    ).fetchone()
    if done:
        return
    conn.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (_now(), work_item_id))
    events.append(conn, work_item_id, ChainEvent.NODE_COMPLETED, {"node_id": node_id})


#: A chain can be set only before the item starts, and only while nothing runs it.
_CHAIN_SETTABLE = f"current_node_id IS NULL AND status NOT IN ({in_list((*HOLDS_SLOT, *ENDED))})"
#: The refusal for an item claimed to run that has not loaded its first node.
STARTING_REFUSAL = "work item is starting; its chain and documents can't change now"


def _refuse_if_starting(row) -> None:
    """`ValueError` for an item claimed to run that has not loaded its first
    node: a change then would land under the walk's feet (`load_chain`)."""
    if row["current_node_id"] is None and row["status"] in HOLDS_SLOT:
        raise ValueError(STARTING_REFUSAL)


def set_chain_template(
    conn: sqlite3.Connection, work_item_id, template_id: str, materialized_chain: str
) -> bool:
    """Switch a not-yet-started item onto a different chain
    (Kraft-gwn6): the caller has already 404'd an unknown chain and 409'd a
    started item, and already re-run `ResolvedChain.materialize` the same way
    `executor.intake` would have -- attachments included, so a switch cannot
    undo the trim the item was filed with -- so this is just the write. Its own
    event type, not folded into `chain_spliced`: that event means the
    chain-review splice path touched the row; this means intake's own
    materialization ran again against a different template, which is a
    different question to answer from the timeline.

    Clears a one-off chain and its approval. False, writing nothing, when the
    item started since the caller looked.
    """
    old_template_id = conn.execute(
        "SELECT chain_template FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()[0]
    done = conn.execute(
        "UPDATE work_items SET chain_template = ?, materialized_chain = ?, updated_at = ?, "
        "one_off_chain = NULL, one_off_approval = NULL, one_off_pending = 0 "
        f"WHERE id = ? AND {_CHAIN_SETTABLE}",
        (template_id, materialized_chain, _now(), work_item_id),
    )
    if done.rowcount != 1:
        return False
    events.append(
        conn,
        work_item_id,
        SettingsEvent.CHAIN_TEMPLATE_CHANGED,
        {"from": old_template_id, "to": template_id},
    )
    return True


def set_one_off_chain(
    conn: sqlite3.Connection, work_item_id, record: dict, materialized_chain: str
) -> bool:
    """Replace a not-yet-started item's chain with the one-off chain `record`
    describes (`deps.OneOff.record`), already re-materialized by the caller.
    Any approval is dropped: it belonged to the chain that is gone. False,
    writing nothing, when the item started since the caller looked."""
    old_template_id = conn.execute(
        "SELECT chain_template FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()[0]
    done = conn.execute(
        "UPDATE work_items SET chain_template = ?, one_off_chain = ?, one_off_approval = NULL, "
        "one_off_pending = ?, materialized_chain = ?, updated_at = ? "
        f"WHERE id = ? AND {_CHAIN_SETTABLE}",
        (
            ONE_OFF_TEMPLATE,
            json.dumps(record),
            1 if record["custom"] else 0,
            materialized_chain,
            _now(),
            work_item_id,
        ),
    )
    if done.rowcount != 1:
        return False
    events.append(
        conn,
        work_item_id,
        SettingsEvent.CHAIN_TEMPLATE_CHANGED,
        {"from": old_template_id, "to": ONE_OFF_TEMPLATE},
    )
    return True


def one_off_of(row) -> dict | None:
    """`row`'s one-off chain record (`deps.OneOff.record`), or None for an item
    filed with a library chain."""
    raw = row["one_off_chain"] if "one_off_chain" in row.keys() else None
    return json.loads(raw) if raw else None


def one_off_unapproved(row) -> bool:
    """Whether `row` holds custom content in a one-off chain that no one has
    approved: it never starts."""
    return row["one_off_pending"] == 1


#: The columns `one_off_view` reads on a board fetch instead of decoding the
#: whole record: a select list adds them after `*`.
ONE_OFF_SUMMARY_COLUMNS = (
    "json_array_length(one_off_chain, '$.custom') AS one_off_custom, "
    "json_extract(one_off_chain, '$.provenance.parent') AS one_off_extends"
)


def one_off_view(row) -> dict | None:
    """What the board and the detail say of `row`'s one-off chain, None for a
    library chain. A row selected with `ONE_OFF_SUMMARY_COLUMNS` is read from
    those, so a board fetch never decodes the record."""
    if row["one_off_chain"] is None:
        return None
    if "one_off_custom" in row.keys():
        extends, custom = row["one_off_extends"], row["one_off_custom"]
    else:
        record = one_off_of(row)
        provenance = record["provenance"]
        extends = provenance["parent"] if provenance else None
        custom = len(record["custom"])
    return {
        "extends": extends,
        "custom": custom,
        "needs_review": one_off_unapproved(row),
        "approval": json.loads(row["one_off_approval"]) if row["one_off_approval"] else None,
    }


def review_digest(row) -> str:
    """What a reviewer approves: the authored chain and the chains that will run,
    so a draft or a re-trim applied after the review changes it."""
    return hashlib.sha256(
        json.dumps(
            [one_off_of(row)["digest"], row["materialized_chain"], row["run_chain"]]
        ).encode()
    ).hexdigest()


ONE_OFF_CHANGED = "the one-off chain changed since it was reviewed; review it again"


def approve_one_off(conn: sqlite3.Connection, work_item_id, approval: dict, digest: str) -> None:
    """Record `approval` of the chain whose `review_digest` is `digest`, in one
    transaction with every check. `ValueError`, with the sentence a person
    reads, when nothing is approved: the item ended or started, its chain is
    already approved, or its chain is no longer the one the caller saw."""
    row = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    if row is None or one_off_of(row) is None:
        raise ValueError(ONE_OFF_CHANGED)
    if row["status"] in ENDED:
        raise ValueError("work item has ended; there is nothing to approve")
    if row["current_node_id"] is not None:
        raise ValueError("work item has already started")
    if not one_off_unapproved(row):
        raise ValueError(
            "this one-off chain is already approved"
            if row["one_off_approval"]
            else "nothing to approve: this one-off chain is library-only"
        )
    if review_digest(row) != digest:
        raise ValueError(ONE_OFF_CHANGED)
    # `digest` is the authored chain's wherever an approval is recorded; the review's is beside it.
    approval.update(digest=one_off_of(row)["digest"], review_digest=digest)
    conn.execute(
        "UPDATE work_items SET one_off_approval = ?, one_off_pending = 0 WHERE id = ?",
        (json.dumps(approval), work_item_id),
    )
    if row["status"] == WorkItemStatus.NEEDS_HUMAN and row["stop_kind"] == StopKind.CONFIG:
        # Stopped by the walk for a chain that changed after its claim, never
        # started: back to paused, and a person starts it.
        write_status(
            conn,
            "UPDATE work_items SET status = 'paused', updated_at = ? "
            "WHERE id = ? AND status = 'needs_human'",
            (_now(), work_item_id),
        )
    events.append(conn, work_item_id, SettingsEvent.ONE_OFF_CHAIN_APPROVED, approval)


def replace_sandbox(
    conn: sqlite3.Connection,
    work_item_id,
    materialized_chain: str,
    run_chain: str | None,
    *,
    old: dict | None,
    new: dict | None,
) -> None:
    """Write a stopped item's snapshots with the sandbox `repos.yaml` gives
    now, the intake one and its run fork's, and record `sandbox_reread` with
    both sandboxes (Kraft-ajzx7). Everything else in them is as it was."""
    conn.execute(
        "UPDATE work_items SET materialized_chain = ?, run_chain = ?, updated_at = ? WHERE id = ?",
        (materialized_chain, run_chain, _now(), work_item_id),
    )
    events.append(conn, work_item_id, SandboxEvent.REREAD, {"old": old, "new": new})


def set_attachments(
    conn: sqlite3.Connection,
    work_item_id,
    attachments: list[dict],
    materialized_chain: str,
    *,
    seen: tuple[str | None, str],
) -> None:
    """Replace a not-yet-started item's attachments and the chain they trim
    (Kraft-s7c04.28), both at once so the trim and the documents that justify
    it cannot disagree. A compare-and-set on `seen`, the `(attachments,
    materialized_chain)` the caller built this write from (Kraft-5pw3f):
    `ValueError` if the item has started or been claimed to start since, or
    another write changed
    either column -- a stale write would put back a document the other one
    dropped, whose copy is already gone."""
    row = conn.execute(
        "SELECT attachments, materialized_chain, current_node_id, status FROM work_items "
        "WHERE id = ?",
        (work_item_id,),
    ).fetchone()
    if row["current_node_id"] is not None:
        raise ValueError("work item has already started")
    _refuse_if_starting(row)
    if (row["attachments"], row["materialized_chain"]) != seen:
        raise ValueError("its attachments or chain changed while this request was made")
    conn.execute(
        "UPDATE work_items SET attachments = ?, materialized_chain = ?, updated_at = ? "
        "WHERE id = ?",
        (json.dumps(attachments), materialized_chain, _now(), work_item_id),
    )
    events.append(
        conn,
        work_item_id,
        SettingsEvent.ATTACHMENTS_CHANGED,
        {
            "from": sorted(a["kind"] for a in json.loads(row["attachments"] or "[]")),
            "to": sorted(a["kind"] for a in attachments),
        },
    )


def revise_chain(
    conn: sqlite3.Connection,
    work_item_id,
    revised: str,
    payload: dict,
    *,
    seen: tuple[str | None, str | None],
) -> None:
    """Replace the chain this item runs with an approved revision
    (`MaterializedChain.to_json`), and record it as `chain_revised` -- one
    transaction, so a reader sees the old chain or the new one and its event,
    never half of it.

    The chain it runs is the run fork's copy once a retry made one
    (`materialized_chain_of` reads `run_chain` first), and the intake snapshot
    before that; the revision replaces whichever that is. The intake snapshot
    of a forked item stays what intake froze, and so does every `run_forks`
    row: those are records of earlier runs, not the chain a reader runs. A
    later retry forks from the revised chain, so it keeps the revision.

    A compare-and-set on `seen`, the `(materialized_chain, run_chain)` the
    revision was computed from, as `set_attachments` is: `ValueError` if a
    write changed either since (a retry's fork, an attachment change, another
    approval), which a revision of the old chain must not overwrite; and if
    the item was claimed to start and has not loaded its first node.
    """
    row = conn.execute(
        "SELECT materialized_chain, run_chain, current_node_id, status FROM work_items "
        "WHERE id = ?",
        (work_item_id,),
    ).fetchone()
    _refuse_if_starting(row)
    if (row["materialized_chain"], row["run_chain"]) != seen:
        raise ValueError("the chain changed while this approval was made; approve it again")
    column = "run_chain" if row["run_chain"] is not None else "materialized_chain"
    conn.execute(
        f"UPDATE work_items SET {column} = ?, updated_at = ? WHERE id = ?",
        (revised, _now(), work_item_id),
    )
    events.append(conn, work_item_id, GateEvent.CHAIN_REVISED, payload)


def skip_node(
    conn: sqlite3.Connection,
    work_item_id: str,
    node_id: str,
    gate: str | None,
    note: str | None,
    *,
    session_ids: list[str] | None = None,
    tips: dict | None = None,
) -> None:
    """Advance past `node_id` (its own gate `gate`, if it has one and that is
    what is being bypassed) without running or approving it.

    `tips` is the head a person passes skipping the chain's final gate
    (`reviewed_head.pin_for`): what the merge may push, as an approval's.

    Sets the item back to `active` the same way `approve_gate` and
    `retry_after_cap` do — the caller spawns `executor.run` right after this
    write, same as every other door onto the chain. `retry_at` is cleared for
    the same reason `pause_work_item` clears it: a `ci_wait`-due item skipped
    out from under the poller must not wake back up under the old wait.

    `session_ids` carries the node's own running sessions when the skip
    interrupts a live attempt — marked `paused` here, *before* the caller's
    `_terminate` signals them, so the adapter's death handler reads a session
    it expected to stop rather than one that just failed (`pause_work_item`'s
    ordering, same race). A session the node left parked on an external wait is
    closed the same way: no walk observes that wait again.
    """
    now = _now()
    parked = conn.execute(
        "SELECT id FROM worker_sessions WHERE work_item_id = ? AND node_id = ? AND status = ? "
        "ORDER BY rowid",
        (work_item_id, node_id, SessionStatus.WAITING),
    ).fetchall()
    if not write_status(
        conn,
        "UPDATE work_items SET status = 'active', retry_at = NULL, updated_at = ? WHERE id = ?",
        (now, work_item_id),
    ):
        return
    events.append(
        conn,
        work_item_id,
        ChainEvent.NODE_SKIPPED,
        {
            "node_id": node_id,
            "gate": gate,
            "note": note,
            **({"tips": tips} if tips is not None else {}),
        },
    )
    for sid in dict.fromkeys([*(session_ids or []), *(r[0] for r in parked)]):
        conn.execute(
            "UPDATE worker_sessions SET status = 'paused', exited_at = ? "
            "WHERE work_item_id = ? AND id = ?",
            (now, work_item_id, sid),
        )
        events.append(conn, work_item_id, SessionEvent.WORKER_SESSION_PAUSED, {"session_id": sid})


def skip_scope(
    conn: sqlite3.Connection,
    work_item_id: str,
    path: str,
    note: str | None,
    *,
    session_ids: list[str] | None = None,
) -> None:
    """Skip a task or a step (`skip-stops-only-the-selected-scope`): the walk
    counts everything under `path` as done from here on, in this run.

    `session_ids` are the running sessions inside the scope, and only those:
    marked `paused` here, before the caller signals them, the same ordering
    `skip_node` keeps. The item's own status is untouched -- a sibling of the
    skipped task may still be running, and the walk it belongs to goes on.

    A session the scope left parked on an external wait is closed the same way:
    no walk observes that wait again, so nothing else would ever end it.
    """
    now = _now()
    parked = conn.execute(
        "SELECT id FROM worker_sessions WHERE work_item_id = ? AND status = ? "
        "AND (hook_point = ? OR substr(hook_point, 1, length(?)) = ?) ORDER BY rowid",
        (work_item_id, SessionStatus.WAITING, path, path + ".", path + "."),
    ).fetchall()
    for sid in [*(session_ids or []), *(r[0] for r in parked)]:
        conn.execute(
            "UPDATE worker_sessions SET status = 'paused', exited_at = ? "
            "WHERE work_item_id = ? AND id = ?",
            (now, work_item_id, sid),
        )
        events.append(conn, work_item_id, SessionEvent.WORKER_SESSION_PAUSED, {"session_id": sid})
    events.append(conn, work_item_id, ChainEvent.SCOPE_SKIPPED, {"path": path, "note": note})


def skipped_paths(conn: sqlite3.Connection, work_item_id: str) -> frozenset[str]:
    """Every task or step path skipped in the current run fork. A retry's fork
    starts with none: it reruns what it covers, a skipped task included."""
    rows = conn.execute(
        "SELECT json_extract(payload, '$.path') FROM events WHERE work_item_id = ? "
        "AND type = ? AND seq > ("
        "  SELECT COALESCE(MAX(after_seq), 0) FROM run_forks WHERE work_item_id = ?"
        ")",
        (work_item_id, ChainEvent.SCOPE_SKIPPED, work_item_id),
    ).fetchall()
    return frozenset(r[0] for r in rows)


def skipped_nodes(conn: sqlite3.Connection, work_item_id: str) -> dict[str, str]:
    """Each node skipped in the current run fork, and when: a session of it
    from before then was cut short by the skip, one after is a later pass."""
    rows = conn.execute(
        "SELECT json_extract(payload, '$.node_id'), created_at FROM events "
        "WHERE work_item_id = ? AND type = ? AND seq > ("
        "  SELECT COALESCE(MAX(after_seq), 0) FROM run_forks WHERE work_item_id = ?"
        ") ORDER BY seq",
        (work_item_id, ChainEvent.NODE_SKIPPED, work_item_id),
    ).fetchall()
    return dict(rows)


def materialized_chain_of(row):
    """`row["materialized_chain"]` as the model that wrote it, or None.

    None for every row the legacy intake path wrote — and for every row written
    before the column existed. A caller that needs V1 has to handle that, which
    is why this returns None rather than raising: the legacy path is still live
    until Task 5 retires it.

    Imported inside the function: `kraft.templates.models` pulls in `kraft.policy`
    and `kraft.harness`, and `kraft.store` is imported by both the API and the
    worker, neither of which should pay for the template schema to read a row.
    """
    from kraft.templates.models import MaterializedChain

    keys = row.keys()
    # The run fork's copy first (`RunFork.materialized_chain`): after a retry
    # that is the chain the item runs, and the intake snapshot is its oldest
    # ancestor's.
    raw = (row["run_chain"] if "run_chain" in keys else None) or (
        row["materialized_chain"] if "materialized_chain" in keys else None
    )
    if not raw:
        return None
    chain = MaterializedChain.from_json(raw)
    # The item's own override, layered on read (Kraft-ab1bh): a reader that
    # selected only some columns gets the snapshot without it, which is only
    # safe because no such reader asks the snapshot for policy.
    override = policy_override_of(row)
    return dataclasses.replace(chain, item_policy=override) if override is not None else chain


def policy_override_of(row):
    """`row["policy_override"]` as the `WorkItemPolicy` it holds, or None."""
    from kraft.policy import FROZEN, WorkItemPolicy

    raw = row["policy_override"] if "policy_override" in row.keys() else None
    if not raw:
        return None
    data = json.loads(raw)
    if isinstance(data, dict) and data.get(RETIRED_WAIT_TIMEOUT) is not None:
        data = _spread_retired_wait_timeout(row, data)
    # Read back leniently, like a snapshot (Kraft-9ct4q): an override stored
    # before tool names were checked must still load. The write doors refuse
    # a rule; a launch refuses one that is already stored.
    return WorkItemPolicy.model_validate(data, context=FROZEN)


def _spread_retired_wait_timeout(row, data: dict) -> dict:
    """An item-wide `wait_timeout_minutes` stored before Ruling 196 meant
    "every wait this item runs": it becomes each wait task's own
    `total_time_cap_minutes`, never a cap on the whole item. A path that
    already sets one keeps it."""
    from kraft.templates.models import MaterializedChain

    deprecated(
        "%s: an item-wide %s is deprecated (Ruling 196); read as each wait task's "
        "total_time_cap_minutes",
        row["id"] if "id" in row.keys() else "a work item",
        RETIRED_WAIT_TIMEOUT,
    )
    value = data[RETIRED_WAIT_TIMEOUT]
    data = {k: v for k, v in data.items() if k != RETIRED_WAIT_TIMEOUT}
    raw_chain = (row["run_chain"] if "run_chain" in row.keys() else None) or (
        row["materialized_chain"] if "materialized_chain" in row.keys() else None
    )
    if not raw_chain:
        return data
    paths = dict(data.get("paths") or {})
    for node in MaterializedChain.from_json(raw_chain).chain.nodes:
        for task in node.tasks():
            if getattr(task.task, "target", None) is not None and task.task.target.waits:
                layer = dict(paths.get(task.path) or {})
                layer.setdefault("total_time_cap_minutes", value)
                paths[task.path] = layer
    return {**data, "paths": paths}


def set_policy_override(conn: sqlite3.Connection, work_item_id: str, override) -> None:
    """Replace the item's own policy override with `override` (a validated
    `WorkItemPolicy`, or None to clear it). The caller has already checked
    it against the item's chain (`MaterializedChain.with_item_policy`)."""
    stored = override.model_dump(exclude_none=True, exclude_defaults=True) if override else None
    conn.execute(
        "UPDATE work_items SET policy_override = ?, updated_at = ? WHERE id = ?",
        (json.dumps(stored) if stored else None, _now(), work_item_id),
    )
    events.append(
        conn, work_item_id, SettingsEvent.POLICY_OVERRIDE_CHANGED, {"policy": stored or {}}
    )


def chain_node_ids(row) -> tuple[str, ...]:
    """Every node id of this item's chain, in order, whichever column holds it.

    **The one reader for an ordering question**, and the reason it exists rather
    than a guard per caller. `chain_definition` is `"{}"` on a V1 row, so
    `json.loads(row["chain_definition"])["nodes"]` raises `KeyError` there --
    and every caller that asks this question (`resume`, `retry`, `skip`,
    `ci_wait`, `rate_limit_retry`) asks it *after* it has already written the
    row's new status. The raise therefore leaves the item claimed `active` with
    no walk behind it: it looks running and is not, which is the worst shape a
    failure can take here.

    A guard in each caller would be a larger diff than this and would leave the
    sixth caller, written next month, broken in exactly the same way. So the
    shape decision lives here, once.

    `()` for a row with neither column populated -- a caller distinguishes "no
    nodes" from "node not found" through `node_index`'s `default`.

    **What it guarantees, precisely: no `KeyError` for any shape Kraft writes.**
    Not "cannot raise", which earlier drafts of this docstring said and the code
    never did. A stored `materialized_chain` that a *later* build's
    `MaterializedChain` no longer validates raises `TemplateLibraryError` from
    `materialized_chain_of`, and a hand-edited `chain_definition` that is not a
    mapping of node dicts with `id` keys raises `JSONDecodeError`/`AttributeError`
    /`KeyError`. Neither is swallowed on purpose: turning a schema mismatch into
    `()` would make a chain silently look empty, and the callers that read this
    after a status write are wrapped in `stops.claimed_or_stopped`, which turns a
    propagating exception into a `needs_human` stop naming the item. Loud and
    stopped beats quiet and empty.
    """
    v1 = materialized_chain_of(row)
    if v1 is not None:
        return tuple(node.id for node in v1.chain.nodes)
    raw = row["chain_definition"] if "chain_definition" in row.keys() else None
    legacy = json.loads(raw) if raw else {}
    return tuple(n["id"] for n in (legacy.get("nodes") or []))


def node_index(row, node_id, *, default=None):
    """The position of `node_id` in this item's chain, or `default`.

    Over `chain_node_ids`, so both chain shapes answer the same way and neither
    raises a `KeyError` for a row Kraft wrote -- see there for the two inputs
    that do raise, and why that is deliberate. `default=None` for a caller that
    must refuse ("no current node to skip"); `default=0` for one whose honest
    fallback is the start of the chain (a `resume` of an item that never reached
    a node).
    """
    ids = chain_node_ids(row)
    return ids.index(node_id) if node_id in ids else default


def gate_node_index(row, gate: str, *, default=None):
    """Where `gate` sits in this item's ordered nodes, or `default`.

    Beside `node_index` because the answer differs by shape and the difference
    is exactly one line: a V1 gate **is** a node, so its id is a node id
    (`gate-is-an-ordered-node`); a legacy gate is a `gate_after` string on the
    node in front of it. Callers ask "which node does this gate sit at" and
    should not have to know which.
    """
    v1 = materialized_chain_of(row)
    if v1 is not None:
        return node_index(row, gate, default=default)
    raw = row["chain_definition"] if "chain_definition" in row.keys() else None
    nodes = (json.loads(raw) if raw else {}).get("nodes") or []
    return next((i for i, n in enumerate(nodes) if n.get("gate_after") == gate), default)


#: `chain=` left out: read the row's materialized chain. A caller that already
#: read it passes it, None for a legacy row included, so a row parses once.
UNREAD = object()


def chain_view(row, chain=UNREAD) -> dict:
    """The item's chain in the shape the API's `chain_definition` field and the
    SPA's `ChainDefinition` type speak, over either column.

    A V1 row's `chain_definition` is `"{}"`, and the board reads
    `chain_definition.nodes` to draw its stage bar and to name the current node
    -- so returning the raw column left the board blank. This projects the
    frozen V1 snapshot into the same `{template_id, nodes: [...]}` envelope
    instead, so the board is *correct* for a V1 item rather than merely not
    crashing.

    **A V1 gate node reports itself under `gate_after`, and that is deliberate.**
    Eleven SPA consumers ask one of three questions of that field -- "is this
    node a gate" (`StageGraph`, `Phone`, `MiniChain`, the gate counts), "what is
    this gate called" (`NotStarted`'s first-gate line, `Inspector/Config`), and
    "which node owns gate X" (`ItemCard.gateNodeId`, `ChainReviewDiff`'s
    `tailStart`, `PolicyPage`'s reject-loop detection). Leaving it null answered
    all three with "this chain has no gates", which is worse than the blank
    board it replaced: `gateNodeId` returned `None` for every V1 item, so the
    gate's document door disappeared.

    Teaching eleven consumers a second rule is the mistake this whole class is
    about, and it is not needed: for a gate node the answer to all three
    questions *is* its own id. A V1 gate is where its own document lives
    (`gates.gate_artifact` reads the gate's own `artifact`), so "which node owns
    gate `spec_approval`" is `spec_approval`. `kind` carries the structural fact
    beside it for a reader that needs to know a gate is a node rather than a
    flag on one (`gate-is-an-ordered-node`), and `MiniChain` uses it.

    The rest of the projection exists because the Config tab renders these
    fields and blanks read as "not configured": `fix_loop` is the *cap key*
    (`walk._loop_key`, `<node>.fix_loop`) rather than an authored loop name,
    which is what `PolicyPage` matches against `policy.yaml`'s `loops:`, and
    `auto_escalate` is whether the gate declares an `auto_review` task, which is
    what `Inspector/Config`'s toggle enables itself on.
    """
    v1 = materialized_chain_of(row) if chain is UNREAD else chain
    if v1 is None:
        raw = row["chain_definition"] if "chain_definition" in row.keys() else None
        legacy = json.loads(raw) if raw else {}
        return {**legacy, "nodes": legacy.get("nodes") or []}
    return {"template_id": v1.chain.id, "nodes": [node_view(n) for n in v1.chain.nodes]}


def node_view(node) -> dict:
    """One resolved V1 node in the shape the SPA's `ChainNode` speaks. See
    `chain_view` for why a gate reports itself under `gate_after`."""
    from kraft.templates.models import GateNode

    gate = node.node if isinstance(node.node, GateNode) else None
    # The node's *own* steps, not `node.tasks()`: the legacy `tasks`/`steps`
    # pair is what the node runs, and `tasks()` also yields the recovery pass,
    # the fix loop, the judge and a gate's reviewer -- which the Config tab
    # would then render as though they were ordinary work.
    steps = [[task.path for task in step.tasks] for step in node.steps]
    return {
        "id": node.id,
        "kind": node.node.kind.value,
        # Canonical task paths, which is what a V1 node has instead of a list of
        # hook-point names. Empty on a gate, which declares no execution shape.
        "tasks": [path for group in steps for path in group],
        "steps": steps or None,
        "gate_after": node.id if gate is not None else None,
        "reject_to": gate.reject_to if gate is not None else None,
        # The cap key `policy.yaml`'s `loops:` would name, not an authored loop
        # name -- V1 has none. Mirrors `walk._loop_key`; duplicated rather than
        # imported because `kraft.store` must not import the executor.
        "fix_loop": f"{node.id}.fix_loop" if node.fix_loop else None,
        "auto_escalate": node.auto_review is not None if gate is not None else None,
        "on_failure": [task.path for step in node.on_failure for task in step.tasks] or None,
        # The attachment kind that would drop this node at intake -- what the
        # intake preview strikes through (Kraft-ene04). Declared, not decided:
        # on an item's own chain the covered nodes are already gone.
        "covered_by": node.covered_by,
        # The document a gate decides, which names it to a reader: "approve spec".
        "artifact": gate.artifact if gate is not None else None,
        # A gate has none (`GateNode`); the UI draws the kind's own for null.
        "icon": getattr(node.node, "icon", None),
    }


def node_overrides_of(row) -> dict:
    """`row["node_overrides"]` decoded, `{}` when there are none."""
    raw = row["node_overrides"] if "node_overrides" in row.keys() else None
    return json.loads(raw) if raw else {}


#: Raw one-off columns: read through `one_off_of`, never carried in a payload.
_ONE_OFF_COLUMNS = (
    "one_off_chain",
    "one_off_approval",
    "one_off_pending",
    "one_off_custom",
    "one_off_extends",
)


def work_item_payload(row) -> dict:
    """A `work_items` row as every API answer that hands one back carries it:
    the item's detail and the action and gate echoes alike. The columns are
    as stored, except the three override columns, which hold JSON text and
    are decoded, so a client reads the same object at every door:
    `agent_overrides` and `policy_override` (`None` when unset) and
    `node_overrides` (`{}` when unset). The one-off columns are summed up as
    `one_off` (`one_off_view`)."""
    payload = {k: row[k] for k in row.keys() if k not in _ONE_OFF_COLUMNS}
    payload["agent_overrides"] = json.loads(row["agent_overrides"] or "null")
    payload["policy_override"] = json.loads(row["policy_override"] or "null")
    payload["node_overrides"] = node_overrides_of(row)
    payload["one_off"] = one_off_view(row)
    return payload


def effective_nodes(chain, node_overrides: dict) -> tuple:
    """A `MaterializedChain`'s ordered nodes with `node_overrides` folded on as
    a **typed** overlay: `tuple[ResolvedNode, ...]`.

    A read-time view that writes nothing, so
    `materialized-chain-is-immutable-work-item-input` still holds -- that
    requirement freezes the stored snapshot, and this never touches it. The
    blind `{**node, **override}` dict merge `effective_chain` does cannot apply
    to a typed node at all: an unknown key would be accepted silently, and a
    key of the wrong type would reach the runtime as one.

    **One key is supported: `auto_escalate: bool`** -- the persisted, publicly
    exposed override name (`db.py`'s `work_items.node_overrides`,
    `set_node_overrides`, `PATCH /work-items/{id}`), which keeps its spelling
    even though the chain field it governs is now `GateNode.auto_review`.
    The split: the **chain declares** the reviewing task, the **override
    permits or suppresses** it. So `false` clears `auto_review`, and `true`
    only confirms what the gate already declares -- **a gate declaring no
    `auto_review` cannot be switched on by an override, because an override
    names no task.** Broadening the supported set is Task 8's
    (`retry-overrides-are-policy-bounded`).

    Every other key in a patch is ignored here rather than rejected: the same
    `node_overrides` blob also carries `model`/`effort`/`attempts`, which
    `dispatch_node` and `_policy.resolve_cap` read for themselves from the raw
    dict. This function answers one question -- what the *nodes* look like.
    """
    from kraft.templates.models import GateNode

    if not node_overrides:
        return chain.chain.nodes
    out = []
    for resolved in chain.chain.nodes:
        patch = node_overrides.get(resolved.id) or {}
        if (
            isinstance(resolved.node, GateNode)
            and patch.get("auto_escalate") is False
            and resolved.node.auto_review is not None
        ):
            # `model_copy`, not a re-validated `model_dump` round trip: the one
            # supported patch sets a field to `None`, which its own annotation
            # already permits, so there is nothing a revalidation could catch
            # that the type does not. A second supported key would change that.
            # Both halves: the model's field and the `ResolvedNode`'s resolved
            # slot, so neither a reader holding the typed node nor one holding
            # the resolved task can see a reviewer this item suppressed.
            out.append(
                dataclasses.replace(
                    resolved,
                    node=resolved.node.model_copy(update={"auto_review": None}),
                    auto_review=None,
                )
            )
        else:
            out.append(resolved)
    return tuple(out)


def effective_chain(chain_definition: dict, node_overrides: dict) -> dict:
    """`chain_definition` with `node_overrides` folded over each node.

    The legacy-dict sibling of `effective_nodes`, and retired with the
    `chain_definition` column itself (Task 5). Its three remaining readers all
    want a node field V1's schema does not have -- the Config tab's rendered
    YAML, `effective_auto_escalate_stuck` and `effective_auto_escalate_delay_s`
    (`auto_escalate_stuck`/`auto_escalate_delay_s` are `policy.yaml` keys in V1,
    not node keys) -- so there is nothing here for a typed overlay to convert
    *to*. Anything asking what a V1 node looks like calls `effective_nodes`.

    A read-time view, not a write: `chain_definition` stays exactly what
    the legacy loader produced at intake (or the last `chain_template`
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


def _v1_node_override(row, key: str):
    """The per-node override value for `key` on the node the item is *currently*
    stopped on, for a **V1** row -- or None when nothing sets it.

    `auto_escalate_stuck` and `auto_escalate_delay_s` are the two keys this
    answers, and in V1 neither is a *node* field at all: `ExecNode`/`GateNode`
    declare neither, both live in `policy.yaml`, and the only per-node layer left
    is the override dict itself. So this reads the override and stops -- there is
    no node value beneath it to fall through to.

    That is a deliberate narrowing of the controller's "read `effective_nodes`":
    the overlay is typed over `ResolvedNode`, and a typed node has no field named
    `auto_escalate_stuck` for an overlay to produce. Reading the override layer
    directly is the whole of what "the per-node value" can mean here.

    Without this, both `effective_*` helpers below returned `default` for every V1
    row -- `chain_definition` is `"{}"`, so their `"nodes" not in` guard fired
    first -- while `PATCH /work-items/{id}` validated the override, persisted it
    and returned 200. The policy-level value still worked; only the per-node
    override was silently dead.
    """
    return node_overrides_of(row).get(row["current_node_id"], {}).get(key)


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
    if row["materialized_chain"] if "materialized_chain" in row.keys() else None:
        value = _v1_node_override(row, "auto_escalate_stuck")
        return default if value is None else value
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
    if row["materialized_chain"] if "materialized_chain" in row.keys() else None:
        value = _v1_node_override(row, "auto_escalate_delay_s")
        return default if value is None else value
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
        "SELECT 1 FROM events WHERE work_item_id = ? AND type = ? "
        "AND json_extract(payload, '$.node_id') = ? LIMIT 1",
        (work_item_id, ChainEvent.NODE_STARTED, node_id),
    ).fetchone()
    return row is not None


def merge_fields(current: dict, patch: dict) -> dict:
    """`patch` over `current`, field by field: a value sets that field and a
    `None` drops it. One rule for a node's override and the item-wide one."""
    out = {**current, **patch}
    return {k: v for k, v in out.items() if v is not None}


def set_node_overrides(conn: sqlite3.Connection, work_item_id: str, patch: dict[str, dict]) -> dict:
    """Merge `patch` into the item's stored `node_overrides` and return the new
    whole object.

    `patch == {}` (the top-level object itself, not a node inside it) clears
    every override -- "Reset to template" (point 2). A non-empty `patch` is
    per-node: `{node_id: {}}` drops just that node's overrides, `{node_id:
    {field: value}}` sets fields on it, and `{node_id: {field: None}}` drops
    that one field, in the same write: a caller never re-sends the fields it
    keeps from a copy that may be stale. A node left with no field goes. The
    caller (the PATCH route) has already validated node ids, field names and
    `node_started` locking.
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
            merged = merge_fields(new.get(node_id, {}), fields) if fields else {}
            if merged:
                new[node_id] = merged
            else:
                new.pop(node_id, None)
    conn.execute(
        "UPDATE work_items SET node_overrides = ?, updated_at = ? WHERE id = ?",
        (json.dumps(new) if new else None, _now(), work_item_id),
    )
    events.append(conn, work_item_id, SettingsEvent.NODE_OVERRIDES_CHANGED, {"overrides": new})
    return new
