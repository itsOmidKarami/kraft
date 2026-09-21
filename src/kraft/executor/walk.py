from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path

from kraft import builtins as _builtins
from kraft import config as _config
from kraft import events, store
from kraft import findings as _findings
from kraft import policy as _policy
from kraft.adapters import beads
from kraft.adapters import subprocess as _subprocess
from kraft.executor import dispatch, entry, gates, prompts, stops
from kraft.executor.context import (
    _ADVANCING,
    BASE_MOVED,
    BUDGET,
    CONFIG_ERROR,
    INFRA_STOP,
    RATE_LIMITED,
    WAITING,
    LaunchContext,
    OnApprove,
    Steer,
)
from kraft.store import _now as _now
from kraft.templates import Registry
from kraft.templates.models import (
    BuiltinTask,
    ExecNode,
    ForgeTask,
    MaterializedChain,
    ResolvedNode,
    ResolvedTask,
)


def chain_of(row) -> MaterializedChain:
    """The work item's frozen V1 chain, or a clear failure.

    `materialized_chain` is the executor's only input
    (`materialized-chain-is-immutable-work-item-input`). A row without one was
    written by the legacy intake path, which Task 5 retires: there is nothing
    to walk and nothing to fall back to, so this says so rather than walking
    something else.
    """
    chain = store.materialized_chain_of(row)
    if chain is None:
        raise LookupError(
            f"work item {row['id']!r} has no materialized chain: it was filed by the legacy "
            "intake path and cannot be executed"
        )
    return chain


#: Task kinds whose work runs inside the orchestrator process, so no commit a
#: fix agent makes in its worktree can change what executes (Kraft-s7c04.24).
#: A subprocess task is deliberately absent: it runs the worktree's own
#: command, which is exactly what a fix commit changes. An agent task is the
#: worker itself.
_IN_PROCESS_KINDS = (BuiltinTask, ForgeTask)


def _in_process(task: ResolvedTask) -> bool:
    return isinstance(task.task, _IN_PROCESS_KINDS)


async def _diagnosis_bundle(db, work_item_id: str, node: ResolvedNode, worktree) -> dict:
    """What a human reconstructs by hand today, gathered once at the moment
    the stuck detector gives up (Kraft-39ep): the worktree's own state, and
    the last measuring session's concerns, if it left any.

    Best-effort throughout -- a bundle missing a piece it could not read is
    still more than the plain reason string this replaces, and nothing here
    may itself fail the stop it is decorating.
    """
    from pathlib import Path

    from kraft.config import git_read

    status = git_read(Path(worktree), "status", "--porcelain", expected_failure=True) or ""
    recent = git_read(Path(worktree), "log", "--oneline", "-5", expected_failure=True) or ""
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    # worker_session_exited carries no hook_point of its own, so a judge verdict
    # (the node's own `fix_loop.judge`, told by JUDGE_PROMPT/SKILL.md that concerns is required on
    # every verdict) and the measuring session it judged both write concerns
    # events, and the judge's always lands last in the same walk_node
    # iteration. Joined against worker_sessions here to skip the judge's own
    # sessions, so this stays the measuring session's concerns, not the
    # judge's reasoning about them.
    judge_session_ids = (
        db.read(
            lambda c: {
                r["id"]
                for r in c.execute(
                    "SELECT id FROM worker_sessions WHERE work_item_id = ? AND hook_point = ?",
                    (work_item_id, node.judge.path),
                ).fetchall()
            }
        )
        if node.judge is not None
        else set()
    )
    concerns = next(
        (
            e["payload"].get("concerns")
            for e in reversed(evts)
            if e["type"] == "worker_session_exited"
            and e["payload"].get("concerns")
            and e["payload"].get("session_id") not in judge_session_ids
        ),
        None,
    )
    return {
        "git_status": status.strip(),
        "recent_log": recent.strip(),
        "last_session_concerns": concerns,
    }


#: The round `on_failure`'s pre-cycle repair (and its re-measure) writes its
#: sessions under, inside a fix_loop node. Never a value `bump_counter` can
#: produce for this node's own `key` counter (those start at 1 and only go
#: up), so it can never alias a paid fix cycle's own round -- unlike the
#: `round=1` `recover_node` used to hardcode, which is also the round the
#: measurement after the *first* paid cycle uses.
_REPAIR_ROUND = -1

#: Kraft-0i6z4: rounds a single fingerprint must survive fix attempts
#: unchanged before it alone (not the whole set) counts as stuck. Must be
#: higher than the whole-set check's effective bar of 2 (previous round ==
#: current round) -- at 2, a fingerprint that is still present while a
#: sibling finding is newly added (progress, not stuck) would false-positive.
#: No policy.yaml knob: same precedent as the fix-loop judge hook, no
#: measured need yet to make this operator-tunable.
_FINGERPRINT_STREAK_LIMIT = 3


def _carry_severity(
    found: list[_findings.Finding], previous: list[_findings.Finding], *, unchanged_tree: bool
) -> list[_findings.Finding]:
    """`found` with each repeat's severity floored at its previous rating, but
    only when no fix cycle ran between the two measurements (Kraft-s7c04.3).

    `policy.loop_severities` is a hard cliff: below it a finding is dropped from
    the loop entirely, so one inconsistent re-rating ends a loop that has not
    converged. 43717ee6 priced the same forge/run.py defect minor -> absent ->
    important across three reviews of a byte-identical tree, and the round that
    said `minor` is what let the loop believe it was converging.

    `unchanged_tree` is the gate: this round's head is the head the previous
    measurement was taken at, so nothing the reviewer is looking at has moved
    and a different answer is the reviewer disagreeing with itself. Flooring
    unconditionally would turn a genuine partial fix -- `important` reduced to
    `minor` *because the fix worked* -- into `prints == previous_prints` and
    park the item at "stuck: 1 finding(s) unchanged", reporting real progress as
    being stuck.

    Deliberately the head and not `fix_ran`: a fix cycle *starting* says nothing
    about whether it changed anything, and in a normal loop one always does, so
    `fix_ran` would disable this entirely. `dispatch_node` commits stragglers
    after every agent task (Kraft-7fip), so a fix that wrote anything has moved
    the head by the time the next measurement is dispatched.

    An upgrade is always taken as given. A severity neither side's payload
    validated (`from_payload` accepts whatever it is handed) floors nothing
    rather than raising inside the loop.
    """
    if not unchanged_tree or not previous:
        return found
    rank = {s: i for i, s in enumerate(_findings.SEVERITIES)}  # critical = 0
    unknown = len(_findings.SEVERITIES)
    was = {f.fingerprint: f.severity for f in previous}
    return [
        replace(f, severity=was[f.fingerprint])
        if f.fingerprint in was
        and rank.get(was[f.fingerprint], unknown) < rank.get(f.severity, unknown)
        else f
        for f in found
    ]


