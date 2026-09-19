from __future__ import annotations

import json

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import executor, store
from kraft.adapters import agent as agent_mod
from kraft.api import api_router, deps
from kraft.api.routes import artifacts, board
from kraft.api.routes.lifecycle import _stop_live_sessions
from kraft.executor import gates
from kraft.templates import (
    CHAIN_REVIEW_GATE,
    ChainNode,
    carry_forward_node_fields,
    strip_non_proposable_carryover_fields,
    validate_nodes,
    validate_proposed_node_overrides,
    with_steps,
)


def _strip_front_matter(text: str) -> str:
    """The body of an artifact file, after its mandatory YAML front matter
    (`agent._ARTIFACT`'s contract, shared by every artifact-carrying hook).
    The whole text back if there is no front-matter block, so a hand-edited
    or malformed file still gets a chance to parse as-is."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end != -1 else text


def _splice_chain_review(st, row) -> tuple[dict | None, dict | None, str | None]:
    """The chain_finalized gate's approval decision (Kraft-hm0, extended
    Kraft-df4tc for escalation targets and the per-node model/effort dial).

    Returns `(spliced_chain, node_override_patch, None)` when the gate may
    advance -- `node_override_patch` is `{}` when the reviewer proposed no
    `proposed_node_overrides` at all -- or `(None, None, reason)` when it
    must not: one bad field anywhere in the envelope stops the whole
    approval, never a partial apply.
    """
    rel = agent_mod.artifact_path("chain_review", row["id"])
    path = st.run_dirs.worktrees / row["id"] / rel
    if not path.is_file():
        return None, None, "chain_review: no artifact found; the worker did not write one"
    try:
        envelope = json.loads(_strip_front_matter(path.read_text()))
    except (OSError, ValueError) as exc:
        return None, None, f"chain_review: could not parse artifact: {exc}"
    if not isinstance(envelope, dict) or envelope.get("status") not in (
        "ready_for_approval",
        "error",
    ):
        return None, None, "chain_review: artifact is missing a valid 'status'"
    if envelope["status"] == "error":
        return None, None, envelope.get("rationale") or "chain_review: reported status 'error'"

    nodes = envelope.get("revised_chain_nodes")
    chain = json.loads(row["chain_definition"])
    tail_start = board._gate_node_index(chain, CHAIN_REVIEW_GATE) + 1
    preceding_ids = frozenset(n["id"] for n in chain["nodes"][:tail_start])
    errs = (
        validate_nodes(nodes, st.registry, preceding_ids=preceding_ids)
        if isinstance(nodes, list)
        else ["not a list"]
    )
    if errs:
        return None, None, f"chain_review: revised_chain_nodes invalid: {errs[0]}"

    # proposed_node_overrides (Kraft-df4tc point 2): reviewer-authored per-
    # node model/effort dial, not a chain_definition field -- pull it off
    # every node before the carryover/splice below ever sees it, validating
    # as we go. Only model/escalate_model/effort are the reviewer's to
    # propose (point 5); auto_escalate and everything else stay the human
    # PATCH route's alone. One bad field anywhere rejects the whole approval.
    proposals: dict[str, dict] = {}
    for n in nodes:
        proposed = n.pop("proposed_node_overrides", None)
        if not proposed:
            continue
        if not isinstance(proposed, dict):
            return (
                None,
                None,
                (f"chain_review: node {n['id']!r} proposed_node_overrides must be an object"),
            )
        field_errs = validate_proposed_node_overrides(proposed)
        if field_errs:
            return (
                None,
                None,
                (f"chain_review: node {n['id']!r} proposed_node_overrides: {field_errs[0]}"),
            )
        proposals[n["id"]] = proposed
    if proposals:
        started = st.db.read(
            lambda c: {nid for nid in proposals if store.node_started(c, row["id"], nid)}
        )
        if started:
            bad = sorted(started)[0]
            return (
                None,
                None,
                (f"chain_review: node {bad!r} has already started; its config is locked"),
            )

    # auto_escalate/auto_escalate_stuck/auto_escalate_delay_s are never the
    # reviewer's to set (SKILL.md, point 5's PATCH-route boundary) -- unlike
    # on_failure/reject_to/rebase_bounce_to, `validate_nodes` above does not
    # (and cannot, since a template author legitimately sets these) reject
    # them, so an agent-authored value must be dropped here, before it can
    # ever reach `carry_forward_node_fields`, which only fills fields a node
    # omits and would otherwise leave an explicit value in place untouched.
    nodes = strip_non_proposable_carryover_fields(nodes)

    # No special-case for an unchanged tail (spec: splicing the same list back
    # in is a no-op in effect) -- one code path for both, not two that drift.
    # `nodes` only carries the schema-taught fields (Kraft-eod0); carry the
    # rest -- auto_escalate*, and any of on_failure/reject_to/
    # rebase_bounce_to the reviewer chose not to set explicitly -- forward
    # from the node each one replaces, or an "unchanged" node silently loses
    # config it had.
    # `materialize` is not the only producer of chain_definition nodes: an
    # approved chain review replaces the tail with agent-authored dicts, and
    # the reviewer's schema only knows `tasks` (SKILL.md's node shape). Both
    # producers run the same normalizer so `measure_node` never has to ask
    # which path a node came from.
    nodes = [with_steps(n) for n in carry_forward_node_fields(chain["nodes"][tail_start:], nodes)]
    # Validate once more over the *merged* tail: the check above only saw the
    # reviewer's own values, and a `reject_to`/`rebase_bounce_to` carried
    # forward from the node being replaced can dangle against the revised tail
    # (the reviewer renamed or dropped the target) or now point forward (the
    # target moved later). Either one reaches walk.py's bare
    # `next(j for j, n in ... if n["id"] == bounce_to)` -- StopIteration
    # mid-walk, or a bounce that silently skips the nodes in between. Intake's
    # `skip_nodes` route guards the identical case (work_items.py).
    errs = validate_nodes(nodes, st.registry, preceding_ids=preceding_ids)
    if errs:
        return None, None, f"chain_review: revised_chain_nodes invalid: {errs[0]}"
    chain["nodes"][tail_start:] = nodes
    return chain, proposals, None


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
    if gate != CHAIN_REVIEW_GATE:
        return json.loads(row["chain_definition"]), None
    chain, node_override_patch, reason = _splice_chain_review(st, row)
    if chain is None:
        return None, reason

    def write(c):
        store.splice_chain(c, row["id"], json.dumps(chain))
        if node_override_patch:
            store.set_node_overrides(c, row["id"], node_override_patch)

    await st.db.write(write)
    return chain, None


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
    chain = json.loads(row["chain_definition"])
    if gate not in ChainNode.gate_names(chain["nodes"]):
        raise HTTPException(404, f"unknown gate {gate!r}")
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

    await st.db.write(lambda c: store.approve_gate(c, wid, gate, by=_decided_by(request)))
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
    chain = json.loads(row["chain_definition"])
    if gate not in ChainNode.gate_names(chain["nodes"]):
        raise HTTPException(404, f"unknown gate {gate!r}")
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
        executor.reject_target(chain, executor.gate_node_index(chain, gate), body.node)
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

    try:
        target = await executor.apply_rejection(
            st.db,
            st.policy,
            work_item_id=wid,
            chain=chain,
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
        try:
            deps.spawn(
                request.app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    gates.auto_escalate_stuck(
                        "needs_human",
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        registry=st.registry,
                        policy=st.policy,
                        launch=deps.launch(st, row["repo"]),
                        bd_cwd=deps.bd_cwd(),
                        on_approve=deps._on_approve(st),
                    ),
                ),
            )
        except deps.AlreadyRunning:
            raise HTTPException(409, "a walk is already running for this work item") from None
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
