from __future__ import annotations

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import executor, store
from kraft.api import api_router, deps
from kraft.api.routes import artifacts, board
from kraft.api.routes.lifecycle import _stop_live_sessions
from kraft.executor import stops
from kraft.templates.models import GateNode


def gate_nodes(st, row) -> tuple:
    """This item's ordered nodes, as the executor sees them: the frozen
    `MaterializedChain`, node overrides folded on
    (`store.effective_nodes`). One reader for every gate route, so the
    approve door, the reject door and the walk cannot disagree about where a
    gate sits."""
    return store.effective_nodes(executor.chain_of(row), store.node_overrides_of(row))


def _gate_or_404(nodes, gate: str):
    node = next((n for n in nodes if isinstance(n.node, GateNode) and n.id == gate), None)
    if node is None:
        raise HTTPException(404, f"unknown gate {gate!r}")
    return node


class GateReject(BaseModel):
    note: str
    #: Where the chain re-enters. Defaults to the gate node's `reject_to`, and
    #: failing that to the gate node itself (Kraft-ko7j).
    node: str | None = None


async def apply_approval(st, row, gate: str) -> tuple[tuple | None, str | None]:
    """Everything an approval does before the chain is allowed to move, and the
    ordered nodes it may move along -- or `(None, reason)` when it must not move
    at all.

    One function, two doors: the `POST .../approve` endpoint (a human) and
    `kraft.executor.gates.review_gates` (an agent's `approve` verdict), which reaches it
    through the `on_approve` callback `_launch_approval` hands the executor.
    The same rule `apply_rejection` already enforces for the other verdict:
    an agent's approval must have exactly the effects a person's would.

    **The final-review gate is selected by `GateNode.chain_finalized`, never by
    its name** (`chain-finalized-remains-a-dedicated-marker`): a chain may call
    that gate whatever it likes.

    What the marker still buys, and it is the difference an ordinary gate must
    not have: **a final-review gate cannot be approved without its document.**
    Every other gate is answerable with nothing to read (`gate_artifact`'s own
    contract -- an agent that reported done without honouring the artifact
    contract still leaves a decidable gate), but the whole subject of this one is
    the review it names, so approving it with no document approves nothing.
    Kraft-iv4y's posture: a clear 422 telling the human to `kraft item retry` the
    node that owed the document, not a 200 that changed nothing.

    **What a `chain_finalized` approval does not do: revise the chain.** The
    legacy splice rewrote `chain_definition` from a reviewer's legacy node
    list; revising a *materialized* V1 chain in place is a different feature,
    with no V1 requirement and no V1 schema, so the approval ingests the
    artifact and advances.
    """
    nodes = gate_nodes(st, row)
    node = _gate_or_404(nodes, gate)
    await artifacts._ingest_approved_gate_artifact(st, row, gate)
    if node.node.chain_finalized and executor.gate_artifact(st.run_dirs, row, gate) is None:
        return None, (
            f"{gate}: the final review document is missing; the node that owed it did not write one"
        )
    return nodes, None


def _decided_by(request: Request) -> str:
    """Which of three kinds of caller made this gate decision (Kraft-s7c04.43).

    A gate exists to record who decided, and this route recorded everyone as a
    person -- it took `by="human"` from `store.gates`' default, so the MCP
    approve/reject tools an agent calls landed here indistinguishable from a
    human clicking Approve. `analytics.py` branches on `by` to decide whether a
    run boundary was real human oversight, so the error ran in the
    safe-looking direction: an agent clearing its own gate read as supervision.

    Three values, because there are three callers and only one is a person:

    * `agent` -- a Kraft worker. `X-Kraft-Session-Id` comes from
      `KRAFT_SESSION_ID`, set only at `adapters.agent`'s dispatch, so its
      presence means a session Kraft itself launched. Checked first: a worker
      reaching the API through MCP carries both headers, and what it *is*
      outranks how it connected.
    * `assistant` -- a session reaching Kraft through the MCP server
      (`mcp.serve_stdio` tags its own process). Not a worker deciding about its
      own artifact, and not a person either.
    * `human` -- a browser, or `kraft item approve` typed at a terminal.

    Deliberately not keyed on the MCP bearer token: `client.transport.http`
    attaches it to every call it makes, CLI included, so it cannot tell an
    agent from a person at all.
    """
    if request.headers.get("x-kraft-session-id"):
        return "agent"
    if request.headers.get("x-kraft-client") == "mcp":
        return "assistant"
    return "human"