async def recover_node(
    db,
    run_dirs,
    work_item_id: str,
    node: ResolvedNode,
    row,
    worktree,
    *,
    failed: list,
    steer: Steer | None,
    launch: LaunchContext | None,
    budget: _policy.Budget,
    measured_round: int,
    round: int = 1,
    loop_severities: frozenset[str] = _policy.DEFAULT_LOOP_SEVERITIES,
    spent: set[str] | None = None,
) -> tuple[str, list, list[BaseException]]:
    """One node-level repair pass over a node whose tasks failed (Kraft-rv6i).

    Runs the node's `on_failure` steps, then measures the node again from its
    first step (`node-recovery-retries-the-entire-node`), and reports that
    second measurement. The re-measure is the point: a node's contract is its
    own tasks passing, so a repair is believed only when they do -- a CI wait
    going green, not a remediator claiming it labelled something.

    Once per entry into the node, not a loop. A repair that did not take is a
    blocker Kraft does not understand, and the honest move is to stop for a
    human rather than to keep pulling the same lever.

    `round` keys the repair's dispatch and its re-measure in
    `sessions_for_round` (`dispatch.collect_findings`,
    `dispatch.needs_context_question`) -- it must not collide with any other
    round the *same node* writes sessions under. The no-fix-loop branch's call
    below needs nothing special: a node with `on_failure` and no `fix_loop`
    never runs `walk_node`'s paid-cycle loop at all, so `round=1` (the
    default) can never alias anything else for that node. The fix-loop branch
    passes its own reserved round instead, because for a node with *both*
    `fix_loop` and `on_failure` `round=1` already means "the measurement after
    the first paid fix cycle" to that same node's
    `collect_findings`/`needs_context_question` calls.
    """
    verdict, r_failed, r_excs = await dispatch.run_recovery(
        db,
        run_dirs,
        work_item_id,
        node,
        row,
        worktree,
        handler=node.on_failure,
        scope="node",
        failed=failed,
        note=prompts.failure_note(node, [t.task.id for t in failed]),
        steer=steer,
        launch=launch,
        budget=budget,
        measured_round=measured_round,
        round=round,
        loop_severities=loop_severities,
    )
    if verdict != "ok":
        return verdict, r_failed, r_excs
    return await dispatch.measure_node(
        db,
        run_dirs,
        work_item_id,
        node,
        row,
        worktree,
        round=round,
        steer=steer,
        launch=launch,
        budget=budget,
        loop_severities=loop_severities,
        spent=spent,
    )


def _node_handler_applies(node: ResolvedNode, failed: list[ResolvedTask]) -> bool:
    """Whether the node's own `on_failure` is the nearest handler for any of
    `failed` (`nearest-recovery-handler-wins`: task, then step, then node, at
    most one). A task whose own handler or whose step's handler already
    answered for it -- successfully or not -- never reaches the node's."""
    if not node.on_failure:
        return False
    step_handled = {t.path for step in node.steps if step.on_failure for t in step.tasks}
    return not failed or any(not t.on_failure and t.path not in step_handled for t in failed)


#: How much of a config_error's own log line the card carries. A human reads
#: the cause on the board; the full log is one click away for anything longer.
_STOP_CAUSE_MAX = 300


def _task_cause(
    db, work_item_id: str, node: ResolvedNode, task: ResolvedTask, status: str = CONFIG_ERROR
) -> str:
    """The first line of the newest `status` session log for `task` -- the
    task's own account of why it stopped -- or "" when there is none or it
    cannot be read. Never raises: a stop must not become less legible than the
    generic pointer to the log, and must never escape the walk."""
    row = db.read(
        lambda c: c.execute(
            "SELECT log_path FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND hook_point = ? AND status = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (work_item_id, node.id, task.path, status),
        ).fetchone()
    )
    try:
        text = Path(row["log_path"]).read_text() if row else ""
    except OSError, ValueError:  # ValueError: UnicodeDecodeError
        return ""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= _STOP_CAUSE_MAX else line[: _STOP_CAUSE_MAX - 1] + "…"


def _in_process_causes(db, work_item_id: str, node: ResolvedNode, failed: list) -> str:
    """ " — cause; cause" for the failed tasks Kraft runs itself, or "".

    A forge or builtin task's log is Kraft's own one-line account of why it
    failed, so it belongs on the card. An agent's or a subprocess's log is that
    tool's output -- the card names the task and the log button opens it."""
    causes = [
        c
        for t in failed
        if _in_process(t) and (c := _task_cause(db, work_item_id, node, t, "failed"))
    ]
    return f" — {'; '.join(causes)}" if causes else ""


async def _stop_for_config_error(
    db, work_item_id: str, node: ResolvedNode, failed: list[ResolvedTask]
) -> str:
    """A task that could not start stops for a human, and the stop names its
    cause (Task 4b: an unmapped forge target stops "for a human naming the
    target") rather than only pointing at the session log."""
    named = ", ".join(t.task.id for t in failed)
    causes = [c for t in failed if (c := _task_cause(db, work_item_id, node, t))]
    detail = "; ".join(causes) if causes else "see the session log"
    reason = f"could not start {named} in node {node.id}: {detail}"
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason))
    return "needs_human"


async def _sentinel_stop(
    db,
    work_item_id: str,
    node: ResolvedNode,
    verdict: str,
    failed: list[ResolvedTask],
    budget: _policy.Budget,
) -> str | None:
    """The stop a non-failure verdict names, or None for `ok`/`failed`.

    One ladder for every outcome in this module -- a measurement, a recovery
    handler, a fix-loop repair, a conflict handler -- so no caller can forget a
    rung and let a rate limit or a wait read as a task failure (Kraft-jdkoq,
    the sibling of the CONFIG_ERROR fix that only one caller had).
    """
    if verdict == "paused":
        return "paused"
    if verdict == CONFIG_ERROR:
        return await _stop_for_config_error(db, work_item_id, node, failed)
    if verdict == RATE_LIMITED:
        return await stops.stop_for_rate_limit(db, work_item_id, node)
    if verdict == WAITING:
        return await stops.stop_for_waiting(db, work_item_id, node)
    if verdict == INFRA_STOP:
        return await stops.stop_for_infra(db, work_item_id, node)
    if verdict == BUDGET:
        return await stops.stop_for_budget(db, work_item_id, node, budget)
    return None


@dataclass(frozen=True)
class _Stuck:
    """A stop the node's recovery and fix loop could not advance past -- the
    one kind of stop its declared `escalation` task may still answer
    (`stuck-escalation-is-an-exec-node-control`). Carried back to `walk_node`
    unrecorded, so a successful escalation retries the node without the item
    ever reading `needs_human`."""

    reason: str
    capped: dict | None = None
    bundle: dict | None = None


#: The round a stuck escalation's session is written under. Distinct from every
#: round a measurement, a repair (`_REPAIR_ROUND`) or a paid cycle uses, so its
#: session never reads as one of theirs.
_STUCK_ESCALATION_ROUND = -2


def _escalation_key(node: ResolvedNode) -> str:
    """The `retry_counters` key that bounds a node's stuck escalations. Built
    from the node id like `ci_wait:<node>`, so `store.clear_loop_counters`
    clears it on a `/retry` without being handed it."""
    return f"{node.id}.escalation"


async def _stop_stuck(db, work_item_id: str, node: ResolvedNode, stuck: _Stuck, extra="") -> str:
    reason = stuck.reason + extra
    await db.write(
        lambda c: store.mark_needs_human(
            c, work_item_id, node.id, reason, stuck.capped, bundle=stuck.bundle
        )
    )
    return "needs_human"


