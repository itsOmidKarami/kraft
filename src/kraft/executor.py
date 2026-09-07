from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from kraft import builtins as _builtins
from kraft import events, store
from kraft import findings as _findings
from kraft import policy as _policy
from kraft import review as _review
from kraft.adapters import agent as _agent
from kraft.adapters import beads
from kraft.adapters import forge as _forge
from kraft.adapters import subprocess as _subprocess
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.templates import ATTACHMENT_GATES, Registry, Template, materialize

logger = logging.getLogger(__name__)

_FIX_PROMPT = (
    "The checks in node {node_id} failed for this work item. Fix the code so they "
    "pass. Make no unrelated changes. Failing hook points: {failed}"
)

#: Appended when the failures came with structured findings. Repeats lead: an
#: agent told it already tried and the reviewer disagreed behaves differently
#: from one seeing the finding fresh.
_FIX_FINDINGS = "\n\nFindings to fix:\n{findings}"
_FIX_REPEAT_NOTE = (
    "\n\nMarked REPEAT above: you attempted this on an earlier cycle and the check "
    "still reports it. Do not repeat the same approach."
)

#: Handed to fix cycle N > 1: cycle N-1's own result file, by path, not by
#: content -- pasting the file costs tokens on every cycle of every work item,
#: and the agent can already read a path itself (spec §4).
_FIX_PREVIOUS_RESULT = "\n\nYour previous attempt's result file is at {result_path}."
_FIX_PREVIOUS_SUMMARY = " Its session summary is at {summary_ref}."


def _format_findings(found: list[_findings.Finding], repeats: set[str]) -> str:
    lines = []
    for f in found:
        where = f"{f.file}:{f.line}" if f.file and f.line else (f.file or "—")
        tag = "REPEAT " if f.fingerprint in repeats else ""
        lines.append(f"- {tag}[{f.severity}] {where} — {f.message} ({f.source_plugin})")
    return "\n".join(lines)


# A human's note — from the capped card's retry (4b) or a resume after pause (4c) —
# prepended to the next agent launch. It leads because it is the reason this task is
# running again.
_STEER_PROMPT = "A human has steered this run: {steer}\n\n"

#: Statuses that let the chain advance. `done_with_concerns` is deliberately
#: here: the agent finished the work — its doubts are information for the human
#: at the next gate, not a control-flow change.
_ADVANCING = ("done", "done_with_concerns")

# What an agent is told about documents attached at intake. It follows the title
# because the title is the task and these are how it was already decided.
_ATTACHMENT_PROMPT = (
    "\n\n{lines}\nFollow the documents above; they are the agreed spec and plan "
    "for this work item. Do not re-plan."
)


@dataclass(frozen=True)
class LaunchContext:
    """The repo config an agent dispatch resolves against.

    `None` on any field means "nothing configured", not "look elsewhere" —
    `agent.resolve_invocation` already treats a missing repo entry and a missing
    steering dir as empty. Threaded keyword-only, `launch: LaunchContext | None
    = None`, from `api.py` down through every walk/resume path so a work item's
    repo config reaches its agent launches, including reattach and the fix cycle.

    `skills_dir` is where an operator may override a bundled method file;
    `None` means the packaged copies only.
    """

    repo_entry: dict | None
    steering_dir: Path | None
    skills_dir: Path | None = None


class Steer:
    """A steer note, good for exactly one agent launch.

    Both entry points mean the same thing by it — "say this to the next agent you
    start" — so it is carried down the walk and consumed by whichever dispatch
    gets there first, rather than each caller guessing which task that will be.
    """

    def __init__(self, text: str | None = None) -> None:
        self._text = text or None

    def take(self) -> str | None:
        text, self._text = self._text, None
        return text

    def __bool__(self) -> bool:
        return self._text is not None