@api_router.post("/work-items/{wid}/gates/{gate:path}/approve")
async def approve_gate(wid: str, gate: str, request: Request):
    st = request.app.state
    row = deps._work_item_row(st, wid)
    _gate_or_404(gate_nodes(st, row), gate)
    if board._pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    # A pending gate's status is already needs_human (never active), so the
    # only thing running here can be this gate's own in-flight auto_escalate
    # review -- and a human decision is exactly what outranks a verdict
    # computed against stale state (executor/gates.py's
    # gate_auto_review_discarded branch). Stop it and proceed, the same way
    # pause/skip already stop a live walk before continuing, rather than 409
    # a human out for the review's whole duration.
    #
    # The session kill is not conditional on `task_is_live`: a running
    # session row can outlive the in-process task that spawned it (a server
    # restart leaves the row, not the task), and that agent is still writing
    # in the worktree the approved walk is about to launch into. A no-op
    # returning [] when nothing is running, which is most of the time.
    await _stop_live_sessions(st, wid)
    if deps.task_is_live(request.app, wid):
        await deps.cancel(request.app, wid, timeout=deps.CANCEL_TIMEOUT)

    nodes, reason = await apply_approval(st, row, gate)
    if nodes is None:
        # Kraft-iv4y: a human hitting `approve` again after this exact failure
        # used to get a 200 back with nothing changed -- the same reason
        # logged a second time, no error, no hint that approving was never
        # going to work. The node that produced the bad artifact needs to be
        # redone, not re-approved, so this is a clear stop, not a silent one.
        await st.db.write(lambda c: store.mark_needs_human(c, wid, row["current_node_id"], reason))
        raise HTTPException(
            422, f"{reason} -- gate {gate!r} cannot be approved; run `kraft item retry` instead"
        )

    # Bracketed from *before* the claim to the hand-off. `store.approve_gate` is
    # an unconditional `UPDATE work_items SET status = 'active'` -- a claim like
    # any other, which `api/deps.py`'s `task_is_live` docstring already names:
    # "calling `store.approve_gate`/`apply_rejection` and only then discovering
    # `spawn` refuses would leave the gate cleared and the item `active` with no
    # walk behind it". The `_gate_node_index` below is a defaultless `next(...)`
    # that raises `StopIteration` for a gate this chain does not have, after the
    # claim -- verbatim the shape `stops.claimed_or_stopped` exists for, and the
    # one `dev/check_claim_handoff.py` could not see until `CLAIMS` stopped
    # being hand-written.
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        row["current_node_id"],
        reason="gate approval cleared the gate but could not start a walk",
        handed_off=lambda: deps.task_is_live(request.app, wid),
    ):
        await st.db.write(lambda c: store.approve_gate(c, wid, gate, by=_decided_by(request)))
        start = board._gate_node_index(nodes, gate) + 1
        try:
            deps.spawn(
                request.app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    executor.run(
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        bd_cwd=deps.bd_cwd(),
                        start_index=start,
                        policy=st.policy,
                        launch=deps.launch(st, row["repo"]),
                        on_approve=deps._on_approve(st),
                    ),
                ),
            )
        except deps.AlreadyRunning:
            raise HTTPException(409, "a walk is already running for this work item") from None
        return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}


@api_router.post("/work-items/{wid}/gates/{gate:path}/reject")
async def reject_gate(wid: str, gate: str, body: GateReject, request: Request):
    """Reject a gate and put the chain back to work (02 §7.2, backward motion).

    Every gate takes this one path now. `human_review_approval` used to be
    terminal: the note landed in an event nothing read, no node was re-run, and
    the only exits left were approving the thing just rejected or abandoning
    the item (Kraft-ko7j). The single thing that may park an item at a rejected
    gate is the reject loop's own cap.
    """
    st = request.app.state
    row = deps._work_item_row(st, wid)
    nodes = gate_nodes(st, row)
    _gate_or_404(nodes, gate)
    if board._pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    if st.invalid_policy:
        # Same posture as intake (§9): a re-run we cannot bound is not started.
        # Before the kill below, not after: this bail-out changes nothing,
        # so it must not be reached having already paused the item and killed
        # its review agent.
        raise HTTPException(
            503, f"policy config invalid, refusing work: {'; '.join(st.invalid_policy)}"
        )
    try:
        executor.reject_target(nodes, executor.gate_node_index(nodes, gate), body.node)
    except ValueError as exc:
        # Same posture as invalid_policy above: a bad target changes nothing,
        # so it must not be reached having already paused the item and killed
        # its review agent.
        raise HTTPException(400, str(exc)) from exc

    # Same reasoning, and same ordering, as approve_gate above: the review
    # this decision outranks is stopped instead of locking the human out for
    # its duration, and both refusals above run before any of it.
    await _stop_live_sessions(st, wid)
    if deps.task_is_live(request.app, wid):
        await deps.cancel(request.app, wid, timeout=deps.CANCEL_TIMEOUT)

    # Bracketed from *before* the claim to the hand-off, the same way the approve
    # door above is. `apply_rejection` performs an `UPDATE work_items SET status
    # = 'active'` when the reject loop still has attempts left (`store.reject_gate`
    # with `reopen=True`) -- a claim like any other, and the other half of the
    # pairing `api/deps.py`'s `task_is_live` docstring has been naming all along:
    # "calling `store.approve_gate`/`apply_rejection` and only then discovering
    # `spawn` refuses would leave the gate cleared and the item `active` with no
    # walk behind it". The approve half was bracketed a round before this one; the
    # f-string SQL in `store.reject_gate` is why `dev/check_claim_handoff.py`
    # could not see this half until its derivation learned to read a `JoinedStr`.
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        row["current_node_id"],
        reason="gate rejection reopened the gate but could not start a walk",
        handed_off=lambda: deps.task_is_live(request.app, wid),
    ):
        try:
            target = await executor.apply_rejection(
                st.db,
                st.policy,
                work_item_id=wid,
                nodes=nodes,
                gate=gate,
                note=body.note,
                node=body.node,
                by=_decided_by(request),
                # A person only ever rejects; `fixed` is a gate-reviewer verdict and
                # has no door here (Kraft-s7c04.16).
                verdict="reject",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if target is None:
            # A spent reject loop goes to a human, not an agent (Ruling 176).
            return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}

        try:
            deps.spawn(
                request.app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    executor.run(
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        bd_cwd=deps.bd_cwd(),
                        start_index=target,
                        policy=st.policy,
                        steer=body.note,
                        launch=deps.launch(st, row["repo"]),
                        on_approve=deps._on_approve(st),
                    ),
                ),
            )
        except deps.AlreadyRunning:
            raise HTTPException(409, "a walk is already running for this work item") from None
        return {k: v for k, v in dict(deps._work_item_row(st, wid)).items()}
