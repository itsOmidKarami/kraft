from __future__ import annotations

import sqlite3
from pathlib import Path

from kraft import findings as _findings
from kraft import progress as _progress
from kraft import review as _review
from kraft.adapters import agent as _agent
from kraft.config import git_read
from kraft.templates.models import AgentTask, ResolvedTask

FIX_PROMPT = (
    "The checks in node {node_id} failed for this work item. Fix the code so they "
    "pass. Make no unrelated changes. Failing hook points: {failed}"
)

#: A cycle can also open with no task failure at all -- a review task that
#: exits clean but reports an eligible-severity finding (`enters_loop` is
#: `eligible` OR `blind_failures`). `FIX_PROMPT`'s "checks ... failed" with a
#: blank "Failing hook points:" would lie about that; this is what fires
#: instead when `failed` is empty.
FIX_PROMPT_FINDINGS_ONLY = (
    "A review of node {node_id} reported findings that need fixing. Fix the "
    "code so they no longer apply. Make no unrelated changes."
)

#: Appended when the failures came with structured findings. Repeats lead: an
#: agent told it already tried and the reviewer disagreed behaves differently
#: from one seeing the finding fresh.
FIX_FINDINGS = "\n\nFindings to fix:\n{findings}"
FIX_REPEAT_NOTE = (
    "\n\nMarked REPEAT above: you attempted this on an earlier cycle and the check "
    "still reports it. Do not repeat the same approach."
)

#: The judge's own read of why the findings are not going down, handed to the
#: fix task it has just let through (Kraft-s7c04.5). On `continue` only -- a
#: stop verdict dispatches no fix task at all.
#:
#: The judge is already asked to explain itself, its `concerns` is already paid
#: for, and on e983d85c it twice diagnosed the loop correctly ("you are chasing
#: variants, root-cause this") while the loop restarted unchanged both times,
#: because nothing interpolated it anywhere.
_JUDGE_NOTE = (
    "\n\nThe fix-loop judge reviewed the trend across every round so far before "
    "letting this cycle run, and said:\n\n{reasoning}\n\nThat is the most "
    "informed read available of why the previous rounds did not resolve this. "
    "Take it seriously before repeating an approach it has already judged."
)


def judge_note(reasoning: str) -> str:
    return _JUDGE_NOTE.format(reasoning=reasoning.strip()) if reasoning.strip() else ""


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


#: What a retry with no explicit steer and no rejection to fall back on leads
#: with (Kraft-7sec, second half): the same per-finding bullets a fix cycle's
#: own FIX_FINDINGS already uses -- not a second findings format to keep in
#: sync with, just a different lead-in sentence, because no human wrote this one.
_SEEDED_FINDINGS_STEER = "Findings the last review of this node left unresolved:\n{findings}"


def seeded_findings_note(found: list[_findings.Finding]) -> str:
    return _SEEDED_FINDINGS_STEER.format(findings=format_findings(found, repeats=set()))


def failure_note(node, failed: list[str]) -> str:
    """What a step- or node-level recovery is told: the orchestrator already
    knows it, and the repair would otherwise have to go and rediscover it
    (Kraft-s7c04.26: on work item 6c712ea8 a repair agent spent 4 of its 16
    tool calls hunting for a log it was told to read but given no path to).

    The re-measure sentence is not decoration. A repair is believed only when
    the tasks it repaired pass afterwards -- that has been the design since
    Kraft-rv6i -- and an agent that does not know it will be checked has every
    incentive to declare success.
    """
    which = ", ".join(failed) if failed else "the node"
    return (
        f"The failing task(s) in node {node.id}: {which}.\n"
        f"After you finish, {which} will be re-measured and that result, not "
        "your own report, decides whether this repair worked."
    )


def task_failure_note(task: str, status: str, session=None) -> str:
    """What a task-level recovery (`TaskBase.on_failure`,
    `task-recovery-retries-only-the-task`) is told about the task it repairs:
    which task, how it ended, and where its own output is. Without it the
    repair starts blind -- `on.ci.repair` reported that the `on.ci.poll`
    diagnosis was absent from its prompt and from the item's events."""
    note = (
        f"The task {task} ended {status}. After you finish, {task} will be "
        "re-measured and that result, not your own report, decides whether this "
        "repair worked."
    )
    if session is not None:
        note += (
            f"\nIts session log is at {session['log_path']} "
            f"and its result at {session['result_path']}"
        )
        if session["session_summary_ref"]:
            note += f", with a session summary at {session['session_summary_ref']}"
        note += "."
    return note


