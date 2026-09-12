from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import shlex
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from kraft import builtins as _builtins
from kraft import config as _config
from kraft import events, store
from kraft import findings as _findings
from kraft import policy as _policy
from kraft.adapters import agent as _agent
from kraft.adapters import forge as _forge
from kraft.adapters import subprocess as _subprocess
from kraft.executor import entry, prompts, stops
from kraft.executor.context import (
    BUDGET,
    CONFIG_ERROR,
    INFRA_STOP,
    RATE_LIMITED,
    WAITING,
    LaunchContext,
    Steer,
)
from kraft.store import _now as _now
from kraft.templates import Registry

logger = logging.getLogger(__name__)


def _current_base_ref(db, work_item_id: str) -> str | None:
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    return row["base_ref"] if row else None


def _path_matches(path: str, patterns: list[str]) -> bool:
    """Gitignore-style precedence over one scope's own `paths` list: the last
    pattern `path` matches wins, so a later `!excl/**` overrides an earlier
    inclusive glob (test-scope design §3.4)."""
    matched = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if fnmatch.fnmatchcase(path, pattern[1:]):
                matched = False
        elif fnmatch.fnmatchcase(path, pattern):
            matched = True
    return matched


def _matched_scopes(scopes: list[dict], changed_paths: list[str]) -> list[dict]:
    """Scopes covering `changed_paths`, deduped and in `scopes`' own order.

    Fails open in both directions the design calls out: a changed path
    matching no scope at all, or an empty diff to begin with, runs every
    scope rather than guess. Under-testing is the bug this exists to close;
    it must never reopen it here (test-scope design §3.5).
    """
    hit: set[int] = set()
    for path in changed_paths:
        path_hit = {i for i, scope in enumerate(scopes) if _path_matches(path, scope["paths"])}
        if not path_hit:
            return list(scopes)
        hit |= path_hit
    return [scopes[i] for i in sorted(hit)] if hit else list(scopes)


