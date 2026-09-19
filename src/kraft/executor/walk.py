from __future__ import annotations

import json
from dataclasses import asdict, replace
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
from kraft.executor.dispatch import _current_base_ref
from kraft.store import _now as _now
from kraft.templates import Registry


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

#: Hook kinds whose handler runs inside the orchestrator process, so no
#: commit a fix agent makes in its worktree can change what executes
#: (Kraft-s7c04.24). `subprocess` is deliberately absent: it runs the
#: worktree's own command, which is exactly what a fix commit changes.
_IN_PROCESS_KINDS = ("forge", "builtin")

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


def _failure_note(node: dict, failed: list[str]) -> str:
    """What the orchestrator already knows and the repair would otherwise have
    to go and rediscover (Kraft-s7c04.26: on work item 6c712ea8 a repair agent
    spent 4 of its 16 tool calls hunting for a log it was told to read but
    given no path to).

    The re-measure sentence is not decoration. `recover_node` believes a repair
    only when the node's own tasks pass afterwards -- that has been the design
    since Kraft-rv6i -- but it was never said to the agent, and an agent that
    does not know it will be checked has every incentive to declare success.
    """
    which = ", ".join(failed) if failed else "the node"
    return (
        f"The failing task(s) in node {node['id']}: {which}.\n"
        f"After you finish, {which} will be re-measured and that result, not "
        "your own report, decides whether this repair worked."
    )


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
    measured_round: int,
    round: int = 1,
    loop_severities: frozenset[str] = _policy.DEFAULT_LOOP_SEVERITIES,
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
    found, _reported = dispatch.collect_findings(db, work_item_id, node, measured_round, registry)
    note = _failure_note(node, failed)
    seeded = prompts.seeded_findings_note(found) if found else None
    context = f"{note}\n\n{seeded}" if seeded else note
    if steer is not None and steer:
        # A person's own instruction leads and keeps its own template;
        # `_SEEDED_FINDINGS_STEER`'s lead-in sentence ("Findings the last
        # review of this node left unresolved:") is what stops the bullets
        # below it reading as something that person wrote. `.take()` because
        # the incoming note is folded into the one replacing it -- leaving it
        # undelivered here would deliver it twice (Kraft-s7c04.58 covers the
        # two-hook case this single Steer cannot serve). The orchestrator's
        # own context (`note`, and `seeded` when there are findings) is
        # appended, never substituted for a human's steer.
        repair_steer = Steer(f"{steer.take()}\n\n{context}", source=steer.source)
    else:
        repair_steer = Steer(context, source="seeded")
    # `steps`, not just `tasks`, must name the repair hooks -- `measure_node`
    # reads `steps` first, and a stale `steps` here would re-run the node's
    # own (still-failing) tasks instead of the repair.
    verdict, r_failed, r_excs = await dispatch.measure_node(
        db,
        run_dirs,
        work_item_id,
        {**node, "tasks": hooks, "steps": [hooks]},
        row,
        registry,
        worktree,
        round=round,
        steer=repair_steer,
        launch=launch,
        budget=budget,
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
        registry,
        worktree,
        round=round,
        steer=steer,
        launch=launch,
        budget=budget,
        loop_severities=loop_severities,
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
    # Which severities open a fix cycle -- and therefore, by subtraction, which
    # findings are "deferred" and owed to the human at the review gate
    # (Kraft-s7c04.4). Threaded down to `dispatch_node` rather than defaulted
    # there: a repo that sets `findings.loop_severities` in its own policy.yaml
    # would otherwise get a board card and a review brief computed from
    # different sets, which is exactly the disagreement one shared function was
    # meant to prevent. `policy` is None on a chain built without one.
    loop_severities = policy.loop_severities if policy else _policy.DEFAULT_LOOP_SEVERITIES
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
            loop_severities=loop_severities,
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
        if verdict == BASE_MOVED:
            # Not a failure: the rebase stopped the node deliberately. Complete
            # it like a clean pass -- `run_once` reads the moved base_ref right
            # after this returns and bounces, re-running the node from its
            # first step against the new base.
            await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
            return "ok"
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
                    measured_round=0,
                    loop_severities=loop_severities,
                )
                if verdict == "paused":
                    return "paused"
                if verdict == BUDGET:
                    return await stops.stop_for_budget(db, work_item_id, node, budget)
                recovered = verdict == "ok"
                if not recovered:
                    question = dispatch.needs_context_question(db, work_item_id, node, round=1)
            if not recovered:
                # A rebase conflict raised by `on.mr.rebase` (any node that rebases)
                # is not "for a human to resolve, not to dispatch an agent
                # into" any more (Kraft-s7c04.23, by the human's own call at
                # the design gate) -- the resolver re-creates the conflict and
                # judges whether the upstream commits it rebased onto change
                # what this work item is supposed to do. Gated on the same
                # `auto_escalate_stuck` switch route-door dispatch uses: a
                # resolver is a different mechanism but the same unprompted
                # spend.
                conflict = next((e for e in excs if isinstance(e, _builtins.RebaseConflict)), None)
                if conflict is not None and (policy is None or policy.auto_escalate_stuck):
                    r_status, _steer_text, _new_base = await resolve_rebase_conflict(
                        db,
                        run_dirs,
                        work_item_id=work_item_id,
                        node=node,
                        row=row,
                        registry=registry,
                        worktree=worktree,
                        policy=policy,
                        launch=launch,
                        conflict=str(conflict),
                    )
                    if r_status == "paused":
                        return "paused"
                    if r_status == BUDGET:
                        return await stops.stop_for_budget(db, work_item_id, node, budget)
                    if r_status == "ok":
                        # Completing here is right only when the rebase was the
                        # node's whole job: `run_once`'s `rebase_bounce_to`
                        # check sees the moved base_ref and bounces, and the
                        # steps this node did not run are the ones the bounce
                        # skips. A node with no bounce target wants the
                        # opposite -- its rebase is hygiene, and completing
                        # would mark it done with the later steps never
                        # dispatched.
                        if node.get("rebase_bounce_to"):
                            await db.write(
                                lambda c: store.complete_node(c, work_item_id, node["id"])
                            )
                            return "ok"
                        return await walk_node(
                            db,
                            run_dirs,
                            work_item_id,
                            node,
                            row,
                            registry,
                            worktree,
                            policy=policy,
                            steer=steer,
                            launch=launch,
                        )
                    # "needs_human": the resolver already recorded the stop.
                    return "needs_human"
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
            loop_severities=loop_severities,
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

        if (
            verdict == "failed"
            and node.get("on_failure")
            and _first_iteration
            and not _repair_tried
        ):
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
                measured_round=round,
                loop_severities=loop_severities,
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

        previous_found, fix_ran, previous_head = dispatch.last_measurement(
            db, work_item_id, node["id"]
        )
        head_sha = _config.git_read(Path(worktree), "rev-parse", "HEAD")
        # The tags this loop's own checks already meant: the *eligible* subset of
        # the last measurement. `last_measurement` returns whole findings now
        # (the reviewer is handed their messages, and a repeat's severity is
        # floored against theirs), so the narrowing that used to live in the
        # event payload's `fingerprints` key happens here instead -- identically,
        # so `prints == previous_prints` and `repeats` below are unchanged.
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
        # *this* entry always fixes freely, no judge call (spec decision 2):
        # gated on the loop counter itself having no row yet (the same signal
        # `bump_counter` below uses to tell a fresh fire from a repeat one),
        # not on whether a fix session ever ran for this node before. A
        # carried-in steer gets the same free pass regardless of the counter:
        # it is the human's answer to exactly the trend the judge might stop
        # on, and a `stop_needs_human` here would discard it before the
        # steered cycle it was meant for ever dispatches, re-stranding the
        # item on the trend `/retry` was supposed to escape.
        previous_fix = dispatch.previous_fix_session(db, work_item_id, node["id"])
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
        # `reported_hooks`, not `reported`: it is the set of hook points that
        # wrote a parseable result file, and `blind_failures` below is computed
        # from it. Named for what it holds so nothing else in this function can
        # quietly shadow it -- a draft of Kraft-s7c04.3 did, which turned every
        # failing hook into a blind failure and put the loop beyond the reach of
        # the stuck detector.
        found, reported_hooks = dispatch.collect_findings(db, work_item_id, node, round, registry)
        # A `same_as` is only believable for a tag this round's reviewer was
        # actually shown, and `carried_findings_note` is what showed it. An
        # invented or stale tag would collapse two distinct defects onto one
        # identity and fire the stuck detector on a fiction (Kraft-s7c04.2).
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
            "node_id": node["id"],
            "cycle": round,
            # The commit this measurement is about, so the next round can tell a
            # re-rating of an untouched tree from a real change.
            "head_sha": head_sha,
            "findings": [
                {**asdict(f), **({"reported_severity": s} if s != f.severity else {})}
                for f, s in zip(found, as_reported, strict=True)
            ],
            "fingerprints": prints,
            "noop_hooks": noop_hooks,
        }
        await db.write(
            lambda c, payload=measured_payload: events.append(
                c, work_item_id, "findings_measured", payload
            )
        )

        # A failed task that produced no findings at all is still non-clean: the
        # findings list refines *why* a task failed, it does not define failure.
        blind_failures = [t for t in failed if t not in reported_hooks]
        enters_loop = bool(eligible) or bool(blind_failures)

        if not enters_loop:
            await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
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
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human"

        # A cycle no worker commit could ever win: every blind failure traces
        # to a hook that runs inside this process, and no co-task either
        # failed in a worker-fixable way or reported a finding of its own
        # (Kraft-s7c04.24). Refusing it here, ahead of the budget check, the
        # judge and `bump_counter`, is the same posture as the `CONFIG_ERROR`
        # stop just below: a cycle that cannot be won must not spend an
        # attempt or buy a judge session to tell us what we already know.
        #
        # After `needs_context_question`, not before (ordering correction,
        # found reviewing the plan): a question a fix agent has already asked
        # is addressed to a human and outranks a stop that only says
        # "reinstall the daemon" -- reachable on the shipped default chain
        # when a fix agent's own `needs_context` is followed, one round
        # later, by a re-measure where the in-process hook blind-fails alone.
        in_process_blind = {
            t for t in blind_failures if registry.hooks.get(t, {}).get("kind") in _IN_PROCESS_KINDS
        }
        if (
            in_process_blind
            and set(blind_failures) == in_process_blind
            and all(f.source_plugin in in_process_blind for f in eligible)
        ):
            named = ", ".join(
                prompts.named_with_kind(t, registry) for t in sorted(in_process_blind)
            )
            reason = (
                f"{named} failed in node {node['id']}, and those tasks are executed by "
                "the running Kraft daemon — a worker commit cannot change them. "
                "Reinstall and restart, or skip the node."
            )
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
                    dispatch.judge_history(db, work_item_id, node["id"], policy.loop_severities),
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
                bundle = await _diagnosis_bundle(db, work_item_id, node, worktree)
                # Deliberately NOT mark_sessions_capped_out: these sessions did
                # not cap out, and only a real cap breach may claim they did.
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
        history = dispatch.judge_history(db, work_item_id, node["id"], policy.loop_severities)
        instruction += prompts.round_history_note(history, dispatch.regressed_fingerprints(history))
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
        _first_iteration = False
        # fix task status is not branched on; loop re-measures