async def _escalate_stuck(
    db,
    run_dirs,
    work_item_id: str,
    node: ResolvedNode,
    row,
    worktree,
    stuck: _Stuck,
    *,
    policy: _policy.Policy | None,
    launch: LaunchContext | None,
    budget: _policy.Budget,
) -> str:
    """Hand a stuck node to its declared `escalation` task, or to a human.

    `"retry"` when the escalation succeeded: the caller reruns the node from
    its first step with a fresh fix-loop budget
    (`successful-stuck-escalation-retries-the-node`). A failed or questioning
    escalation leaves the item for a human, carrying the original reason
    (`failed-or-questioning-stuck-escalation-needs-human`). Bounded by
    `policy.auto_escalate_stuck_cap` per node, counted until a `/retry` or a
    base-change restart clears it.
    """
    task = node.escalation
    if task is None:
        return await _stop_stuck(db, work_item_id, node, stuck)
    cap = policy.auto_escalate_stuck_cap if policy else _policy.DEFAULT_AUTO_ESCALATE_STUCK_CAP
    key = _escalation_key(node)
    counter = db.read(lambda c: store.read_counter(c, work_item_id, key))
    used = counter["count"] if counter is not None else 0
    if used >= cap:
        return await _stop_stuck(
            db, work_item_id, node, stuck, f" (stuck escalation spent: {used} of {cap})"
        )
    await db.write(lambda c: store.bump_counter(c, work_item_id, key, _policy.Cap(cap, 10**9)))
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            "stuck_escalation_started",
            {"node_id": node.id, "task": task.path, "reason": stuck.reason, "attempt": used + 1},
        )
    )
    status = await dispatch.dispatch_node(
        db,
        run_dirs,
        task,
        node,
        row,
        worktree,
        instruction_override=prompts.stuck_escalation_instruction(task, node, stuck.reason),
        round=_STUCK_ESCALATION_ROUND,
        launch=launch,
        budget=budget,
    )
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            "stuck_escalation_finished",
            {"node_id": node.id, "task": task.path, "status": status},
        )
    )
    if status == "paused":
        return "paused"
    if status in _ADVANCING:
        # A retried node is a fresh pass: the loop that got stuck must not
        # re-breach on the attempt count that stuck it.
        await db.write(
            lambda c: store.clear_loop_counters(
                c, work_item_id, node.id, _loop_key(node) if node.fix_loop else None
            )
        )
        return "retry"
    if status == "needs_context":
        session = dispatch._latest_session(db, work_item_id, node, task)
        question = (
            _subprocess.read_question(Path(session["result_path"])) if session else None
        ) or "(no question given)"
        reason = f"needs_context: {question}"
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason))
        return "needs_human"
    cause = _task_cause(db, work_item_id, node, task, status)
    return await _stop_stuck(
        db,
        work_item_id,
        node,
        stuck,
        f" (stuck escalation {prompts.named_with_kind(task)} ended {status}"
        + (f": {cause}" if cause else "")
        + ")",
    )


def _conflicted(
    db, work_item_id: str, node: ResolvedNode, failed: list[ResolvedTask], excs
) -> tuple[list[ResolvedTask], str]:
    """The failed tasks a rebase conflict stopped, and what git said -- empty
    unless the node declares an explicit conflict handler, which is the only
    thing that may act on one (`rebase-conflict-requires-explicit-handler`)."""
    if not node.on_conflict:
        return [], ""
    raised = [e for e in excs if isinstance(e, _builtins.RebaseConflict)]
    hit = [
        t
        for t in failed
        if (s := dispatch._latest_session(db, work_item_id, node, t)) is not None
        and s["status"] == "conflict"
    ]
    if raised and not hit:
        hit = list(failed)
    detail = "; ".join(
        [str(e) for e in raised]
        + [c for t in hit if (c := _task_cause(db, work_item_id, node, t, "conflict"))]
    )
    return hit, detail or "a rebase conflict"


async def _resolve_conflict(
    db,
    run_dirs,
    work_item_id: str,
    node: ResolvedNode,
    row,
    worktree,
    conflicted: list[ResolvedTask],
    detail: str,
    *,
    steer: Steer | None,
    launch: LaunchContext | None,
    budget: _policy.Budget,
    round: int,
    loop_severities: frozenset[str],
) -> str:
    """Run the node's `on_base_changed.on_conflict` handler over a conflict.

    Believed only when the worktree now sits on origin's current tip, the
    same check the legacy resolver made: an agent reporting success without
    finishing the rebase has not resolved anything. A resolution that moved
    the base is a base change like any other, so this answers `BASE_MOVED`
    and `run_once` restarts the declared span
    (`resolved-conflict-restarts-from-base-change-target`).
    """
    old_base = dispatch._current_base_ref(db, work_item_id)
    new_base = await _builtins.upstream_head(Path(row["repo"]))
    note = prompts.rebase_resolve_note(
        worktree,
        store.branch_for(row),
        old_base or "(unknown)",
        new_base or "(unknown)",
        detail,
        entry.attachments_of(row),
    )
    verdict, failed, _excs = await dispatch.run_recovery(
        db,
        run_dirs,
        work_item_id,
        node,
        row,
        worktree,
        handler=node.on_conflict,
        scope="conflict",
        failed=conflicted,
        note=note,
        steer=steer,
        launch=launch,
        budget=budget,
        measured_round=round,
        round=round,
        loop_severities=loop_severities,
    )
    stop = await _sentinel_stop(db, work_item_id, node, verdict, failed, budget)
    if stop is not None:
        return stop
    if verdict == "ok":
        moved = new_base is not None and (
            _config.git_read(
                Path(worktree),
                "merge-base",
                "--is-ancestor",
                new_base,
                "HEAD",
                expected_failure=True,
            )
            is not None
        )
        if moved:
            await db.write(lambda c: store.set_base_ref(c, work_item_id, new_base))
            return BASE_MOVED
        reason = (
            f"the conflict handler in node {node.id} finished without rebasing onto "
            f"{new_base or 'the upstream tip'}: {detail}"
        )
    else:
        reason = f"the conflict handler in node {node.id} could not resolve it: {detail}"
    await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason))
    return "needs_human"


async def _moved_base(db, work_item_id: str, node: ResolvedNode) -> str:
    """A step moved `base_ref` and stopped the node on purpose. A node that
    declares `on_base_changed` hands the restart to `run_once`
    (`base-change-restarts-a-declared-chain-span`) without completing: its
    later steps never ran. `BASE_MOVED` only arrives for such a node (dispatch
    asks the forge for it only there), so the completion below is a defence
    for a stored chain that lost the declaration, not a path anything takes."""
    if isinstance(node.node, ExecNode) and node.node.on_base_changed is not None:
        return BASE_MOVED
    await db.write(lambda c: store.complete_node(c, work_item_id, node.id))
    return "ok"


def _loop_key(node: ResolvedNode) -> str:
    """The `retry_counters` key a node's fix loop counts under.

    The fix loop's own canonical container path, so two nodes reusing one
    library component still count separately -- which a shared, authored loop
    name never did. `policy.yaml`'s `loops:` may name the same key to override
    the cap, and a `max_attempts` on the loop itself overrides `attempts` from
    there (`template-policy-may-replace-operational-defaults`).
    """
    return f"{node.id}.fix_loop"


async def walk_node(
    db,
    run_dirs,
    work_item_id: str,
    node: ResolvedNode,
    row,
    worktree,
    *,
    policy: _policy.Policy | None = None,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    start_step: int = 0,
) -> str:
    """One entry into an execution node: measure it, recover, fix, and, when
    none of those can advance it, escalate -- a successful escalation reruns
    the node from its first step, as a fresh entry."""
    # Derived here rather than passed in: every caller already hands us the
    # policy, so no call site can forget the cap and silently lose it. Folded
    # through the item's own cap (UI v2 · 04 point 4) -- re-read fresh from
    # the DB rather than trusting the caller's `row`, which run_once/resume
    # read once at run start: a per-item cap set or lowered via PATCH mid-run
    # must be picked up here, the same way gates.review_gates re-reads before
    # its own budget check.
    fresh_row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    budget = store.effective_budget(
        fresh_row if fresh_row is not None else row, policy.budget if policy else _policy.NO_BUDGET
    )
    while True:
        result = await _walk_node_once(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            worktree,
            fresh_row=fresh_row,
            budget=budget,
            policy=policy,
            steer=steer,
            launch=launch,
            start_step=start_step,
            # A handler runs at most once per entry into the node; a retry the
            # escalation earned is a new entry.
            spent=set(),
        )
        if not isinstance(result, _Stuck):
            return result
        outcome = await _escalate_stuck(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            worktree,
            result,
            policy=policy,
            launch=launch,
            budget=budget,
        )
        if outcome != "retry":
            return outcome
        start_step = 0


