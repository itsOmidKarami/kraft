from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from kraft import builtins as _builtins
from kraft import escalate, events, gate_review, store
from kraft import policy as _policy
from kraft.adapters import agent as _agent
from kraft.executor import stops
from kraft.executor.context import LaunchContext, OnApprove
from kraft.store import _now as _now
from kraft.templates import Registry
from kraft.templates.models import ExecNode, GateNode, ResolvedNode

logger = logging.getLogger(__name__)


def pending_gate(db, work_item_id: str, evts: list | None = None) -> str | None:
    """The gate name this item is currently stopped on, or None (Kraft-zr3s).

    Moved out of `kraft.api` unchanged, so both a human's approve/reject door and
    an agent's gate review read the same reverse scan of the same event
    types -- a second reader of one timeline is how two readers start
    disagreeing. `evts`, when given, is a timeline the caller already fetched
    (`gates.auto_escalate_stuck`'s single read) -- every other call site
    still passes nothing and gets a fresh read, unchanged.

    `node_skipped` closes a pending gate the same way `gate_approved`/
    `gate_rejected` do: `store.skip_node` (a `kraft item skip`) advances the
    item past the gate's node without ever writing one of those two events,
    so without this a gate bypassed by skip reads as pending forever --
    the delay poller (`auto_escalate_delay.tick`) would then auto-review a
    dead gate and rewind the chain back to it (code review finding).
    """
    evts = evts if evts is not None else db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected", "node_skipped"):
            return e["payload"]["gate"] if e["type"] == "gate_requested" else None
    return None


def reject_loop_key(gate: str) -> str:
    """The `retry_counters` key a gate's reject loop counts under.

    One spelling, two writers. `apply_rejection` bumps it and
    `api/routes/lifecycle.py`'s retry clears it, and they had the f-string each
    -- `f"{gate}_reject_loop"` here and `f"{node.id}_reject_loop"` there. Those
    agree only because a V1 gate's node id *is* its gate name, which is true today
    and is exactly the kind of coincidence that stops being true quietly: a retry
    that cleared a key nothing bumped leaves the gate re-opening onto a spent
    counter, and every rejection after that is refused forever (Kraft-ko7j §A4).
    `store.retry_after_cap`'s docstring names this function rather than a third
    spelling of the same format string.
    """
    return f"{gate}_reject_loop"


def gate_node_index(nodes: Sequence[ResolvedNode], gate: str) -> int:
    """Where `gate` sits in the chain's ordered nodes.

    A V1 gate *is* a node (`gate-is-an-ordered-node`), so its identity is that
    node's id and there is nothing to search a task list for. Raises
    `StopIteration` for a gate this chain does not have, exactly as the
    `gate_after` scan it replaces did.
    """
    return next(i for i, n in enumerate(nodes) if isinstance(n.node, GateNode) and n.id == gate)


def gate_artifact(run_dirs, row, gate: str | None) -> str | None:
    """The document the pending gate is a decision *about*, or None.

    The gate's own `artifact` field names the kind
    (`gate-owns-gate-behaviour`), and the kind plus the work item id is the path
    (`agent.artifact_path`). It is not stored and nothing goes stale when a
    re-run revises the same file.

    Replaces a scan of the *preceding* node's hook bindings for an `artifact:`
    key -- a gate that had to be told what it was about by the node in front of
    it, which is exactly the positional inference V1 deletes.

    None when there is no pending gate, when the gate declares no `artifact`, or
    when the file is not on disk -- the last case is an agent that reported done
    without honouring the contract, and the gate is still answerable, just
    without a document to read.
    """
    if not gate:
        return None
    chain = store.materialized_chain_of(row)
    if chain is None:
        return None
    node = next(
        (n for n in chain.chain.nodes if isinstance(n.node, GateNode) and n.id == gate), None
    )
    if node is None or node.node.artifact is None:
        return None
    rel = _agent.artifact_path(node.node.artifact, row["id"])
    return rel if (run_dirs.worktrees / row["id"] / rel).is_file() else None


def preceding_exec_node(nodes: Sequence[ResolvedNode], gate_index: int) -> int | None:
    """The index of the last execution node before `gate_index`, or None.

    What a rejection re-enters at when nothing named a target: the node that
    produced what the gate is about, so the smallest amount of work is redone
    and the repair is *measured* rather than trusted (Kraft-rv6i). The gate node
    itself is no longer a usable answer -- a V1 gate has no execution shape at
    all, so re-entering there dispatches nothing and immediately re-requests the
    same gate, a ping-pong bounded only by the reject loop's cap (Ruling 54).
    """
    return next(
        (i for i in range(gate_index - 1, -1, -1) if isinstance(nodes[i].node, ExecNode)), None
    )