async def intake(
    db,
    run_dirs,
    *,
    title: str,
    repo: str,
    template: Template,
    bd_cwd: str | None = None,
    submodules: list[str] | None = None,
    root_merge_policy: str = "bump",
    attachments: list[dict] | None = None,
    status: str = "active",
    bead_id: str | None = None,
    bead_cwd: str | None = None,
) -> str:
    work_item_id = uuid.uuid4().hex
    # An auto-intaken bead already exists; filing a second one for the same work
    # is the duplicate this parameter prevents.
    bead_id = bead_id or await beads.intake(title, cwd=bd_cwd)
    satisfied = frozenset(ATTACHMENT_GATES[a["kind"]] for a in attachments or [])
    chain_definition = json.dumps(materialize(template, satisfied_gates=satisfied))
    await db.write(
        lambda c: store.create_work_item(
            c,
            id=work_item_id,
            bead_id=bead_id,
            title=title,
            repo=repo,
            chain_template=template.id,
            chain_definition=chain_definition,
            bead_cwd=bead_cwd,
            submodules=submodules,
            root_merge_policy=root_merge_policy,
            attachments=attachments,
            status=status,
        )
    )
    return work_item_id


def _attachments(work_item_row) -> list[dict]:
    """The row's intake attachments, tolerating a row that predates the column."""
    if "attachments" not in work_item_row.keys():
        return []
    raw = work_item_row["attachments"]
    return json.loads(raw) if raw else []


def _attachment_note(attachments: list[dict]) -> str:
    if not attachments:
        return ""
    lines = "\n".join(f"{a['kind'].capitalize()}: {a['path']}" for a in attachments)
    return _ATTACHMENT_PROMPT.format(lines=lines)


#: `_dispatch` returned without launching because a spend cap was already over.
#: It is not "failed" — the agent never ran, so nothing about it failed — and it
#: outranks a co-task's failure for exactly that reason.
BUDGET = "budget"


def _budget_breach(db, work_item_id: str, budget: _policy.Budget) -> dict | None:
    """The breached cap, or None. Evaluated fresh: it is a query, not a counter.

    A breach refuses the *next* launch. It cannot stop a running agent — cost is
    only known once that agent's session has exited (`usage.read_envelope`) — so
    the overshoot is bounded by the cost of one task, not by the cap.
    """
    if budget.work_item_usd is None and budget.daily_usd is None:
        return None
    since = store.local_midnight_utc() if budget.daily_usd is not None else None
    item_usd, daily_usd = db.read(lambda c: store.budget_spend(c, work_item_id, since=since))
    if budget.work_item_usd is not None and item_usd >= budget.work_item_usd:
        return {"scope": "work_item", "spent_usd": item_usd, "cap_usd": budget.work_item_usd}
    if budget.daily_usd is not None and daily_usd >= budget.daily_usd:
        return {"scope": "daily", "spent_usd": daily_usd, "cap_usd": budget.daily_usd}
    return None


def _budget_reason(breach: dict) -> str:
    where = "this work item" if breach["scope"] == "work_item" else "today, across every work item"
    return (
        f"budget cap reached: ${breach['spent_usd']:.2f} spent on {where}, "
        f"cap ${breach['cap_usd']:.2f}. Nothing new was started; a running agent "
        "was not interrupted."
    )


async def _stop_for_budget(db, work_item_id: str, node: dict, budget: _policy.Budget) -> str:
    # The fallback cannot fire in practice — sums only grow between the dispatch
    # that returned BUDGET and here — but a None would crash the escalation path
    # rather than stop the item, which is the wrong failure.
    breach = _budget_breach(db, work_item_id, budget) or {
        "scope": "work_item",
        "spent_usd": 0.0,
        "cap_usd": 0.0,
    }
    reason = _budget_reason(breach)
    await db.write(
        lambda c: store.mark_needs_human(c, work_item_id, node["id"], reason, None, breach)
    )
    return "needs_human"


#: The hooks whose job is to judge a change rather than make one. They are the
#: only ones handed a review package: everything else is working *in* the diff.
REVIEW_HOOKS = frozenset({"on.review.local.run", "on.review.mr.run"})


