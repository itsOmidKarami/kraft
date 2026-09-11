from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from kraft import builtins as _builtins
from kraft import events, store
from kraft import policy as _policy
from kraft.executor import dispatch, entry, gates, prompts, stops
from kraft.executor.context import (
    BUDGET,
    CONFIG_ERROR,
    RATE_LIMITED,
    WAITING,
    LaunchContext,
    OnApprove,
    Steer,
)
from kraft.executor.dispatch import _current_base_ref
from kraft.store import _now as _now
from kraft.templates import Registry


def _previous_fix_session(db, work_item_id: str, node_id: str) -> sqlite3.Row | None:
    """The most recently dispatched fix task for this node, or None if none
    has run yet.

    Deliberately NOT scoped to a `round` passed in by the caller: `round` is
    `walk_node`'s own local counter, reset to 0 on every fresh entry into that
    function -- a resume/crash-recovery re-entry, or a `/retry` that deletes
    the `retry_counters` row so the next `bump_counter` restarts at 1 -- while
    the persisted `worker_sessions` rows from before that re-entry are still in
    the table. A lookup keyed on the caller's local round can collide with an
    abandoned attempt that happens to land on the same round number after a
    retry (worse than nothing: it hands over a plausible-looking file from a
    cycle that was already exhausted), or, on a fresh round-0 re-entry, miss
    every previous attempt outright. Ordering by `created_at` and taking the
    last row sidesteps both: whichever fix task actually ran most recently for
    this node is always the right one to hand forward, regardless of what
    round it or the caller's local counter think they're at.
    """
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND hook_point = 'on.implementation.start' ORDER BY created_at",
            (work_item_id, node_id),
        ).fetchall()
    )
    return rows[-1] if rows else None


async def recover_node(
    db,
    run_dirs,
    work_item_id: str,
    node: dict,
    row,
    registry: Registry,
    worktree,
    *,
    failed: list[str],
    steer: Steer | None,
    launch: LaunchContext | None,
    budget: _policy.Budget,
) -> tuple[str, list[str], list[BaseException]]:
    """One repair pass over a node whose tasks failed (Kraft-rv6i).

    Runs the node's `on_failure` tasks, then lets the node measure itself
    again, and reports that second measurement. The re-measure is the point:
    a node's contract is its own tasks passing, so a repair is believed only
    when they do -- `on.ci.poll` going green, not a remediator claiming it
    labelled something.

    Once per entry into the node, not a loop. A repair that did not take is a
    blocker Kraft does not understand, and the honest move is to stop for a
    human rather than to keep pulling the same lever.
    """
    hooks = list(node["on_failure"])
    await db.write(
        lambda c: events.append(
            c,
            work_item_id,
            "node_recovery_started",
            {"node_id": node["id"], "failed_tasks": failed, "tasks": hooks},
        )
    )
    # `round=1` separates the repair's sessions and the re-measure's from the
    # first attempt's, so per-round usage reads back without joining the events.
    # Safe to borrow the fix-loop's counter here because a node may not have
    # both `fix_loop` and `on_failure` (templates.py).
    verdict, r_failed, r_excs = await dispatch.measure_node(
        db,
        run_dirs,
        work_item_id,
        {**node, "tasks": hooks},
        row,
        registry,
        worktree,
        round=1,
        steer=steer,
        launch=launch,
        budget=budget,
    )
    if verdict != "ok":
        return verdict, r_failed, r_excs
    return await dispatch.measure_node(
        db,
        run_dirs,
        work_item_id,
        node,
        row,
        registry,
        worktree,
        round=1,
        steer=steer,
        launch=launch,
        budget=budget,
    )


