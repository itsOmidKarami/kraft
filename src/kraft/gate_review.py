"""Agent review of a gate before a human sees it
(docs/superpowers/specs/2026-09-10-auto-escalate-gates-design.md, Kraft-zr3s).

Dispatched through the same `run_agent_task` engine every chain node uses, and
deliberately *as a worker*: `identify_as_worker=True` means
`client.context._forbid_self_action` already refuses to let this session approve or
reject its own item. The agent therefore cannot clear the gate however it is
prompted -- it must report a verdict, and `kraft.executor.gates.review_gates`
applies it.
That is the opposite choice from `escalate.py`, which is not a worker precisely
so that a human's escalation agent *can* act.
"""

from __future__ import annotations

import json
import uuid

from kraft import caps, events, executor
from kraft import harness as _harness
from kraft.adapters import agent as _agent
from kraft.adapters import subprocess as _subprocess
from kraft.adapters.profiles import HarnessUnavailable, harness_table
from kraft.executor.fallback import fallback_list
from kraft.templates.models import AgentTask, ResolvedNode
from kraft.worker import steering as _steering

#: The only strings a verdict may be. Anything else -- a typo, a sentence, a
#: missing key -- is `undecided`, which leaves the gate pending for a human.
VERDICTS = frozenset({"approve", "reject", "fixed", "undecided"})

#: Statuses that mean the session did not finish thinking. A verdict written
#: beside one of these is not a judgement, whatever it says.
_UNTRUSTWORTHY = frozenset({"failed", "needs_context", "rate_limited"})

_PROMPT = (
    "This is a gate review. A Kraft work item has reached the gate "
    "{gate!r} on node {node_id!r}, and is waiting for a decision. You are "
    "deciding whether it needs a person.\n"
    "\n"
    "Title: {title}\n"
    "{description_line}"
    "{artifact_line}"
    "\n"
    "You are a Kraft worker for this launch, so you cannot approve or reject "
    "the gate yourself -- and must not try. Report your decision by writing "
    "`verdict` into your result file, with your reasoning in `concerns`:\n"
    "\n"
    '  "verdict": "approve"   -- the gate should clear; you are confident.\n'
    '  "verdict": "reject"    -- send it back to be redone. `concerns` becomes '
    "the steering note for the agent that redoes it, so write it as an "
    "instruction, not a complaint. Required: a rejection with no reasoning is "
    "discarded.\n"
    '  "verdict": "fixed"     -- you repaired something in this worktree. You '
    "may edit and commit here, but you may not then approve your own edit: the "
    "node re-runs and is measured again. `concerns` says what you changed. Note "
    "what that costs: the whole node runs again and is reviewed again, so this "
    "is the right call for a defect you would otherwise have rejected for, and "
    "the wrong one for a nit you could have let through.\n"
    '  "verdict": "undecided" -- anything else. A person looks at it, with your '
    "`concerns` in front of them.\n"
    "\n"
    "Prefer `undecided` whenever you are unsure. A gate wrongly cleared is "
    "expensive and a gate wrongly held costs a human one glance."
)


def _artifact_line(rel: str | None) -> str:
    if not rel:
        return "There is no artifact document for this gate; read the worktree.\n"
    return f"The document this gate is about: {rel} (relative to this worktree)\n"


