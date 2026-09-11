"""Escalate to Kraft-Agent (docs/superpowers/specs/2026-09-10-escalate-to-kraft-agent-design.md).

One escalation turn dispatches through the same `_subprocess.run_task`
engine every chain node uses, `--resume`d once a thread exists. The
dispatch is deliberately not a Kraft worker (`identify_as_worker=False`):
`client.resolve_context()` then resolves it the same as a human's own
session standing in the worktree, which is what lets it call
`kraft item retry` on the very item it is escalating without any change to
`client.context._forbid_self_action`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from kraft import events, store
from kraft.adapters import agent as _agent
from kraft.executor import LaunchContext

#: Prepended to every turn's prompt, regenerated fresh each call rather than
#: diffed against a prior turn -- the live state is always correct to hand
#: over, which is the simplest way to guarantee an agent resuming a thread
#: from an earlier episode is never told something stale.
_STATE = (
    "This is an escalation: a human is asking you to help resolve a Kraft "
    "work item that is stopped and waiting on a person. The state below is "
    "current as of right now -- if it differs from what you remember from an "
    "earlier turn on this same item, trust this over your memory.\n"
    "Status: needs_human\n"
    "Current node: {node_id}\n"
    "Why it is stopped: {reason}\n"
    "{description_line}"
    "\n"
    "You are not a Kraft worker session for this launch: if you resolve the "
    "problem, you may run `kraft item retry` yourself to resume the chain. "
    "If you are not confident it is fixed, say so and stop instead -- a "
    "human decides from there.\n"
    "\n"
    "The human's message:\n{message}"
)


def _description_line(row) -> str:
    return f"Description: {row['description']}\n" if row["description"] else ""


def _reason(db, work_item_id: str) -> str:
    """The most recent `work_item_needs_human` event's reason -- the live
    answer to "why is this stopped", same reverse-scan idiom
    `kraft.executor.dispatch.last_measurement`/`needs_context_question`
    already use."""
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] == "work_item_needs_human":
            return e["payload"].get("reason") or "(no reason recorded)"
    return "(no reason recorded)"


def _extract_cli_session_id(log_path: Path) -> str | None:
    """The `claude` CLI's own session id, off the `system`/`init` line every
    `--output-format stream-json` run starts with -- the identity `--resume`
    takes, distinct from Kraft's own `worker_sessions.id`.

    Scanned across every line rather than assumed to be the first, the same
    defensive shape `adapters.subprocess._rate_limit_rejection` uses reading
    this same log: a line Kraft cannot parse yet must not crash a session
    that otherwise ran fine.
    """
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "system" and obj.get("subtype") == "init":
            sid = obj.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
    return None


async def dispatch(
    db,
    run_dirs,
    *,
    work_item_id: str,
    message: str,
    launch: LaunchContext,
) -> str:
    """One escalation turn: send `message` into `work_item_id`'s escalation
    thread, resuming it if `work_items.escalation_session_id` is already set.

    Assumes its caller already checked the item is `needs_human` and that no
    escalation turn is currently running for it -- kraft.api.routes.lifecycle's job, the same
    separation `executor.run`/`resume` keep from their own preconditions.
    """
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")

    session_id = uuid.uuid4().hex
    worktree = run_dirs.worktrees / work_item_id

    task_instruction = _STATE.format(
        node_id=row["current_node_id"],
        reason=_reason(db, work_item_id),
        description_line=_description_line(row),
        message=message,
    )

    # A minimal binding, no hook: `resolve_invocation` still folds in repo-level
    # deny_tools/steering/default_model, which is all "full tools" means here --
    # repo policy still applies, only a hook's own narrowing is absent because
    # there is no hook. Its own `escalate:` kwarg (left at the `False` default)
    # is the fix loop's unrelated "buy a stronger model" bump -- same word,
    # different feature; not to be confused with this module.
    inv = _agent.resolve_invocation(
        {"command": "claude"}, launch.repo_entry, launch.steering_dir, skills_dir=launch.skills_dir
    )
    status = await _agent.run_agent_task(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=row["current_node_id"],
        hook_point="escalation",
        command=inv.command,
        profile=inv.profile,
        model=inv.model,
        deny_tools=inv.deny_tools,
        steering_texts=inv.steering_texts,
        task_instruction=task_instruction,
        title=row["title"],
        repo_path=row["repo"],
        cwd=worktree,
        resume_session_id=row["escalation_session_id"],
        autocompact="auto",
        identify_as_worker=False,
    )

    cli_session_id = _extract_cli_session_id(run_dirs.logs / f"{session_id}.log")
    if cli_session_id:
        await db.write(lambda c: store.set_escalation_session(c, work_item_id, cli_session_id))
    return status