def reject_target(nodes: Sequence[ResolvedNode], gate_index: int, requested: str | None) -> int:
    """The index a rejection re-enters the chain at (Kraft-ko7j).

    `requested`, else the gate node's `reject_to`, else `preceding_exec_node`,
    else the gate node itself (a gate with nothing before it: there is no work
    to redo, so the gate simply re-opens carrying the note).

    `Chain`'s validator already guarantees an authored `reject_to` names an
    *earlier execution node*, so the only `reject_to` that can miss is one whose
    target `trim_for_attachments` dropped -- and that one is nulled at trim
    time, so it arrives here as no target at all rather than as a dangling name.

    `requested` is the `node` field of an HTTP request body and is validated by
    nothing, so it keeps its own bounds check and its own `ValueError`: a human
    rejecting *forward* past the gate would otherwise skip every node in between.
    """
    gate = nodes[gate_index]
    name = requested or (gate.node.reject_to if isinstance(gate.node, GateNode) else None)
    if not name:
        fallback = preceding_exec_node(nodes, gate_index)
        return gate_index if fallback is None else fallback
    index = next((i for i, n in enumerate(nodes) if n.id == name), None)
    if index is None or index >= gate_index:
        raise ValueError(f"cannot reject to {name!r}: not a node of this chain before {gate.id!r}")
    return index