# A human's note — from the capped card's retry (4b) or a resume after pause (4c) —
# prepended to the next agent launch. It leads because it is the reason this task is
# running again.
_STEER_PROMPT = "A human has steered this run: {steer}\n\n"

#: What the next `verify` agent task is told after a rebase-triggered bounce
#: (Kraft-4bgg). Deliberately not `_STEER_PROMPT`: no human wrote this, and
#: that template's wording would say one did.
_REBASE_PROMPT = (
    "This branch was rebased onto a newer {branch} before opening its MR. "
    "Commits landed upstream while this work was in progress:\n\n{body}\n\n"
    "Check whether they affect this work; fix it if so.\n\n"
)

#: Chars, not bytes -- this is a prompt prefix, not a diff view. A commit log
#: plus `--stat` is meant to be a pointer at what to go look at, not the
#: change itself; a full diff would cost far more context than the note is
#: worth.
_REBASE_NOTE_MAX = 2000


def rebase_drift_note(worktree, branch: str, old_base: str, new_base: str) -> str:
    """A short pointer at what changed upstream during a rebase bounce back
    to `verify` (Kraft-4bgg): commit subjects and touched files, not a full
    diff, so the next review agent knows what to go looking for instead of
    blindly re-reviewing everything from scratch.
    """
    log = git_read(worktree, "log", "--oneline", f"{old_base}..{new_base}") or "(no log)"
    stat = git_read(worktree, "diff", "--stat", f"{old_base}..{new_base}") or "(no changes)"
    body = f"Commits:\n{log}\n\nFiles touched:\n{stat}"
    if len(body) > _REBASE_NOTE_MAX:
        body = body[:_REBASE_NOTE_MAX] + "\n... (truncated)"
    return _REBASE_PROMPT.format(branch=branch, body=body)


#: What the rebase-conflict resolver (`walk.resolve_rebase_conflict`,
#: Kraft-s7c04.23) is dispatched with. Two jobs, not one -- resolving the
#: conflict is mechanical; judging whether the upstream commits it rebased
#: onto change what this work item is supposed to do is the reason an agent
#: is doing this rather than a script (design §4.1). A real diff, not a
#: `--stat`: that relevance judgement cannot be made from file names alone.
_REBASE_RESOLVE_PROMPT = (
    "A rebase conflict stopped this work item. Branch {branch} could not be "
    "replayed onto {new_base} (it was based on {old_base}); the rebase has "
    "already been aborted and the worktree is clean. git reported:\n\n"
    "{conflict}\n\n"
    "What landed upstream, {old_base}..{new_base}:\n\n{diff}\n\n"
    "{attachments}"
    "You have two jobs:\n"
    "1. Re-run the rebase (`git rebase {new_base}`) and resolve the conflict "
    "yourself, then finish it (`git rebase --continue`).\n"
    "2. Judge whether the commits that landed upstream change what this work "
    "item is supposed to do -- not just whether the text merges cleanly. A "
    "clean rebase can still invalidate the plan; a conflict can be "
    "irrelevant to it.\n\n"
    "Report through the normal result file:\n"
    "- status: done -- resolved, and upstream does not affect what this "
    "item does.\n"
    "- status: done_with_concerns -- resolved, but upstream touches what "
    "this item does; say what in `concerns`.\n"
    "- status: needs_context -- upstream invalidates the agreed spec or "
    "plan; ask in `question`.\n"
    "- status: failed -- could not resolve the conflict.\n"
)

#: Same posture as `_REBASE_NOTE_MAX`, but larger: the resolver's whole job
#: is judging this diff's relevance, unlike the drift note's reviewer, who
#: only needs a pointer at what to go re-look at.
_REBASE_RESOLVE_DIFF_MAX = 6000


def rebase_resolve_note(
    worktree,
    branch: str,
    old_base: str,
    new_base: str,
    conflict: str,
    attachments: list[dict],
) -> str:
    log = git_read(worktree, "log", "--oneline", f"{old_base}..{new_base}") or "(no log)"
    diff = git_read(worktree, "diff", f"{old_base}..{new_base}") or "(no changes)"
    body = f"Commits:\n{log}\n\nDiff:\n{diff}"
    if len(body) > _REBASE_RESOLVE_DIFF_MAX:
        body = body[:_REBASE_RESOLVE_DIFF_MAX] + "\n... (truncated)"
    return _REBASE_RESOLVE_PROMPT.format(
        branch=branch,
        old_base=old_base,
        new_base=new_base,
        conflict=conflict.strip(),
        diff=body,
        attachments=attachment_note(attachments, method_is_own=False),
    )


