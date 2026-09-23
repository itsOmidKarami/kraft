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

import shutil
import uuid

from kraft import caps, events, executor, store
from kraft import harness as _harness
from kraft import policy as _policy
from kraft import usage as _usage
from kraft.adapters import agent as _agent
from kraft.adapters.subprocess import result_path_for
from kraft.executor import LaunchContext, stops
from kraft.templates.models import AgentTask, GateNode, TaskKind
from kraft.worker import steering as _steering

_SESSIONS = ".engineering/sessions"

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
#: `{action_line}` for a `needs_human` item: `retry_work_item` accepts a
#: self-retry from exactly this session (lifecycle.py's
#: `work_item_self_retry_requested` deferral), so telling the agent it may
#: run `kraft item retry` itself is true for this status only.
_NEEDS_HUMAN_ACTION = (
    "You are not a Kraft worker session for this launch: if you resolve the "
    "problem, you may run `kraft item retry` yourself to resume the chain. "
    "If you are not confident it is fixed, say so and stop instead -- a "
    "human decides from there.\n"
)
#: `{action_line}` for a `paused` item (Kraft-k5ol widened `/escalate` to
#: accept one). Neither self-action route works here: `retry_work_item` 409s
#: on anything but `needs_human`, and `resume_work_item` 409s while *any*
#: escalation session is running, including this one -- unlike retry, it has
#: no self-caller carve-out. Telling the agent to run `kraft item retry` (the
#: needs_human wording) would send it at a route that refuses it; this
#: version asks it to report back instead of pretending it can act.
_PAUSED_ACTION = (
    "This item is paused, not stopped on an error -- a human parked it here "
    "deliberately, and resuming it is that human's call, not yours. You are "
    "not a Kraft worker session for this launch, but running `kraft item "
    "resume` or `kraft item retry` yourself will fail here (both refuse a "
    "paused item mid-escalation). Investigate and report what you find; say "
    "clearly whether you think it's ready to resume, and let a human run "
    "`kraft item resume` themselves.\n"
)
#: After `{action_line}` on every turn, manual or automatic, needs_human or
#: paused (Ruling 209, Kraft-s7c04.67): nothing pre-approves these verbs for
#: an escalation agent, so a person's own classifier would refuse the call,
#: and the turn hands the person the one command instead of trying.
_HANDS_OFF = (
    "You are not allowed to skip or abandon this work item yourself, for "
    "safety. If a person asks you to skip it, or you conclude skipping is "
    "right, say plainly that you may not take that action yourself, and give "
    "them the one command that does: `kraft item skip {work_item_id}` (or the "
    "Skip action on the board). Abandoning is theirs as well: `kraft item "
    "abandon --yes {work_item_id}`.\n"
)
_STATE = (
    "{opening}"
    "Status: {status}\n"
    "Current node: {node_id}\n"
    "{reason_line}"
    "{judge_line}"
    "{description_line}"
    "\n"
    "{action_line}"
    "{hands_off}"
    "\n"
    "The human's message:\n{message}"
)


def _description_line(row) -> str:
    return f"Description: {row['description']}\n" if row["description"] else ""


def _last_stop(db, work_item_id: str, evts: list | None = None) -> dict:
    """The most recent `work_item_needs_human` event's payload, or `{}` -- the
    live answer to "why is this stopped", same reverse-scan idiom
    `kraft.executor.dispatch.last_measurement`/`needs_context_question`
    already use. `evts`, when given, is a timeline the caller already
    fetched (`gates.auto_escalate_stuck`'s single read, threaded through
    `dispatch`) -- read fresh only when nothing was handed in, which is
    every call from the manual `/escalate` route today."""
    evts = evts if evts is not None else db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] == "work_item_needs_human":
            return e["payload"]
    return {}


