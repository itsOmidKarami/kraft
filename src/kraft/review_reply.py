"""The agent that answers review threads after a `comment` review
(docs/superpowers/specs/2026-09-27-review-flow-backend-design.md §3).

Launched like `kraft.gate_review`: a worker (`identify_as_worker` stays True,
so it cannot clear the gate), its session filed under the *gate's* node id so
`_stop_live_sessions` finds it when a person approves or rejects mid-run. Its
task -- harness, model, profile -- is the one the gate would re-run on
rejection, the agent that wrote the code.

It must not change the worktree. Edit and NotebookEdit are denied; Write and
Bash cannot be, because every agent writes its result file and calls
`kraft item reply` with them. So the worktree is compared before and after,
and a change is recorded as `reply_agent_wrote` -- deliberately not a stop:
`read_only.stop` would `mark_needs_human`, which notifies, and this agent
failing is not a person's problem.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from kraft import caps, events, executor, store
from kraft import overrides as _overrides
from kraft.adapters import agent as _agent
from kraft.executor import read_only
from kraft.templates.models import AgentTask
from kraft.vocab import GateEvent
from kraft.worker import callback as _callback
from kraft.worker import steering as _steering

logger = logging.getLogger(__name__)

DENIED = ("Edit", "NotebookEdit")

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
    except asyncio.CancelledError:
        raise
    except AssertionError:
        raise
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
    worktree = run_dirs.worktrees / work_item_id
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
        )
    except _steering.SteeringError:
        return "no_agent"
    before = read_only.snapshot(db, row, launch, worktree)
    await _agent.run_agent_task(
        db,
        run_dirs,
        session_id=uuid.uuid4().hex,
        work_item_id=work_item_id,
        node_id=gate,
        hook_point=f"{gate}.reply",
        command=inv.command,
        harness=inv.harness,
        model=inv.model,
        effort=inv.effort,
        deny_tools=tuple(dict.fromkeys([*inv.deny_tools, *DENIED])),
        allowed_tools=inv.allowed_tools,
        grants=inv.grants,
        permission_mode=inv.permission_mode,
        method_text=inv.method_text,
        steering_texts=inv.steering_texts,
        sandbox=sandbox,
        checkout=executor.sandbox_checkout(row, launch, worktree) if sandbox else None,
        task_instruction=_PROMPT.format(
            title=row["title"], threads=store.render_note(threads, summary)
        ),
        title=row["title"],
        repo_path=row["repo"],
        cwd=worktree,
        repo_entry=launch.repo_entry,
        time_cap=time_cap,
        harness_id=task.task.harness,
    )
    files = read_only.changed(db, row, launch, worktree, before)
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