def _review_package(
    db, run_dirs, work_item_id: str, worktree, task_hook: str, session_id: str
) -> str | None:
    """The change under review, written out for a reviewer, or None.

    None on every non-review hook, on an item with no `base_ref` (pre-migration
    items and any template with no env_setup node), and on a git failure -- a
    review with no diff is worse than one whose prompt never promised a file.

    `base_ref` is read fresh rather than off the `work_items` row `run` opened
    with: `env_setup` stamps it during the chain's first node, so that row is
    always the pre-stamp one.
    """
    if task_hook not in REVIEW_HOOKS:
        return None
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    if row is None or not row["base_ref"]:
        return None
    path = _review.write_package(run_dirs.results, worktree, row["base_ref"], session_id)
    return str(path) if path else None


async def _dispatch(
    db,
    run_dirs,
    task_hook,
    node,
    work_item_row,
    registry: Registry,
    worktree,
    *,
    instruction_override: str | None = None,
    round: int = 0,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    budget: _policy.Budget = _policy.NO_BUDGET,
    escalate: bool = False,
) -> str:
    binding = registry.hooks[task_hook]
    session_id = uuid.uuid4().hex
    kind = binding["kind"]
    common = dict(
        session_id=session_id,
        work_item_id=work_item_row["id"],
        node_id=node["id"],
        round=round,
    )
    if kind == "builtin" and binding.get("handler") == "env_setup":
        return await _builtins.env_setup(
            db,
            run_dirs,
            repo=work_item_row["repo"],
            attachments=_attachments(work_item_row),
            **common,
        )
    if kind == "builtin" and binding.get("handler") == "noop":
        return await _builtins.noop(db, run_dirs, hook_point=task_hook, **common)
    if kind == "agent":
        # Only agent tasks. A subprocess or builtin costs nothing, and stopping
        # `on.test.run` for a budget would strand the item mid-node for no saving.
        if _budget_breach(db, work_item_row["id"], budget) is not None:
            return BUDGET
        note = steer.take() if steer else None
        instruction = instruction_override or (
            work_item_row["title"] + _attachment_note(_attachments(work_item_row))
        )
        inv = _agent.resolve_invocation(
            binding,
            launch.repo_entry if launch else None,
            launch.steering_dir if launch else None,
            skills_dir=launch.skills_dir if launch else None,
            escalate=escalate,
        )
        return await _agent.run_agent_task(
            db,
            run_dirs,
            hook_point=task_hook,
            review_package=_review_package(
                db, run_dirs, work_item_row["id"], worktree, task_hook, session_id
            ),
            command=inv.command,
            profile=inv.profile,
            model=inv.model,
            deny_tools=inv.deny_tools,
            steering_texts=inv.steering_texts,
            artifact=binding.get("artifact"),
            method_text=inv.method_text,
            title=work_item_row["title"],
            task_instruction=(_STEER_PROMPT.format(steer=note) if note else "") + instruction,
            repo_path=work_item_row["repo"],
            cwd=worktree,
            **common,
        )
    if kind == "subprocess":
        return await _subprocess.run_task(
            db,
            run_dirs,
            hook_point=task_hook,
            cmd=list(binding["command"]),
            cwd=worktree,
            # The fix loop re-runs the test command after an agent edits source in
            # the same worktree. A .pyc written on an earlier cycle has the same
            # second-resolution mtime and (often) size as the fixed source, so
            # CPython would import the stale bytecode and the re-measure would
            # never see the fix. Never writing bytecode keeps every cycle honest.
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            **common,
        )
    if kind == "forge":
        return await _forge.run_task(
            db,
            run_dirs,
            hook_point=task_hook,
            handler=binding["handler"],
            backend=binding["backend"],
            # The worktree, not the repo: every forge CLI resolves the merge
            # request from the *current branch*, and the repo is on whatever
            # the human has checked out.
            repo=worktree,
            branch=f"kraft/{work_item_row['id']}",
            title=work_item_row["title"],
            **common,
        )
    raise RuntimeError(
        f"unhandled binding for {task_hook!r}: kind={kind!r} handler={binding.get('handler')!r}"
    )