#: The same note, over a document this node has already written. A bare steer
#: made a rejected plan cost a full re-plan: nothing in the dispatch said the
#: file existed or that the human objected to one paragraph of it (Kraft-bol).
_REVISE_PROMPT = (
    "A human read {path} and sent it back with this note: {steer}\n"
    "Revise that document in place: change what the note objects to, and "
    "leave the rest of it alone.\n\n"
)


#: A note Kraft carried forward for itself -- the seeded recap of the last
#: review's unresolved findings (`_SEEDED_FINDINGS_STEER`, Kraft-7sec second
#: half). Deliberately not `_STEER_PROMPT`/`_REVISE_PROMPT`, for the same
#: reason `_REBASE_PROMPT` is not: both of those name a human as the author,
#: and telling an agent a human typed Kraft's own recap is the misattribution
#: `Steer.human` exists to keep out of the prompt as well as out of the judge.
_SEEDED_PROMPT = (
    "Kraft carried this forward from an automated review; no human typed it:\n\n{note}\n\n"
)

#: An automated gate review's own verdict, carried into the re-run it triggered
#: (Kraft-s7c04.6). Neither `_STEER_PROMPT`, which names a human as the author,
#: nor `_SEEDED_PROMPT`, which is Kraft's own recap of findings: this is an
#: agent's judgement about this artifact, and on a `fixed` verdict it has
#: already committed in this worktree. The re-running node could not tell those
#: commits from a human's, so the brief it wrote told the human they had fixed
#: it themselves.
_GATE_REVIEW_PROMPT = (
    "An automated review of this work item's gate reported the following, and "
    "may have committed changes in this worktree itself. No human wrote it, and "
    "any commit it describes is the reviewer's, not a person's:\n\n{note}\n\n"
)

#: Which lead-in each `Steer.source` gets. `human` is absent deliberately: it is
#: the only one that depends on whether an artifact is on disk, so it is decided
#: below rather than by a lookup.
_SOURCE_PROMPTS = {"seeded": _SEEDED_PROMPT, "gate_review": _GATE_REVIEW_PROMPT}


def steer_prefix(
    artifact_kind: str | None, work_item_row, worktree, note: str, *, source: str = "human"
) -> str:
    """What a steered agent launch leads with.

    Keys on the artifact being on disk rather than on which gate was rejected,
    so it covers the spec gate and any future `produces:` task for free.

    Anything but `"human"` gets neither template that claims a person as the
    author, and no revision framing either -- those notes are about findings or
    repairs in the code, not about a document a human read.
    """
    template = _SOURCE_PROMPTS.get(source)
    if template is not None:
        return template.format(note=note)
    rel = _agent.artifact_path(artifact_kind, work_item_row["id"]) if artifact_kind else None
    if rel and (Path(worktree) / rel).is_file():
        return _REVISE_PROMPT.format(path=rel, steer=note)
    return _STEER_PROMPT.format(steer=note)


# What an agent is told about documents attached at intake. It follows the brief
# because the brief is the task and these are how it was already decided.
_ATTACHMENT_PROMPT = (
    "\n\n{lines}\nFollow the documents above; they are the agreed spec and plan "
    "for this work item. Do not re-plan."
)

# For a hook whose method is its own skill: the spec and plan state what the
# change was agreed to do, and the agent judges the change against them. The
# imperative above is addressed to an implementer -- `on.mr.describe` was given
# it and responded by running the full test suite in the node that writes an MR
# description (Kraft-s7c04.52).
_ATTACHMENT_REFERENCE = (
    "\n\n{lines}\nThose are the agreed spec and plan for this work item: they "
    "state what this change was agreed to do. Judge the change against them."
)

#: Appended for a binding that carries a `skill:`. Says only that *implementing*
#: is another node's job -- deliberately silent about verifying and judging,
#: which `carried_findings_note` asks every review hook to do.
METHOD_NOTE = (
    "\n\nImplementing this work item is a different node's job. Your own task is "
    "the `## Method` section of your system prompt; the brief above is the "
    "context that task runs in."
)

# A repo's own tracking-issue guidance (e.g. CLAUDE.md's beads workflow) tells
# any agent to close a bead once it judges the work done. That is right for a
# human session and wrong here: at implementation time, verify, review and
# merge are all still ahead, and closing a bead early makes the tracker say
# "done" for work a rejected gate or a red pipeline can still undo. Kraft
# closes the work item's own tracking bead itself, once the chain actually
# completes (see `kraft.executor.entry.close_beads` below) -- a worker closing
# any bead, including its own, only duplicates or races that (Kraft-a03).
BEAD_NOTE = (
    "\n\nDo not run `bd close` on any bead, including this work item's own "
    "tracking bead. Kraft closes it automatically once the whole chain "
    "completes; closing it here would mark work done before verify, review "
    "and merge have run."
)