def _reason_line(stop: dict, status: str) -> str:
    """The `{reason_line}` `_STATE` slot, with the stop's `suggested_action`
    when it has one (Kraft-s7c04.27), so a turn starts from what the chain
    already concluded instead of rediscovering it. `needs_human` only -- a
    `paused` item's most recent `work_item_needs_human` reason (if it has one
    at all) belongs to whatever it stopped for *before* being paused, not to
    why it is paused now, so reporting it here would be stale or actively
    misleading (Kraft-k5ol code-review finding)."""
    if status != "needs_human":
        return ""
    line = f"Why it is stopped: {stop.get('reason') or '(no reason recorded)'}\n"
    if suggested := stop.get("suggested_action"):
        line += (
            f"What Kraft suggests a person do: {suggested['action']} -- {suggested['reason']} "
            f"(`kraft item {suggested['action']}`)\n"
        )
    return line


#: Events that close an episode, so a judge verdict before one of them describes
#: a trend that is over. Modelled on `kraft.store.gates`' `_REJECTION_SPENT` --
#: deliberately NOT on `_reason` above, which has no boundary at all.
#: `node_started` stays out because `dispatch.measure_node` fires one per
#: measurement round.
_JUDGE_SPENT = ("work_item_retried", "gate_approved", "gate_rejected")


def _judge_line(db, work_item_id: str, node_id: str | None, evts: list | None = None) -> str:
    """The `{judge_line}` slot: the fix-loop judge's own read of why this node's
    rounds were not converging, or "" (Kraft-s7c04.5).

    The judge is asked to explain every verdict, its reasoning is already paid
    for, and nothing downstream read it -- so on 43717ee6 the escalation
    re-derived the root cause the judge had already named, 17 minutes and $3.72
    later.

    Bounded on two axes. `_JUDGE_SPENT`, because a verdict from a closed episode
    is stale rather than context; and on `node_id`, because `judge_verdict`
    payloads name their node and an earlier node's fix-loop trend says nothing
    about the node this escalation is actually about.
    """
    if node_id is None:
        return ""
    evts = evts if evts is not None else db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in _JUDGE_SPENT:
            return ""
        if e["type"] == "judge_verdict" and e["payload"].get("node_id") == node_id:
            reasoning = (e["payload"].get("reasoning") or "").strip()
            if not reasoning:
                return ""
            return f"What the fix-loop judge made of it: {reasoning}\n"
    return ""


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


def last_escalation_status(db, work_item_id: str) -> str | None:
    """The status of `work_item_id`'s most recently *created* escalation
    session, finished or still running -- unlike `escalation_running`, which
    only ever returns an id for a `pending`/`running` one. Lets
    `gates.auto_escalate_stuck` tell "the last turn asked a question and is
    still waiting on a human to answer it" (status `needs_context`) from "the
    last turn finished some other way and nobody has looked since"
    (Kraft-b52cm) -- the existing `needs_context:` reason guard only catches
    a *chain node's* needs_context stop, not the escalation session's own.
    """
    row = db.read(
        lambda c: c.execute(
            "SELECT status FROM worker_sessions WHERE work_item_id = ? "
            "AND hook_point = 'escalation' ORDER BY created_at DESC LIMIT 1",
            (work_item_id,),
        ).fetchone()
    )
    return row["status"] if row else None


