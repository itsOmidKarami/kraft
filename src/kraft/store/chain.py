from __future__ import annotations

import dataclasses
import json
import sqlite3

from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store._common import ENDED

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
    }
)


def load_chain(conn: sqlite3.Connection, work_item_id, first_node_id) -> None:
    conn.execute(
        "UPDATE work_items SET current_node_id = ?, updated_at = ? WHERE id = ?",
        (first_node_id, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "chain_loaded", {})


def enter_node(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    # A node always starts at group 0 unless something resumed it: the cursor
    # survives re-entering the same node and resets when the node changes.
    conn.execute(
        "UPDATE work_items SET "
        "current_step = CASE WHEN current_node_id IS ? THEN current_step ELSE 0 END, "
        "current_node_id = ?, updated_at = ? WHERE id = ?",
        (node_id, node_id, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "node_started", {"node_id": node_id})


def complete_node(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    # Idempotent: resume can re-enter an already-completed node (a reconciled
    # non-fix node, or a fix_loop node re-measured after a crash in the
    # complete_node -> enter_node window) and must not emit a second
    # node_completed. See Kraft-gbt / Kraft-126. Within one run fork only: a
    # retry reruns completed work (`retry-can-target-completed-work`), and the
    # rerun's completion is its own.
    done = conn.execute(
        "SELECT 1 FROM events WHERE work_item_id = ? AND type = 'node_completed' "
        "AND json_extract(payload, '$.node_id') = ? AND seq > ("
        "  SELECT COALESCE(MAX(after_seq), 0) FROM run_forks WHERE work_item_id = ?"
        ") LIMIT 1",
        (work_item_id, node_id, work_item_id),
    ).fetchone()
    if done:
        return
    conn.execute("UPDATE work_items SET updated_at = ? WHERE id = ?", (_now(), work_item_id))
    events.append(conn, work_item_id, "node_completed", {"node_id": node_id})


def set_chain_template(
    conn: sqlite3.Connection, work_item_id, template_id: str, materialized_chain: str
) -> None:
    """Switch a not-yet-started item onto a different chain template
    (Kraft-gwn6): the caller has already 404'd an unknown template and 409'd a
    started item, and already re-run `ResolvedChain.materialize` the same way
    `executor.intake` would have -- attachments included, so a switch cannot
    undo the trim the item was filed with -- so this is just the write. Its own
    event type, not folded into `chain_spliced`: that event means the
    chain-review splice path touched the row; this means intake's own
    materialization ran again against a different template, which is a
    different question to answer from the timeline.
    """
    old_template_id = conn.execute(
        "SELECT chain_template FROM work_items WHERE id = ?", (work_item_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE work_items SET chain_template = ?, materialized_chain = ?, updated_at = ? "
        "WHERE id = ?",
        (template_id, materialized_chain, _now(), work_item_id),
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
        "UPDATE work_items SET status = 'active', retry_at = NULL, updated_at = ? "
        "WHERE id = ? AND status NOT IN (?, ?)",
        (now, work_item_id, *ENDED),
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
    """
    now = _now()
    for sid in session_ids or []:
        conn.execute(
            "UPDATE worker_sessions SET status = 'paused', exited_at = ? WHERE id = ?",
            (now, sid),
        )
        events.append(conn, work_item_id, "worker_session_paused", {"session_id": sid})
    events.append(conn, work_item_id, "scope_skipped", {"path": path, "note": note})


def skipped_paths(conn: sqlite3.Connection, work_item_id: str) -> frozenset[str]:
    """Every task or step path skipped in the current run fork. A retry's fork
    starts with none: it reruns what it covers, a skipped task included."""
    rows = conn.execute(
        "SELECT json_extract(payload, '$.path') FROM events WHERE work_item_id = ? "
        "AND type = 'scope_skipped' AND seq > ("
        "  SELECT COALESCE(MAX(after_seq), 0) FROM run_forks WHERE work_item_id = ?"
        ")",
        (work_item_id, work_item_id),
    ).fetchall()
    return frozenset(r[0] for r in rows)


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
    return MaterializedChain.from_json(raw) if raw else None


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


def chain_view(row) -> dict:
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
    v1 = materialized_chain_of(row)
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
    }


def node_overrides_of(row) -> dict:
    """`row["node_overrides"]` decoded, `{}` when there are none."""
    raw = row["node_overrides"] if "node_overrides" in row.keys() else None
    return json.loads(raw) if raw else {}


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