def brief(work_item_row) -> str:
    """What the work item is, as an agent is told it.

    The title is a label; the description is the actual brief, and the spec node
    is expected to write a design from it. A work item with no description is
    the title alone — exactly the string this returned before descriptions
    existed.
    """
    description = work_item_row["description"]
    if not description:
        return work_item_row["title"]
    return f"{work_item_row['title']}\n\n{description}"


def attachment_note(attachments: list[dict], *, method_is_own: bool = False) -> str:
    """Documents attached at intake, as an agent is told about them.

    The implementer (`method_is_own=False`, the default) gets the imperative:
    follow the documents, do not re-plan. A hook with its own skill
    (`method_is_own=True`) gets the same paths but is told to judge the change
    against them, not to build from them.
    """
    if not attachments:
        return ""
    lines = "\n".join(f"{a['kind'].capitalize()}: {a['path']}" for a in attachments)
    template = _ATTACHMENT_REFERENCE if method_is_own else _ATTACHMENT_PROMPT
    return template.format(lines=lines)


# How the implementer reports where it is in the plan, so the board can say
# "3 of 6 · title" instead of leaving a human to read the log. The commit tag is
# the fallback Kraft reads when a report was never made.
_PROGRESS_NOTE = (
    "\n\nThis plan has {total} tasks. When you start task K, run "
    "`kraft item progress K`. Include `(task K)` in the subject of each "
    "commit for that task."
)


def progress_note(task: AgentTask, work_item_row, worktree) -> str:
    """Only for the task doing the work from the brief, and only for a plan with
    `## Task N` headings to count.

    "The task doing the work" is an agent task with no `skill:` of its own: a
    task that selects a skill states its own method (write a spec, judge a
    change), and only the one working from the brief walks a plan. V1 has no
    hook name to key this on, and keying it on a node id was already wrong
    (Kraft-s7c04.45).
    """
    if task.skill is not None:
        return ""
    tasks = _progress.tasks_for(work_item_row, Path(worktree))
    return _PROGRESS_NOTE.format(total=len(tasks)) if tasks else ""


#: What verify will run, shown to the node that can still act on it. 49c0cefd
#: changed 21 frontend files, read the justfile, and never ran `just e2e-ci`
#: (`grep -c e2e-ci` in its implementation log = 0); the flake that reached
#: verify instead cost $17.41 and ~3h there. The agent had the capability and
#: not the mapping (Kraft-s7c04.8).
#:
#: The scope TABLE, not a resolved selection: at implementation dispatch the
#: agent has written nothing, so the diff is empty and `_matched_scopes` fails
#: open to every scope. Printing "all of them" would teach it nothing. verify's
#: own selection is unchanged -- this hands the agent the same mapping so it
#: applies the same rule to what it actually changed.
_SCOPE_NOTE = (
    "\n\nBefore you finish, run the checks that gate the paths you changed. "
    "verify runs exactly these against your diff, and a failure there costs a "
    "full review round:\n{rows}\n"
    "A changed path matching no scope runs every scope."
)


def scope_note(task: AgentTask, repo_entry: dict | None) -> str:
    """The repo's path->command test mapping, for the task working from the
    brief only -- the same "no skill of its own" rule `progress_note` applies,
    and never a node id: a chain whose implementing node is called `build` must
    still get this (Kraft-s7c04.45).
    """
    if task.skill is not None:
        return ""
    repo = repo_entry or {}
    scopes = repo.get("test_scopes")
    if not scopes and repo.get("test_command"):
        scopes = [{"paths": ["**"], "command": repo["test_command"]}]
    if not scopes:
        return ""
    rows = "\n".join(f"  {', '.join(s['paths'])}\n      {s['command']}" for s in scopes)
    return _SCOPE_NOTE.format(rows=rows)