async def _walk_node_once(
    db,
    run_dirs,
    work_item_id: str,
    node: ResolvedNode,
    row,
    worktree,
    *,
    fresh_row,
    budget: _policy.Budget,
    policy: _policy.Policy | None,
    steer: Steer | None,
    launch: LaunchContext | None,
    start_step: int,
    spent: set[str],
) -> str | _Stuck:
    loop = node.node.fix_loop if isinstance(node.node, ExecNode) else None
    key = _loop_key(node) if loop is not None else None
    # Which severities open a fix cycle -- and therefore, by subtraction, which
    # findings are "deferred" and owed to the human at the review gate
    # (Kraft-s7c04.4). Threaded down to `dispatch_node` rather than defaulted
    # there: a repo that sets `findings.loop_severities` in its own policy.yaml
    # would otherwise get a board card and a review brief computed from
    # different sets, which is exactly the disagreement one shared function was
    # meant to prevent. `policy` is None on a chain built without one.
    loop_severities = policy.loop_severities if policy else _policy.DEFAULT_LOOP_SEVERITIES

    if loop is None:
        verdict, failed, excs = await dispatch.measure_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            worktree,
            steer=steer,
            launch=launch,
            budget=budget,
            loop_severities=loop_severities,
            start_step=start_step,
            spent=spent,
        )
        if verdict == BASE_MOVED:
            return await _moved_base(db, work_item_id, node)
        stop = await _sentinel_stop(db, work_item_id, node, verdict, failed, budget)
        if stop is not None:
            return stop
        if verdict == "failed":
            conflicted, detail = _conflicted(db, work_item_id, node, failed, excs)
            if conflicted:
                resolved = await _resolve_conflict(
                    db,
                    run_dirs,
                    work_item_id,
                    node,
                    row,
                    worktree,
                    conflicted,
                    detail,
                    steer=steer,
                    launch=launch,
                    budget=budget,
                    round=0,
                    loop_severities=loop_severities,
                )
                if resolved == BASE_MOVED:
                    return await _moved_base(db, work_item_id, node)
                return resolved
            question = dispatch.needs_context_question(db, work_item_id, node, round=0)
            recovered = False
            repaired = bool(spent)
            if question is None and _node_handler_applies(node, failed):
                # Deliberately not reached on needs_context: a question an agent
                # asked is addressed to a human, and no repair task can answer
                # it (Kraft-rv6i).
                repaired = True
                verdict, failed, excs = await recover_node(
                    db,
                    run_dirs,
                    work_item_id,
                    node,
                    row,
                    worktree,
                    failed=failed,
                    steer=steer,
                    launch=launch,
                    budget=budget,
                    measured_round=0,
                    loop_severities=loop_severities,
                    spent=spent,
                )
                # The same ladder as the measurement: a repair that could not
                # start, or a re-measure that hit a sentinel, is not "task
                # failed" (review finding 1).
                stop = await _sentinel_stop(db, work_item_id, node, verdict, failed, budget)
                if stop is not None:
                    return stop
                if verdict == BASE_MOVED:
                    return await _moved_base(db, work_item_id, node)
                recovered = verdict == "ok"
                if not recovered:
                    question = dispatch.needs_context_question(db, work_item_id, node, round=1)
            if not recovered:
                if question is not None:
                    reason = f"needs_context: {question}"
                    await db.write(
                        lambda c: store.mark_needs_human(c, work_item_id, node.id, reason)
                    )
                    return "needs_human"
                # Kraft-5m7t: a `retry --steer` against this node only
                # reaches an agent that reads it; a forge/builtin/subprocess
                # task has no session for a steer to land in, so it silently
                # no-ops and a human can burn several retries assuming
                # otherwise. Naming each failed task's kind here is the
                # cheapest way to tell them apart without guessing whether
                # *this* retry's steer would land.
                named = ", ".join(prompts.named_with_kind(t) for t in failed)
                reason = f"task failed in node {node.id}: {named}"
                reason += _in_process_causes(db, work_item_id, node, failed)
                if excs:
                    reason += f" ({', '.join(repr(e) for e in excs)})"
                if repaired:
                    reason += " (after on_failure)"
                return _Stuck(reason)
        await db.write(lambda c: store.complete_node(c, work_item_id, node.id))
        return "ok"

    if policy is None:
        raise RuntimeError(f"node {node.id!r} has fix_loop but no policy was provided")

    override_row = fresh_row if fresh_row is not None else row
    cap = _policy.resolve_cap(policy, key, store.node_overrides_of(override_row).get(node.id))
    if loop.max_attempts is not None:
        # The chain's own number wins over `policy.yaml`'s default for this key
        # (`template-policy-may-replace-operational-defaults`); a per-item node
        # override still wins over both, because `resolve_cap` applied it above
        # only when the operator set one.
        cap = replace(cap, attempts=loop.max_attempts)
    # `round` is the fix-cycle index every session in this pass is stamped with,
    # so per-round usage can be read back without joining against the events.
    #
    # Seeded from the counter, not 0: `round` already tracks this same counter
    # from the first completed cycle onward (`round = count` at the bottom of
    # the loop), and the entry point was the one place the two diverged. A
    # resumed entry therefore measured at round 0 while the counter read N, so
    # `reusable_session`'s `round = ?` filter could not match a still-valid
    # measurement of a byte-identical tree -- 7ced80e6 re-bought one for $5.85
    # and 823s of a 3600s wall cap the judge then stopped the loop over.
    counter_row = db.read(lambda c: store.read_counter(c, work_item_id, key))
    round = counter_row["count"] if counter_row is not None else 0
    # One repair per call to `walk_node` -- exactly one per entry into the
    # node (a fresh call on every re-entry via `run_once`'s loop, a crash
    # resume, or the `ci_wait` poller).
    _repair_tried = False
    # "First iteration of this entry" used to be spelled `round == 0`. Since
    # `round` now seeds from the counter the two are different questions, and
    # every site that meant the former needs saying so out loud.
    _first_iteration = True
    # Where the next measurement starts: the caller's step on the entry pass
    # (a resume skips steps that already passed), then the node's FIRST step
    # after every repair attempt (`fix-loop-remeasures-the-whole-node`,
    # Kraft-mq752): a repair can break what an earlier step already passed --
    # a repaired merge request has to be awaited on CI again, not only on the
    # review that failed.
    resume_step = start_step
    # The last fix pass's exceptions, for the cap's reason on the next cycle.
    fix_excs: list[BaseException] = []
    while True:
        verdict, failed, excs = await dispatch.measure_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            worktree,
            round=round,
            steer=steer,
            launch=launch,
            budget=budget,
            loop_severities=loop_severities,
            start_step=resume_step,
            spent=spent,
        )
        resume_step = 0
        if verdict == BASE_MOVED:
            # Not a failure: no fix cycle is spent on code a restart is about
            # to re-measure (`base-change-is-not-an-execution-failure`).
            return await _moved_base(db, work_item_id, node)
        # CONFIG_ERROR is checked before bump_counter: a repair pass cannot
        # install a binary either, and the counter must stay untouched
        # (Kraft-579).
        stop = await _sentinel_stop(db, work_item_id, node, verdict, failed, budget)
        if stop is not None:
            return stop

        if verdict == "failed":
            conflicted, detail = _conflicted(db, work_item_id, node, failed, excs)
            if conflicted:
                resolved = await _resolve_conflict(
                    db,
                    run_dirs,
                    work_item_id,
                    node,
                    row,
                    worktree,
                    conflicted,
                    detail,
                    steer=steer,
                    launch=launch,
                    budget=budget,
                    round=round,
                    loop_severities=loop_severities,
                )
                if resolved == BASE_MOVED:
                    return await _moved_base(db, work_item_id, node)
                return resolved

        if (
            verdict == "failed"
            and _node_handler_applies(node, failed)
            and _first_iteration
            and not _repair_tried
        ):
            # Once per entry into this node, and only ahead of the very first
            # cycle: `recover_node` re-measures for real, so a repair that
            # resolved things costs nothing further here, and one that didn't
            # falls straight through to the normal fix cycle below with the
            # failure it actually is.
            _repair_tried = True
            r_verdict, r_failed, r_excs = await recover_node(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                worktree,
                failed=failed,
                steer=steer,
                launch=launch,
                budget=budget,
                round=_REPAIR_ROUND,
                measured_round=round,
                loop_severities=loop_severities,
                spent=spent,
            )
            # `recover_node`'s re-measure is a real `dispatch.measure_node` call
            # against the node's own tasks again -- every sentinel that call can
            # produce for the ordinary top-of-loop measure can just as well come
            # back from here: a fix loop that fell through the WAITING case, in
            # particular, would spend a paid fix cycle on a pipeline that has
            # not even finished settling for the repaired head.
            stop = await _sentinel_stop(db, work_item_id, node, r_verdict, r_failed, budget)
            if stop is not None:
                return stop
            if r_verdict == BASE_MOVED:
                return await _moved_base(db, work_item_id, node)
            if r_verdict == "ok":
                await db.write(lambda c: store.complete_node(c, work_item_id, node.id))
                return "ok"
            # Only "failed" reaches here, and that fall-through is deliberate:
            # the repair ran, didn't resolve things, and the ordinary paid fix
            # cycle below should now see the failure for what it actually is.
            #
            # The repair's own re-measure just wrote its findings/session rows
            # under `_REPAIR_ROUND`, not the `round` this loop was on when it
            # entered this block -- the fall-through below reads
            # `collect_findings`/`needs_context_question` at `round`, so `round`
            # has to move to match, or those calls read the stale pre-repair
            # rows instead of the fresher re-measure this repair just produced.
            verdict, failed, excs, round = r_verdict, r_failed, r_excs, _REPAIR_ROUND

        previous_found, fix_ran, previous_head = dispatch.last_measurement(
            db, work_item_id, node.id
        )
        head_sha = _config.git_read(Path(worktree), "rev-parse", "HEAD")
        # The tags this loop's own checks already meant: the *eligible* subset of
        # the last measurement. `last_measurement` returns whole findings now
        # (a repeat's severity is floored against theirs), so the narrowing that
        # used to live in the event payload's `fingerprints` key happens here
        # instead -- identically, so `prints == previous_prints` and `repeats`
        # below are unchanged.
        previous_prints = (
            sorted({f.fingerprint for f in previous_found if f.severity in policy.loop_severities})
            if previous_found is not None
            else None
        )
        # `dispatch.previous_fix_session` is history-wide, not scoped to this
        # `walk_node` entry -- a `/retry` or an auto-escalation retry clears
        # the loop counter (`retry_counters` row deleted) but leaves the old
        # `worker_sessions` rows in place, so it alone can't tell "round 1 of
        # this entry" from "round 5 of the item's whole history". Round 1 of
        # *this* entry always fixes freely, no judge call (spec decision 2,
        # `fix-loop-judge-runs-after-the-first-attempt`): gated on the loop
        # counter itself having no row yet (the same signal `bump_counter`
        # below uses to tell a fresh fire from a repeat one), not on whether a
        # fix session ever ran for this node before. A carried-in steer gets
        # the same free pass regardless of the counter: it is the human's
        # answer to exactly the trend the judge might stop on, and a
        # `stop_needs_human` here would discard it before the steered cycle it
        # was meant for ever dispatches, re-stranding the item on the trend
        # `/retry` was supposed to escape.
        previous_fix = dispatch.previous_fix_session(db, work_item_id, node)
        counter_row = db.read(lambda c: store.read_counter(c, work_item_id, key))
        # Which steers get the free pass the comment above describes is
        # `Steer.exempts_judge`'s call, not a `human` test. A *seeded* steer
        # (Kraft's own recap of findings the last review already left,
        # Kraft-7sec second half) is not a person's deliberate answer to the
        # trend this judge exists to brake and must not stand in for one. A gate
        # reviewer's verdict is not a person's either, but it is the entire
        # content of a `fixed`/`reject` decision and exists nowhere else, so
        # discarding it before delivery is the data loss this comment warns
        # about rather than the misattribution (Kraft-s7c04.6).
        exempt = steer is not None and bool(steer) and steer.exempts_judge
        judge_due = previous_fix is not None and counter_row is not None and not exempt
        # `reported_hooks`, not `reported`: it is the set of task paths that
        # wrote a parseable result file, and `blind_failures` below is computed
        # from it. Named for what it holds so nothing else in this function can
        # quietly shadow it -- a draft of Kraft-s7c04.3 did, which turned every
        # failing task into a blind failure and put the loop beyond the reach of
        # the stuck detector.
        found, reported_hooks = dispatch.collect_findings(db, work_item_id, node, round)
        # A `same_as` is only believable for a tag this round's reviewer was
        # actually shown. An invented or stale tag would collapse two distinct
        # defects onto one identity and fire the stuck detector on a fiction
        # (Kraft-s7c04.2). Under V1 nothing shows a reviewer its previous
        # findings at all -- see `findings.resolve_identity`, which says what
        # that costs and why the call stays: as it stands this strips nothing
        # because no `same_as` can arrive.
        found = _findings.resolve_identity(
            found, known={f.fingerprint for f in previous_found or []}
        )
        # Parallel to `found`, never a dict keyed on fingerprint: two findings in
        # one round can share a tag if the reviewer pointed both at the same
        # `same_as`, and a dict would silently drop one of them.
        as_reported = [f.severity for f in found]
        found = _carry_severity(
            found,
            previous_found or [],
            # Both known and equal, never "both None": a pair of unreadable heads
            # is not evidence that nothing moved.
            unchanged_tree=head_sha is not None and head_sha == previous_head,
        )
        eligible = [f for f in found if f.severity in policy.loop_severities]
        prints = sorted({f.fingerprint for f in eligible})
        # Built here rather than inside the lambda, the same way `judge_payload`
        # below is: every name a deferred lambda reads out of this loop has to be
        # bound at definition time or it sees the next iteration's value.
        #
        # `reported_severity` rides along only where the floor overrode the
        # reviewer (Kraft-s7c04.3). Refusing a downgrade silently would make a
        # reviewer inconsistency indistinguishable from a reviewer agreeing.
        # `findings.from_payload` reads key-by-key and ignores the extra key --
        # exactly the case its docstring exists for.
        measured_payload = {
            "node_id": node.id,
            "cycle": round,
            # The commit this measurement is about, so the next round can tell a
            # re-rating of an untouched tree from a real change.
            "head_sha": head_sha,
            "findings": [
                {**asdict(f), **({"reported_severity": s} if s != f.severity else {})}
                for f, s in zip(found, as_reported, strict=True)
            ],
            "fingerprints": prints,
        }
        await db.write(
            lambda c, payload=measured_payload: events.append(
                c, work_item_id, "findings_measured", payload
            )
        )

        # A failed task that produced no findings at all is still non-clean: the
        # findings list refines *why* a task failed, it does not define failure.
        blind_failures = [t for t in failed if t.path not in reported_hooks]
        enters_loop = bool(eligible) or bool(blind_failures)

        if not enters_loop:
            await db.write(lambda c: store.complete_node(c, work_item_id, node.id))
            return "ok"

        # Checked before bump_counter, not after: the counter is bumped to
        # dispatch the fix task, so only a *measuring* task's needs_context can
        # land here without ever consuming a cycle. A needs_context from the
        # fix task itself surfaces on the next iteration's measure, at which
        # point the cycle it belongs to has already been bumped.
        question = dispatch.needs_context_question(
            db, work_item_id, node, round, first_iteration=_first_iteration
        )
        if question is not None:
            reason = f"needs_context: {question}"
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node.id, reason)
            )
            return "needs_human"

        # A cycle no worker commit could ever win: every blind failure traces
        # to a task that runs inside this process, and no co-task either
        # failed in a worker-fixable way or reported a finding of its own
        # (Kraft-s7c04.24). Refusing it here, ahead of the budget check, the
        # judge and `bump_counter`, is the same posture as the `CONFIG_ERROR`
        # stop just below: a cycle that cannot be won must not spend an
        # attempt or buy a judge session to tell us what we already know.
        #
        # After `needs_context_question`, not before (ordering correction,
        # found reviewing the plan): a question a fix agent has already asked
        # is addressed to a human and outranks a stop that only says
        # "reinstall the daemon".
        in_process_blind = {t.path for t in blind_failures if _in_process(t)}
        if (
            in_process_blind
            and {t.path for t in blind_failures} == in_process_blind
            and all(f.source_plugin in in_process_blind for f in eligible)
        ):
            named = ", ".join(
                sorted(prompts.named_with_kind(t) for t in blind_failures if _in_process(t))
            )
            reason = (
                f"{named} failed in node {node.id}"
                f"{_in_process_causes(db, work_item_id, node, blind_failures)}, and those "
                "tasks are executed by the running Kraft daemon — a worker commit cannot "
                "change them. Reinstall and restart, or skip the node."
            )
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node.id, reason)
            )
            return "needs_human"

        # Also ahead of the counter bump and the `fix_cycle_started` event: a
        # refused launch must not spend a fix-loop attempt or leave a cycle in
        # the log for an agent that never ran. The check inside `dispatch_node`
        # stays as the backstop for any future agent task reached by another
        # path.
        #
        # After needs_context, not before: a question a task actually asked is
        # the more useful thing to hand a human, and raising the cap would not
        # answer it. Either order prevents the phantom bump, which is the part
        # that matters; only the reason on the card differs.
        if stops.budget_breach(db, work_item_id, budget) is not None:
            return await stops.stop_for_budget(db, work_item_id, node, budget)

        # Fix-loop judge (2026-09-12-verify-fix-loop-judge-design): a brake on
        # top of the existing cap, never a second way to get stuck. Anything
        # the judge itself cannot be trusted on already fell open to
        # "continue" inside `dispatch.judge_verdict`
        # (`invalid-judge-result-does-not-block-the-loop`); the cap/stuck
        # checks below run exactly as they do today regardless of what runs
        # here, so no verdict can buy a cycle past the cap
        # (`fix-loop-judge-cannot-override-limits`).
        # Re-initialised every iteration, inside the loop: bound above it, a
        # later round where `judge_due` is False would re-serve the previous
        # round's reasoning and tell the fixer the judge had just said it.
        reasoning = ""
        judge_ran = False
        if judge_due:
            judge_ran = True
            verdict, reasoning = await dispatch.judge_verdict(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                worktree,
                round=round,
                key=key,
                cap=cap,
                policy=policy,
                launch=launch,
                budget=budget,
            )
            if verdict == "paused":
                return "paused"
            judge_payload = {
                "node_id": node.id,
                "cycle": round,
                "verdict": verdict,
                "reasoning": reasoning,
                "findings": [asdict(f) for f in eligible],
            }
            await db.write(
                lambda c, payload=judge_payload: events.append(
                    c, work_item_id, "judge_verdict", payload
                )
            )
            if verdict == "stop_needs_human":
                return _Stuck(f"judge: {reasoning}")
            # "as if there were no eligible findings" (spec) -- a task that
            # failed outright (blind or not: a red pipeline reports findings
            # *and* fails) would still be in the loop once its eligible
            # findings are downgraded, so gate on `failed` itself rather than
            # on the blind subset. Only a task with no failure at all can be
            # treated as clean here.
            if verdict == "stop_downgrade" and not failed:
                await db.write(lambda c: store.complete_node(c, work_item_id, node.id))
                return "ok"

        # bump_counter returns the cap snapshotted on the row (spec §2.C: written
        # once at first fire, not re-resolved per attempt). Across a restart with
        # an edited policy.yaml, `cap` here is the freshly-resolved one; the row's
        # snapshot is authoritative for the breach check.
        count, started_at, cap = await db.write(
            lambda c, cap=cap: store.bump_counter(c, work_item_id, key, cap)
        )
        if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
            reason = f"{key} exhausted after {count - 1} fix cycle(s)"
            # What raised, measuring or fixing, is the only account of why when
            # a task never reported (Kraft-hr0xr) -- the loopless path already
            # says it, and the loop threw it away.
            raised = [*excs, *fix_excs]
            if raised:
                reason += f" ({', '.join(repr(e) for e in raised)})"
            measured = [t.path for step in node.steps for t in step.tasks]
            await db.write(
                lambda c, measured=measured: store.mark_sessions_capped_out(
                    c, work_item_id, node.id, measured
                )
            )
            return _Stuck(reason, capped={"cycles": count - 1, "attempts": cap.attempts})

        if prints and fix_ran:
            # Whole-set equality catches "nothing at all changed". Kraft-0i6z4:
            # a single fingerprint recurring for `_FINGERPRINT_STREAK_LIMIT`
            # rounds is also stuck, even while a co-occurring finding keeps
            # changing shape and the set as a whole never repeats exactly.
            # Only reached when the cheap set check didn't already answer it,
            # so the common (non-stuck) path pays no extra history read.
            stuck_fp = (
                None
                if prints == previous_prints
                else dispatch.stuck_fingerprint(
                    dispatch.judge_history(
                        db,
                        work_item_id,
                        node.id,
                        policy.loop_severities,
                        fix_paths=dispatch.fix_task_paths(node),
                    ),
                    _FINGERPRINT_STREAK_LIMIT,
                )
            )
            if prints == previous_prints or stuck_fp:
                reason = (
                    f"stuck: {len(prints)} finding(s) unchanged across cycle {count - 1}"
                    if stuck_fp is None
                    else f"stuck: finding {stuck_fp} unchanged across "
                    f"{_FINGERPRINT_STREAK_LIMIT} cycles"
                )
                # Deliberately NOT mark_sessions_capped_out: these sessions did
                # not cap out, and only a real cap breach may claim they did.
                return _Stuck(
                    reason, bundle=await _diagnosis_bundle(db, work_item_id, node, worktree)
                )

        payload = {
            "node_id": node.id,
            "cycle": count,
            "failed_tasks": [t.path for t in failed],
        }
        await db.write(
            lambda c, payload=payload: events.append(c, work_item_id, "fix_cycle_started", payload)
        )
        instruction = (
            prompts.FIX_PROMPT.format(node_id=node.id, failed=", ".join(t.task.id for t in failed))
            if failed
            else prompts.FIX_PROMPT_FINDINGS_ONLY.format(node_id=node.id)
        )
        if eligible:
            repeats = set(previous_prints or [])
            instruction += prompts.FIX_FINDINGS.format(
                findings=prompts.format_findings(eligible, repeats)
            )
            # The tag is unconditional -- "this finding was in the last
            # measurement" is true either way -- but the note claims an earlier
            # attempt was made, which is false on the crash/resume path where
            # the measurement was recorded and the process died before any
            # fix_cycle_started.
            if fix_ran and repeats & set(prints):
                instruction += prompts.FIX_REPEAT_NOTE
        # Only a `continue` reaches here at all -- both stop verdicts return
        # above -- but `verdict` is reassigned by the judge and shadows the
        # measure verdict from the top of the loop, so this is gated on the
        # judge having actually run rather than on the name's current value.
        if judge_ran:
            instruction += prompts.judge_note(reasoning)
        # Every round, not just the last one (Kraft-s7c04.7). `last_measurement`
        # gives the fixer one round of memory, which is why round N+2 reverted
        # the security property round N established on e983d85c -- the loop had
        # the information and could not see two rounds back. Read here rather
        # than hoisted: this is the uncommon path (a fix is about to be
        # dispatched), and the stuck check above is deliberately lazy about the
        # same history so the common path pays nothing.
        history = dispatch.judge_history(
            db,
            work_item_id,
            node.id,
            policy.loop_severities,
            fix_paths=dispatch.fix_task_paths(node),
        )
        instruction += prompts.round_history_note(history, dispatch.regressed_fingerprints(history))
        instruction += prompts.previous_attempt_note(previous_fix)
        # The fix loop is an ordered shape of its own (`fix-loop-supports-one-
        # ordered-repair-shape`), so the repair runs through the same steps
        # walk every other group does rather than one hardcoded task.
        fix, fix_failed, fix_excs = await dispatch.measure_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            worktree,
            steps=node.fix_loop,
            instruction_override=instruction,
            round=count,
            steer=steer,
            launch=launch,
            budget=budget,
            loop_severities=loop_severities,
        )
        if not _repair_outcome(fix, fix_failed):
            # Only a genuine repair outcome spends an attempt (Kraft-jdkoq). A
            # repair that was paused, rate limited, left waiting, refused to
            # start, or whose own plumbing failed (a sync step that never
            # pushed) has not tried anything the next measurement could judge:
            # re-measuring would spend the cycle and stop as "stuck", blaming a
            # fix that never ran.
            await db.write(lambda c: store.refund_counter(c, work_item_id, key))
            await db.write(
                lambda c, refund={"node_id": node.id, "cycle": count, "outcome": fix}: (
                    events.append(c, work_item_id, "fix_cycle_refunded", refund)
                )
            )
            if fix == BASE_MOVED:
                return await _moved_base(db, work_item_id, node)
            stop = await _sentinel_stop(db, work_item_id, node, fix, fix_failed, budget)
            if stop is not None:
                return stop
            named = ", ".join(prompts.named_with_kind(t) for t in fix_failed)
            reason = (
                f"fix attempt {count} in node {node.id} could not finish: {named} failed"
                f"{_in_process_causes(db, work_item_id, node, fix_failed)}"
            )
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node.id, reason)
            )
            return "needs_human"
        round = count
        _first_iteration = False


