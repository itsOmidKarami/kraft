"""The agent that answers review threads after a `comment` review
(docs/superpowers/specs/2026-09-27-review-flow-backend-design.md §3).

Launched like `kraft.gate_review`: a worker (`identify_as_worker` stays True,
so it cannot clear the gate), its session filed under the *gate's* node id so
`_stop_live_sessions` finds it when a person approves or rejects mid-run. Its
task -- harness, model, profile -- is the one the gate would re-run on
rejection, the agent that wrote the code.

It must not change the worktree. It is launched read-only, so its context
tells it to change nothing and never to commit or run tests. No tool is denied:
it needs Write and Bash for its result file and `kraft item reply`, and a
harness that cannot express a deny list would refuse the launch. So the
worktree is compared before and after, and a change is recorded as
`reply_agent_wrote`. In place it holds the checkout's lease but works in a
throwaway copy of the item's branch (`inplace.copy`), never the person's
checkout: the copy is compared whole, plus the person's branch tip, which a
commit pushed from the copy moves, and removed after. A copy that cannot be
made is `reply_agent_failed`. A change is deliberately not a stop:
`read_only.stop` would `mark_needs_human`, which notifies, and this agent
failing is not a person's problem.
"""

from __future__ import annotations

import asyncio
import json
import logging

from kraft import caps, events, executor, inplace, reviewed_head, store
from kraft import overrides as _overrides
from kraft.adapters import agent as _agent
from kraft.adapters import subprocess as _subprocess
from kraft.executor import read_only
from kraft.remote import placement as _placement
from kraft.templates.models import AgentTask
from kraft.vocab import GateEvent
from kraft.worker import callback as _callback
from kraft.worker import steering as _steering

logger = logging.getLogger(__name__)

_PROMPT = (
    "A reviewer left comments on this Kraft work item's change, and wants answers "
    "before deciding. You wrote this code. Do not change any file in this "
    "worktree: this is a conversation, not a fix. Read what you need, then reply "
    "to every thread below exactly once:\n"
    "\n"
    '  kraft item reply <thread-id> --claim answered --body "..."    -- an answer\n'
    '  kraft item reply <thread-id> --claim should_fix --body "..."  -- you agree a '
    "change is needed; say what it is\n"
    "\n"
    "Title: {title}\n"
    "\n"
    "{threads}"
)


def _agent_task(nodes, gate: str):
    """The first agent task of the node a rejection would re-run, or None."""
    target = nodes[executor.reject_target(nodes, executor.gate_node_index(nodes, gate), None)]
    for step in target.steps:
        for t in step.tasks:
            if isinstance(t.task, AgentTask):
                return t
    return None


async def run(db, run_dirs, *, work_item_id: str, gate: str, nodes, launch) -> str:
    """A failure anywhere in here -- resolving the agent's config, launching
    it, reading the worktree -- leaves threads unanswered and records
    `reply_agent_failed` rather than escalating: a broken reply agent is not a
    person's problem (docs/superpowers/specs/2026-09-27-review-flow-backend-design.md
    §3), so the caller must not wrap this in `deps.guard` (Kraft-dl5fl)."""
    try:
        return await _run(
            db, run_dirs, work_item_id=work_item_id, gate=gate, nodes=nodes, launch=launch
        )
    except (asyncio.CancelledError, AssertionError, store.CheckoutBusy, inplace.CheckoutRefused):
        raise  # nothing started: the caller queues, skips or stops on it
    except Exception as exc:  # noqa: BLE001
        logger.exception("reply agent failed for %s at gate %s", work_item_id, gate)
        error = repr(exc)
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                GateEvent.REPLY_AGENT_FAILED,
                {"gate": gate, "error": error},
                node_id=gate,
            )
        )
        return "failed"


async def refused_without_channel(db, row, launch, gate: str) -> bool:
    """True, recorded on the item as `reply_agent_skipped`, when the item's
    sandbox has no `network:`: the reply agent answers by `kraft item
    reply`, which such a sandbox has no route for, so none runs."""
    try:
        sandbox = executor.item_sandbox(row, launch)
    except executor.SandboxUnresolved:
        return False
    if _callback.reachable(sandbox):
        return False
    reason = "the item's sandbox has no `network:`, so no route to Kraft: no reply agent runs"
    await db.write(
        lambda c: events.append(
            c,
            row["id"],
            GateEvent.REPLY_AGENT_SKIPPED,
            {"gate": gate, "reason": reason},
            node_id=gate,
        )
    )
    return True