def session_status(db, session_id: str) -> str | None:
    """The status of a single `worker_sessions` row by its own id, or None if
    no such session exists. `gates._current_run_escalation_session_id`'s
    counterpart: that picks out *which* session id belongs to the current
    run of `needs_human` stuckness (scoped by `_RUN_BOUNDARY`), this reads
    that specific session's status rather than `last_escalation_status`'s
    unscoped "newest ever" (Kraft-b52cm)."""
    row = db.read(
        lambda c: c.execute(
            "SELECT status FROM worker_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    )
    return row["status"] if row else None


#: The escalation role, as an ordinary agent task
#: (`agent-roles-use-ordinary-agent-task-runtime-configuration`): its launch
#: resolves `harness:` through `adapters.agent.harness_profile` like every other
#: V1 agent launch, so the runtime is a `harnesses.yaml` profile -- its
#: executable and defaults -- and no field special to escalation exists.
#: Which profile is policy's `escalation_harness` (Kraft-wge0e), `claude`
#: unless a layer says otherwise; this constant's is only the default's. The
#: turn is a resumable conversation, so the thread id is read off the log with
#: the provider's own reader (`adapters.agent.log_reader`). A disabled or
#: missing profile stops the turn as a config error rather than substituting
#: another (`unavailable-selected-harness-needs-human`).
ESCALATION_TASK = AgentTask(
    id="escalation",
    kind=TaskKind.AGENT,
    harness=_policy.DEFAULT_ESCALATION_HARNESS,
    prompt="Help resolve a stopped work item.",
)


def item_harness(db, row) -> str | None:
    """The `harnesses.yaml` profile `row`'s own work last ran on, for an
    `escalation_harness: item` (Kraft-wge0e): the latest session of an agent
    task of an execution node -- a gate's reviewer and an escalation turn are
    not the item's work -- on the profile that task declares, or the one a
    fallback switched that session to (`launch_fallback`). None when no agent
    task of the item has run."""
    snapshot = store.materialized_chain_of(row)
    if snapshot is None:
        return None
    declared = {
        t.path: t.task.harness
        for n in snapshot.chain.nodes
        if not isinstance(n.node, GateNode)
        for t in n.tasks()
        if isinstance(t.task, AgentTask)
    }
    wid = row["id"]
    ran = db.read(
        lambda c: c.execute(
            "SELECT id, hook_point FROM worker_sessions WHERE work_item_id = ? ORDER BY rowid DESC",
            (wid,),
        ).fetchall()
    )
    last = next((r for r in ran if r["hook_point"] in declared), None)
    if last is None:
        return None
    switched = [
        e["payload"]["to"]["harness"]
        for e in db.read(lambda c: events.read_after(c, 0, wid))
        if e["type"] == "launch_fallback"
        and e["payload"].get("session_id") == last["id"]
        and e["payload"].get("to")
    ]
    return switched[-1] if switched else declared[last["hook_point"]]


async def _refused(db, run_dirs, *, session_id: str, row, log: str) -> str:
    """An escalation turn that could not launch, recorded as its own session so
    the stop names why (the same shape `dispatch.config_error_session` gives a chain
    task)."""
    from kraft import builtins as _builtins
    from kraft.executor.context import CONFIG_ERROR

    _, log_path, result_path = await _builtins.start_session(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=row["id"],
        node_id=row["current_node_id"],
        hook_point="escalation",
        round=0,
    )
    return await _builtins.finish_session(
        db, log_path, result_path, session_id=session_id, status=CONFIG_ERROR, log=log
    )


#: What an escalation thread's runtime is: fixed for the life of a thread.
_RUNTIME = ("command", "harness", "model", "effort", "permission_mode")


async def _record_message(db, work_item_id, session_id, message, auto, thread, turn, runtime):
    """The turn's `escalation_message`, before it launches -- a refused launch
    included, so every turn counts toward the auto-escalation bound."""
    payload = {
        "session_id": session_id,
        "message": message,
        "auto": auto,
        "thread": thread,
        "turn": turn,
    }
    if runtime is not None:
        payload["runtime"] = runtime
    await db.write(lambda c: events.append(c, work_item_id, "escalation_message", payload))


def _thread_runtime(db, work_item_id: str, thread: int) -> dict | None:
    """The runtime escalation `thread` started on, off its first turn's
    `escalation_message`; None for a thread that predates the record."""
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in evts:
        if e["type"] == "escalation_message" and e["payload"].get("thread") == thread:
            runtime = e["payload"].get("runtime")
            return {k: runtime[k] for k in _RUNTIME if k in runtime} if runtime else None
    return None


async def dispatch(
    db,
    run_dirs,
    *,
    work_item_id: str,
    message: str,
    launch: LaunchContext,
    auto: bool = False,
    #: Start a fresh agent session instead of continuing the item's latest
    #: escalation thread (ESCALATION_THREADS_SPEC.md §1). No-op on an item's
    #: very first escalation -- there is no prior thread to be "new" from,
    #: and `row["escalation_session_id"]` is already `None` either way.
    new_thread: bool = False,
    #: A timeline the caller already fetched, reused instead of a fresh
    #: `events.read_after` here. `None` (the manual `/escalate` route's
    #: only call site) falls back to reading it fresh -- that path calls
    #: this once per human action, not once per `run()`/`resume()`, so the
    #: extra read there costs nothing worth threading a parameter through
    #: `deps.spawn` for.
    evts: list | None = None,
) -> str:
    """One escalation turn: send `message` into `work_item_id`'s escalation
    thread, resuming it if `work_items.escalation_session_id` is already set
    (unless `new_thread` starts a fresh one instead).

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

    latest_thread = db.read(lambda c: store.latest_escalation_thread(c, work_item_id))
    thread = latest_thread + 1 if new_thread else (latest_thread or 1)
    resume_session_id = None if new_thread else row["escalation_session_id"]
    turn = db.read(lambda c: store.escalation_thread_turn_count(c, work_item_id, thread)) + 1

    session_id = uuid.uuid4().hex
    worktree = run_dirs.worktrees / work_item_id

    task_instruction = _STATE.format(
        opening=_AUTO_OPENING if auto else _MANUAL_OPENING,
        status=row["status"],
        node_id=row["current_node_id"],
        reason_line=_reason_line(_last_stop(db, work_item_id, evts), row["status"]),
        judge_line=_judge_line(db, work_item_id, row["current_node_id"], evts=evts),
        description_line=_description_line(row),
        action_line=_NEEDS_HUMAN_ACTION if row["status"] == "needs_human" else _PAUSED_ACTION,
        hands_off=_HANDS_OFF.format(work_item_id=work_item_id),
        message=message,
    )

    # Node-scoped (Kraft-l8ype, `stuck-escalation-is-an-exec-node-control`):
    # the turn runs under the policy of the node the item stopped at, resolved
    # from its snapshot like every other agent launch -- instance, repository,
    # work item, chain and node. Its tool lists, frozen sandbox, harness
    # allowlist and token budget all bound it; no snapshot, or no such node in
    # it, is no policy anyone could know, and refused rather than run unbounded.
    policy = _node_policy(row)
    breach = (
        stops.budget_breach(
            db, work_item_id, _policy.NO_BUDGET, row=row, path=row["current_node_id"]
        )
        if policy is not None
        else None
    )
    if policy is None or breach is not None:
        await _record_message(db, work_item_id, session_id, message, auto, thread, turn, None)
        return await _refused(
            db,
            run_dirs,
            session_id=session_id,
            row=row,
            log=(
                stops.budget_reason(breach)
                if breach is not None
                else f"escalation has no policy to run under: work item {work_item_id} has "
                f"no node {row['current_node_id']!r} in a materialized chain"
            )
            + "\n",
        )

    # Which profile the turn runs on is policy too (Kraft-wge0e): the node's
    # `escalation_harness`, or the item's own harness for `item`.
    harness_id = policy.escalation_harness
    if harness_id == _policy.FOLLOW_ITEM:
        harness_id = item_harness(db, row)
        if harness_id is None:
            await _record_message(db, work_item_id, session_id, message, auto, thread, turn, None)
            return await _refused(
                db,
                run_dirs,
                session_id=session_id,
                row=row,
                log=f"escalation_harness is {_policy.FOLLOW_ITEM!r}, and no agent task of "
                f"work item {work_item_id} has run for it to follow\n",
            )

    # Its own `escalate:` kwarg (left at the `False` default) is the fix loop's
    # unrelated "buy a stronger model" bump -- same word, different feature;
    # not to be confused with this module.
    #
    # `harness:`, **not** `command: "claude"`. Same shape Task 4b removed from
    # `gate_review.py`, and removed here for the same reason: a hardcoded
    # `command` bypasses the harness declaration entirely, so an operator who
    # overlays `~/.kraft/templates/harnesses/claude.yaml` is ignored and a test
    # fixture cannot substitute a fake. `run_agent_task` falls through to the
    # harness's own declared command when `command` is empty
    # (`adapters/agent.py`'s `command=command or None`), which is what every
    # other V1 agent launch already does.
    try:
        # The item's sandbox, as for every launch of it (Ruling 189).
        sandbox = executor.item_sandbox(row, launch)
    except executor.SandboxUnresolved as exc:
        await _record_message(db, work_item_id, session_id, message, auto, thread, turn, None)
        return await _refused(db, run_dirs, session_id=session_id, row=row, log=f"{exc}\n")
    try:
        inv = _agent.resolve_agent_task(
            ESCALATION_TASK.model_copy(update={"harness": harness_id}),
            launch.repo_entry,
            launch.library_steering,
            skills_dir=launch.skills_dir,
            # The repository's steering, frozen with the item at intake.
            **executor.frozen_steering(row),
            # The item's own repo as recorded at intake (Kraft-jzdyp): an
            # escalation turn runs in the item's own checkout.
            item_repo=row["repo"],
            policy=policy,
        )
    except _agent.HarnessUnavailable as exc:
        await _record_message(db, work_item_id, session_id, message, auto, thread, turn, None)
        return await _refused(
            db,
            run_dirs,
            session_id=session_id,
            row=row,
            log=f"escalation selects harness {harness_id!r}, which is not available: {exc}\n",
        )
    except _steering.SteeringError as exc:
        # A snapshot stored before repository steering was frozen selecting a
        # profile the live library no longer defines (`for_repository`'s
        # pre-freeze branch) stops this turn the same clean way a harness
        # problem does, rather than the walk's generic guard catching it.
        await _record_message(db, work_item_id, session_id, message, auto, thread, turn, None)
        return await _refused(db, run_dirs, session_id=session_id, row=row, log=f"{exc}\n")
    # A turn that resumes a thread runs on the runtime the thread started on
    # (`resumed-escalation-preserves-original-runtime`): the profile may have
    # changed since, and a resumed conversation on another model or harness is
    # not the session it claims to be. Only a new thread takes the current
    # profile.
    original = _thread_runtime(db, work_item_id, thread) if resume_session_id else None
    if original is not None:
        inv = inv._replace(**original)
    runtime = {k: getattr(inv, k) for k in _RUNTIME}
    # An automatic turn is its node running, so the node's time cap bounds it
    # (Kraft-8en38). A person's turn is a person talking: no cap.
    time_cap = None
    if auto:
        hit = db.read(lambda c: caps.for_turn(c, row, row["current_node_id"]))
        if hit is not None and hit.remaining_s <= 0:
            await _record_message(db, work_item_id, session_id, message, auto, thread, turn, None)
            return await _refused(
                db, run_dirs, session_id=session_id, row=row, log=f"not started: {hit.reason}\n"
            )
        time_cap = caps.Deadline(caps.monotonic() + hit.remaining_s, hit) if hit else None
    await _record_message(db, work_item_id, session_id, message, auto, thread, turn, runtime)
    files = thread_files(work_item_id, thread)
    await _archive_last_turn(db, run_dirs, worktree, files)
    try:
        status = await _agent.run_agent_task(
            db,
            run_dirs,
            session_id=session_id,
            work_item_id=work_item_id,
            node_id=row["current_node_id"],
            hook_point="escalation",
            command=inv.command,
            harness=inv.harness,
            model=inv.model,
            effort=inv.effort,
            permission_mode=inv.permission_mode,
            deny_tools=inv.deny_tools,
            allowed_tools=inv.allowed_tools,
            steering_texts=inv.steering_texts,
            sandbox=sandbox,
            task_instruction=task_instruction,
            title=row["title"],
            repo_path=row["repo"],
            cwd=worktree,
            resume_session_id=resume_session_id,
            autocompact="auto",
            identify_as_worker=False,
            harness_id=harness_id,
            repo_entry=launch.repo_entry,
            thread=thread,
            files=files,
            time_cap=time_cap,
        )
    except _agent.LaunchRefused as exc:
        return await _refused(db, run_dirs, session_id=session_id, row=row, log=f"{exc}\n")

    # The thread's own harness's reader: `inv.harness` is the provider the turn
    # ran on, the thread's original one when it resumed.
    provider = _harness.load(None).valid.get(inv.harness)
    reader = _agent.log_reader(provider) if provider is not None else None
    cli_session_id = (
        _usage.READERS[reader].session_id(run_dirs.logs / f"{session_id}.log") if reader else None
    )
    if cli_session_id:
        await db.write(lambda c: store.set_escalation_session(c, work_item_id, cli_session_id))
    return status


def thread_files(work_item_id: str, thread: int) -> str:
    """The name every turn of one escalation thread writes its result file
    and session summary under (Kraft-s7c04.54,
    `a-resumed-escalation-turn-writes-where-it-remembers`).

    Per thread, not per turn: a `--resume`d conversation remembers the
    literal paths an earlier turn wrote to, and a model that trusts that
    memory over the fresh instruction wrote to the earlier turn's file
    (b5afe84c), which `require_result_file` then recorded as `failed`. With
    one name per thread, the path it remembers is this turn's path.
    """
    return f"escalation-{work_item_id}-{thread}"


async def _archive_last_turn(db, run_dirs, worktree, files: str) -> None:
    """Give the thread's last turn its own names back before the next turn
    writes, so this turn starts with no result file to be mistaken for its
    own and the last turn's stays readable from its row.

    The result family (`.json` and its `.exit`/`.cid` sidecars) moves to the
    last turn's session id and its row follows. The summary is copied, not
    moved: it sits in the worktree, where a repo that does not ignore
    `.engineering/` may track it.
    """
    shared = result_path_for(run_dirs, files)
    summary = f"{_SESSIONS}/{files}.md"
    last = db.read(
        lambda c: c.execute(
            "SELECT id, session_summary_ref FROM worker_sessions WHERE result_path = ?",
            (str(shared),),
        ).fetchone()
    )
    if last is not None:
        own = result_path_for(run_dirs, last["id"])
        for suffix in (".json", ".exit", ".cid"):
            if shared.with_suffix(suffix).exists():
                shared.with_suffix(suffix).replace(own.with_suffix(suffix))
        ref = last["session_summary_ref"]
        if ref == summary and (worktree / summary).exists():
            ref = f"{_SESSIONS}/{last['id']}.md"
            shutil.copyfile(worktree / summary, worktree / ref)
        await db.write(
            lambda c: c.execute(
                "UPDATE worker_sessions SET result_path = ?, session_summary_ref = ? WHERE id = ?",
                (str(own), ref, last["id"]),
            )
        )
    # A turn that never got a row (refused before launch) owns nothing here.
    for suffix in (".json", ".exit", ".cid"):
        shared.with_suffix(suffix).unlink(missing_ok=True)


def _node_policy(row) -> _policy.InstancePolicy | None:
    """The policy of the node `row` stopped at, from its snapshot; None when
    there is no snapshot or no such node in it."""
    snapshot = store.materialized_chain_of(row)
    nodes = snapshot.chain.nodes if snapshot is not None else ()
    node = next((n for n in nodes if n.id == row["current_node_id"]), None)
    return executor.scope_policy(row, node) if node is not None else None
