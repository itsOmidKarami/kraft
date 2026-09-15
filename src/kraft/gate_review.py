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

from kraft import events, executor
from kraft.adapters import agent as _agent
from kraft.adapters import subprocess as _subprocess

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
    "node re-runs and is measured again. `concerns` says what you changed.\n"
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


async def review(
    db,
    run_dirs,
    *,
    work_item_id: str,
    gate: str,
    node: dict,
    registry,
    launch: executor.LaunchContext,
) -> tuple[str, str]:
    """Dispatch one gate review. Returns `(verdict, note)`.

    Never raises for an agent that misbehaved: every degraded outcome is
    `("undecided", note)`, which the caller turns into "leave the gate pending".
    """
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")

    session_id = uuid.uuid4().hex
    rel = executor.gate_artifact(registry, run_dirs, row, gate)
    task_instruction = _PROMPT.format(
        gate=gate,
        node_id=node["id"],
        title=row["title"],
        description_line=f"Brief: {row['description']}\n" if row["description"] else "",
        artifact_line=_artifact_line(rel),
    )

    await db.write(
        lambda c: events.append(
            c, work_item_id, "gate_auto_review_started", {"gate": gate, "session_id": session_id}
        )
    )

    inv = _agent.resolve_invocation(
        {"command": "claude", "skill": "gate-review"},
        launch.repo_entry,
        launch.steering_dir,
        skills_dir=launch.skills_dir,
        # Item-wide overrides only -- an item marked cheap stays cheap for its
        # gate reviews too (Kraft-ui79: no per-gate floor). Deliberately *not*
        # the per-node `node_overrides` model/effort dial that
        # `dispatch.dispatch_node` also merges (Kraft-df4tc): a node dialed to
        # a different model does not carry that dial into its own gate review.
        item_override=json.loads(row["agent_overrides"]) if row["agent_overrides"] else None,
    )
    status = await _agent.run_agent_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node["id"],
        hook_point="gate_review",
        command=inv.command,
        profile=inv.profile,
        model=inv.model,
        effort=inv.effort,
        deny_tools=inv.deny_tools,
        steering_texts=inv.steering_texts,
        sandbox=inv.sandbox,
        task_instruction=task_instruction,
        title=row["title"],
        repo_path=row["repo"],
        cwd=run_dirs.worktrees / work_item_id,
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