async def _run(db, run_dirs, *, work_item_id: str, gate: str, nodes, launch) -> str:
    threads = db.read(lambda c: store.unanswered(c, work_item_id))
    if not threads:
        return "nothing_to_answer"
    task = _agent_task(nodes, gate)
    if task is None:
        return "no_agent"
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    worktree = inplace.item_folder(run_dirs, row)
    # The `comment` review that launched this, whose summary is part of what was asked.
    review = db.read(lambda c: store.last_review(c, work_item_id))
    summary = review["summary"] if review else None
    try:
        sandbox = executor.item_sandbox(row, launch)
    except executor.SandboxUnresolved:
        return "no_agent"
    if await refused_without_channel(db, row, launch, gate):
        return "no_channel"
    hit = db.read(lambda c: caps.at_launch(c, row, task))
    if hit is not None and hit.remaining_s <= 0:
        return "no_agent"
    time_cap = caps.Deadline(caps.monotonic() + hit.remaining_s, hit) if hit else None
    item_override = json.loads(row["agent_overrides"]) if row["agent_overrides"] else None
    # A model stored before the write doors checked model ids never reaches the
    # command line: `run` records the refusal as `reply_agent_failed`.
    refusal = _overrides.stored_model_refusal(work_item_id, item_override)
    if refusal is not None:
        raise ValueError(refusal)
    await executor.restore_pins(row)
    try:
        inv = _agent.resolve_agent_task(
            task.task,
            launch.repo_entry,
            launch.library_steering,
            skills_dir=launch.skills_dir,
            item_override=item_override,
            **executor.frozen_steering(row),
            item_repo=row["repo"],
            policy=executor.scope_policy(row, task),
            repo_id=launch.item_repo_id,
        )
    except _steering.SteeringError:
        return "no_agent"
    session_id = db.read(lambda c: store.new_session_id(c, work_item_id))
    # In place it reads a copy of the branch, never the person's checkout.
    copy = inplace.copy_path(run_dirs, work_item_id, session_id) if store.is_in_place(row) else None
    before = None if copy else read_only.snapshot(db, row, launch, worktree)
    since = None

    async def after_checkout() -> None:
        nonlocal since
        since = inplace.watch_copy(db, row, launch, copy, worktree)

    # The head it starts over, at the final gate: a person's approval that
    # stops it pins only a head it did not move (`reviewed_head.moved_under`).
    tips = reviewed_head.pin_for(db, row, gate, worktree)
    try:
        await _agent.run_agent_task(
            db,
            run_dirs,
            session_id=session_id,
            work_item_id=work_item_id,
            node_id=gate,
            hook_point=f"{gate}.reply",
            command=inv.command,
            harness=inv.harness,
            model=inv.model,
            effort=inv.effort,
            deny_tools=inv.deny_tools,
            allowed_tools=inv.allowed_tools,
            grants=inv.grants,
            permission_mode=inv.permission_mode,
            method_text=inv.method_text,
            steering_texts=inv.steering_texts,
            sandbox=sandbox,
            read_only=True,
            checkout=executor.sandbox_checkout(row, launch, worktree) if sandbox else None,
            task_instruction=_PROMPT.format(
                title=row["title"], threads=store.render_note(threads, summary)
            ),
            title=row["title"],
            repo_path=str(copy) if copy else row["repo"],
            cwd=copy or worktree,
            repo_entry=launch.repo_entry,
            time_cap=time_cap,
            harness_id=task.task.harness,
            takes_checkout=True,
            tips=tips,
            after_checkout=after_checkout if copy else None,
            in_copy=copy is not None,
            runner=_placement.remote(_placement.node_runner(row, gate)),
            follow_node=True,
        )
        if copy:
            # None: refused before its copy was made, so nothing ran in it.
            files = since() if since else []
        else:
            files = read_only.changed(db, row, launch, worktree, before)
    finally:
        if copy is not None:
            await _subprocess.uncancellable(asyncio.to_thread(inplace.remove_copy, copy))
    if files:
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                GateEvent.REPLY_AGENT_WROTE,
                {"gate": gate, "files": files},
                node_id=gate,
            )
        )
        return "wrote"
    return "replied"