def _profile_fallback_refusal(task: AgentTask, gate: str) -> str | None:
    """None, or the operator-facing reason `task` must not launch (Kraft-t4y8g):
    its `profile:` names an agent profile that itself carries a `fallback:`
    list.

    `GateNode._no_handler` already refuses a gate whose `auto_review` task
    declares its own `fallback:`, at template load -- "a gate review never
    falls back" (this module launches its reviewer once; the loop lives in
    `dispatch.dispatch_node` alone, and stays there: making `gate_review` run
    it is a feature, not this fix). A profile's list is invisible to that
    check: `profile:` is read live from `harnesses.yaml`
    (`kraft.adapters.profiles`, Kraft-ps1ao), not at template load, so a
    profile with no fallback list today can grow one tomorrow without the
    chain that selects it ever being re-validated. That is also why the
    refusal lives here, at the gate's actual launch, rather than in template
    lint or `kraft admin doctor`: both would read `harnesses.yaml` once and
    could go stale the moment an operator edits it, while a launch-time check
    is the one place it is always current.

    Reuses `executor.fallback.fallback_list`, the same function
    `dispatch.dispatch_node` walks -- it already knows the precedence (the
    task's own list wins, else its profile's) and already names which one it
    found.
    """
    if task.profile is None:
        return None
    try:
        table, _path = harness_table(_harness.load(None))
    except HarnessUnavailable:
        # The launch a moment later hits the identical problem and reports
        # it; nothing here needs to say it twice.
        return None
    entries, source = fallback_list(task, table)
    if not entries or source == "the task's list":
        # The task's own list is `GateNode._no_handler`'s refusal, already
        # caught before this chain could ever be materialized.
        return None
    return (
        f"gate {gate!r}'s auto_review task selects profile {task.profile!r}, whose "
        f"{source} is not empty; a gate review launches its reviewer once and never "
        "runs the fallback loop, so it is refused rather than silently ignored"
    )


