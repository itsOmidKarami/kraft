from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from kraft import builtins as _builtins
from kraft import events, store
from kraft import policy as _policy
from kraft.adapters import beads
from kraft.executor import dispatch, entry, gates, prompts, stops
from kraft.executor.context import (
    BUDGET,
    CONFIG_ERROR,
    INFRA_STOP,
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


async def _diagnosis_bundle(db, work_item_id: str, node: dict, worktree) -> dict:
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
    # (JUDGE_HOOK, told by JUDGE_PROMPT/SKILL.md that concerns is required on
    # every verdict) and the measuring session it judged both write concerns
    # events, and the judge's always lands last in the same walk_node
    # iteration. Joined against worker_sessions here to skip the judge's own
    # sessions, so this stays the measuring session's concerns, not the
    # judge's reasoning about them.
    judge_session_ids = db.read(
        lambda c: {
            r["id"]
            for r in c.execute(
                "SELECT id FROM worker_sessions WHERE work_item_id = ? AND hook_point = ?",
                (work_item_id, dispatch.JUDGE_HOOK),
            ).fetchall()
        }
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
    round: int = 1,
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

    `round` keys the repair hook's dispatch and its re-measure in
    `sessions_for_round` (`dispatch.collect_findings`,
    `dispatch.needs_context_question`) -- it must not collide with any other
    round the *same node* writes sessions under. The no-fix-loop branch's call
    below needs nothing special: a node with `on_failure` and no `fix_loop`
    never runs `walk_node`'s paid-cycle loop at all, so `round=1` (the
    default) can never alias anything else for that node. The fix-loop
    branch, added in a later task, passes its own reserved round instead,
    because for a node with *both* `fix_loop` and `on_failure` (Task 6 allows
    this now; it used to be forbidden precisely to dodge this collision)
    `round=1` already means "the measurement after the first paid fix cycle"
    to that same node's `collect_findings`/`needs_context_question` calls.
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
    verdict, r_failed, r_excs = await dispatch.measure_node(
        db,
        run_dirs,
        work_item_id,
        {**node, "tasks": hooks},
        row,
        registry,
        worktree,
        round=round,
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
        round=round,
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
        if verdict == INFRA_STOP:
            return await stops.stop_for_infra(db, work_item_id, node)
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

    override_row = fresh_row if fresh_row is not None else row
    cap = _policy.resolve_cap(policy, key, store.node_overrides_of(override_row).get(node["id"]))
    # `round` is the fix-cycle index every session in this pass is stamped with,
    # so per-round usage can be read back without joining against the events.
    round = 0
    # One repair per call to `walk_node` -- exactly one per entry into the
    # node (a fresh call on every re-entry via `run_once`'s loop, a crash
    # resume, or the `ci_wait` poller).
    _repair_tried = False
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
        if verdict == INFRA_STOP:
            return await stops.stop_for_infra(db, work_item_id, node)
        if verdict == BUDGET:
            return await stops.stop_for_budget(db, work_item_id, node, budget)

        if verdict == "failed" and node.get("on_failure") and round == 0 and not _repair_tried:
            # Once per entry into this node, and only ahead of the very first
            # cycle: `recover_node` re-measures for real (on.ci.poll going
            # green), so a repair that resolved things costs nothing further
            # here, and one that didn't falls straight through to the normal
            # fix cycle below with the failure it actually is.
            _repair_tried = True
            r_verdict, r_failed, _r_excs = await recover_node(
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
                round=_REPAIR_ROUND,
            )
            if r_verdict == "paused":
                return "paused"
            # `recover_node`'s re-measure is a real `dispatch.measure_node` call
            # against the node's own tasks again -- every sentinel that call can
            # produce for the ordinary top-of-loop measure two lines above this
            # block can just as well come back from here: a fix loop that fell
            # through the WAITING case, in particular, would spend a paid fix
            # cycle on a pipeline that has not even finished settling for the
            # repaired head, every time the repair itself doesn't resolve
            # things on the first try (the common case for a code failure,
            # since a metadata-only repair almost never touches the code CI
            # actually ran). Checked and handled here, in the same order and
            # against the same `stops` functions `walk_node`'s own top-of-loop
            # checks above already use for these sentinels, so a repair's
            # re-measure stops exactly the way an ordinary measure would have.
            if r_verdict == CONFIG_ERROR:
                named = ", ".join(r_failed)
                reason = f"could not start {named} in node {node['id']} — see the session log"
                await db.write(
                    lambda c, reason=reason: store.mark_needs_human(
                        c, work_item_id, node["id"], reason
                    )
                )
                return "needs_human"
            if r_verdict == RATE_LIMITED:
                return await stops.stop_for_rate_limit(db, work_item_id, node)
            if r_verdict == WAITING:
                return await stops.stop_for_waiting(db, work_item_id, node)
            if r_verdict == INFRA_STOP:
                return await stops.stop_for_infra(db, work_item_id, node)
            if r_verdict == BUDGET:
                return await stops.stop_for_budget(db, work_item_id, node, budget)
            if r_verdict == "ok":
                await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
                return "ok"
            # Only "failed" reaches here (`dispatch.measure_node`'s remaining
            # sentinel), and that fall-through is deliberate: the repair ran,
            # didn't resolve things, and the ordinary paid fix cycle below
            # should now see the failure for what it actually is.
            #
            # The repair's own re-measure just wrote its findings/session rows
            # under `_REPAIR_ROUND`, not the `round` this loop was on when it
            # entered this block (0, on the only entry this ever fires for) --
            # the fall-through below reads `collect_findings`/
            # `needs_context_question` at `round`, so `round` has to move to
            # match, or those calls read the stale pre-repair rows instead of
            # the fresher re-measure this repair just produced.
            verdict, failed, round = r_verdict, r_failed, _REPAIR_ROUND

        previous_prints, fix_ran = dispatch.last_measurement(db, work_item_id, node["id"])
        # `_previous_fix_session` is history-wide, not scoped to this
        # `walk_node` entry -- a `/retry` or an auto-escalation retry clears
        # the loop counter (`retry_counters` row deleted) but leaves the old
        # `worker_sessions` rows in place, so it alone can't tell "round 1 of
        # this entry" from "round 5 of the item's whole history". Round 1 of
        # *this* entry always fixes freely, no judge call (spec decision 2):
        # gated on the loop counter itself having no row yet (the same signal
        # `bump_counter` below uses to tell a fresh fire from a repeat one),
        # not on whether a fix session ever ran for this node before. A
        # carried-in steer gets the same free pass regardless of the counter:
        # it is the human's answer to exactly the trend the judge might stop
        # on, and a `stop_needs_human` here would discard it before the
        # steered cycle it was meant for ever dispatches, re-stranding the
        # item on the trend `/retry` was supposed to escape.
        previous_fix = _previous_fix_session(db, work_item_id, node["id"])
        counter_row = db.read(lambda c: store.read_counter(c, work_item_id, key))
        # Only a *human*-authored steer gets the free pass the comment above
        # describes. A seeded one (Kraft's own recap of findings the last
        # review already left, Kraft-7sec second half) is not a person's
        # deliberate answer to the trend this judge exists to brake, so it
        # must not silently stand in for one (review finding on this plan).
        judge_due = (
            previous_fix is not None and counter_row is not None and not (steer and steer.human)
        )
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

        # Fix-loop judge (2026-09-12-verify-fix-loop-judge-design): a brake on
        # top of the existing cap, never a second way to get stuck. Anything
        # the judge itself cannot be trusted on already fell open to
        # "continue" inside `dispatch.judge_verdict`; the cap/stuck checks
        # below run exactly as they do today regardless of what runs here.
        if judge_due:
            verdict, reasoning = await dispatch.judge_verdict(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                registry,
                worktree,
                round=round,
                key=key,
                eligible=eligible,
                policy=policy,
                launch=launch,
                budget=budget,
            )
            if verdict == "paused":
                return "paused"
            judge_payload = {
                "node_id": node["id"],
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
                reason = f"judge: {reasoning}"
                await db.write(
                    lambda c, reason=reason: store.mark_needs_human(
                        c, work_item_id, node["id"], reason
                    )
                )
                return "needs_human"
            # "as if there were no eligible findings" (spec) -- a task that
            # failed outright (blind or not: on.ci.poll on a red pipeline
            # reports findings *and* fails) would still be in the loop once
            # its eligible findings are downgraded, so gate on `failed`
            # itself rather than on the blind subset. Only a task with no
            # failure at all can be treated as clean here.
            if verdict == "stop_downgrade" and not failed:
                await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
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
            reason = f"stuck: {len(prints)} finding(s) unchanged across cycle {count - 1}"
            bundle = await _diagnosis_bundle(db, work_item_id, node, worktree)
            # Deliberately NOT mark_sessions_capped_out: these sessions did not
            # cap out, and only a real cap breach may claim they did.
            await db.write(
                lambda c, reason=reason, bundle=bundle: store.mark_needs_human(
                    c, work_item_id, node["id"], reason, bundle=bundle
                )
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
        instruction += prompts.previous_attempt_note(previous_fix)
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
    steer_seeded: bool = False,
    launch: LaunchContext | None = None,
) -> str:
    # the note is good for one agent launch, whichever task gets there first
    carried = Steer(steer, human=not steer_seeded)
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
    # The real edges live on the source beads named in the description,
    # extracted at intake into `implements_beads` (`entry._extract_beads`).
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
                    c, work_item_id, nodes[start_index]["id"], blocked_by
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
        )
    except RuntimeError as exc:
        failing_node = nodes[start_index]["id"]
        reason = str(exc)
        await db.write(lambda c: store.enter_node(c, work_item_id, failing_node))
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, failing_node, reason))
        return "needs_human"

    i = start_index
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
    steer_seeded: bool = False,
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
        steer_seeded=steer_seeded,
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