#: PARKED under Template Schema V1: `_last_review_session`'s other readers,
#: `carried_findings_note`, `previous_review_note` and `fix_attempt_note`, have
#: no `src/` caller. `review_package` is live again, delivered to an agent task
#: that declares `inputs: [review_package]` (`AgentTask.inputs`, Ruling 47);
#: carried findings and the continuity note have no declaration yet, and
#: `findings.resolve_identity` records what their absence costs
#: (`carried-findings-are-delivered-to-a-reviewing-task`,
#: `continuity-note-is-delivered-to-a-resumed-reviewer`, both unenforced).


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
    """The previous round's findings, for the reviewer about to measure again.

    "" when there is no previous round, so a work item's first and most
    important review is byte-identical to what it is today.

    **Parked, not live.** No `src/` caller under Template Schema V1: delivering the
    previous round's findings was keyed on legacy hook *names*, and V1 has no name to key
    on. The change under review came back as a declared `AgentTask` input
    (`inputs: [review_package]`); this has no declaration yet
    (`carried-findings-are-delivered-to-a-reviewing-task`, unenforced), which is why it is
    kept rather than deleted. Do not read it as describing what runs today.
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


def _session_note(row, template: str, summary_template: str) -> str:
    """One session's artifacts, by path. "" for no row at all, and a missing
    `session_summary_ref` drops that clause rather than interpolating a path
    the agent would spend a tool call discovering does not exist."""
    if row is None:
        return ""
    ref = row["session_summary_ref"]
    return template.format(
        result_path=row["result_path"],
        summary=summary_template.format(summary_ref=ref) if ref else "",
    )


def previous_review_note(row) -> str:
    """**Parked: see `carried_findings_note`.**"""
    return _session_note(row, _PREVIOUS_REVIEW, _PREVIOUS_REVIEW_SUMMARY)


def fix_attempt_note(row) -> str:
    """**Parked: see `carried_findings_note`.**"""
    return _session_note(row, _FIX_ATTEMPT, _FIX_ATTEMPT_SUMMARY)


#: A review session that did not finish is not a head anything was reviewed at.
#: Same "was this a real judgement" allowlist `dispatch._JUDGE_TRUSTED_STATUS`
#: and `gate_review._UNTRUSTWORTHY` apply, for the same reason.
_REVIEWED_STATUS = ("done", "done_with_concerns")


def _last_review_session(db, work_item_id: str, task_hook: str) -> sqlite3.Row | None:
    """The previous *completed* session on this hook, or None (Kraft-s7c04.1):
    where `review_package`'s range starts from a task's second session on.

    Its `head_sha` is the head that session was dispatched at.

    `worker_sessions.head_sha` is stamped by `dispatch.dispatch_node` at
    dispatch, so it is the commit that review was actually about. Read from the
    table rather than carried in a local, for the reason `last_measurement`'s
    docstring gives: `resuming.reconcile_current_node` re-enters `walk_node`
    after a crash and a loop holding its history in a stack frame forgets
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
            f"AND head_sha IS NOT NULL AND status IN ({','.join('?' * len(_REVIEWED_STATUS))}) "
            "ORDER BY created_at DESC LIMIT 1",
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
    previous = _last_review_session(db, work_item_id, task_hook)
    since = previous["head_sha"] if previous else None
    if since and git_read(worktree, "rev-parse", "--verify", f"{since}^{{commit}}") is None:
        since = None
    path = _review.write_package(
        run_dirs.results, worktree, row["base_ref"], session_id, since=since
    )
    return str(path) if path else None


def previous_attempt_note(previous_fix) -> str:
    """The fix-prompt addendum for `kraft.executor.walk`'s previous-fix lookup,
    or "" if there was no previous attempt. A plain function so the no-summary
    branch is testable without driving a session through the executor."""
    if previous_fix is None:
        return ""
    note = _FIX_PREVIOUS_RESULT.format(result_path=previous_fix["result_path"])
    if previous_fix["session_summary_ref"]:
        note += _FIX_PREVIOUS_SUMMARY.format(summary_ref=previous_fix["session_summary_ref"])
    return note


#: What a node's declared stuck escalation is told, after its own prompt
#: (`stuck-escalation-is-an-exec-node-control`). The last paragraph is the
#: contract `walk._escalate_stuck` acts on: a clean finish retries the node from
#: its first step, and anything else leaves the item for a human.
_STUCK_ESCALATION = (
    "{prompt}\n\n"
    "Node {node_id} of this work item is stuck: its recovery and its fix loop "
    "could not advance it. Why it stopped:\n\n{reason}\n\n"
    "Resolve what is blocking it if you can, and commit what you change. When "
    "you finish cleanly, Kraft reruns node {node_id} from its first step. If you "
    "cannot resolve it, report `failed`; if a person has to decide something, "
    "report `needs_context` and ask in `question`. Either way the work item "
    "then waits for a human.\n"
)


def stuck_escalation_instruction(task: ResolvedTask, node, reason: str) -> str:
    prompt = getattr(task.task, "prompt", "")
    return _STUCK_ESCALATION.format(prompt=prompt, node_id=node.id, reason=reason)


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
