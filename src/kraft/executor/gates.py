from __future__ import annotations

import json

from kraft import events, gate_review, store
from kraft import policy as _policy
from kraft.adapters import agent as _agent
from kraft.executor import stops
from kraft.executor.context import LaunchContext, OnApprove
from kraft.store import _now as _now
from kraft.templates import Registry


def pending_gate(db, work_item_id: str) -> str | None:
    """The gate name this item is currently stopped on, or None (Kraft-zr3s).

    Moved out of `kraft.api` unchanged, so both a human's approve/reject door and
    an agent's gate review read the same reverse scan of the same three event
    types -- a second reader of one timeline is how two readers start
    disagreeing.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected"):
            return e["payload"]["gate"] if e["type"] == "gate_requested" else None
    return None


def gate_node_index(chain: dict, gate: str) -> int:
    return next(i for i, n in enumerate(chain["nodes"]) if n.get("gate_after") == gate)


def gate_artifact(registry, run_dirs, row, gate: str | None) -> str | None:
    """The document the pending gate is a decision *about*, or None.

    Derived from the binding, not stored: the gate's node names its hooks, a
    hook with `artifact:` names a kind, and the kind plus the work item id is
    the path (`agent.artifact_path`). Nothing here to migrate and nothing to go
    stale when a rerun revises the same file.

    None when there is no pending gate, when none of the node's hooks produce
    an artifact, or when the file is not on disk — the last case is an agent
    that reported done without honouring the contract, and the gate is still
    answerable, just without a document to read.
    """
    if not gate:
        return None
    chain = json.loads(row["chain_definition"])
    try:
        node = chain["nodes"][gate_node_index(chain, gate)]
    except StopIteration:
        return None
    worktree = run_dirs.worktrees / row["id"]
    for task in node["tasks"]:
        kind = registry.hooks.get(task, {}).get("artifact")
        if not kind:
            continue
        rel = _agent.artifact_path(kind, row["id"])
        if (worktree / rel).is_file():
            return rel
    return None


def reject_target(chain: dict, gate_index: int, requested: str | None) -> int:
    """The index a rejection re-enters the chain at (Kraft-ko7j).

    `requested`, else the gate node's `reject_to`, else the gate node itself.
    A `reject_to` that `materialize` dropped — an intake attachment satisfied
    that node's gate — falls back to the gate node rather than raising; a bad
    `node` in the request body is the caller's error and raises `ValueError`.
    """
    nodes = chain["nodes"]
    name = requested or nodes[gate_index].get("reject_to")
    if not name:
        return gate_index
    index = next((i for i, n in enumerate(nodes) if n["id"] == name), None)
    if index is None or index > gate_index:
        if requested:
            raise ValueError(
                f"cannot reject to {name!r}: not a node of this chain at or before "
                f"{nodes[gate_index]['id']!r}"
            )
        return gate_index
    return index


async def apply_rejection(
    db,
    policy,
    *,
    work_item_id: str,
    chain: dict,
    gate: str,
    note: str,
    node: str | None = None,
    by: str = "human",
) -> int | None:
    """Record a gate rejection and return the node index the chain re-enters at,
    or None when the reject loop's cap breached and the item is now parked.

    One function, two callers: the `POST .../reject` endpoint (a human) and
    `review_gates` (an agent's verdict). They must not drift -- an agent
    rejection that counted differently, or landed somewhere else, from a human
    one is the single difference between them that must never exist.

    `reject_target` runs before any write, so a bad target leaves the gate
    pending and the events table untouched -- the property `kraft.api` held when
    this logic lived there.
    """
    gate_index = gate_node_index(chain, gate)
    target = reject_target(chain, gate_index, node)
    key = f"{gate}_reject_loop"
    cap = _policy.resolve_cap(policy, key)
    count, started_at, cap = await db.write(
        lambda c, cap=cap: store.bump_counter(c, work_item_id, key, cap)
    )
    replan = _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "ok"
    target_id = chain["nodes"][target]["id"]
    await db.write(
        lambda c: store.reject_gate(
            c, work_item_id, gate, note, reopen=replan, node=target_id, by=by
        )
    )
    if replan:
        return target
    await db.write(
        lambda c: store.mark_needs_human(
            c,
            work_item_id,
            chain["nodes"][gate_index]["id"],
            f"{key} exhausted after {count - 1} rejection(s)",
            {"cycles": count - 1, "attempts": cap.attempts},
        )
    )
    return None


def gate_cleared(db, work_item_id: str, gate: str) -> bool:
    """True iff the most recent gate_* event for the item is gate_approved <gate>."""
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected"):
            return e["type"] == "gate_approved" and e["payload"].get("gate") == gate
    return False


async def maybe_gate(db, work_item_id: str, node: dict) -> bool:
    """If the node ends in a gate, request it and return True (caller stops the walk)."""
    gate = node.get("gate_after")
    if not gate:
        return False
    await db.write(lambda c: store.request_gate(c, work_item_id, node["id"], gate))
    return True


async def review_gates(
    status: str,
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
    bd_cwd: str | None = None,
    on_approve: OnApprove | None = None,
) -> str:
    """Let an agent decide the gates a chain author and a human both marked
    reviewable, re-entering the walk with whatever it decides (Kraft-zr3s).

    A loop rather than a recursion, and sequential rather than `_spawn`ed: a
    second concurrent `run` for one work item is the failure this feature most
    has to avoid. Every turn either terminates -- `undecided`, an unarmed gate,
    an exhausted budget, a breached cap -- or re-enters `kraft.executor.walk.run_once`
    having bumped `<gate>_reject_loop`, so the loop is bounded by policy rather
    than by how agreeable the reviewer is.
    """
    from kraft.executor import walk

    budget = policy.budget if policy else _policy.NO_BUDGET
    while status == "awaiting_gate":
        gate = pending_gate(db, work_item_id)
        row = db.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
        )
        if gate is None or not row["auto_gate"]:
            return status
        chain = json.loads(row["chain_definition"])
        gate_index = gate_node_index(chain, gate)
        node = chain["nodes"][gate_index]
        if not node.get("auto_escalate"):
            return status

        # Checked before the dispatch, not after: same posture as every other
        # agent launch (`kraft.executor.walk.walk_node`'s BUDGET rung). Logged
        # rather than silent -- a gate that quietly stopped being reviewed
        # looks like a broken feature.
        if stops.budget_breach(db, work_item_id, budget) is not None:
            await db.write(
                lambda c, gate=gate: events.append(
                    c,
                    work_item_id,
                    "gate_auto_review_skipped",
                    {"gate": gate, "reason": "budget"},
                )
            )
            return status

        verdict, note = await gate_review.review(
            db,
            run_dirs,
            work_item_id=work_item_id,
            gate=gate,
            node=node,
            registry=registry,
            launch=launch,
        )

        # The agent may have worked for minutes. A decision a person made in the
        # meantime outranks a verdict computed against the state before it.
        if pending_gate(db, work_item_id) != gate:
            await db.write(
                lambda c, gate=gate, verdict=verdict: events.append(
                    c,
                    work_item_id,
                    "gate_auto_review_discarded",
                    {"gate": gate, "verdict": verdict, "reason": "gate no longer pending"},
                )
            )
            return _status_of(db, work_item_id)

        if verdict == "undecided":
            return status
        if verdict == "approve":
            # An approval is not just a status change: `chain_finalized` splices
            # the reviewed nodes in, and every artifact-carrying gate indexes its
            # document, which nothing else durably keeps. `kraft.api.routes.gates.apply_approval` is
            # that work, reached through `on_approve` because it needs the
            # indexer this layer has no handle on. Without the callback the
            # effects cannot run, so the gate is left for a person rather than
            # cleared with half of them (Kraft-zr3s).
            if on_approve is None:
                return status
            chain, reason = await on_approve(row, gate)
            if chain is None:
                await db.write(
                    lambda c, reason=reason, node=row["current_node_id"]: store.mark_needs_human(
                        c, work_item_id, node, reason
                    )
                )
                return "needs_human"
            await db.write(
                lambda c, gate=gate: store.approve_gate(c, work_item_id, gate, by="agent")
            )
            start, steer = gate_node_index(chain, gate) + 1, None
        else:
            # `fixed` is a rejection that repaired something on its way out: it
            # re-enters at the gate node so the repair is measured rather than
            # trusted -- the Kraft-rv6i rule, applied at a gate. `reject` takes
            # the chain's own `reject_to`. Both count against the same cap.
            target = await apply_rejection(
                db,
                policy,
                work_item_id=work_item_id,
                chain=chain,
                gate=gate,
                note=note,
                node=node["id"] if verdict == "fixed" else None,
                by="agent",
            )
            if target is None:
                return "needs_human"
            start, steer = target, note

        status = await walk.run_once(
            db,
            run_dirs,
            work_item_id=work_item_id,
            registry=registry,
            policy=policy,
            launch=launch,
            bd_cwd=bd_cwd,
            start_index=start,
            steer=steer,
        )
    return status


def _status_of(db, work_item_id: str) -> str:
    row = db.read(
        lambda c: c.execute(
            "SELECT status FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    return row["status"]