async def dispatch_node(
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
    # The commit the measurement is about (Kraft-lu2). Resolved here, once, at
    # dispatch: a sha read later would be whatever HEAD moved to while the task
    # ran, which is the opposite of the question the gate asks. `git_read`
    # never raises -- None for on.env.prepare, whose worktree does not exist yet.
    common = dict(
        session_id=session_id,
        work_item_id=work_item_row["id"],
        node_id=node["id"],
        round=round,
        head_sha=_config.git_read(Path(worktree), "rev-parse", "HEAD"),
    )
    if kind == "builtin" and binding.get("handler") == "env_setup":
        return await _builtins.env_setup(
            db,
            run_dirs,
            repo=work_item_row["repo"],
            attachments=entry.attachments_of(work_item_row),
            **common,
        )
    if kind == "builtin" and binding.get("handler") == "noop":
        return await _builtins.noop(db, run_dirs, hook_point=task_hook, **common)
    if kind == "builtin" and binding.get("handler") == "scan_submodules":
        return await _builtins.scan_submodules(
            db,
            run_dirs,
            hook_point=task_hook,
            repo=work_item_row["repo"],
            worktree=str(worktree),
            **common,
        )
    if kind == "builtin" and binding.get("handler") == "mr_rebase":
        return await _builtins.mr_rebase(
            db,
            run_dirs,
            hook_point=task_hook,
            repo=work_item_row["repo"],
            worktree=str(worktree),
            branch=store.branch_for(work_item_row),
            **common,
        )
    if kind == "agent":
        # Only agent tasks. A subprocess or builtin costs nothing, and stopping
        # `on.test.run` for a budget would strand the item mid-node for no saving.
        if stops.budget_breach(db, work_item_row["id"], budget) is not None:
            return BUDGET
        note = steer.take() if steer else None
        attachment_note = prompts.attachment_note(entry.attachments_of(work_item_row))
        instruction = (
            instruction_override
            or (
                prompts.brief(work_item_row)
                + attachment_note
                + prompts.progress_note(task_hook, work_item_row, worktree)
            )
        ) + prompts.BEAD_NOTE
        inv = _agent.resolve_invocation(
            binding,
            launch.repo_entry if launch else None,
            launch.steering_dir if launch else None,
            skills_dir=launch.skills_dir if launch else None,
            escalate=escalate,
            item_override=(
                json.loads(work_item_row["agent_overrides"])
                if work_item_row["agent_overrides"]
                else None
            ),
        )
        status = await _agent.run_agent_task(
            db,
            run_dirs,
            hook_point=task_hook,
            review_package=prompts.review_package(
                db, run_dirs, work_item_row["id"], worktree, task_hook, session_id
            ),
            command=inv.command,
            profile=inv.profile,
            model=inv.model,
            deny_tools=inv.deny_tools,
            effort=inv.effort,
            allowed_tools=inv.allowed_tools,
            permission_mode=inv.permission_mode,
            steering_texts=inv.steering_texts,
            artifact=binding.get("artifact"),
            method_text=inv.method_text,
            title=work_item_row["title"],
            task_instruction=(
                prompts.steer_prefix(binding, work_item_row, worktree, note) if note else ""
            )
            + instruction,
            repo_path=work_item_row["repo"],
            cwd=worktree,
            **common,
        )
        # The agent is told to commit everything it changes before it exits.
        # When it does not, the work is still on disk -- so `verify` passes,
        # and only `_assert_clean` two nodes later notices, by which point the
        # failure names a hook rather than the cause and a human has to type
        # `git commit` in someone else's worktree (Kraft-7fip). Kraft owns the
        # worktree, so it takes the work rather than reporting it missing.
        #
        # Never at the cost of the run itself: an index lock a co-task holds, a
        # submodule that `add -A` finds nothing to stage in -- either of those
        # would turn a *successful* agent task into a failed node, and on the
        # fix-loop's direct dispatch would escape `run()` entirely. (Unset
        # `user.email` used to be on this list too; `ensure_worktree` now pins
        # identity before any node dispatches, so it is structurally prevented
        # rather than tolerated here -- Kraft-cppp.) Losing the sweep only puts
        # us back where Kraft-7fip found us: the work is still on disk and
        # `_assert_clean` names it at open_mr.
        # Before the sweep, not after: a straggler committed while HEAD sat on
        # a diagnostic branch an agent forgot to check out of would land on
        # that branch instead of the item's own (Kraft-v5qd).
        _builtins.restore_branch(Path(worktree), store.branch_for(work_item_row))
        try:
            await _forge.commit_stragglers(
                Path(worktree), message=f"wip: uncommitted work from {node['id']}"
            )
        except _forge.ForgeError as exc:
            logger.warning("could not commit stragglers after %s: %r", task_hook, exc)
            # A log line only reaches whoever is tailing the server at the
            # time. The failure it describes doesn't surface again until
            # `_assert_clean` refuses `open_mr`, nodes later, with no trail
            # back to why the work was left uncommitted (Kraft-hf12) -- so a
            # human debugging that refusal has something to find.
            await db.write(
                lambda c, task_hook=task_hook, exc=exc: events.append(
                    c,
                    work_item_row["id"],
                    "sweep_failed",
                    {"node_id": node["id"], "task_hook": task_hook, "error": str(exc)},
                )
            )
        return status
    if kind == "subprocess":
        # The repo's own command(s) win over the registry's. The registry is
        # per install and one command for every repo on it; test_scopes is a
        # property of the repo, and a hardcoded single command is how verify
        # ends up running something CI does not, or the wrong stack's suite
        # entirely (Kraft-579, Kraft-9wzy). Same source the forge branch below
        # reads for `forge`. `config.load_repos` already wraps a legacy
        # `test_command` into a single `["**"]` scope, so this is one shape
        # regardless of which field an operator set.
        repo_entry = (launch.repo_entry or {}) if launch else {}
        repo_scopes = repo_entry.get("test_scopes")
        if not repo_scopes and repo_entry.get("test_command"):
            # `config.load_repos` already wraps a bare `test_command` into a
            # `test_scopes` entry for any repo it reads off disk -- this
            # mirrors that for a `LaunchContext` built by hand (tests, or any
            # future caller that skips the yaml round-trip).
            repo_scopes = [{"paths": ["**"], "command": repo_entry["test_command"]}]
        scopes = (
            [{"paths": s["paths"], "cmd": shlex.split(s["command"])} for s in repo_scopes]
            if repo_scopes
            else [{"paths": ["**"], "cmd": list(binding["command"])}]
        )
        # test-scope design §3.2-3.3: the diff since base_ref decides which of
        # those scopes actually apply. Re-queried fresh rather than trusting
        # `work_item_row`, matching `prompts.review_package`'s base_ref read
        # above -- `work_item_row` can predate `env_setup`'s stamp. `git_read`
        # never raises; a git failure or missing base_ref means "cannot tell",
        # which fails open to every scope rather than guessing at fewer.
        base_ref = _current_base_ref(db, work_item_row["id"])
        diff = (
            _config.git_read(Path(worktree), "diff", "--name-only", f"{base_ref}...HEAD")
            if base_ref
            else None
        )
        to_run = scopes if diff is None else _matched_scopes(scopes, diff.splitlines())
        status = "done"
        for scope in to_run:
            status = await _subprocess.run_task(
                db,
                run_dirs,
                hook_point=task_hook,
                cmd=scope["cmd"],
                cwd=worktree,
                # The fix loop re-runs the test command after an agent edits source in
                # the same worktree. A .pyc written on an earlier cycle has the same
                # second-resolution mtime and (often) size as the fixed source, so
                # CPython would import the stale bytecode and the re-measure would
                # never see the fix. Never writing bytecode keeps every cycle honest.
                env={"PYTHONDONTWRITEBYTECODE": "1"},
                **{**common, "session_id": uuid.uuid4().hex},
            )
            if status != "done":
                break
        return status
    if kind == "forge":
        # Only what the binding actually sets, so the adapter's constants stay
        # the one place a default lives.
        poll = {k: binding[k] for k in ("poll_timeout", "poll_interval") if k in binding}
        return await _forge.run_task(
            db,
            run_dirs,
            hook_point=task_hook,
            handler=binding["handler"],
            backend=binding["backend"],
            # `backend: auto` resolves against the forge recorded for this repo
            # (repos.yaml), because the registry is per install and the forge is
            # a property of the repo. Same source the agent branch reads above.
            repo_forge=(launch.repo_entry or {}).get("forge") if launch else None,
            # The worktree, not the repo: every forge CLI resolves the merge
            # request from the *current branch*, and the repo is on whatever
            # the human has checked out.
            repo=worktree,
            # The original repo path, not the worktree: a confirmed-conflict
            # rebase (Kraft-9h7v) needs origin's current default branch tip,
            # which `refresh_worktree_base` fetches from here.
            orig_repo=Path(work_item_row["repo"]),
            branch=store.branch_for(work_item_row),
            title=work_item_row["title"],
            **poll,
            **common,
        )
    raise RuntimeError(
        f"unhandled binding for {task_hook!r}: kind={kind!r} handler={binding.get('handler')!r}"
    )


async def measure_node(
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
            dispatch_node(
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
    # A human's interruption still outranks this, but a task that never
    # launched outranks a rate limit, a budget breach and a co-task's failure:
    # none of those are evidence about anything while a task in this node
    # could not even start (Kraft-579).
    if any(r == CONFIG_ERROR for r in results):
        failed = [tasks[i] for i, r in enumerate(results) if r == CONFIG_ERROR]
        return CONFIG_ERROR, failed, []
    if any(r == RATE_LIMITED for r in results):
        return RATE_LIMITED, [], []
    if any(r == WAITING for r in results):
        return WAITING, [], []
    if any(r == INFRA_STOP for r in results):
        return INFRA_STOP, [], []
    # Logged before the BUDGET rung returns: a co-task can raise in the same node
    # as a budget-refused agent, and that traceback is the only record of it.
    excs = [r for r in results if isinstance(r, BaseException)]
    for exc in excs:
        logger.error("measuring task raised in node %s: %r", node["id"], exc)
    # paused > rate_limited > budget > failed. A pause is a human's instruction
    # and outranks everything. A rate limit and a budget breach both outrank a
    # co-task's failure because the agent's "failure" is not evidence about the
    # code; a rate limit outranks a budget breach because it is Kraft's own
    # spend policy refusing to start, not an external constraint the agent hit.
    if any(r == BUDGET for r in results):
        return BUDGET, [], []
    failed = [
        tasks[i]
        for i, r in enumerate(results)
        if isinstance(r, BaseException) or r in ("failed", "needs_context", "conflict")
    ]
    if failed:
        return "failed", failed, excs
    return "ok", [], []


def collect_findings(db, work_item_id: str, node: dict, round: int):
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


def needs_context_question(db, work_item_id: str, node: dict, round: int) -> str | None:
    """The question from a `needs_context` row in this round, or None.

    Same latest-row-per-hook-point read as `collect_findings` (a resume or
    `/retry` re-enters at round 0 with stale rows still sitting there, so a
    first-match scan could re-stop the item on a historical row forever) but
    deliberately NOT its `row["hook_point"] in node["tasks"]` filter: the fix
    task (`on.implementation.start`) is dispatched with this same round, and
    including it is exactly how a fix task's own `needs_context` is meant to
    surface, one iteration later.

    `JUDGE_HOOK` is the one exception. The judge is a brake bolted onto the
    cap and never a second way to get stuck (`_judge_result` fails open
    in-process), but its session row persists -- so without this filter a
    judge that exited `needs_context` would strand the item on the judge's
    own question at the next re-entry, which is exactly the design's
    forbidden case arriving one iteration late.
    """
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["hook_point"] == JUDGE_HOOK:
            continue
        latest[row["hook_point"]] = row  # ordered by created_at, so last wins
    for row in latest.values():
        if row["status"] == "needs_context":
            return _subprocess.read_question(Path(row["result_path"])) or "(no question given)"
    return None


def last_measurement(db, work_item_id: str, node_id: str) -> tuple[list[str] | None, bool]:
    """This node's most recent `findings_measured` fingerprints, and whether a
    `fix_cycle_started` for the node followed it.

    Read from the event log rather than carried in a local: `kraft.executor.
    resuming.reconcile_current_node` re-enters `kraft.executor.walk.walk_node`
    after a crash or a resume with the counter intact, and a loop holding its
    history in the stack frame forgets everything it has seen — on exactly the
    path that motivates escalation.

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


#: The fix loop's judge hook (2026-09-12-verify-fix-loop-judge-design):
#: dispatched directly by `kraft.executor.walk.walk_node`, the same way
#: `on.implementation.start` is -- not from a node's own `tasks` list, so it
#: never contaminates `collect_findings`/`needs_context_question`'s per-task
#: reads, and every node with a `fix_loop` gets it unconditionally (spec
#: decision 4: no new policy field).
JUDGE_HOOK = "on.fix_loop.judge"

#: Statuses that mean the judge session actually finished thinking -- the
#: same "was this a real judgement" gate `gate_review._UNTRUSTWORTHY`
#: applies, phrased as the allowlist its own `VERDICTS` check mirrors.
_JUDGE_TRUSTED_STATUS = frozenset({"done", "done_with_concerns"})
_JUDGE_VERDICTS = frozenset({"continue", "stop_needs_human", "stop_downgrade"})


def _judge_result(status: str, verdict: str | None) -> str:
    """Fail open to "continue" for anything the judge cannot be trusted on --
    an untrusted status, a missing or unknown verdict. The judge is a brake
    bolted onto the existing fix-loop cap, never a second way to get stuck:
    whatever this returns, the cap and the stuck detector in
    `kraft.executor.walk.walk_node` run exactly as they do today.
    """
    if status in _JUDGE_TRUSTED_STATUS and verdict in _JUDGE_VERDICTS:
        return verdict
    return "continue"


def judge_history(
    db, work_item_id: str, node_id: str, loop_severities: frozenset[str]
) -> list[dict]:
    """Every round measured so far for this node, oldest first: the judge's
    cross-round view (spec's "cheap pointers, not full transcripts" input) --
    each round's eligible findings, with the fingerprint stable across
    rounds already computed, and that round's fix session's own result-file
    path.

    Reconstructed from the event log and `worker_sessions` rather than
    carried in a local across calls, the same resume-safety reasoning
    `last_measurement` gives for reading its own history back from events
    instead of a stack frame.
    """
    # Kept in event order in a list, never keyed by the event's own `cycle`:
    # `walk_node` resets `round` to 0 on every re-entry (ci_wait poller, crash
    # resume, /retry) while the loop counter persists, so two entries' first
    # measurements both land on cycle 0. A dict keyed on that number silently
    # dropped the older one and then `sorted()` re-labelled the *newest*
    # measurement as round 0, the oldest -- handing the judge a truncated
    # trend pointing the wrong way, on mr_checks/on.ci.poll every time.
    # _REPAIR_ROUND (-1) is a repair pass, not a paid fix cycle -- it has
    # nothing to do with the budget the judge is weighing.
    measured: list[tuple[int, list[_findings.Finding]]] = []
    for e in db.read(lambda c: events.read_after(c, 0, work_item_id)):
        if e["type"] == "findings_measured" and e["payload"].get("node_id") == node_id:
            cycle = e["payload"]["cycle"]
            if cycle < 0:
                continue
            measured.append(
                (
                    cycle,
                    [
                        _findings.from_payload(f)
                        for f in e["payload"].get("findings", [])
                        if f.get("severity") in loop_severities
                    ],
                )
            )
    fix_rows = db.read(
        lambda c: c.execute(
            "SELECT round, result_path FROM worker_sessions WHERE work_item_id = ? "
            "AND node_id = ? AND hook_point = 'on.implementation.start' ORDER BY round",
            (work_item_id, node_id),
        ).fetchall()
    )
    # Fix rounds come from `bump_counter`, which does not reset across
    # re-entries, so unlike the measurements these are collision-free.
    fix_by_round = {r["round"]: r["result_path"] for r in fix_rows}
    return [
        {"round": i, "findings": found, "fix_result_path": fix_by_round.get(cycle)}
        for i, (cycle, found) in enumerate(measured)
    ]


async def judge_verdict(
    db,
    run_dirs,
    work_item_id: str,
    node: dict,
    row,
    registry: Registry,
    worktree,
    *,
    round: int,
    key: str,
    eligible: list[_findings.Finding],
    policy: _policy.Policy,
    launch: LaunchContext | None,
    budget: _policy.Budget,
) -> tuple[str, str]:
    """Ask the fix loop's judge whether the cycle about to be dispatched is
    still worth it (2026-09-12-verify-fix-loop-judge-design). Returns
    `(verdict, reasoning)`; `verdict` is `"paused"` (propagate, no event) or
    one of `"continue"`/`"stop_needs_human"`/`"stop_downgrade"`, never
    anything else -- `_judge_result` is the only place that decides which.

    A registry with no `JUDGE_HOOK` binding at all -- a hand-built test
    registry, or a real install's `registry.yaml` from before this feature
    landed -- fails open the same as any other untrusted outcome, rather
    than a `KeyError` out of `dispatch_node`'s own lookup.
    """
    if JUDGE_HOOK not in registry.hooks:
        return "continue", ""
    cap = _policy.resolve_cap(policy, key)
    counter = db.read(lambda c: store.read_counter(c, work_item_id, key))
    attempts_used = counter["count"] if counter else 0
    started_at = counter["started_at"] if counter else _now()
    elapsed = (datetime.fromisoformat(_now()) - datetime.fromisoformat(started_at)).total_seconds()
    history = judge_history(db, work_item_id, node["id"], policy.loop_severities)
    instruction = prompts.JUDGE_PROMPT.format(
        node_id=node["id"],
        history=prompts.format_judge_history(history),
        attempts_used=attempts_used,
        cap_attempts=cap.attempts,
        elapsed_s=int(elapsed),
        cap_wall_clock_s=cap.wall_clock_s,
    )
    status = await dispatch_node(
        db,
        run_dirs,
        JUDGE_HOOK,
        node,
        row,
        registry,
        worktree,
        instruction_override=instruction,
        round=round,
        launch=launch,
        budget=budget,
    )
    if status == "paused":
        return "paused", ""
    session = None
    for s in db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round)):
        if s["hook_point"] == JUDGE_HOOK:
            session = s  # ordered by created_at -- last one wins
    if session is None:
        return "continue", ""
    result_path = Path(session["result_path"])
    verdict = _judge_result(status, _subprocess.read_verdict(result_path))
    reasoning = _subprocess.read_concerns(result_path) or ""
    return verdict, reasoning
