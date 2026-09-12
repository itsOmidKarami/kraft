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
_MANUAL_OPENING = (
    "This is an escalation: a human is asking you to help resolve a Kraft "
    "work item that is stopped and waiting on a person. The state below is "
    "current as of right now -- if it differs from what you remember from an "
    "earlier turn on this same item, trust this over your memory.\n"
)
_AUTO_OPENING = (
    "This is an automatic escalation: Kraft itself is asking you to help "
    "resolve a work item that is stopped and waiting on a person, because "
    "nobody has acted on it yet. The state below is current as of right now "
    "-- if it differs from what you remember from an earlier turn on this "
    "same item, trust this over your memory.\n"
)
_STATE = (
    "{opening}"
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


def _reason(db, work_item_id: str, evts: list | None = None) -> str:
    """The most recent `work_item_needs_human` event's reason -- the live
    answer to "why is this stopped", same reverse-scan idiom
    `kraft.executor.dispatch.last_measurement`/`needs_context_question`
    already use. `evts`, when given, is a timeline the caller already
    fetched (`gates.auto_escalate_stuck`'s single read, threaded through
    `dispatch`) -- read fresh only when nothing was handed in, which is
    every call from the manual `/escalate` route today."""
    evts = evts if evts is not None else db.read(lambda c: events.read_after(c, 0, work_item_id))
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


def escalation_running(db, work_item_id: str) -> str | None:
    """The id of a `pending`/`running` escalation session for
    `work_item_id`, or None.

    Moved out of `kraft.api.routes.lifecycle` (Kraft-lpdd) so
    `kraft.executor.gates.auto_escalate_stuck` can run the same check
    `retry`, `resume`, `escalate`, and `skip` already do before
    dispatching a fresh agent into a worktree an escalation turn may
    already be live in.

    `retry` (lifecycle.py) also uses this to tell a caller apart from an
    *unrelated* second escalation turn: it compares the id this returns
    against the caller's own `X-Kraft-Session-Id` rather than excluding
    that session from the search here -- a genuine self-retry still has to
    be recognised as "an escalation turn is live", just handled differently
    (deferred, not refused) than a stranger's call landing mid-turn.
    """
    row = db.read(
        lambda c: c.execute(
            "SELECT id FROM worker_sessions WHERE work_item_id = ? AND hook_point = 'escalation' "
            "AND status IN ('pending', 'running') LIMIT 1",
            (work_item_id,),
        ).fetchone()
    )
    return row["id"] if row else None


async def dispatch(
    db,
    run_dirs,
    *,
    work_item_id: str,
    message: str,
    launch: LaunchContext,
    auto: bool = False,
    #: A timeline the caller already fetched, reused instead of a fresh
    #: `events.read_after` here. `None` (the manual `/escalate` route's
    #: only call site) falls back to reading it fresh -- that path calls
    #: this once per human action, not once per `run()`/`resume()`, so the
    #: extra read there costs nothing worth threading a parameter through
    #: `deps.spawn` for.
    evts: list | None = None,
) -> str:
    """One escalation turn: send `message` into `work_item_id`'s escalation
    thread, resuming it if `work_items.escalation_session_id` is already set.

    Assumes its caller already checked the item is `needs_human` and that no
    escalation turn is currently running for it (`escalation_running`) --
    `kraft.api.routes.lifecycle`'s manual `/escalate` route and
    `kraft.executor.gates.auto_escalate_stuck`'s unattended trigger both do,
    the same separation `executor.run`/`resume` keep from their own
    preconditions.
    """
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")

    session_id = uuid.uuid4().hex
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            "escalation_message",
            {"session_id": session_id, "message": message, "auto": auto},
        )
    )
    worktree = run_dirs.worktrees / work_item_id

    task_instruction = _STATE.format(
        opening=_AUTO_OPENING if auto else _MANUAL_OPENING,
        node_id=row["current_node_id"],
        reason=_reason(db, work_item_id, evts=evts),
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