async def review(
    db,
    run_dirs,
    *,
    work_item_id: str,
    gate: str,
    node: ResolvedNode,
    launch: executor.LaunchContext,
) -> tuple[str, str]:
    """Dispatch one gate review. Returns `(verdict, note)`.

    `node` is the gate itself, and **the gate declares the reviewing task**:
    `GateNode.auto_review` is an ordinary `AnyTask`, resolved and launched like
    any other (`gate-auto-review-is-explicit-and-bounded`). This module used to
    hardwire `{"command": "claude", "skill": "gate-review"}` -- a reviewer
    declared nowhere, which no chain could change and no template could see.
    Only the *prompt* is Kraft's now, because the output contract
    (`verdict`/`concerns`) is what the caller applies.

    Never raises for an agent that misbehaved: every degraded outcome is
    `("undecided", note)`, which the caller turns into "leave the gate pending".
    """
    auto_review = node.auto_review
    if auto_review is None or not isinstance(auto_review.task, AgentTask):
        # Defence in depth, and unreachable from a validated chain:
        # `GateNode.auto_review` is typed `AgentTask | None`, so a non-agent
        # reviewer cannot be represented, and `auto_check_due` refuses an
        # *undeclared* one before this is called. Note what `auto_check_due` does
        # **not** do -- it tests `node.auto_review is None` and never the task's
        # kind, which is why this arm existed at all. It stays for a
        # `MaterializedChain` round-tripped out of a row an older build wrote,
        # which the new type cannot retroactively police.
        #
        # **The event is not optional.** `_gate_review_attempts` counts an
        # `gate_auto_review_skipped` that is not the tail of a
        # `gate_auto_review_started`, and this was the one early return that
        # wrote neither -- so the attempt tally stayed at zero, `auto_check_due`
        # stayed True, and the delay poller re-armed the same dead gate every
        # tick forever: a `max_concurrent` slot burned and a human's `retry`
        # 409'd, which is the exact failure `auto_check_due` exists to prevent.
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                "gate_auto_review_skipped",
                {"gate": gate, "reason": "unreviewable"},
            )
        )
        return "undecided", (f"gate {gate!r} declares no agent task to review it; a person decides")
    if (refusal := _profile_fallback_refusal(auto_review.task, gate)) is not None:
        # Same posture as the kind-check above: an unpaired skip, no
        # `_started`, so `_gate_review_attempts` still counts it as one
        # attempt and the delay poller does not re-arm a dead gate forever.
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                "gate_auto_review_skipped",
                {"gate": gate, "reason": "profile_fallback"},
            )
        )
        return "undecided", refusal
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")

    session_id = uuid.uuid4().hex
    rel = executor.gate_artifact(run_dirs, row, gate)
    task_instruction = _PROMPT.format(
        gate=gate,
        node_id=node.id,
        title=row["title"],
        description_line=f"Brief: {row['description']}\n" if row["description"] else "",
        artifact_line=_artifact_line(rel),
    )

    await db.write(
        lambda c: events.append(
            c, work_item_id, "gate_auto_review_started", {"gate": gate, "session_id": session_id}
        )
    )

    try:
        # The item's sandbox, as for every launch of it (Ruling 189).
        sandbox = executor.item_sandbox(row, launch)
    except executor.SandboxUnresolved as exc:
        # Recorded as the reviewer's own session, so the pending gate names
        # why no review ran (Kraft-9t2dp); nothing launches.
        await executor.config_error_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            f"{auto_review.path}: {exc}\n",
        )
        return "undecided", str(exc)
    # The gate's time cap bounds its review, as a node's does its tasks
    # (Kraft-8en38): spent refuses the launch; otherwise the one run is killed
    # at the deadline.
    hit = db.read(lambda c: caps.at_launch(c, row, auto_review))
    if hit is not None and hit.remaining_s <= 0:
        await executor.time_capped_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            hit,
        )
        return "undecided", hit.reason
    time_cap = caps.Deadline(caps.monotonic() + hit.remaining_s, hit) if hit else None
    try:
        inv = _agent.resolve_agent_task(
            auto_review.task,
            launch.repo_entry,
            launch.library_steering,
            skills_dir=launch.skills_dir,
            # Item-wide overrides only -- an item marked cheap stays cheap for its
            # gate reviews too (Kraft-ui79: no per-gate floor). Deliberately *not*
            # the per-node `node_overrides` model/effort dial that
            # `dispatch.dispatch_node` also merges (Kraft-df4tc): a node dialed to
            # a different model does not carry that dial into its own gate review.
            item_override=json.loads(row["agent_overrides"]) if row["agent_overrides"] else None,
            # The reviewer's and the repository's steering, frozen at intake.
            **executor.frozen_steering(row),
            # The item's own repo as recorded at intake (Kraft-jzdyp): a gate
            # review runs in the item's own checkout, never a fanned-out member's.
            item_repo=row["repo"],
            # The gate scope's policy: its tool lists and sandbox, as for any task.
            policy=executor.scope_policy(row, auto_review),
        )
    except _steering.SteeringError as exc:
        # Modelled on the `SandboxUnresolved` handler above: recorded as the
        # reviewer's own session, so the pending gate names why no review ran,
        # and `gate_auto_review_started` above is not left dangling with no
        # terminal event for `_gate_review_attempts` to count.
        await executor.config_error_session(
            db,
            run_dirs,
            dict(
                session_id=session_id,
                work_item_id=work_item_id,
                node_id=node.id,
                hook_point=auto_review.path,
                round=0,
                head_sha=None,
            ),
            f"{auto_review.path}: {exc}\n",
        )
        return "undecided", str(exc)
    status = await _agent.run_agent_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node.id,
        hook_point=auto_review.path,
        command=inv.command,
        harness=inv.harness,
        model=inv.model,
        effort=inv.effort,
        deny_tools=inv.deny_tools,
        allowed_tools=inv.allowed_tools,
        permission_mode=inv.permission_mode,
        method_text=inv.method_text,
        steering_texts=inv.steering_texts,
        sandbox=sandbox,
        task_instruction=task_instruction,
        title=row["title"],
        repo_path=row["repo"],
        cwd=run_dirs.worktrees / work_item_id,
        repo_entry=launch.repo_entry,
        time_cap=time_cap,
        harness_id=auto_review.task.harness,
    )

    result_path = run_dirs.results / f"{session_id}.json"
    note = _subprocess.read_concerns(result_path) or ""
    verdict = _subprocess.read_verdict(result_path)
    if status in _UNTRUSTWORTHY or verdict not in VERDICTS:
        return "undecided", note
    if verdict in ("reject", "fixed") and not note.strip():
        # The note is the steering instruction for the re-run. Without one there
        # is nothing to send back with, so it goes to a human instead.
        return "undecided", note
    return verdict, note