async def _measure_node(
    db,
    run_dirs,
    work_item_id,
    node,
    row,
    registry,
    worktree,
    *,
    round: int = 0,
    steer: Steer | None = None,
    launch: LaunchContext | None = None,
    budget: _policy.Budget = _policy.NO_BUDGET,
) -> tuple[str, list[str], list[BaseException]]:
    await db.write(lambda c, node=node: store.enter_node(c, work_item_id, node["id"]))
    tasks = node["tasks"]
    results = await asyncio.gather(
        *(
            _dispatch(
                db,
                run_dirs,
                t,
                node,
                row,
                registry,
                worktree,
                round=round,
                steer=steer,
                launch=launch,
                budget=budget,
            )
            for t in tasks
        ),
        return_exceptions=True,
    )
    # A pause stops the walk where it stands: the node is neither done nor failed,
    # and resume relaunches it. It outranks a co-task's failure, which was almost
    # certainly the same SIGTERM arriving on a different row.
    if any(r == "paused" for r in results):
        return "paused", [], []
    # Logged before the BUDGET rung returns: a co-task can raise in the same node
    # as a budget-refused agent, and that traceback is the only record of it.
    excs = [r for r in results if isinstance(r, BaseException)]
    for exc in excs:
        logger.error("measuring task raised in node %s: %r", node["id"], exc)
    # paused > budget > failed. A pause is a human's instruction and outranks
    # everything. A budget breach outranks a co-task's failure because the agent
    # never ran, so its "failure" is not evidence about the code.
    if any(r == BUDGET for r in results):
        return BUDGET, [], []
    failed = [
        tasks[i]
        for i, r in enumerate(results)
        if isinstance(r, BaseException) or r in ("failed", "needs_context")
    ]
    if failed:
        return "failed", failed, excs
    return "ok", [], []


def _collect_findings(db, work_item_id: str, node: dict, round: int):
    """(findings, hook points that reported at least one) for one cycle.

    Only the node's own measuring tasks: the fix task is dispatched with
    `round=count` and the next measuring pass runs at that same round, so an
    unfiltered query folds the fix agent's result file into the cycle. Only the
    most recent row per hook point, because a resume can re-enter this node with
    `round` reset while stale rows sit at the same number.
    """
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["hook_point"] in node["tasks"]:
            latest[row["hook_point"]] = row  # ordered by created_at, so last wins
    found: list[_findings.Finding] = []
    reported: set[str] = set()
    for hook, row in latest.items():
        parsed = _findings.parse(row["result_path"])
        if parsed:
            reported.add(hook)
        found.extend(parsed)
    return found, reported


def _needs_context_question(db, work_item_id: str, node: dict, round: int) -> str | None:
    """The question from a `needs_context` row in this round, or None.

    Same latest-row-per-hook-point read as `_collect_findings` (a resume or
    `/retry` re-enters at round 0 with stale rows still sitting there, so a
    first-match scan could re-stop the item on a historical row forever) but
    deliberately NOT its `row["hook_point"] in node["tasks"]` filter: the fix
    task (`on.implementation.start`) is dispatched with this same round, and
    including it is exactly how a fix task's own `needs_context` is meant to
    surface, one iteration later.
    """
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest[row["hook_point"]] = row  # ordered by created_at, so last wins
    for row in latest.values():
        if row["status"] == "needs_context":
            return _subprocess.read_question(Path(row["result_path"])) or "(no question given)"
    return None