#: The round the rebase-conflict resolver writes its session under
#: (Kraft-s7c04.23). Distinct from `_REPAIR_ROUND` because one node can
#: reach both -- `mr_checks` declares `on_failure` (which writes at
#: `_REPAIR_ROUND`) *and* is a legitimate resolver target from the
#: `/resume`/`/retry` route doors, so both would otherwise write sessions at
#: the same round on the same node, and `needs_context_question` does not
#: filter by task (`dispatch.py:713-716`) -- it would read the resolver's row
#: as the repair's.
_RESOLVE_ROUND = -2


async def resolve_rebase_conflict(
    db,
    run_dirs,
    *,
    work_item_id: str,
    node: dict,
    row,
    registry: Registry,
    worktree,
    policy: _policy.Policy | None,
    launch: LaunchContext | None,
    conflict: str,
) -> tuple[str, str | None, str | None]:
    """Dispatch `on.implementation.start` to resolve a rebase conflict and
    judge whether the commits it rebased onto change what this work item is
    supposed to do (design §4.1 -- the second job is the point, not a
    formality).

    Returns `(status, steer_text, new_base)`:
    * `"ok"` -- resolved (`done`/`done_with_concerns`); `new_base` is the sha
      to persist, `steer_text` is the resolver's own concerns, carried
      forward as a `seeded` `Steer`, when it reported `done_with_concerns`.
    * `"needs_human"` -- `needs_context`, `failed`, or a cap breach; the stop
      has already been recorded via `store.mark_needs_human`.
    * `"paused"` / `BUDGET` -- propagate exactly as any other dispatch.
    """
    key = "rebase_conflict"
    cap = (
        _policy.resolve_cap(policy, key)
        if policy is not None
        else _policy.Cap(attempts=3, wall_clock_s=3600)
    )
    count, started_at, cap = await db.write(lambda c: store.bump_counter(c, work_item_id, key, cap))
    if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
        reason = f"{key} exhausted after {count - 1} attempt(s): {conflict}"
        await db.write(
            lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
        )
        return "needs_human", None, None

    branch = store.branch_for(row)
    old_base = _current_base_ref(db, work_item_id)
    new_base = await _builtins.upstream_head(Path(row["repo"]))
    instruction = prompts.rebase_resolve_note(
        worktree,
        branch,
        old_base or "(unknown)",
        new_base or "(unknown)",
        conflict,
        entry.attachments_of(row),
    )
    status = await dispatch.dispatch_node(
        db,
        run_dirs,
        "on.implementation.start",
        node,
        row,
        registry,
        worktree,
        instruction_override=instruction,
        round=_RESOLVE_ROUND,
        launch=launch,
    )
    if status in ("paused", BUDGET):
        return status, None, None
    session = db.read(
        lambda c: c.execute(
            "SELECT result_path FROM worker_sessions WHERE work_item_id = ? AND node_id = ? "
            "AND hook_point = 'on.implementation.start' AND round = ? ORDER BY created_at DESC "
            "LIMIT 1",
            (work_item_id, node["id"], _RESOLVE_ROUND),
        ).fetchone()
    )
    if status in ("done", "done_with_concerns"):
        # An agent that reported success without actually completing the
        # rebase is downgraded to the failure path -- `refresh_worktree_base`
        # cannot be reused here, since it returns None both for "already up
        # to date" and for "nothing to do", which every caller reads as "no
        # movement" and skips `set_base_ref` (design §4.4).
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
        if not moved:
            reason = (
                f"rebase-conflict resolver reported {status!r} without completing "
                f"the rebase: {conflict}"
            )
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human", None, None
        await db.write(lambda c, new_base=new_base: store.set_base_ref(c, work_item_id, new_base))
        steer_text = None
        if status == "done_with_concerns" and session is not None:
            steer_text = _subprocess.read_concerns(Path(session["result_path"]))
        return "ok", steer_text, new_base
    if status == "needs_context":
        question = (
            _subprocess.read_question(Path(session["result_path"])) if session is not None else None
        )
        reason = f"needs_context: {question or '(no question given)'}"
        await db.write(
            lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
        )
        return "needs_human", None, None
    # "failed", or any other status dispatch_node can report for an agent task.
    reason = f"rebase-conflict resolver could not resolve the conflict: {conflict}"
    await db.write(
        lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
    )
    return "needs_human", None, None


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
            repo_entry=launch.repo_entry if launch else None,
        )
    except (RuntimeError, _config.ConfigError) as exc:
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
        if bounce_to:
            new_base = _current_base_ref(db, work_item_id)
            if new_base != pre_base:
                bounced = await bounce(db, work_item_id, node, bounce_to, policy)
                if bounced == "needs_human":
                    return "needs_human"
                target = next(j for j, n in enumerate(nodes) if n["id"] == bounce_to)
                # A bounce is a fresh pass over every node in the span it
                # re-enters, not just the target -- an intervening node's own
                # fix-loop counter (e.g. `mr_checks`' `ci_fix_loop` on a bounce
                # from `merge`) is exactly as stale as the target's
                # (Kraft-s7c04.25 spec §2). Inclusive of the bouncing node
                # itself: bouncing from `merge` re-runs `merge` too.
                cleared = [n["id"] for n in nodes[target : i + 1]]
                for n in nodes[target : i + 1]:
                    await db.write(
                        lambda c, n=n: store.clear_loop_counters(
                            c, work_item_id, n["id"], n.get("fix_loop")
                        )
                    )
                await db.write(
                    lambda c, cleared=cleared, bounce_to=bounce_to: events.append(
                        c,
                        work_item_id,
                        "loop_counters_reset",
                        {"nodes": cleared, "reason": "rebase_bounce", "bounce_to": bounce_to},
                    )
                )
                # `carried` is about to be replaced by Kraft's own rebase note
                # -- if the original steer (human or seeded) survived this
                # far untaken, report it before it's overwritten, or it is
                # lost with no trace (Kraft-s7c04.50, found in spec review).
                await _report_if_undelivered(db, work_item_id, carried)
                carried = Steer(
                    prompts.rebase_drift_note(worktree, store.branch_for(row), pre_base, new_base),
                    # Kraft wrote this one, not a person (the `_REBASE_PROMPT`
                    # template already says so); it must not claim the judge
                    # exemption a human's own answer gets.
                    source="seeded",
                )
                i = target
                continue
        if await gates.maybe_gate(db, work_item_id, node):
            await _report_if_undelivered(db, work_item_id, carried)
            return "awaiting_gate"
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
