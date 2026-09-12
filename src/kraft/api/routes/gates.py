from __future__ import annotations

import json

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import executor, store
from kraft.adapters import agent as agent_mod
from kraft.api import api_router, deps
from kraft.api.routes import artifacts, board
from kraft.api.routes.lifecycle import _terminate
from kraft.templates import GATE_NAMES, carry_forward_node_fields, validate_nodes


def _strip_front_matter(text: str) -> str:
    """The body of an artifact file, after its mandatory YAML front matter
    (`agent._ARTIFACT`'s contract, shared by every artifact-carrying hook).
    The whole text back if there is no front-matter block, so a hand-edited
    or malformed file still gets a chance to parse as-is."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end != -1 else text


def _splice_chain_review(st, row) -> tuple[dict | None, str | None]:
    """The chain_finalized gate's approval decision (Kraft-hm0).

    Reads the `chain_review` artifact, parses its `{status,
    revised_chain_nodes, rationale}` envelope (JSON in the artifact body,
    after the shared front-matter block), and validates a revision through
    Kraft-unk's splice-tier `validate_nodes` before it is trusted anywhere
    near `chain_definition`.

    Returns `(spliced_chain, None)` when the gate may advance, or `(None,
    reason)` when it must not — approving a gate must never be the thing
    that lets a corrupt chain through, so every failure here stops the item
    at needs_human instead of silently keeping the old tail.
    """
    rel = agent_mod.artifact_path("chain_review", row["id"])
    path = st.run_dirs.worktrees / row["id"] / rel
    if not path.is_file():
        return None, "chain_review: no artifact found; the worker did not write one"
    try:
        envelope = json.loads(_strip_front_matter(path.read_text()))
    except (OSError, ValueError) as exc:
        return None, f"chain_review: could not parse artifact: {exc}"
    if not isinstance(envelope, dict) or envelope.get("status") not in (
        "ready_for_approval",
        "error",
    ):
        return None, "chain_review: artifact is missing a valid 'status'"
    if envelope["status"] == "error":
        return None, envelope.get("rationale") or "chain_review: reported status 'error'"

    nodes = envelope.get("revised_chain_nodes")
    errs = validate_nodes(nodes, st.registry) if isinstance(nodes, list) else ["not a list"]
    if errs:
        return None, f"chain_review: revised_chain_nodes invalid: {errs[0]}"

    chain = json.loads(row["chain_definition"])
    tail_start = board._gate_node_index(chain, "chain_finalized") + 1
    # No special-case for an unchanged tail (spec: splicing the same list back
    # in is a no-op in effect) -- one code path for both, not two that drift.
    # `nodes` only carries the four fields the skill's schema teaches
    # (Kraft-eod0); carry the rest -- on_failure, reject_to, rebase_bounce_to,
    # auto_escalate -- forward from the node each one replaces, or a reviewer
    # that says "unchanged" silently strips the repair/reject/escalate config
    # those nodes had.
    nodes = carry_forward_node_fields(chain["nodes"][tail_start:], nodes)
    chain["nodes"][tail_start:] = nodes
    return chain, None


async def _stop_live_review(st, wid: str) -> None:
    """Stop a gate's own in-flight auto_escalate review before cancelling it.

    Same ordering as pause_work_item: mark the node's running sessions paused
    and SIGTERM them before `deps.cancel` tears down the task. `deps.cancel`
    alone never kills the review agent's process (`run_task` only stops the
    log watcher on CancelledError), so without this the agent survives as an
    orphan in the item's worktree -- right where the approved walk's next
    node is about to launch its own agent -- and the session's row is left
    'running' forever, unreachable once current_node_id moves on.
    """
    sessions = st.db.read(lambda c: store.running_sessions_for_node(c, wid))
    ids = [s["id"] for s in sessions]
    await st.db.write(lambda c: store.pause_work_item(c, wid, ids))
    for s in sessions:
        _terminate(s["pid"])


class GateReject(BaseModel):
    note: str
    #: Where the chain re-enters. Defaults to the gate node's `reject_to`, and
    #: failing that to the gate node itself (Kraft-ko7j).
    node: str | None = None


async def apply_approval(st, row, gate: str) -> tuple[dict | None, str | None]:
    """Everything an approval does before the chain is allowed to move, and the
    chain it may move along -- or `(None, reason)` when it must not move at all.

    One function, two doors: the `POST .../approve` endpoint (a human) and
    `kraft.executor.gates.review_gates` (an agent's `approve` verdict), which reaches it
    through the `on_approve` callback `_launch_approval` hands the executor.
    The same rule `apply_rejection` already enforces for the other verdict:
    an agent's approval must have exactly the effects a person's would, or
    `chain_review`'s revised nodes are silently discarded on the path this
    feature makes the default (Kraft-zr3s).

    Ingest before any splice: the artifact this approval is about belongs to
    `row`'s chain as it stood when the gate opened, same as `_gate_artifact`
    everywhere else it's called.
    """
    await artifacts._ingest_approved_gate_artifact(st, row, gate)
    if gate != "chain_finalized":
        return json.loads(row["chain_definition"]), None
    chain, reason = _splice_chain_review(st, row)
    if chain is None:
        return None, reason
    await st.db.write(lambda c: store.splice_chain(c, row["id"], json.dumps(chain)))
    return chain, None


@api_router.post("/work-items/{wid}/gates/{gate}/approve")
async def approve_gate(wid: str, gate: str, request: Request):
    st = request.app.state
    row = deps._work_item_row(st, wid)
    if gate not in GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if board._pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    if deps.task_is_live(request.app, wid):
        # A pending gate's status is already needs_human (never active), so
        # the only thing a live task can be here is this gate's own
        # in-flight auto_escalate review -- and a human decision is exactly
        # what outranks a verdict computed against stale state
        # (executor/gates.py's gate_auto_review_discarded branch). Stop it
        # and proceed, the same way pause/skip already stop a live walk
        # before continuing, rather than 409 a human out for the review's
        # whole duration.
        await _stop_live_review(st, wid)
        await deps.cancel(request.app, wid)

    chain, reason = await apply_approval(st, row, gate)
    if chain is None:
        # Kraft-iv4y: a human hitting `approve` again after this exact failure
        # used to get a 200 back with nothing changed -- the same reason
        # logged a second time, no error, no hint that approving was never
        # going to work. The node that produced the bad artifact needs to be
        # redone, not re-approved, so this is a clear stop, not a silent one.
        await st.db.write(lambda c: store.mark_needs_human(c, wid, row["current_node_id"], reason))
        raise HTTPException(
            422, f"{reason} -- gate {gate!r} cannot be approved; run `kraft item retry` instead"
        )

    await st.db.write(lambda c: store.approve_gate(c, wid, gate))
    start = board._gate_node_index(chain, gate) + 1
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
                    registry=st.registry,
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


@api_router.post("/work-items/{wid}/gates/{gate}/reject")
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
    if gate not in GATE_NAMES:
        raise HTTPException(404, f"unknown gate {gate!r}")
    if board._pending_gate(st, wid) != gate:
        raise HTTPException(409, f"gate {gate!r} is not pending")
    if st.invalid_policy:
        # Same posture as intake (§9): a re-run we cannot bound is not started.
        # Before _stop_live_review, not after: this bail-out changes nothing,
        # so it must not be reached having already paused the item and killed
        # its review agent.
        raise HTTPException(
            503, f"policy config invalid, refusing work: {'; '.join(st.invalid_policy)}"
        )
    chain = json.loads(row["chain_definition"])
    try:
        executor.reject_target(chain, executor.gate_node_index(chain, gate), body.node)
    except ValueError as exc:
        # Same posture as invalid_policy above: a bad target changes nothing,
        # so it must not be reached having already paused the item and killed
        # its review agent.
        raise HTTPException(400, str(exc)) from exc

    if deps.task_is_live(request.app, wid):
        # Same reasoning as approve_gate: a live task here can only be this
        # gate's own in-flight auto_escalate review, and a human's decision
        # is meant to outrank it -- stop the review instead of locking the
        # human out for its duration.
        await _stop_live_review(st, wid)
        await deps.cancel(request.app, wid)

    try:
        target = await executor.apply_rejection(
            st.db,
            st.policy,
            work_item_id=wid,
            chain=chain,
            gate=gate,
            note=body.note,
            node=body.node,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if target is None:
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
                    registry=st.registry,
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