def _previous_fix_session(db, work_item_id: str, node_id: str) -> sqlite3.Row | None:
    """The most recently dispatched fix task for this node, or None if none
    has run yet.

    Deliberately NOT scoped to a `round` passed in by the caller: `round` is
    `_walk_node`'s own local counter, reset to 0 on every fresh entry into that
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


def _previous_attempt_note(previous_fix: sqlite3.Row | None) -> str:
    """The fix-prompt addendum for `_previous_fix_session`'s return, or "" if
    there was no previous attempt. A plain function so the no-summary branch
    is testable without driving a session through the executor."""
    if previous_fix is None:
        return ""
    note = _FIX_PREVIOUS_RESULT.format(result_path=previous_fix["result_path"])
    if previous_fix["session_summary_ref"]:
        note += _FIX_PREVIOUS_SUMMARY.format(summary_ref=previous_fix["session_summary_ref"])
    return note


def _last_measurement(db, work_item_id: str, node_id: str) -> tuple[list[str] | None, bool]:
    """This node's most recent `findings_measured` fingerprints, and whether a
    `fix_cycle_started` for the node followed it.

    Read from the event log rather than carried in a local: `_reconcile_current_node`
    re-enters `_walk_node` after a crash or a resume with the counter intact, and a
    loop holding its history in the stack frame forgets everything it has seen —
    on exactly the path that motivates escalation.

    The two return values answer two different questions, which is why they are
    not collapsed into one. REPEAT marking asks only "was this finding in the
    last measurement", so it uses the fingerprints unconditionally. No-progress
    escalation additionally requires the fix flag: spec §4's "no progress" means
    a fix cycle ran and changed nothing — not merely that the same code was
    measured twice in a row. A `POST /retry` on a no-progress stop (or a
    crash/resume between the escalating findings_measured and the fix it never
    got to dispatch) re-enters at round 0 and measures before it fixes; without
    the flag, a deterministic reviewer seeing unchanged code would report the
    same fingerprints and the loop would escalate straight back to needs_human
    without ever giving the steered retry a chance to run.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    fix_seen = False
    for e in reversed(evts):
        if e["type"] == "fix_cycle_started" and e["payload"].get("node_id") == node_id:
            fix_seen = True
        elif e["type"] == "findings_measured" and e["payload"].get("node_id") == node_id:
            return e["payload"].get("fingerprints"), fix_seen
    return None, False