def _repair_outcome(fix: str, fix_failed: list[ResolvedTask]) -> bool:
    """Whether a fix-loop pass is a genuine repair attempt the next
    measurement may judge: the steps ran clean, or a repair task that does the
    repairing (an agent's, a subprocess's) reported that it failed. Kraft's own
    in-process steps -- a forge sync, a builtin -- failing is plumbing, not a
    repair outcome, and every stop verdict is its own cause."""
    if fix == "ok":
        return True
    return fix == "failed" and not any(_in_process(t) for t in fix_failed)


async def _report_if_undelivered(db, work_item_id: str, carried: Steer) -> None:
    """Kraft-s7c04.50: `carried` is good for one agent launch, whichever
    dispatch gets there first (Steer's own docstring) -- but a walk that
    stops, or a rebase-bounce that replaces `carried` with its own seeded
    note, before any dispatch ever calls `.take()` loses the original text
    with nothing to show for it. The steer mechanism itself works (verified
    live against acf59aafa6bb4512bd68049705414fc7's actual session logs);
    this only makes the *failure to deliver* case visible instead of silent.
    `.take()` both reads the leftover text and empties it, defensive against
    a future call path invoking this twice on the same `carried`."""
    if carried:
        await db.write(
            lambda c: events.append(c, work_item_id, "steer_undelivered", {"steer": carried.take()})
        )


