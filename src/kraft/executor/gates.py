from __future__ import annotations

import json
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
            c, work_item_id, gate, note, reopen=replan, node=target_id, by=by, verdict=verdict
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

    while status == "awaiting_gate":
        evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
        gate = pending_gate(db, work_item_id, evts=evts)
        row = db.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
        )
        if gate is None or not row["auto_gate"]:
            return status
        # The item's own chain, node overrides folded in (UI v2 · 04 point 1):
        # an `auto_escalate` override must arm/disarm review here the same way
        # it does the Config tab's read, not just the template's own binding.
        chain = store.effective_chain(
            json.loads(row["chain_definition"]), store.node_overrides_of(row)
        )
        gate_index = gate_node_index(chain, gate)
        node = chain["nodes"][gate_index]
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
        chain = store.effective_chain(
            json.loads(row["chain_definition"]), store.node_overrides_of(row)
        )
        node = chain["nodes"][gate_node_index(chain, gate)]
        if not node.get("auto_escalate"):
            return False
        if _gate_already_reviewed(evts, gate):
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


def _gate_already_reviewed(evts: list, gate: str) -> bool:
    """True iff the current `gate_requested <gate>` already had a review
    *attempt* -- a `gate_auto_review_started`, or any
    `gate_auto_review_skipped` for this gate regardless of `reason`
    (`"undecided"`, `"budget"`) -- since it was requested. One review
    attempt per `gate_requested` is the contract: every non-terminating
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
    """
    for e in reversed(evts):
        if e["type"] == "gate_requested" and e["payload"].get("gate") == gate:
            return False
        if e["type"] in _RUN_BOUNDARY:
            return False
        if (
            e["type"] in ("gate_auto_review_skipped", "gate_auto_review_started")
            and e["payload"].get("gate") == gate
        ):
            return True
    return False


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
#: machinery unblocking itself, not a person looking at the item.
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
    chain = json.loads(row["chain_definition"])
    start = next(i for i, n in enumerate(chain["nodes"]) if n["id"] == node_id)
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