async def _walk_node(
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
    # policy, so no call site can forget the cap and silently lose it.
    budget = policy.budget if policy else _policy.NO_BUDGET

    if not key:
        verdict, failed, excs = await _measure_node(
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
        if verdict == BUDGET:
            return await _stop_for_budget(db, work_item_id, node, budget)
        if verdict == "failed":
            question = _needs_context_question(db, work_item_id, node, round=0)
            if question is not None:
                reason = f"needs_context: {question}"
            else:
                reason = f"task failed in node {node['id']}: {', '.join(failed)}"
                if excs:
                    reason += f" ({', '.join(repr(e) for e in excs)})"
            await db.write(lambda c: store.mark_needs_human(c, work_item_id, node["id"], reason))
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
        verdict, failed, _excs = await _measure_node(
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
        if verdict == BUDGET:
            return await _stop_for_budget(db, work_item_id, node, budget)

        previous_prints, fix_ran = _last_measurement(db, work_item_id, node["id"])
        found, reported = _collect_findings(db, work_item_id, node, round)
        eligible = [f for f in found if f.severity in policy.loop_severities]
        prints = sorted({f.fingerprint for f in eligible})
        await db.write(
            lambda c, r=round, found=found, prints=prints: events.append(
                c,
                work_item_id,
                "findings_measured",
                {
                    "node_id": node["id"],
                    "cycle": r,
                    "findings": [asdict(f) for f in found],
                    "fingerprints": prints,
                },
            )
        )

        # A failed task that produced no findings at all is still non-clean: the
        # findings list refines *why* a task failed, it does not define failure.
        blind_failures = [t for t in failed if t not in reported]
        enters_loop = bool(eligible) or bool(blind_failures)

        if verdict == "ok" or not enters_loop:
            await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
            return "ok"

        # Checked before bump_counter, not after: the counter is bumped to
        # dispatch the fix task, so only a *measuring* task's needs_context can
        # land here without ever consuming a cycle. A needs_context from the
        # fix task itself surfaces on the next iteration's measure, at which
        # point the cycle it belongs to has already been bumped.
        question = _needs_context_question(db, work_item_id, node, round)
        if question is not None:
            reason = f"needs_context: {question}"
            await db.write(
                lambda c, reason=reason: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human"

        # Also ahead of the counter bump and the `fix_cycle_started` event: a
        # refused launch must not spend a fix-loop attempt or leave a cycle in
        # the log for an agent that never ran. The check inside `_dispatch`
        # stays as the backstop for any future agent task reached by another
        # path.
        #
        # After needs_context, not before: a question a task actually asked is
        # the more useful thing to hand a human, and raising the cap would not
        # answer it. Either order prevents the phantom bump, which is the part
        # that matters; only the reason on the card differs.
        if _budget_breach(db, work_item_id, budget) is not None:
            return await _stop_for_budget(db, work_item_id, node, budget)

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
        instruction = _FIX_PROMPT.format(node_id=node["id"], failed=", ".join(failed))
        if eligible:
            repeats = set(previous_prints or [])
            instruction += _FIX_FINDINGS.format(findings=_format_findings(eligible, repeats))
            # The tag is unconditional -- "this finding was in the last
            # measurement" is true either way -- but the note claims an earlier
            # attempt was made, which is false on the crash/resume path where
            # the measurement was recorded and the process died before any
            # fix_cycle_started.
            if fix_ran and repeats & set(prints):
                instruction += _FIX_REPEAT_NOTE
        instruction += _previous_attempt_note(_previous_fix_session(db, work_item_id, node["id"]))
        fix = await _dispatch(
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
            return await _stop_for_budget(db, work_item_id, node, budget)
        round = count
        # fix task status is not branched on; loop re-measures


def _gate_cleared(db, work_item_id: str, gate: str) -> bool:
    """True iff the most recent gate_* event for the item is gate_approved <gate>."""
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected"):
            return e["type"] == "gate_approved" and e["payload"].get("gate") == gate
    return False


async def _maybe_gate(db, work_item_id: str, node: dict) -> bool:
    """If the node ends in a gate, request it and return True (caller stops the walk)."""
    gate = node.get("gate_after")
    if not gate:
        return False
    await db.write(lambda c: store.request_gate(c, work_item_id, node["id"], gate))
    return True


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
    # branch in `resume`), and the WS gets fewer events than a client
    # waiting on this chain to progress at all is entitled to expect.
    if start_index == 0:
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))

    # Before the first dispatch, not inside the `env_setup` node: `default.yaml`
    # runs `spec` and `plan` first, and both need a checkout — and, for `plan`'s
    # attached-spec fallback to have anything to find, attachments already
    # copied in — to write into.
    #
    # A git failure here (bad repo, no permission, ...) is attributed to the
    # node that was about to dispatch rather than left to `api._guard`'s bare
    # crash handler: this call moved out of `env_setup`'s own session so spec
    # and plan can use the checkout too, but the observable shape of "a node
    # failed to start" -- node_started, then needs_human naming it -- should
    # not disappear just because the failure now happens a moment earlier.
    try:
        worktree = await _builtins.ensure_worktree(
            db, run_dirs, repo=row["repo"], work_item_id=work_item_id, attachments=_attachments(row)
        )
    except RuntimeError as exc:
        failing_node = nodes[start_index]["id"]
        reason = str(exc)
        await db.write(lambda c: store.enter_node(c, work_item_id, failing_node))
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, failing_node, reason))
        return "needs_human"

    for node in nodes[start_index:]:
        result = await _walk_node(
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
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    try:
        await beads.complete(row["bead_id"], cwd=row["bead_cwd"] or bd_cwd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
    return "completed"


async def _reconcile_current_node(
    db,
    run_dirs,
    work_item_id,
    node,
    row,
    registry,
    worktree,
    adopted,
    *,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
) -> str:
    node_id = node["id"]
    sessions = db.read(
        lambda c: c.execute(
            "SELECT id, status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )

    if node.get("fix_loop"):
        # A fix_loop node that has run >=1 cycle keeps cycle-0's permanently-failed
        # measuring session plus extra fix / re-measure sessions, so the
        # len(final)==len(tasks) & all-done clean-check below is structurally
        # unsatisfiable and would wrongly escalate on every crash-recovery. The
        # loop is its own reconciliation: await any adopted in-flight session,
        # then re-enter _walk_node. The surviving retry_counters row continues the
        # wall-clock budget from its original started_at (spec §2.C, §6.4, §9).
        for s in sessions:
            task = adopted.get(s["id"])
            if task is not None:
                await task
        return (
            "ok"
            if await _walk_node(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                registry,
                worktree,
                policy=policy,
                launch=launch,
            )
            == "ok"
            else "needs_human"
        )

    if not sessions:
        # crash landed between enter_node and the first create_session; safe to
        # re-dispatch because current_node_id only advances with node_completed.
        return (
            "ok"
            if await _walk_node(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                registry,
                worktree,
                policy=policy,
                launch=launch,
            )
            == "ok"
            else "needs_human"
        )

    for s in sessions:
        task = adopted.get(s["id"])
        if task is not None:
            await task

    final = db.read(
        lambda c: c.execute(
            "SELECT status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )
    # ponytail: single-task-node resume only. A crash mid-fan-out of a multi-task
    # node (fewer sessions than tasks, none failed) -> needs_human, no partial
    # re-dispatch. Upgrade with per-task session reconciliation if multi-task
    # nodes ship.
    if len(final) == len(node["tasks"]) and all(r["status"] in _ADVANCING for r in final):
        await db.write(lambda c: store.complete_node(c, work_item_id, node_id))
        return "ok"
    await db.write(
        lambda c: store.mark_needs_human(
            c, work_item_id, node_id, "resume: current-node session did not resolve cleanly"
        )
    )
    return "needs_human"


async def resume(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    adopted: dict,
    bd_cwd: str | None = None,
    policy: _policy.Policy | None = None,
    launch: LaunchContext | None = None,
) -> str:
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
    cur = row["current_node_id"]

    if cur is None:
        # crash between create_work_item and the first load_chain; nothing ran.
        # Recorded before ensure_worktree below for the same reason `run` records
        # it first: a worktree that can never be created must not leave the item
        # looking like it crashed before load_chain ever happened.
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))
        cur = nodes[0]["id"]

    # Before the first dispatch, not inside the `env_setup` node: `default.yaml`
    # runs `spec` and `plan` first, and both need a checkout — and, for `plan`'s
    # attached-spec fallback to have anything to find, attachments already
    # copied in — to write into.
    #
    # See the matching comment in `run`: a git failure here is attributed to
    # the current node rather than left to `api._guard`'s bare crash handler.
    try:
        worktree = await _builtins.ensure_worktree(
            db, run_dirs, repo=row["repo"], work_item_id=work_item_id, attachments=_attachments(row)
        )
    except RuntimeError as exc:
        reason = str(exc)
        await db.write(lambda c: store.enter_node(c, work_item_id, cur))
        await db.write(lambda c: store.mark_needs_human(c, work_item_id, cur, reason))
        return "needs_human"

    start = next(i for i, n in enumerate(nodes) if n["id"] == cur)

    node0 = nodes[start]
    gate0 = node0.get("gate_after")
    if gate0 and _gate_cleared(db, work_item_id, gate0):
        # the gate was approved before the crash; do not reconcile or re-request it.
        start += 1
        if start >= len(nodes):
            await db.write(lambda c: store.mark_completed(c, work_item_id))
            try:
                await beads.complete(row["bead_id"], cwd=row["bead_cwd"] or bd_cwd)
            except Exception as exc:  # noqa: BLE001
                logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
            return "completed"
        # fall through: reconcile from the post-gate node instead

    if (
        await _reconcile_current_node(
            db,
            run_dirs,
            work_item_id,
            nodes[start],
            row,
            registry,
            worktree,
            adopted,
            policy=policy,
            launch=launch,
        )
        == "needs_human"
    ):
        return "needs_human"

    if await _maybe_gate(db, work_item_id, nodes[start]):
        return "awaiting_gate"

    for node in nodes[start + 1 :]:
        if (
            await _walk_node(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                registry,
                worktree,
                policy=policy,
                launch=launch,
            )
            == "needs_human"
        ):
            return "needs_human"
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    try:
        await beads.complete(row["bead_id"], cwd=row["bead_cwd"] or bd_cwd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
    return "completed"
