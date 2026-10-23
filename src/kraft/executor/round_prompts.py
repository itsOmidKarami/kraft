"""What an agent task is told about earlier rounds: the findings carried
from the last review, the reviewer's and the fixer's last sessions, the
review package, a stuck escalation, deferred findings, the fix rounds so far
and the judge's prompt. Re-exported by `kraft.executor.prompts`."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from kraft import findings as _findings
from kraft import item_paths
from kraft import review as _review
from kraft.config import git_read
from kraft.templates.models import ForgeAction, ForgeTask, ResolvedTask
from kraft.vocab import ADVANCING, RESTARTS_RUN
from kraft.vocab.sql import marks

#: Handed to fix cycle N > 1: cycle N-1's own result file, by path, not by
#: content -- pasting the file costs tokens on every cycle of every work item,
#: and the agent can already read a path itself (spec §4).
_FIX_PREVIOUS_RESULT = "\n\nYour previous attempt's result file is at {result_path}."
_FIX_PREVIOUS_SUMMARY = " Its session summary is at {summary_ref}."


def format_findings(
    found: list[_findings.Finding], repeats: set[str], *, tags: bool = False
) -> str:
    """One bullet per finding, plus one indented line per job it came from
    naming exactly where that job's own session log lives -- so a fix agent
    can pull the full output for any finding without re-running anything
    (Kraft-s7c04.34/.35). `tags` prefixes each finding with its stable
    identity, for the two readers that have to refer back to a specific
    finding across rounds -- the reviewer being asked "is this one still
    there?" (`carried_findings_note`) and the fixer reading a round history
    (`format_judge_history` already shows a tag for the same reason)."""
    lines = []
    for f in found:
        where = f"{f.file}:{f.line}" if f.file and f.line else (f.file or "—")
        tag = "REPEAT " if f.fingerprint in repeats else ""
        ident = f"[{f.fingerprint}] " if tags else ""
        lines.append(f"- {ident}{tag}[{f.severity}] {where} — {f.message} ({f.source_plugin})")
        for job in f.jobs:
            lines.append(f"    {job.label} → {job.log_ref}")
    return "\n".join(lines)


def summary_at(ref: str, run_dirs) -> str:
    """Where a session's recorded summary is, for a prompt: an absolute path
    for one in its item's folder (`items/...`, relative to the run folder),
    else the ref as it is, relative to the worktree as it always was."""
    return str(run_dirs.base / ref) if Path(ref).parts[:1] == (item_paths.ITEMS,) else ref


#: Delivered to an agent task that declares them (`AgentTask.inputs`):
#: `review_package`, `carried_findings` (`carried_findings_note`) and
#: `previous_review` (`previous_review_note`). `fix_attempt_note` is still
#: PARKED: no input declares it.


#: What the reviewer said last round, handed back to it (Kraft-s7c04.1). The
#: tags are the load-bearing part: without a way to say "this is that one", a
#: reworded repeat reads downstream as a defect that was fixed and a new one
#: that appeared, which is how findings went 3 -> 2 -> 3 -> 4 on 6c712ea8
#: without ever converging.
_CARRIED_FINDINGS = (
    "\n\nThe last review of this node reported the findings below. Each carries a "
    "stable tag in the first bracket.\n\n{findings}\n\n"
    "For each one, decide whether it is still present in the code as it stands "
    "now:\n"
    "- Still present: report it again and set `same_as` to its tag, even if you "
    "would word it differently now. Reusing the tag is what tells the loop this "
    "is the same defect rather than a new one.\n"
    "- Fixed: do not report it. Its absence is how the loop learns the fix "
    "worked.\n\n"
    "Do not lower a finding's severity below what is shown above unless the code "
    "that caused it has changed; if you do, say why in the message. These are "
    "not a checklist to work from -- review the change on its own terms as well, "
    "and report anything new you find."
)


def carried_findings_note(previous: list[_findings.Finding]) -> str:
    """The previous round's findings, for the reviewer about to measure again,
    delivered to a task declaring `inputs: [carried_findings]`
    (`carried-findings-are-delivered-to-a-reviewing-task`).

    "" when there is no previous round, so a work item's first and most
    important review carries nothing extra.
    """
    if not previous:
        return ""
    return _CARRIED_FINDINGS.format(findings=format_findings(previous, repeats=set(), tags=True))


#: The reviewer's own last session on this hook, by path (Kraft-qzkux). Not a
#: resumed conversation and not a re-paste: `carried_findings_note` has already
#: rendered the findings verbatim immediately above this, so what is left to
#: hand over is the reasoning behind them -- which the agent wrote itself, to a
#: file, knowing it was the durable record.
#:
#: Worded as evidence to check rather than a position to defend. A reviewer
#: handed its own prior conclusions is anchored the same way a resumed session
#: would be, only more weakly, and the `same_as` tag machinery exists precisely
#: to get continuity without that anchoring.
_PREVIOUS_REVIEW = (
    "\n\nYour own last review of this node wrote a result file at {result_path}"
    "{summary}\n"
    "Read it if you need your earlier reasoning, not to defend it: a finding "
    "you would now judge differently is a conclusion to change, not a record "
    "to keep consistent."
)
_PREVIOUS_REVIEW_SUMMARY = ", and a session summary at {summary_ref}"

#: The fix cycle that produced the change now under review (Kraft-qzkux). The
#: asymmetry this closes was total and one-way: the fixer already receives the
#: reviewer's findings, the round history, the regression warning and the
#: judge's reasoning (`walk.py`), while the reviewer received nothing of the
#: fixer's and inferred intent from commits.
_FIX_ATTEMPT = (
    "\n\nThe change since your last review is one fix cycle's work, dispatched "
    "against the findings above. Its result file is at {result_path}{summary}\n"
    "Read what it says it did before deciding a finding is still present. A "
    "finding it deliberately did not fix is a disagreement to judge on the "
    "merits, not an oversight to re-report unchanged."
)
_FIX_ATTEMPT_SUMMARY = ", and its session summary at {summary_ref}"


def _session_note(row, template: str, summary_template: str, *, run_dirs) -> str:
    """One session's artifacts, by path. "" for no row at all, and a missing
    `session_summary_ref` drops that clause rather than interpolating a path
    the agent would spend a tool call discovering does not exist."""
    if row is None:
        return ""
    ref = row["session_summary_ref"]
    return template.format(
        result_path=row["result_path"],
        summary=summary_template.format(summary_ref=summary_at(ref, run_dirs)) if ref else "",
    )


def previous_review_note(row, *, run_dirs) -> str:
    """The task's own last completed session, by path, for a task declaring
    `inputs: [previous_review]`; "" on its first session
    (`continuity-note-is-delivered-to-a-resumed-reviewer`)."""
    return _session_note(row, _PREVIOUS_REVIEW, _PREVIOUS_REVIEW_SUMMARY, run_dirs=run_dirs)


def fix_attempt_note(row, *, run_dirs) -> str:
    """**Parked:** no `AgentTask` input declares it, so nothing delivers it."""
    return _session_note(row, _FIX_ATTEMPT, _FIX_ATTEMPT_SUMMARY, run_dirs=run_dirs)


#: A review session that did not finish is not a head anything was reviewed at.
#: Same "was this a real judgement" allowlist `dispatch._JUDGE_TRUSTED_STATUS`
#: and gate review apply (all `vocab.ADVANCING`), for the same reason.
_REVIEWED_STATUS = ADVANCING


def last_review_session(db, work_item_id: str, task_hook: str) -> sqlite3.Row | None:
    """The previous *completed* session on this hook, or None (Kraft-s7c04.1):
    where `review_package`'s range starts from a task's second session on.

    Its `head_sha` is the head that session was dispatched at.

    `worker_sessions.head_sha` is stamped by `dispatch.dispatch_node` at
    dispatch, so it is the commit that review was actually about. Read from the
    table rather than carried in a local, for the reason `last_measurement`'s
    docstring gives: crash resume re-enters the node through `walk.run_once`
    and a loop holding its history in a stack frame forgets
    everything it has seen.

    The row for the session now being dispatched does not exist yet -- this runs
    as an argument to `run_agent_task`, before it writes one -- so there is no
    self-match to exclude.

    The status filter is load-bearing. A session that failed, hit a config
    error, or was killed mid-run still carries a `head_sha`, and taking it would
    narrow the next review past code **no reviewer has ever seen** -- the one
    outcome worse than re-reading the whole branch.

    Returns the row rather than `head_sha` alone because the same session's
    `result_path` and `session_summary_ref` are what `previous_review_note`
    hands the next reviewer. The status filter serves both: a session that
    failed or was killed is the wrong diff bound *and* may never have written
    its summary.
    """
    return db.read(
        lambda c: c.execute(
            "SELECT head_sha, result_path, session_summary_ref FROM worker_sessions "
            "WHERE work_item_id = ? AND hook_point = ? "
            f"AND head_sha IS NOT NULL AND status IN ({marks(_REVIEWED_STATUS)}) "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (work_item_id, task_hook, *_REVIEWED_STATUS),
        ).fetchone()
    )


def review_package(
    db, run_dirs, work_item_id: str, worktree, task_hook: str, session_id: str
) -> str | None:
    """The change under review, written out for the task at `task_hook`, or
    None. `dispatch.dispatch_node` calls it for an agent task that declares
    `inputs: [review_package]` and for no other.

    None on an item with no `base_ref` (pre-migration items), and on a git
    failure -- a review with no diff is worse than one whose prompt never
    promised a file.

    `base_ref` is read fresh rather than off the `work_items` row `run` opened
    with: `env_setup` stamps it during the chain's first node, so that row is
    always the pre-stamp one.

    From the second round of a fix loop onward the range starts at the head this
    hook's last completed session was dispatched at, not at `base_ref`
    (Kraft-s7c04.1): re-reading the whole branch every round is what made
    `verify` half of all Kraft spend. Two fallbacks, both to the whole branch --
    no such session (round 0, or a hook that has never run), and a sha git no
    longer knows, which makes the range meaningless.

    Deliberately *not* guarded: a `since` that is no longer an ancestor of HEAD,
    which is what an `open_mr` rebase bounce produces. `git diff <old_sha>` then
    shows a superset of the round's change -- the upstream commits included --
    which is noisy but never hides anything, and a bounce is exactly the case
    where a wider look is wanted (`rebase_bounce_to: verify` exists for it).
    """
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    if row is None or not row["base_ref"]:
        return None
    previous = last_review_session(db, work_item_id, task_hook)
    since = previous["head_sha"] if previous else None
    if since and git_read(worktree, "rev-parse", "--verify", f"{since}^{{commit}}") is None:
        since = None
    path = _review.write_package(
        item_paths.session_files(run_dirs, work_item_id, session_id).review,
        worktree,
        row["base_ref"],
        since=since,
    )
    return str(path) if path else None


def bot_feedback_source(db, run_dirs, work_item_id: str, node, round: int) -> Path | None:
    """The collection this pass of `node` has to read, or None when it holds
    nothing to read: the newest session of this node and round, begun since the
    run last restarted, of one of its `mr.automated_review` tasks (the review
    wait's collect path wrote `bot_feedback.json` in its folder), unless every
    reviewer in it has no summary, no open thread and no failed check.

    This node and this round, and nothing from before a restart: a collection
    from a pass before the last repair describes a head that is gone, and
    handing it on would have the triage judge feedback the repair already
    answered. Not "since the node last started": a wait that parked is
    re-entered through `node_started` again, after its own session began."""
    hooks = [
        t.path
        for t in node.tasks()
        if isinstance(t.task, ForgeTask) and t.task.target is ForgeAction.MR_AUTOMATED_REVIEW
    ]
    if not hooks:
        return None
    restarted = db.read(
        lambda c: c.execute(
            "SELECT created_at FROM events WHERE work_item_id = ? "
            f"AND type IN ({marks(RESTARTS_RUN)}) ORDER BY seq DESC LIMIT 1",
            (work_item_id, *RESTARTS_RUN),
        ).fetchone()
    )
    row = db.read(
        lambda c: c.execute(
            "SELECT id FROM worker_sessions WHERE work_item_id = ? AND node_id = ? AND round = ? "
            f"AND hook_point IN ({marks(hooks)}) AND created_at >= ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (work_item_id, node.id, round, *hooks, restarted["created_at"] if restarted else ""),
        ).fetchone()
    )
    if row is None:
        return None
    source = item_paths.session_files(run_dirs, work_item_id, row["id"]).bot_feedback
    try:
        collected = json.loads(source.read_text())["reviewers"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    said = any(
        isinstance(r, dict) and (r.get("summaries") or r.get("threads") or r.get("check"))
        for r in collected
    )
    return source if said else None


def bot_feedback(db, run_dirs, work_item_id: str, node, round: int, session_id: str) -> str | None:
    """What the review bots said, copied to where the session `session_id` is
    handed it (`SessionFiles.bot_feedback`), and that path; None when there is
    nothing to read (`bot_feedback_source`). `dispatch` calls it for an agent
    task that declares `inputs: [bot_feedback]` and for no other."""
    source = bot_feedback_source(db, run_dirs, work_item_id, node, round)
    if source is None:
        return None
    own = item_paths.session_files(run_dirs, work_item_id, session_id).bot_feedback
    own.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, own)
    return str(own)


def previous_attempt_note(previous_fix, *, run_dirs) -> str:
    """The fix-prompt addendum for `kraft.executor.walk`'s previous-fix lookup,
    or "" if there was no previous attempt. A plain function so the no-summary
    branch is testable without driving a session through the executor."""
    if previous_fix is None:
        return ""
    note = _FIX_PREVIOUS_RESULT.format(result_path=previous_fix["result_path"])
    if ref := previous_fix["session_summary_ref"]:
        note += _FIX_PREVIOUS_SUMMARY.format(summary_ref=summary_at(ref, run_dirs))
    return note


#: What a node's declared stuck escalation is told, after its own prompt
#: (`stuck-escalation-is-an-exec-node-control`). The last paragraph is the
#: contract `walk._escalate_stuck` acts on: a clean finish retries the node from
#: its first step -- on a merge node, unless it committed, which rewinds the
#: chain instead -- and anything else leaves the item for a human.
_STUCK_ESCALATION = (
    "{prompt}\n\n"
    "Node {node_id} of this work item is stuck: its recovery and its fix loop "
    "could not advance it. Why it stopped:\n\n{reason}\n\n"
    "Resolve what is blocking it if you can, and commit what you change. {rerun}"
    "If you cannot resolve it, report `failed`; if a person has to decide "
    "something, report `needs_context` and ask in `question`. Either way the "
    "work item then waits for a human.\n"
)
_RERUNS = "When you finish cleanly, Kraft reruns node {node_id} from its first step. "
#: A merge node's (`on_merge_rejected`): a commit is never merged in place
#: (`walk._escalate_stuck`).
_REWINDS = (
    "Node {node_id} merges the merge request, so Kraft never merges a change of "
    "yours in place: when you finish cleanly having committed, the chain goes "
    "back to node {restart_from}, and a person's final review sees your change "
    "before it can merge. When you finish cleanly with nothing committed, Kraft "
    "reruns node {node_id} from its first step. "
)


def stuck_escalation_instruction(task: ResolvedTask, node, reason: str) -> str:
    prompt = getattr(task.task, "prompt", "")
    rejected = getattr(node.node, "on_merge_rejected", None)
    rerun = (
        _REWINDS.format(node_id=node.id, restart_from=rejected.restart_from)
        if rejected is not None
        else _RERUNS.format(node_id=node.id)
    )
    return _STUCK_ESCALATION.format(prompt=prompt, node_id=node.id, reason=reason, rerun=rerun)


def named_with_kind(task: ResolvedTask) -> str:
    """A task as a failure reason names it: its own local id, with its kind
    alongside (Kraft-5m7t) -- `[forge]`/`[builtin]`/`[subprocess]` reads as "no
    agent here to steer" without a human having to go and look the task up.

    The local id, not the canonical path: the reason already names the node, and
    an operator-facing string should read as a name rather than an address
    (`kraft.templates.models` owns the path; this is the label)."""
    return f"{task.task.id} [{task.task.kind.value}]"


# `chain_review_context` lived here: the resolved `registry.yaml` binding for
# every hook in a chain-review tail. V1 has no registry to describe and no node
# dictionaries to render, and the tail-revision flow it fed is Task 4b's to
# redefine on gate nodes, so it is deleted rather than ported to a shape nothing
# yet consumes.


#: What the review brief is told about findings that never entered the fix loop
#: (Kraft-s7c04.4). `skills/review-brief/SKILL.md` already promises the human
#: "the local review findings, including the ones ruled minor" and "a minor
#: finding nobody is shown is a silent discard" -- and nothing supplied them, so
#: on 6c712ea8 four real defects, a spec-phasing violation and an empty sweep
#: board among them, were detected, recorded and never reached the brief.
_DEFERRED_FINDINGS = (
    "\n\nReviews of this work item reported the findings below and the fix loop "
    "did not act on them: they were rated below the severity that opens a fix "
    "cycle. Nobody has fixed them and nobody has decided not to.\n\n{findings}"
    "\n\nName every one of them in the brief, with enough detail for the reader "
    "to judge it. They are the reason this section of the brief exists: a "
    "defect that was found and then quietly dropped is the one thing the person "
    "approving this merge cannot discover for themselves."
)


def deferred_findings_note(found: list[dict]) -> str:
    """The sub-threshold findings, for the agent writing the human's brief.

    Takes raw payload dicts, which is what `dispatch.deferred_findings` returns
    for the board -- one shape, so the gate and the brief cannot disagree.
    """
    if not found:
        return ""
    parsed = [_findings.from_payload(f) for f in found]
    return _DEFERRED_FINDINGS.format(findings=format_findings(parsed, repeats=set()))


#: The fixer's cross-round view (Kraft-s7c04.7). `last_measurement` gives it one
#: round; two rounds back was invisible, which is how round N+2 reverted the
#: security property round N established on e983d85c.
_FIX_ROUND_HISTORY = (
    "\n\nEvery round of this fix loop so far, oldest first. A finding's short "
    "hex tag is its identity across rounds -- the same tag reappearing is the "
    "same finding, not a new one:\n{history}"
)

#: What a tag that came back means, worded as a check rather than an
#: instruction. A false positive here would steer the fixer away from the
#: correct fix on a loop that is converging, which is worse than not flagging at
#: all -- hence `regressed_fingerprints`' two guards, and hence this wording.
_FIX_REGRESSION = (
    "\n\nWatch out: {tags} was reported in an earlier round, absent in a later "
    "one, and is back now. That usually means a fix in this loop undid an "
    "earlier one. Before changing anything, work out what the earlier fix "
    "established and whether the later one removed it. If two properties are "
    "genuinely in conflict, say so rather than alternating between them."
)


def round_history_note(history: list[dict], regressed: list[str]) -> str:
    """The fixer's view of every round so far, plus a warning for any finding
    that was fixed and came back (Kraft-s7c04.7).

    Renders with `format_judge_history`, the judge's own rendering, rather than
    a second format to keep in sync -- it already shows tags and each round's
    fix result path, which is exactly what a fixer needs to look two rounds
    back.

    "" for fewer than two rounds: there is nothing to say, and the first cycle
    is the one this batch must not make more expensive.
    """
    if len(history) < 2:
        return ""
    note = _FIX_ROUND_HISTORY.format(history=format_judge_history(history))
    if regressed:
        note += _FIX_REGRESSION.format(tags=", ".join(regressed))
    return note


#: The fix-loop judge's task instruction (2026-09-12-verify-fix-loop-judge-
#: design). The full verdict-reporting contract lives here, the same way
#: `gate_review._PROMPT` carries its own -- the hook's `skill:` binding
#: (`fix-loop-judge`, Task 4) supplies only the judgment *method*, appended
#: separately under "## Method" by `adapters.agent.run_agent_task`.
JUDGE_PROMPT = (
    "The fix loop on node {node_id} is about to spend another cycle. Before "
    "it does, judge whether that is still worth it, from the trend across "
    "the rounds already run.\n\n"
    "Rounds so far, oldest first (a finding's short hex tag is its stable "
    "identity across rounds -- the same tag reappearing is the same "
    "finding, not a new one):\n{history}\n\n"
    "Budget so far: {attempts_used} of {cap_attempts} attempts used, "
    "{elapsed_s}s of {cap_wall_clock_s}s wall-clock used.\n\n"
    "Report your decision by writing `verdict` into your result file, with "
    "your reasoning in `concerns`:\n\n"
    '  "verdict": "continue"          -- another cycle is worth spending: '
    "findings are shrinking, or this looks like real progress.\n"
    '  "verdict": "stop_needs_human"  -- the loop is not converging (the '
    "same findings recur, or new ones keep appearing as fast as old ones "
    "are fixed); stop and hand this to a person.\n"
    '  "verdict": "stop_downgrade"    -- the remaining findings are real '
    "but not worth the cost of another cycle; let the chain proceed with "
    "them unresolved. `concerns` becomes the note a human sees at the "
    "review gate later -- write it for them, not for the next fix cycle.\n\n"
    "`concerns` is required either way: it is the only record of why."
)


def format_judge_history(history: list[dict]) -> str:
    if not history:
        return "(no rounds measured yet)"
    lines = []
    for h in history:
        found = h["findings"]
        if found:
            text = "; ".join(
                f"[{f.severity}] {f.fingerprint} ({f.source_plugin}) {f.message}" for f in found
            )
        else:
            text = "clean"
        fix_note = (
            f" (that round's fix: {h['fix_result_path']})" if h.get("fix_result_path") else ""
        )
        lines.append(f"round {h['round']}: {text}{fix_note}")
    return "\n".join(lines)


#: What a paused agent task is told when it resumes its own session
#: (`dispatch._resumable_session`): the conversation already holds the brief,
#: so this is the whole new turn, after any steer.
AGENT_RESUMED_NOTE = (
    "An operator paused you mid-task and has now resumed you. Carry on with the "
    "task you were given, from where you stopped, and finish it as instructed."
)
