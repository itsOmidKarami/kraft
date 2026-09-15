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
from kraft import sandbox as _sandbox
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
        # Authorship travels with the note, not with the caller: a seeded
        # steer is Kraft's own recap of the last review's unresolved findings,
        # and the human templates in `steer_prefix` would tell the agent a
        # person wrote it (the same misattribution `Steer.human` keeps out of
        # the fix-loop judge). Read before `take()`, and on `is not None`, not
        # truthiness: `Steer.__bool__` is about having text left, and `take()`
        # has just emptied it.
        note_by_human = steer.human if steer is not None else True
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
        if binding.get("artifact") == "chain_review":
            chain_nodes = json.loads(work_item_row["chain_definition"])["nodes"]
            at = next((i for i, n in enumerate(chain_nodes) if n["id"] == node["id"]), None)
            tail = chain_nodes[at + 1 :] if at is not None else []
            preceding = tuple(n["id"] for n in chain_nodes[: at + 1]) if at is not None else ()
            instruction += prompts.chain_review_context(tail, registry, preceding)
        item_override = (
            json.loads(work_item_row["agent_overrides"]) if work_item_row["agent_overrides"] else {}
        )
        node_override = store.node_overrides_of(work_item_row).get(node["id"], {})
        model_effort = {
            k: v for k, v in node_override.items() if k in ("model", "escalate_model", "effort")
        }
        merged_override = {**item_override, **model_effort}
        inv = _agent.resolve_invocation(
            binding,
            launch.repo_entry if launch else None,
            launch.steering_dir if launch else None,
            skills_dir=launch.skills_dir if launch else None,
            escalate=escalate,
            item_override=merged_override or None,
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
            sandbox=inv.sandbox,
            steering_texts=inv.steering_texts,
            artifact=binding.get("artifact"),
            method_text=inv.method_text,
            title=work_item_row["title"],
            task_instruction=(
                prompts.steer_prefix(binding, work_item_row, worktree, note, human=note_by_human)
                if note
                else ""
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
        sandbox = _sandbox.resolve(binding, repo_entry)
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
                sandbox=sandbox,
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
            # `merge`'s conflict-rebase shortcut may only report "done"
            # without calling forge.merge when this node's own frozen
            # chain_definition will actually bounce the walk back to verify
            # afterwards (code-review) -- not whatever the current
            # templates/default.yaml happens to say.
            has_rebase_bounce=bool(node.get("rebase_bounce_to")),
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
    # Kraft-gl9d: a crash/resume re-entry into this same (node, round) must not
    # re-spend an agent session on a task whose session already reached 'done'
    # against the worktree as it stands right now. Read once, ahead of the
    # per-task loop below -- the worktree's HEAD does not move while this
    # node's own tasks are still being measured. `worktree` is None only in a
    # unit test that stubs `dispatch_node` out entirely (no git to read); a
    # null head_sha just means "never reusable", same as any other.
    head_sha = _config.git_read(Path(worktree), "rev-parse", "HEAD") if worktree else None

    async def _measure(t: str) -> str:
        # A null head_sha is never reusable (`reusable_session` itself would
        # say so) -- skip the read entirely rather than asking a test double
        # that has no worktree, and thus no HEAD, to answer it.
        reused = (
            db.read(
                lambda c: store.reusable_session(c, work_item_id, node["id"], t, round, head_sha)
            )
            if head_sha is not None
            else None
        )
        if reused is not None:
            return reused["status"]
        return await dispatch_node(
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

    results = await asyncio.gather(*(_measure(t) for t in tasks), return_exceptions=True)
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


#: A task in one of these states failed outright -- the same set
#: `measure_node` treats as failed.
_FAILING_STATUSES = ("failed", "needs_context", "conflict")


def collect_findings(db, work_item_id: str, node: dict, round: int, registry: Registry):
    """(findings, hook points that reported at least one) for one cycle.

    Only the node's own measuring tasks: the fix task is dispatched with
    `round=count` and the next measuring pass runs at that same round, so an
    unfiltered query folds the fix agent's result file into the cycle. Only the
    most recent row per hook point, because a re-entry can measure at a round a
    previous pass already used -- `round` seeds from the persisted fix-loop
    counter, which a gate rejection or a `ci_wait` poll does not clear, and a
    `/retry` deletes the counter row so the next pass restarts at 1 instead.
    Either way stale rows sit at the same number.

    A hook that failed without writing a findings file at all -- `on.test.run`
    is the common case, `kind: subprocess` with no findings schema to write to
    -- gets a synthesized `Finding` via `_findings.from_blind_failure` instead
    of vanishing (traced live on a work item that spun for 7 cycles on an
    identical, invisible test failure). `reported` is not extended for it:
    that set means "wrote a real, parseable result file", which a synthesized
    finding does not change -- `walk.py`'s `blind_failures` still computes the
    same way and still forces the loop open, now redundantly with `eligible`,
    which is harmless.
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
        elif row["status"] in _FAILING_STATUSES:
            binding = registry.hooks.get(hook, {})
            reproduce = (
                shlex.join(binding["command"])
                if binding.get("kind") == "subprocess" and binding.get("command")
                else None
            )
            found.append(
                _findings.from_blind_failure(
                    hook, row["log_path"], work_item_id, row["id"], reproduce=reproduce
                )
            )
    return found, reported


def needs_context_question(
    db, work_item_id: str, node: dict, round: int, *, first_iteration: bool = False
) -> str | None:
    """The question from a `needs_context` row in this round, or None.

    Same latest-row-per-hook-point read as `collect_findings` (a resume or
    `/retry` re-enters with stale rows still sitting there, so a first-match
    scan could re-stop the item on a historical row forever) but deliberately
    NOT its `row["hook_point"] in node["tasks"]` filter: the fix task
    (`on.implementation.start`) is dispatched with this same round, and
    including it is exactly how a fix task's own `needs_context` is meant to
    surface, one iteration later.

    `first_iteration` is the exception to that, and only that: on the entry's
    very first pass there is by definition no fix from *this* entry yet, so
    any fix row at this round belongs to a bygone one. It matters because
    `walk_node` now seeds `round` from the persisted counter, and it is also
    re-entered on paths that are not resumes -- a gate rejection walking back
    to a fix_loop node, and the `ci_wait` poller -- where the `retry_counters`
    row survives (only `retry_after_cap` deletes it). Without this, the last
    pass's fix question would stop the new pass before it ran a single cycle:
    the same stranding `ESCALATION_HOOK` below closes, through a third door.
    Defaults off so every other caller keeps today's behaviour.

    `JUDGE_HOOK` is the one exception. The judge is a brake bolted onto the
    cap and never a second way to get stuck (`_judge_result` fails open
    in-process), but its session row persists -- so without this filter a
    judge that exited `needs_context` would strand the item on the judge's
    own question at the next re-entry, which is exactly the design's
    forbidden case arriving one iteration late.

    `ESCALATION_HOOK` is excluded for the same reason, through a different
    door. An escalation is a conversation with a human, not a measurement:
    its `needs_context` *is* the question that opened the conversation, so
    re-reading it once the human has answered re-stops the node on a question
    that has already been settled -- forever, because nothing ever rewrites
    that row. On e983d85c that cost $4.86 and 38 minutes, the human's answer
    reviewed and discarded twice with no code changed, and the item was then
    skipped with a CRITICAL finding still open (Kraft-7itv follow-up).
    """
    rows = db.read(lambda c: store.sessions_for_round(c, work_item_id, node["id"], round))
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["hook_point"] in (JUDGE_HOOK, ESCALATION_HOOK):
            continue
        if first_iteration and row["hook_point"] == "on.implementation.start":
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

#: The escalation hook's own `hook_point`. Not a node task and not dispatched
#: from a `tasks` list -- `escalate.dispatch` writes it directly -- so, like
#: `JUDGE_HOOK`, its session rows sit in the same `(node, round)` scan that
#: `needs_context_question` reads and must be skipped there. Named rather than
#: spelled out at each site because `reattach` compares against it too.
ESCALATION_HOOK = "escalation"

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
    # `walk_node` seeds `round` from the loop counter on every re-entry
    # (ci_wait poller, crash resume, /retry), and that counter persists across
    # the re-entries that are not resumes -- so a new entry's first measurement
    # lands on a cycle number the previous entry already measured at, and a
    # /retry that cleared the counter lands back on 0 where the first entry
    # was. A dict keyed on that number silently dropped the older one and then
    # `sorted()` re-labelled the *newest* measurement as the oldest -- handing
    # the judge a truncated trend pointing the wrong way, on
    # mr_checks/on.ci.poll every time. Seeding narrowed the collision; it did
    # not remove it, so this stays a list.
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


def stuck_fingerprint(history: list[dict], min_repeats: int) -> str | None:
    """The first fingerprint (of the latest round's) that has survived
    `min_repeats` consecutive fix attempts unchanged, or None.

    Generalizes `walk.walk_node`'s whole-set stuck check (`prints ==
    previous_prints`) to a single recurring fingerprint: a blind-failure
    fingerprint (Kraft-0i6z4) can persist for many rounds while a
    co-occurring review finding keeps changing shape, so the set as a whole
    never repeats -- but this one thing never moved. A round only extends a
    streak from the one before it if its OWN `fix_result_path` is set --
    that is the fix which ran and produced this measurement (see
    `judge_history`'s round/cycle alignment); two measurements taken back to
    back with no fix in between (crash/resume) never had a chance to change
    and must not count as "no progress".
    """
    streaks: dict[str, int] = {}
    for round_ in history:
        prints = {f.fingerprint for f in round_["findings"]}
        fix_ran = round_["fix_result_path"] is not None
        streaks = {fp: streaks.get(fp, 0) + 1 if fix_ran and fp in streaks else 1 for fp in prints}
    return next((fp for fp, n in streaks.items() if n >= min_repeats), None)


def unresolved_findings_steer(
    db, work_item_id: str, node_id: str, policy: _policy.Policy | None
) -> str | None:
    """A retry's steer when the caller gave none and there is no rejection to
    fall back on (Kraft-7sec, second half): the node's most recent
    measurement, formatted the same way a fix cycle already receives it.

    `judge_history`'s last entry already is the unresolved set -- reused
    rather than a third reader of `findings_measured` (spec section 2
    forbids one, and `last_measurement` is already the second): a measurement
    re-reports what is still there every round, so the most recent one needs
    no `findings_resolved` counterpart to subtract, and a finding fixed in an
    earlier cycle is already absent from it.

    None when there is nothing to seed -- no measurement recorded yet for
    this node, or the last one had no eligible finding at all (a blind task
    failure with nothing structured to report) -- so the caller's own
    `last_rejection` fallback still applies.
    """
    severities = policy.loop_severities if policy else _policy.DEFAULT_LOOP_SEVERITIES
    history = judge_history(db, work_item_id, node_id, severities)
    if not history:
        return None
    found = history[-1]["findings"]
    if not found:
        return None
    return prompts.seeded_findings_note(found)


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
    cap = _policy.resolve_cap(policy, key, store.node_overrides_of(row).get(node["id"]))
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