async def walk_node(
    db,
    run_dirs,
    work_item_id: str,
    node: dict,
    row,
    registry: Registry,
    worktree,
    *,
    policy: _policy.Policy | None = None,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
) -> str:
    key = node.get("fix_loop")
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

    if not key:
        verdict, failed, excs = await dispatch.measure_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            registry,
            worktree,
            steer=steer,
            launch=launch,
            budget=budget,
        )
        if verdict == "paused":
            return "paused"
        if verdict == CONFIG_ERROR:
            named = ", ".join(failed)
            reason = f"could not start {named} in node {node['id']} — see the session log"
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human"
        if verdict == RATE_LIMITED:
            return await stops.stop_for_rate_limit(db, work_item_id, node)
        if verdict == WAITING:
            return await stops.stop_for_waiting(db, work_item_id, node)
        if verdict == BUDGET:
            return await stops.stop_for_budget(db, work_item_id, node, budget)
        if verdict == "failed":
            question = dispatch.needs_context_question(db, work_item_id, node, round=0)
            recovered = False
            if question is None and node.get("on_failure"):
                # Deliberately not reached on needs_context: a question an agent
                # asked is addressed to a human, and no repair task can answer
                # it (Kraft-rv6i).
                verdict, failed, excs = await recover_node(
                    db,
                    run_dirs,
                    work_item_id,
                    node,
                    row,
                    registry,
                    worktree,
                    failed=failed,
                    steer=steer,
                    launch=launch,
                    budget=budget,
                )
                if verdict == "paused":
                    return "paused"
                if verdict == BUDGET:
                    return await stops.stop_for_budget(db, work_item_id, node, budget)
                recovered = verdict == "ok"
                if not recovered:
                    question = dispatch.needs_context_question(db, work_item_id, node, round=1)
            if not recovered:
                if question is not None:
                    reason = f"needs_context: {question}"
                else:
                    # Kraft-5m7t: a `retry --steer` against this node only
                    # reaches an agent that reads it; a forge/builtin/subprocess
                    # task -- ci_poll among them -- has no session for a steer
                    # to land in, so it silently no-ops and a human can burn
                    # several retries assuming otherwise. Naming each failed
                    # task's kind here is the cheapest way to tell them apart
                    # without guessing whether *this* retry's steer would land.
                    named = ", ".join(prompts.named_with_kind(t, registry) for t in failed)
                    reason = f"task failed in node {node['id']}: {named}"
                    if excs:
                        reason += f" ({', '.join(repr(e) for e in excs)})"
                    if node.get("on_failure"):
                        reason += " (after on_failure)"
                await db.write(
                    lambda c: store.mark_needs_human(c, work_item_id, node["id"], reason)
                )
                return "needs_human"
        await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
        return "ok"

    if policy is None:
        raise RuntimeError(f"node {node['id']!r} has fix_loop but no policy was provided")

    cap = _policy.resolve_cap(policy, key)
    # `round` is the fix-cycle index every session in this pass is stamped with,
    # so per-round usage can be read back without joining against the events.
    round = 0
    while True:
        verdict, failed, _excs = await dispatch.measure_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            registry,
            worktree,
            round=round,
            steer=steer,
            launch=launch,
            budget=budget,
        )
        if verdict == "paused":
            return "paused"
        if verdict == CONFIG_ERROR:
            # Checked before bump_counter: a repair pass cannot install a
            # binary either, and the counter must stay untouched (Kraft-579).
            named = ", ".join(failed)
            reason = f"could not start {named} in node {node['id']} — see the session log"
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human"
        if verdict == RATE_LIMITED:
            return await stops.stop_for_rate_limit(db, work_item_id, node)
        if verdict == WAITING:
            return await stops.stop_for_waiting(db, work_item_id, node)
        if verdict == BUDGET:
            return await stops.stop_for_budget(db, work_item_id, node, budget)

        previous_prints, fix_ran = dispatch.last_measurement(db, work_item_id, node["id"])
        found, reported = dispatch.collect_findings(db, work_item_id, node, round)
        eligible = [f for f in found if f.severity in policy.loop_severities]
        prints = sorted({f.fingerprint for f in eligible})
        # A task on `builtin: noop` exits 'done' in milliseconds having done
        # nothing, contributes no findings, and is therefore indistinguishable
        # in this event from a review that ran and found nothing (Kraft-yenu).
        # Naming them costs a dict lookup and is the only signal a reader gets
        # that a tier of review is not actually running.
        noop_hooks = [
            t
            for t in node["tasks"]
            if registry.hooks.get(t, {}).get("kind") == "builtin"
            and registry.hooks.get(t, {}).get("handler") == "noop"
        ]
        await db.write(
            lambda c, r=round, found=found, prints=prints, noop_hooks=noop_hooks: events.append(
                c,
                work_item_id,
                "findings_measured",
                {
                    "node_id": node["id"],
                    "cycle": r,
                    "findings": [asdict(f) for f in found],
                    "fingerprints": prints,
                    "noop_hooks": noop_hooks,
                },
            )
        )

        # A failed task that produced no findings at all is still non-clean: the
        # findings list refines *why* a task failed, it does not define failure.
        blind_failures = [t for t in failed if t not in reported]
        enters_loop = bool(eligible) or bool(blind_failures)

        if not enters_loop:
            await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
            return "ok"

        # Checked before bump_counter, not after: the counter is bumped to
        # dispatch the fix task, so only a *measuring* task's needs_context can
        # land here without ever consuming a cycle. A needs_context from the
        # fix task itself surfaces on the next iteration's measure, at which
        # point the cycle it belongs to has already been bumped.
        question = dispatch.needs_context_question(db, work_item_id, node, round)
        if question is not None:
            reason = f"needs_context: {question}"
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
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

        # bump_counter returns the cap snapshotted on the row (spec §2.C: written
        # once at first fire, not re-resolved per attempt). Across a restart with
        # an edited policy.yaml, `cap` here is the freshly-resolved one; the row's
        # snapshot is authoritative for the breach check.
        count, started_at, cap = await db.write(
            lambda c, cap=cap: store.bump_counter(c, work_item_id, key, cap)
        )
        if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
            reason = f"{key} exhausted after {count - 1} fix cycle(s)"
            await db.write(
                lambda c: store.mark_sessions_capped_out(
                    c, work_item_id, node["id"], list(node["tasks"])
                )
            )
            capped = {"cycles": count - 1, "attempts": cap.attempts}
            await db.write(
                lambda c, reason=reason, capped=capped: store.mark_needs_human(
                    c, work_item_id, node["id"], reason, capped
                )
            )
            return "needs_human"

        if prints and fix_ran and prints == previous_prints:
            reason = f"no_progress: {len(prints)} finding(s) unchanged across cycle {count - 1}"
            # Deliberately NOT mark_sessions_capped_out: these sessions did not
            # cap out, and only a real cap breach may claim they did.
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human"

        payload = {"node_id": node["id"], "cycle": count, "failed_tasks": failed}
        await db.write(
            lambda c, payload=payload: events.append(c, work_item_id, "fix_cycle_started", payload)
        )
        instruction = (
            prompts.FIX_PROMPT.format(node_id=node["id"], failed=", ".join(failed))
            if failed
            else prompts.FIX_PROMPT_FINDINGS_ONLY.format(node_id=node["id"])
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
        instruction += prompts.previous_attempt_note(
            _previous_fix_session(db, work_item_id, node["id"])
        )
        fix = await dispatch.dispatch_node(
            db,
            run_dirs,
            "on.implementation.start",
            node,
            row,
            registry,
            worktree,
            instruction_override=instruction,
            round=count,
            steer=steer,
            launch=launch,
            budget=budget,
            # Ordered deliberately, and only reachable here: the no-progress stop
            # above returns first, so a loop that is stuck never buys a more
            # expensive model for the same blind approach (spec 6).
            escalate=cap.escalate_after is not None and count > cap.escalate_after,
        )
        if fix == "paused":
            return "paused"
        if fix == BUDGET:
            return await stops.stop_for_budget(db, work_item_id, node, budget)
        round = count
        # fix task status is not branched on; loop re-measures


async def bounce(db, work_item_id: str, node: dict, target: str, policy) -> str:
    """Bump the `rebase_bounce` cap and decide whether another bounce to
    `target` is still allowed (Kraft-4bgg). Same `Cap`/`retry_counters`
    machinery a fix_loop's own cap check uses in `walk_node`, just keyed on
    the whole item's rebase bounces rather than one node's fix cycles.
    """
    if policy is None:
        raise RuntimeError(f"node {node['id']!r} has rebase_bounce_to but no policy was provided")
    key = "rebase_bounce"
    cap = _policy.resolve_cap(policy, key)
    count, started_at, cap = await db.write(lambda c: store.bump_counter(c, work_item_id, key, cap))
    if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
        reason = f"{key} exhausted after {count - 1} bounce(s) back to {target!r}"
        capped = {"cycles": count - 1, "attempts": cap.attempts}
        await db.write(
            lambda c: store.mark_needs_human(c, work_item_id, node["id"], reason, capped)
        )
        return "needs_human"
    return "ok"


async def run_once(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    bd_cwd: str | None = None,
    start_index: int = 0,
    policy: _policy.Policy | None = None,
    steer: str | None = None,
    launch: LaunchContext | None = None,
) -> str:
    # the note is good for one agent launch, whichever task gets there first
    carried = Steer(steer)
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
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
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))

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
        )
    except RuntimeError as exc:
        failing_node = nodes[start_index]["id"]
        reason = str(exc)
        await db.write(lambda c: store.enter_node(c, work_item_id, failing_node))
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, failing_node, reason))
        return "needs_human"

    i = start_index
    while i < len(nodes):
        node = nodes[i]
        bounce_to = node.get("rebase_bounce_to")
        pre_base = _current_base_ref(db, work_item_id) if bounce_to else None
        result = await walk_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            registry,
            worktree,
            policy=policy,
            # `carried` empties itself on the first agent launch, so the note reaches
            # the next agent to run and no later one
            steer=carried,
            launch=launch,
        )
        if result == "paused":
            return "paused"
        if result == "needs_human":
            return "needs_human"
        if result == RATE_LIMITED:
            return RATE_LIMITED
        if result == WAITING:
            return WAITING
        if bounce_to:
            new_base = _current_base_ref(db, work_item_id)
            if new_base != pre_base:
                bounced = await bounce(db, work_item_id, node, bounce_to, policy)
                if bounced == "needs_human":
                    return "needs_human"
                target = next(j for j, n in enumerate(nodes) if n["id"] == bounce_to)
                carried = Steer(
                    prompts.rebase_drift_note(worktree, store.branch_for(row), pre_base, new_base)
                )
                i = target
                continue
        if await gates.maybe_gate(db, work_item_id, node):
            return "awaiting_gate"
        i += 1

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    await entry.close_beads(db, row, bd_cwd)
    return "completed"


async def run(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    bd_cwd: str | None = None,
    start_index: int = 0,
    policy: _policy.Policy | None = None,
    steer: str | None = None,
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
        policy=policy,
        steer=steer,
        launch=launch,
    )
    return await gates.review_gates(
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