async def _restart_for_base_change(
    db, work_item_id: str, nodes, index: int, restart_from: str, policy: _policy.Policy | None
) -> int | None:
    """Restart the chain at `restart_from` because node `index` moved the
    worktree base (`base-change-restarts-a-declared-chain-span`), or stop for a
    human once the node's restarts are spent.

    A base change is not an execution failure
    (`base-change-is-not-an-execution-failure`): no recovery ran for it and no
    fix attempt is bumped. Every node in the span gets a fresh fix-loop budget
    instead -- re-running a node is a fresh pass over it, and a clock from
    before the restart would price the new pass at the old one's cost
    (Kraft-s7c04.25). The restarts themselves are bounded by their own
    `<node>.on_base_changed` counter, which the span never clears: a base that
    moves on every pass would otherwise restart forever.
    """
    node = nodes[index]
    key = f"{node.id}.on_base_changed"
    cap = (
        _policy.resolve_cap(policy, key)
        if policy is not None
        else _policy.Cap(attempts=3, wall_clock_s=3600)
    )
    count, started_at, cap = await db.write(lambda c: store.bump_counter(c, work_item_id, key, cap))
    if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
        reason = f"{key} exhausted after {count - 1} restart(s) from {restart_from!r}"
        capped = {"cycles": count - 1, "attempts": cap.attempts}
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, node.id, reason, capped))
        return None
    target = next(j for j, n in enumerate(nodes) if n.id == restart_from)
    span = [n for n in nodes[target : index + 1] if isinstance(n.node, ExecNode)]
    for n in span:
        await db.write(
            lambda c, n=n: store.clear_loop_counters(
                c, work_item_id, n.id, _loop_key(n) if n.node.fix_loop else None
            )
        )
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            "base_change_restart",
            {
                "node_id": node.id,
                "restart_from": restart_from,
                "nodes": [n.id for n in span],
                "restart": count,
            },
        )
    )
    return target