async def apply_rejection(
    db,
    policy,
    *,
    work_item_id: str,
    nodes: Sequence[ResolvedNode],
    gate: str,
    note: str,
    node: str | None = None,
    by: str = "human",
    verdict: str | None = None,
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
    gate_index = gate_node_index(nodes, gate)
    target = reject_target(nodes, gate_index, node)
    key = reject_loop_key(gate)
    cap = _policy.resolve_cap(policy, key)
    count, started_at, cap = await db.write(
        lambda c, cap=cap: store.bump_counter(c, work_item_id, key, cap)
    )
    replan = _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "ok"
    target_id = nodes[target].id
    await db.write(
        lambda c: store.reject_gate(
            c, work_item_id, gate, note, reopen=replan, node=target_id, by=by, verdict=verdict
        )
    )
    if replan:
        return target
    await db.write(
        lambda c: store.mark_needs_human(
            c,
            work_item_id,
            nodes[gate_index].id,
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


async def maybe_gate(db, work_item_id: str, node: ResolvedNode) -> bool:
    """Whether the walk stops here because `node` is an unanswered gate
    (`gate-node-opens-and-halts-execution`).

    True opens the gate and stops the caller. False means walk on, which covers
    both an execution node and a gate this item has *already* cleared -- a
    resumed or re-entered walk passes over an approved gate rather than
    re-requesting one a human has answered.

    `enter_node` before the request: a gate node is where the item now is, so
    `current_node_id` has to say so for resume and for the approve door to find
    it.
    """
    if not isinstance(node.node, GateNode):
        return False
    if gate_cleared(db, work_item_id, node.id):
        return False
    await db.write(lambda c: store.enter_node(c, work_item_id, node.id))
    await db.write(lambda c: store.request_gate(c, work_item_id, node.id, node.id))
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

    while status == "awaiting_gate":
        evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
        gate = pending_gate(db, work_item_id, evts=evts)
        row = db.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
        )
        if gate is None or not row["auto_gate"]:
            return status
        # The item's own chain, node overrides folded in (UI v2 · 04 point 1):
        # an `auto_escalate` override must suppress review here the same way it
        # does the Config tab's read, not just the gate's own declaration.
        nodes = store.effective_nodes(walk.chain_of(row), store.node_overrides_of(row))
        gate_index = gate_node_index(nodes, gate)
        node = nodes[gate_index]
        # Armed, past its delay-before-fire, and not already reviewed this
        # round (Kraft-vyk8) -- see `auto_check_due`. At the default delay of
        # 0 the elapsed half is always true, so this stays a no-op for
        # anyone who hasn't opted in.
        if not auto_check_due(row, gate, evts, policy):
            return status
        budget = store.effective_budget(row, policy.budget if policy else _policy.NO_BUDGET)

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
            return status_of(db, work_item_id)

        if verdict == "undecided":
            # Marks this `gate_requested` as reviewed so `_gate_already_reviewed`
            # stops the poller from spawning another session against it next
            # tick -- see the comment at that guard's call site.
            await db.write(
                lambda c, gate=gate: events.append(
                    c,
                    work_item_id,
                    "gate_auto_review_skipped",
                    {"gate": gate, "reason": "undecided"},
                )
            )
            return status
        # Bracketed from *before* the claim to the hand-off, for the same reason
        # `apply_approval`'s route door is: `store.approve_gate` below is an
        # unconditional `UPDATE work_items SET status = 'active'`, and
        # `gate_node_index(approved, gate) + 1` is a defaultless `next(...)` that
        # raises `StopIteration` for a gate this chain does not have -- after the
        # claim. `dev/check_claim_handoff.py` cannot see that exit (it is a
        # propagating exception, not a `return`/`raise` statement), which is
        # exactly why the fix is a bracket over the region rather than a stop per
        # exit the checker happens to list.
        async with stops.claimed_or_stopped(
            db,
            work_item_id,
            row["current_node_id"],
            reason="gate review cleared or rejected the gate but could not start a walk",
        ):
            if verdict == "approve":
                # An approval is not just a status change: `chain_finalized` splices
                # the reviewed nodes in, and every artifact-carrying gate indexes its
                # document, which nothing else durably keeps.
                # `kraft.api.routes.gates.apply_approval` is that work, reached
                # through `on_approve` because it needs the
                # indexer this layer has no handle on. Without the callback the
                # effects cannot run, so the gate is left for a person rather than
                # cleared with half of them (Kraft-zr3s).
                if on_approve is None:
                    return status
                approved, reason = await on_approve(row, gate)
                if approved is None:
                    await db.write(
                        lambda c, reason=reason, node=row["current_node_id"]: (
                            store.mark_needs_human(c, work_item_id, node, reason)
                        )
                    )
                    return "needs_human"
                await db.write(
                    lambda c, gate=gate: store.approve_gate(c, work_item_id, gate, by="agent")
                )
                start, steer = gate_node_index(approved, gate) + 1, None
            else:
                # `fixed` is a rejection that repaired something on its way out, so
                # the repair is measured rather than trusted -- the Kraft-rv6i rule,
                # applied at a gate. It re-enters at the **execution node before the
                # gate**, not at the gate itself: a V1 gate has no execution shape,
                # so "re-enter at the gate" would dispatch nothing and re-request
                # the same gate (Ruling 54). `None` when the gate is first in the
                # chain -- there is nothing to re-measure, and `reject_target`'s own
                # fallback lands back on the gate. `reject` takes the chain's own
                # `reject_to`. Both count against the same cap.
                repaired = preceding_exec_node(nodes, gate_index)
                target = await apply_rejection(
                    db,
                    policy,
                    work_item_id=work_item_id,
                    nodes=nodes,
                    gate=gate,
                    note=note,
                    node=nodes[repaired].id
                    if verdict == "fixed" and repaired is not None
                    else None,
                    by="agent",
                    verdict=verdict,
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
                # An agent's verdict is not a human's steer (Kraft-s7c04.6). Without
                # this the re-run's prompt led with "A human has steered this run"
                # over a note an agent wrote -- and on a `fixed` verdict, over
                # commits the agent had just made -- which is how a review brief
                # came to tell the human they had fixed it themselves. `steer` is
                # None on the approve path, where `source` is never read.
                steer_source="gate_review",
            )
    return status


def status_of(db, work_item_id: str) -> str:
    row = db.read(
        lambda c: c.execute(
            "SELECT status FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    return row["status"]


def auto_check_due(row, gate: str | None, evts: list, policy) -> bool:
    """Whether a delayed auto-check could do anything at all for this row
    right now: armed, past its `auto_escalate_delay_s`, and not already
    handled this round.

    The preconditions `review_gates` (when `gate` is not None) and
    `auto_escalate_stuck` (when it is) each check for themselves before
    dispatching -- factored out so `auto_escalate_delay.tick` can apply the
    same ones *before* it spends a `max_concurrent` slot and a task slot on
    a call that can only return its status unchanged (code review finding).
    A parked row with `auto_escalate_stuck: false`, or a human-only gate,
    would otherwise be re-dispatched every tick forever, crowding out the
    rows that are genuinely due and 409-ing a human's `retry` whenever its
    no-op task happened to be installed.

    Deliberately not the *whole* precondition set: the reason check, the
    running-escalation check, budget and cap all stay where they are, since
    each needs work this pre-filter has no business doing twice. This is the
    cheap "is anything armed and due" half.
    """
    delay = store.effective_auto_escalate_delay_s(
        row, policy.auto_escalate_delay_s if policy else 0
    )
    if gate is not None:
        if not row["auto_gate"]:
            return False
        from kraft.executor import walk

        nodes = store.effective_nodes(walk.chain_of(row), store.node_overrides_of(row))
        node = nodes[gate_node_index(nodes, gate)]
        # The gate's own declared reviewing task is the whole arming condition
        # (`gate-auto-review-is-explicit-and-bounded`): no task, no review, and
        # no `auto_escalate: true` override can supply one.
        if node.auto_review is None:
            return False
        attempts = policy.auto_review_attempts if policy else 1
        if _gate_review_attempts(evts, gate) >= attempts:
            return False
        elapsed = _seconds_since(
            evts,
            lambda e, gate=gate: e["type"] == "gate_requested" and e["payload"].get("gate") == gate,
        )
    else:
        # A conservative False, not `policy.auto_escalate_stuck`'s own True
        # default, when policy failed to load entirely -- same fallback
        # `auto_escalate_stuck` itself applies.
        if not store.effective_auto_escalate_stuck(
            row, policy.auto_escalate_stuck if policy else False
        ):
            return False
        if _auto_escalate_already_handled(evts):
            return False
        elapsed = _seconds_since(evts, lambda e: e["type"] == "work_item_needs_human")
    return elapsed is not None and elapsed >= delay


def _gate_review_attempts(evts: list, gate: str) -> int:
    """How many review *attempts* the current `gate_requested <gate>` has had
    since it was requested.

    An attempt is one `gate_auto_review_started`, **plus** one
    `gate_auto_review_skipped` that does not immediately follow a `_started`.
    The pairing is **structural and reads no `reason` at all**: a `_started`
    opens an attempt, the next skip closes the one it belongs to without adding
    to the tally, and an unpaired skip counts for itself whatever its reason
    says. So a caller that returns without launching spends an attempt no matter
    which reason string it writes, and `reason` is documentation for a human
    reading the timeline rather than an input to this count.

    Two superseded descriptions of this function have each sent a careful reader
    to the wrong conclusion, so the rule above is the only one to trust and the
    history is kept only to stop a third: it once read "any skip regardless of
    reason", which contradicted its own body and led a reviewer to clear a real
    re-dispatch loop as safe; it was then corrected to "any skip whose reason is
    not `undecided`", which described the body accurately *in the same commit
    that replaced that body* with the pairing pass, so it was false on arrival.
    Neither claim is true now. If you change the tally rule, change **this
    paragraph and the one above it first** -- both previous failures were a
    leading summary left behind by a correct body.

    `policy.auto_review_attempts` is the bound the caller compares this
    against; 1, its default, is the one-attempt-per-`gate_requested` contract
    this used to hardcode as a boolean. Every non-terminating
    outcome (an undecided verdict, a budget-breach skip, or a crash
    mid-review that `deps.guard` turns into `mark_needs_human`) must
    suppress the next tick's re-attempt the same
    way, not just `undecided` (code review finding).

    Scans newest-first and stops at the `gate_requested` that armed this
    round: an older `gate_auto_review_skipped` for the same gate name from a
    *previous* request (e.g. after a rejection re-requested it) must not
    suppress a fresh review of the new request. It stops at `_RUN_BOUNDARY`
    for the same reason `_auto_escalate_already_handled` does (code review
    finding): a review that crashed mid-flight leaves its
    `gate_auto_review_started` on the timeline with no verdict after it, and
    without this reset the gate would be silently demoted to human-only for
    good -- a human's resume/retry gives it one more attempt, exactly like
    the `auto_escalate_stuck` side.

    `gate_auto_review_started` counts as the attempt (`gate_review.review`
    writes it before it launches anything), so nothing has to stamp a
    separate marker for the crash case.

    One attempt, not two, when a review ran and came back `undecided`: that
    writes a `gate_auto_review_started` *and* a `gate_auto_review_skipped
    {reason: undecided}`, and counting both would halve every bound above 1. A
    `{reason: budget}` skip is the other way round -- it is refused before
    anything launches, so it has no `_started` of its own and has to count for
    itself.

    So the rule is **"every `_started`, plus every `_skipped` that is not the tail
    of one"**, and it is implemented as exactly that: a chronological pass that
    pairs an `undecided` skip with the `_started` it closes, and counts an
    unpaired skip for itself. Approximating "not the tail of one" as "reason is
    not `undecided`" is what it used to do, and that made correctness depend on
    every early return picking a distinct reason string -- one that did not (the
    non-agent `auto_review` guard in `gate_review.review`) left the tally at zero
    and the delay poller re-arming the same dead gate every tick forever. The
    structural pairing cannot be got wrong by a caller, so the reason is now
    documentation rather than load-bearing.
    """
    # Chronological, not the reverse scan the boundary search wants, because
    # pairing a skip with the `_started` before it needs the events in order.
    since: list[dict] = []
    for e in reversed(evts):
        if e["type"] == "gate_requested" and e["payload"].get("gate") == gate:
            break
        if e["type"] in _RUN_BOUNDARY:
            break
        if e["payload"].get("gate") == gate and e["type"] in (
            "gate_auto_review_started",
            "gate_auto_review_skipped",
        ):
            since.append(e)
    attempts = 0
    open_started = False
    for e in reversed(since):
        if e["type"] == "gate_auto_review_started":
            attempts += 1
            open_started = True
        elif open_started:
            open_started = False  # the tail of the attempt already counted
        else:
            attempts += 1
    return attempts


def _seconds_since(evts: list, predicate) -> float | None:
    """Seconds between now and the most recent event in `evts` (a timeline
    the caller already fetched) matching `predicate`, scanning from the
    newest event backwards -- or None if nothing matches. Shared by
    `review_gates`'s delay-before-fire check (`gate_requested`) and
    `auto_escalate_stuck`'s (`work_item_needs_human`) (Kraft-vyk8).
    """
    for e in reversed(evts):
        if predicate(e):
            now = datetime.fromisoformat(_now())
            return (now - datetime.fromisoformat(e["created_at"])).total_seconds()
    return None


_AUTO_ESCALATE_MESSAGE = (
    "(Auto-escalated: no one has looked at this yet. Diagnose why it "
    "stopped and fix it if you can; if you're not confident, stop and say "
    "so instead of guessing.)"
)

#: Event types that close out the current run of needs_human/escalation
#: activity for `_auto_dispatch_count`'s cap counter. Newest-wins reverse
#: scan, the same shape `kraft.api.routes.board._STOP_BOUNDARY` already
#: uses for a sibling question ("what stop is the item currently sitting
#: on"): each of these means a *human* acted since the last
#: `work_item_needs_human`, so any escalation attempt dispatched before it
#: belongs to a run that is already over and must not keep counting
#: against today's cap.
#:
#: Only human-attributable events are boundaries. Chain movement --
#: `node_started`, `gate_requested`, `work_item_rate_limited` -- is
#: deliberately *not* a boundary: an escalated self-retry moves the chain
#: by design, and a stop re-reached after that movement is the same run of
#: unattended stuckness, not a new one. Counting it as a boundary reads
#: the count as 0 on every cycle and the escalate -> self-retry -> re-stop
#: loop never hits the cap (Kraft code-review finding 2). The same reason
#: the scan skips a `work_item_retried` tagged `{"escalated": true}` and a
#: `gate_approved`/`gate_rejected` decided `by: "agent"`: those are the
#: machinery unblocking itself, not a person looking at the item. `by:
#: "assistant"` (Kraft-s7c04.43) is deliberately NOT skipped alongside
#: "agent": a person told the assistant to clear the gate, so a person was
#: paged and the run really did end.
#:
#: The boundaries themselves:
#:
#:   - work_item_retried / work_item_resumed -- a human answered
#:     `kraft item retry` / `kraft item resume` (an `{"escalated": true}`
#:     retry is skipped, see above).
#:   - work_item_created -- defensive: a freshly created item has no prior
#:     run to inherit a count from.
#:   - pause_requested -- a human paused the item.
#:   - gate_approved / gate_rejected -- a human decided a gate (an agent's
#:     own gate-review decision is skipped, see above).
#:   - work_item_completed -- the chain finished.
#:   - work_item_abandoned / work_item_restored -- a human abandoned or
#:     restored the item.
#:
#: Everything NOT in this tuple -- including `work_item_needs_human`
#: itself, every `escalation_message` (counted, not boundary-checked,
#: below), and every session-lifecycle/progress event
#: (`worker_session_created`/`_started`/`_exited`/`_paused`,
#: `session_unknown`, `session_reattached`, `task_progress`,
#: `budget_changed`, ...) -- is ignored by the scan rather than treated as
#: a boundary. That distinction is load-bearing: `store.create_session`
#: appends `worker_session_created` unconditionally for *every* dispatched
#: session, including the escalation session this very function just
#: spawned, and the run then appends `worker_session_started` and
#: `worker_session_exited` around it too. A scan that broke on "any event
#: type other than needs_human/escalation_message" (the shape this
#: replaces) hit one of those three on the very next call and read the
#: count as 0 forever -- the cap never engaged, and every later
#: `run()`/`resume()` landing on the same stop dispatched another
#: escalation turn indefinitely.
_RUN_BOUNDARY = (
    "work_item_retried",
    "work_item_resumed",
    "work_item_created",
    "pause_requested",
    "gate_approved",
    "gate_rejected",
    "work_item_completed",
    "work_item_abandoned",
    "work_item_restored",
)


def _latest_needs_human_reason(evts) -> str | None:
    """The most recent `work_item_needs_human` event's reason in `evts`, or
    None if the item has never stopped that way. Same reverse-scan idiom as
    `kraft.escalate._reason`, kept separate: that one always hands back a
    display string ("(no reason recorded)"), and this one needs a real
    `None` to tell "never stopped this way" apart from "stopped with an
    empty reason".

    Takes the timeline itself rather than `(db, work_item_id)` --
    `auto_escalate_stuck` fetches it once and shares the same list across
    this, `_auto_dispatch_count`, `pending_gate`, and `escalate.dispatch`.
    """
    for e in reversed(evts):
        if e["type"] == "work_item_needs_human":
            return e["payload"].get("reason")
    return None


def _auto_dispatch_count(evts) -> int:
    """How many auto-dispatched escalation turns (`escalation_message`
    events tagged `{"auto": true}`) have fired since the item's current
    run of `needs_human` stops began, scanning `evts` (the already-fetched
    timeline -- see `_latest_needs_human_reason`) from the newest event
    backwards.

    Stops at the first event whose type is in `_RUN_BOUNDARY`. Every
    other event type -- including `work_item_needs_human` itself, all
    chain movement, and any non-auto `escalation_message` -- is ignored
    and the scan continues past it; only an `escalation_message` tagged
    `{"auto": true}` increments the count. Two `_RUN_BOUNDARY` types are
    skipped rather than treated as a boundary -- see the tuple's docstring
    -- because they are the machinery acting, not a human: a
    `work_item_retried` tagged `{"escalated": true}` (the escalated agent
    retrying itself) and a `gate_approved`/`gate_rejected` decided
    `by: "agent"` (gate auto-review).
    """
    count = 0
    for e in reversed(evts):
        if e["type"] == "work_item_retried" and e["payload"].get("escalated"):
            continue
        # Not `in ("agent", "assistant")` (Kraft-s7c04.43): see the
        # `_RUN_BOUNDARY` docstring. `agent` is skipped because it is the
        # machinery unblocking itself; an assistant clearing a gate is a person
        # looking at the item, and this run of stuckness really did end.
        if e["type"] in ("gate_approved", "gate_rejected") and e["payload"].get("by") == "agent":
            continue
        if e["type"] in _RUN_BOUNDARY:
            break
        if e["type"] == "escalation_message" and e["payload"].get("auto"):
            count += 1
    return count


def _auto_escalate_already_handled(evts: list) -> bool:
    """True iff this run of `needs_human` stuckness already got a
    non-dispatching outcome -- a budget-breach skip
    (`work_item_auto_escalate_skipped`) or a cap-breach
    (`work_item_auto_escalate_capped`) -- that the poller must not repeat
    every tick. The `auto_escalate_stuck` sibling of `_gate_already_reviewed`,
    scoped like `_auto_dispatch_count` rather than by a single
    `work_item_needs_human` event: bounded by `_RUN_BOUNDARY`, so a self-retry
    re-stopping on the same problem stays the same run, not a new one that
    would get a fresh attempt.
    """
    for e in reversed(evts):
        if e["type"] in _RUN_BOUNDARY:
            return False
        if e["type"] in ("work_item_auto_escalate_skipped", "work_item_auto_escalate_capped"):
            return True
    return False


def _current_run_escalation_session_id(evts: list) -> str | None:
    """The `session_id` of the newest `escalation_message` dispatched since
    this run of `needs_human` stuckness began, or None if none was.

    Scoped like `_auto_dispatch_count` and `_auto_escalate_already_handled`:
    bounded by `_RUN_BOUNDARY`, so a human retry that starts a fresh run
    doesn't inherit a verdict from an escalation session that answered a
    *previous* stop (Kraft-b52cm). `escalate.last_escalation_status` looks
    at the item's newest escalation session ever, which is exactly the
    unscoped read that bug came from.
    """
    for e in reversed(evts):
        if e["type"] in _RUN_BOUNDARY:
            break
        if e["type"] == "escalation_message":
            return e["payload"].get("session_id")
    return None


async def auto_escalate_stuck(
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
    """Dispatch an agent onto a `needs_human` stop nobody has looked at yet
    (docs/superpowers/specs/2026-09-12-auto-escalate-non-gate-needs-human-design.md).

    A sibling to `review_gates`, called right after it in `walk.run` and
    `resuming.resume`: same "status in, status out unchanged unless it
    acts" shape, but a single conditional call rather than a loop -- except
    for the one continuation below, which mirrors `review_gates`'s own
    `walk.run_once` call rather than adding a second loop shape to this
    module.

    Reads the item's event timeline once (`evts` below) and shares it
    across `pending_gate`, `_latest_needs_human_reason`,
    `_auto_dispatch_count`, and `escalate.dispatch`'s own `_reason`
    lookup -- four separate full `events.read_after` reads on every single
    `run()`/`resume()` call otherwise, for one function.
    """
    if launch is None:
        # The only known caller passing this (startup.py's reattach path,
        # pre-Kraft-atdbw) crashed into `deps.guard`, overwriting the item's
        # real stop reason with "executor crashed: ...". Kraft-atdbw removes
        # that call site; this guard is defense in depth (Kraft-9046).
        return status
    if status != "needs_human":
        return status
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    if pending_gate(db, work_item_id, evts=evts) is not None:
        return status  # the gate flow owns this stop, unrelated to this feature
    reason = _latest_needs_human_reason(evts)
    if reason is not None and reason.startswith("needs_context:"):
        # A worker asked a direct question. Dispatching an agent back onto
        # it only asks again; a human has to actually answer.
        return status
    current_session_id = _current_run_escalation_session_id(evts)
    if current_session_id and escalate.session_status(db, current_session_id) == "needs_context":
        # The *escalation session itself* asked a question and is waiting on
        # a human to answer it -- distinct from the guard above, which only
        # catches a chain node's own needs_context stop (Kraft-b52cm). Scoped
        # to this run of stuckness (see `_current_run_escalation_session_id`)
        # so a stale needs_context from a prior run doesn't suppress escalation
        # forever. Dispatching another turn just asks again.
        await db.write(
            lambda c: events.append(
                c, work_item_id, "work_item_auto_escalate_skipped", {"reason": "needs_context"}
            )
        )
        return status
    if escalate.escalation_running(db, work_item_id) is not None:
        return status
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    # A conservative False, not `policy.auto_escalate_stuck`'s own True
    # default, when policy failed to load entirely -- an explicit
    # per-item or per-node override still wins inside
    # `effective_auto_escalate_stuck`; only the "nobody said anything"
    # fallback gets more conservative when there's no policy to trust.
    # Armed (a conservative False when policy failed to load entirely), past
    # its delay-before-fire, and without a non-dispatching outcome already
    # recorded for this run -- see `auto_check_due`.
    if not auto_check_due(row, None, evts, policy):
        return status
    # Checked before the dispatch, the same posture as `review_gates`'s own
    # auto-review launch and `walk.walk_node`'s BUDGET rung: an item that
    # stopped *because* its spend cap was breached must not spend another
    # `auto_escalate_stuck_cap` agent turns on top of it (the spec's
    # "budget/attempt-capped"). Logged rather than silent, like
    # `gate_auto_review_skipped`.
    budget = store.effective_budget(row, policy.budget if policy else _policy.NO_BUDGET)
    if stops.budget_breach(db, work_item_id, budget) is not None:
        await db.write(
            lambda c: events.append(
                c, work_item_id, "work_item_auto_escalate_skipped", {"reason": "budget"}
            )
        )
        return status
    cap = policy.auto_escalate_stuck_cap if policy else _policy.DEFAULT_AUTO_ESCALATE_STUCK_CAP
    count = _auto_dispatch_count(evts)
    if count >= cap:
        await db.write(
            lambda c: events.append(
                c, work_item_id, "work_item_auto_escalate_capped", {"cap": cap, "count": count}
            )
        )
        return status
    cursor = evts[-1]["seq"] if evts else 0
    await escalate.dispatch(
        db,
        run_dirs,
        work_item_id=work_item_id,
        message=_AUTO_ESCALATE_MESSAGE,
        launch=launch,
        auto=True,
        evts=evts,
    )
    return await resume_after_escalation(
        db,
        run_dirs,
        work_item_id=work_item_id,
        cursor=cursor,
        registry=registry,
        policy=policy,
        launch=launch,
        bd_cwd=bd_cwd,
        on_approve=on_approve,
    )


async def resume_after_escalation(
    db,
    run_dirs,
    *,
    work_item_id: str,
    cursor: int,
    registry: Registry,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
    bd_cwd: str | None = None,
    on_approve: OnApprove | None = None,
) -> str:
    """Act on a deferred self-retry request left on the timeline by an
    escalation turn that just finished -- shared by `auto_escalate_stuck`
    and the manual `/escalate` route (`kraft.api.routes.lifecycle`), since
    an agent calling `kraft item retry` on itself defers the same way
    (lifecycle.py's `work_item_self_retry_requested` branch) whether the
    turn was auto-dispatched or a human started it. Without this consumer
    on the manual path, a human-escalated agent that fixes the problem and
    retries gets an HTTP 200 and the item silently stays `needs_human`
    forever (Kraft code-review finding).

    `escalate.dispatch` only returns once its `run_agent_task` does, which
    only returns once the escalation session's terminal `worker_sessions`
    row is already written -- so the deferred request is safe to act on
    now: the session that would have raced a live rebase/spawn against is
    no longer live.

    `cursor` is the event seq the caller read before dispatching, so this
    only sees events the escalation turn itself produced.

    Returns the item's current status unchanged when there is nothing to
    consume.
    """
    from kraft.executor import walk  # local: walk imports this module

    new_evts = db.read(lambda c: events.read_after(c, cursor, work_item_id))
    request_evt = next((e for e in new_evts if e["type"] == "work_item_self_retry_requested"), None)
    if request_evt is None:
        return status_of(db, work_item_id)
    payload = request_evt["payload"]
    node_id, key, gate_key, steer = (
        payload["node_id"],
        payload["key"],
        payload["gate_key"],
        payload["steer"],
    )
    # Absent on an event written before this field existed, or on a
    # hand-built test payload -- default False rather than KeyError either way.
    seeded = payload.get("seeded", False)
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    # The escalation turn ran for minutes; a human may have abandoned, paused
    # or otherwise moved the item in the meantime -- and `retry_after_cap` /
    # `mark_needs_human` both UPDATE unconditionally, so acting on a stale
    # request would resurrect it (worktree possibly already gone). The
    # deferred request is dropped: the item is no longer on the stop it was
    # filed against.
    # Claims the status here, before the awaited rebase, for the same reason
    # `/retry` does (Kraft-11e0) -- and `retry_after_cap` no longer flips it.
    # A failed claim means a human abandoned, paused or otherwise moved the
    # item during the minutes the escalation turn ran: the deferred request
    # is dropped rather than resurrecting a stop it is no longer on.
    # Bracketed from *before* the claim to the hand-off. There is no
    # `task_is_live` callback -- this site awaits its walk inline rather than
    # registering a task, so by the time the bracket's `finally` runs a successful
    # walk has already set its own terminal status and the bracket's `active` test
    # is false. `walk.chain_of` raises `LookupError` on a legacy row and
    # `store.node_index` answers `None` for a node this chain does not have;
    # both used to leave the item claimed.
    #
    # `handed_off=lambda: not claimed` is what keeps the dropped-self-retry return
    # inside the bracket honest. A claim out of `needs_human` fails when the status
    # has moved, and one thing it can have moved to is `active` -- a human resumed
    # the item during the minutes the escalation turn ran. Without this callback the
    # bracket would stamp `needs_human` over an item a walk owns, which is the exact
    # opposite of what `work_item_self_retry_dropped` means. The bracket covers only
    # the claim *this* call made; whoever else set the status owns it.
    claimed = False
    async with stops.claimed_or_stopped(
        db,
        work_item_id,
        node_id,
        reason="the escalation retry claimed this item but could not start a walk",
        handed_off=lambda: not claimed,
    ):
        claimed = await db.write(
            lambda c: store.claim_for_run(c, work_item_id, from_statuses=["needs_human"])
        )
        if not claimed:
            status = status_of(db, work_item_id)
            await db.write(
                lambda c: events.append(
                    c,
                    work_item_id,
                    "work_item_self_retry_dropped",
                    {"node_id": node_id, "status": status},
                )
            )
            return status
        worktree = run_dirs.worktrees / work_item_id
        try:
            new_base = await _builtins.refresh_worktree_base(
                worktree, Path(row["repo"]), store.branch_for(row)
            )
        except RuntimeError as exc:
            reason = str(exc)
            await db.write(lambda c: store.mark_needs_human(c, work_item_id, node_id, reason))
            return status_of(db, work_item_id)
        if new_base:
            await db.write(lambda c: store.set_base_ref(c, work_item_id, new_base))
        await db.write(
            lambda c: store.retry_after_cap(
                c,
                work_item_id,
                node_id,
                key,
                steer,
                gate_key=gate_key,
                escalated=True,
                seeded=seeded,
            )
        )
        # `store.node_index`, not `next(i for i, n in enumerate(walk.chain_of(
        # row).chain.nodes) ...)`: that raised `LookupError` for a legacy row and
        # `StopIteration` for an unknown node, both *after* `retry_after_cap`.
        start = store.node_index(row, node_id)
        if start is None:
            # No `return` here: the status this function reports has to be read
            # *after* the bracket has performed its stop, or the caller is told
            # `active` about an item that is about to be `needs_human`. Falls
            # through to the read below the `async with`.
            logger.warning(
                "escalation retry: %s has no node %r in its chain, not relaunching",
                work_item_id,
                node_id,
            )
        else:
            return await walk.run(
                db,
                run_dirs,
                work_item_id=work_item_id,
                registry=registry,
                bd_cwd=bd_cwd,
                start_index=start,
                policy=policy,
                steer=steer,
                steer_source="seeded" if seeded else "human",
                launch=launch,
                on_approve=on_approve,
            )
    return status_of(db, work_item_id)