async def run_once(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    bd_cwd: str | None = None,
    start_index: int = 0,
    start_step: int = 0,
    policy: _policy.Policy | None = None,
    steer: str | None = None,
    steer_source: str = "human",
    launch: LaunchContext | None = None,
) -> str:
    # the note is good for one agent launch, whichever task gets there first
    carried = Steer(steer, source=steer_source)
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    nodes = chain_of(row).chain.nodes
    # Record the chain as loaded before touching the filesystem: this is
    # bookkeeping about the item, not about the worktree, and a work item
    # whose worktree can never be created (bad repo, git failure) must still
    # end up with a `chain_loaded` event and a non-NULL `current_node_id` --
    # otherwise it looks indistinguishable from a crash between
    # create_work_item and the first load_chain (see the `cur is None`
    # branch in `kraft.executor.resuming.resume`), and the WS gets fewer
    # events than a client waiting on this chain to progress at all is
    # entitled to expect.
    if start_index == 0:
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0].id))

    # Kraft-tsfpk: a bd dependency the tracker already knows about (`bd dep
    # add ... --type blocks`) must stop this walk before it spends a paid
    # agent session re-discovering the blocker from prose. Checked once per
    # dispatch attempt -- intake, resume, retry, and every poller re-entry all
    # pass through here -- not once per node inside the loop below: a merge
    # landing mid-walk is the rare case, and a `bd blocked` call on every node
    # buys correctness nobody has hit yet at the cost of one more subprocess
    # launch per node, every walk, forever.
    #
    # `row["bead_id"]` alone is a near-no-op for the case that motivated this
    # bead (plan-review finding 1): `entry.intake` files a *fresh* tracking
    # bead for every manually created item, and a brand-new bead has no `bd
    # dep` edges of its own -- `bead_id`'s own `blocked_by` is always `[]`.
    # The real edges live on the source beads the item states in
    # `implements_beads` at intake.
    # Both go into the one `bd blocked --json` call.
    bead_ids = [i for i in (row["bead_id"], *entry._implements_beads(row)) if i]
    if bead_ids:
        # `row["bead_cwd"] or bd_cwd`, the same precedence
        # `entry.close_beads` (`entry.py:221`) already uses -- not `bd_cwd or
        # row["repo"]`. With `KRAFT_BD_CWD` set instance-wide, a bead filed
        # into a per-repo workspace must still be looked up there, or it
        # silently never reads as blocked (plan-review finding 4).
        blocked_by = await beads.blocked_by(bead_ids, cwd=row["bead_cwd"] or bd_cwd)
        # Blockers that are themselves part of this item's own bead set
        # (e.g. two bundled beads with a `blocks` edge between them) aren't
        # external dependencies -- this walk is how they get implemented.
        blocked_by = [b for b in blocked_by if b not in set(bead_ids)]
        if blocked_by:
            await db.write(
                lambda c: store.mark_blocked_by_dependency(
                    c, work_item_id, nodes[start_index].id, blocked_by
                )
            )
            return "paused"

    # Before the first dispatch, not inside the `env_setup` node: `default.yaml`
    # runs `spec` and `plan` first, and both need a checkout — and, for `plan`'s
    # attached-spec fallback to have anything to find, attachments already
    # copied in — to write into.
    #
    # A git failure here (bad repo, no permission, ...) is attributed to the
    # node that was about to dispatch rather than left to `kraft.api.deps.guard`'s bare
    # crash handler: this call moved out of `env_setup`'s own session so spec
    # and plan can use the checkout too, but the observable shape of "a node
    # failed to start" -- node_started, then needs_human naming it -- should
    # not disappear just because the failure now happens a moment earlier.
    try:
        worktree = await _builtins.ensure_worktree(
            db,
            run_dirs,
            repo=row["repo"],
            work_item_id=work_item_id,
            attachments=entry.attachments_of(row),
            repo_entry=launch.repo_entry if launch else None,
        )
        # What the deleted `env_setup` node used to do, as implicit runtime
        # preparation: V1 has no builtin action for it, and every node from the
        # first one on can commit, so the environment the repo declares has to
        # be there before anything dispatches. Recorded as an event rather than
        # a session -- there is no task here to own one; `kraft view events` is
        # where a human reads the report back.
        #
        # Only when the walk is actually starting, unlike `ensure_worktree`
        # above it. `env_setup` was an ordinary node, so a re-entry at
        # `start_index > 0` skipped it -- and `run_once` is re-entered that way
        # by the `ci_wait` poller (up to `loops.ci_wait` times for one pipeline),
        # `rate_limit_retry`, gate approval and every `/retry`. Running the
        # repo's `setup_command` per dispatch attempt rather than per item would
        # re-`uv sync` a worktree sixty times over one CI wait and append its
        # whole stdout to the events table each time.
        report = (
            await _builtins.prepare_runtime(
                worktree, Path(row["repo"]), launch.repo_entry if launch else None
            )
            if start_index == 0
            else None
        )
    except (RuntimeError, _config.ConfigError) as exc:
        failing_node = nodes[start_index].id
        reason = str(exc)
        await db.write(lambda c: store.enter_node(c, work_item_id, failing_node))
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, failing_node, reason))
        return "needs_human"
    if report is not None:
        await db.write(
            lambda c: events.append(c, work_item_id, "worktree_prepared", {"report": report})
        )

    i = start_index
    step = start_step
    while i < len(nodes):
        # Kraft-e7pm: a session verdict of "paused" is not the only way a walk
        # has to stop. Pausing between two nodes' dispatches leaves no live
        # session to signal at all -- the SIGTERM path in `pause_work_item`
        # never fires -- so without this read the loop would march straight
        # into the next node reading a row that already says paused. One read
        # per node; every non-active status (paused, needs_human by any other
        # door, abandoned) gets the same "paused" verdict every caller of
        # `run_once`/`run` already knows how to handle -- there is nothing a
        # second string would let a caller do that this doesn't.
        if gates.status_of(db, work_item_id) != "active":
            return "paused"
        node = nodes[i]
        # A gate is an ordered node of its own (`gate-is-an-ordered-node`): it
        # runs nothing, so it is recognised here rather than after an execution
        # node's own tasks. `maybe_gate` answers False for a gate this item has
        # already cleared, which is what lets a resumed walk pass one.
        if await gates.maybe_gate(db, work_item_id, node):
            await _report_if_undelivered(db, work_item_id, carried)
            return "awaiting_gate"
        base_change = node.node.on_base_changed if isinstance(node.node, ExecNode) else None
        pre_base = dispatch._current_base_ref(db, work_item_id) if base_change else None
        result = await walk_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            worktree,
            policy=policy,
            # `carried` empties itself on the first agent launch, so the note reaches
            # the next agent to run and no later one
            steer=carried,
            launch=launch,
            start_step=step,
        )
        step = 0  # only the entry node resumes mid-way
        if result in ("paused", "needs_human", RATE_LIMITED, WAITING):
            # This call is ending without giving `node` -- or any later node
            # in this same run_once, since none of these statuses continue
            # the loop -- a chance to consume `carried` beyond what it just
            # got. Report it here, once, rather than duplicating this check
            # at each of the four returns below (Kraft-s7c04.50).
            await _report_if_undelivered(db, work_item_id, carried)
        if result == "paused":
            return "paused"
        if result == "needs_human":
            return "needs_human"
        if result == RATE_LIMITED:
            return RATE_LIMITED
        if result == WAITING:
            return WAITING
        new_base = dispatch._current_base_ref(db, work_item_id) if base_change else None
        if base_change is not None and (result == BASE_MOVED or new_base != pre_base):
            target = await _restart_for_base_change(
                db, work_item_id, nodes, i, base_change.restart_from, policy
            )
            if target is None:
                await _report_if_undelivered(db, work_item_id, carried)
                return "needs_human"
            # `carried` is about to be replaced by Kraft's own drift note -- if
            # the original steer survived this far untaken, report it before
            # it is overwritten (Kraft-s7c04.50).
            await _report_if_undelivered(db, work_item_id, carried)
            carried = Steer(
                prompts.rebase_drift_note(
                    worktree, store.branch_for(row), pre_base or "HEAD", new_base or "HEAD"
                ),
                # Kraft wrote this one, not a person; it must not claim the
                # judge exemption a human's own answer gets.
                source="seeded",
            )
            i = target
            continue
        i += 1

    # The whole chain ran to completion without any node's dispatch ever
    # taking `carried` -- e.g. every remaining node was builtin/subprocess
    # only. Report it the same as every other exit (Kraft-s7c04.50).
    await _report_if_undelivered(db, work_item_id, carried)
    await db.write(lambda c: store.mark_completed(c, work_item_id))
    await entry.close_beads(db, row, bd_cwd, run_dirs)
    return "completed"


async def run(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    bd_cwd: str | None = None,
    start_index: int = 0,
    start_step: int = 0,
    policy: _policy.Policy | None = None,
    steer: str | None = None,
    steer_source: str = "human",
    launch: LaunchContext | None = None,
    on_approve: OnApprove | None = None,
) -> str:
    status = await run_once(
        db,
        run_dirs,
        work_item_id=work_item_id,
        registry=registry,
        bd_cwd=bd_cwd,
        start_index=start_index,
        start_step=start_step,
        policy=policy,
        steer=steer,
        steer_source=steer_source,
        launch=launch,
    )
    status = await gates.review_gates(
        status,
        db,
        run_dirs,
        work_item_id=work_item_id,
        registry=registry,
        policy=policy,
        launch=launch,
        bd_cwd=bd_cwd,
        on_approve=on_approve,
    )
    return await gates.auto_escalate_stuck(
        status,
        db,
        run_dirs,
        work_item_id=work_item_id,
        registry=registry,
        policy=policy,
        launch=launch,
        bd_cwd=bd_cwd,
        on_approve=on_approve,
    )
