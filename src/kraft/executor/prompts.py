from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from kraft import findings as _findings
from kraft import item_paths
from kraft import progress as _progress
from kraft import store as _store
from kraft.config import RepoEntry, git_read
from kraft.executor.round_prompts import AGENT_RESUMED_NOTE as AGENT_RESUMED_NOTE
from kraft.executor.round_prompts import JUDGE_PROMPT as JUDGE_PROMPT
from kraft.executor.round_prompts import bot_feedback as bot_feedback
from kraft.executor.round_prompts import bot_feedback_source as bot_feedback_source
from kraft.executor.round_prompts import carried_findings_note as carried_findings_note
from kraft.executor.round_prompts import deferred_findings_note as deferred_findings_note
from kraft.executor.round_prompts import fix_attempt_note as fix_attempt_note
from kraft.executor.round_prompts import format_findings as format_findings
from kraft.executor.round_prompts import format_judge_history as format_judge_history
from kraft.executor.round_prompts import last_review_session as last_review_session
from kraft.executor.round_prompts import named_with_kind as named_with_kind
from kraft.executor.round_prompts import previous_attempt_note as previous_attempt_note
from kraft.executor.round_prompts import previous_review_note as previous_review_note
from kraft.executor.round_prompts import review_package as review_package
from kraft.executor.round_prompts import round_history_note as round_history_note
from kraft.executor.round_prompts import (
    stuck_escalation_instruction as stuck_escalation_instruction,
)
from kraft.executor.round_prompts import summary_at as summary_at
from kraft.executor.round_prompts import triage_verdict_note as triage_verdict_note
from kraft.templates.models import AgentTask
from kraft.worker import callback as _callback

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


#: Appended to every recovery's note (`dispatch.run_recovery`): how a repair
#: that concludes no repair can help says what a person should do instead, in
#: a form the stop offers as one command (`stops.suggestion`, Kraft-s7c04.27).
#: On 2026-09-15 a repair concluded "a human should skip" in prose twice, and
#: nothing could offer it.
SUGGEST_ACTION = (
    "If you conclude that no repair can make it pass -- a person should skip "
    "this node, retry it later, or abandon the work item -- do not force it: "
    'report status "failed" and add to your result file "suggested_action": '
    '{"action": "skip" | "retry" | "abandon", "reason": "<why, one sentence>"}. '
    "Kraft offers it to the person as one command."
)


def task_failure_note(task: str, status: str, session=None, *, run_dirs) -> str:
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
            summary = summary_at(session["session_summary_ref"], run_dirs)
            note += f", with a session summary at {summary}"
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
    "This branch was rebased onto a newer {base} before opening its MR. "
    "Commits landed upstream while this work was in progress:\n\n{body}\n\n"
    "Check whether they affect this work; fix it if so.\n\n"
)

#: Chars, not bytes -- this is a prompt prefix, not a diff view. A commit log
#: plus `--stat` is meant to be a pointer at what to go look at, not the
#: change itself; a full diff would cost far more context than the note is
#: worth.
_REBASE_NOTE_MAX = 2000


def rebase_drift_note(worktree, base: str, old_base: str, new_base: str) -> str:
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
    return _REBASE_PROMPT.format(base=base, body=body)


#: What the rebase-conflict resolver (`walk.resolve_rebase_conflict`,
#: Kraft-s7c04.23) is dispatched with. Two jobs, not one -- resolving the
#: conflict is mechanical; judging whether the upstream commits it rebased
#: onto change what this work item is supposed to do is the reason an agent
#: is doing this rather than a script (design §4.1). A real diff, not a
#: `--stat`: that relevance judgement cannot be made from file names alone.
_REBASE_RESOLVE_PROMPT = (
    "A rebase conflict stopped this work item. Branch {branch} could not be "
    "replayed onto {new_base} (it was based on {old_base}); the rebase has "
    "already been aborted{folder} git reported:\n\n"
    "{conflict}\n\n"
    "What landed upstream, {old_base}..{new_base}:\n\n{diff}\n\n"
    "{attachments}"
    "You have two jobs:\n"
    "1. {rerun}\n"
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

#: Where the resolver works and how it re-runs the rebase there: a clean
#: worktree; or in place a person's checkout, whose uncommitted edits git
#: refuses a plain rebase over and the agent may not stash (`agent._IN_PLACE`),
#: so `--autostash` sets them aside, as Kraft's own rebase does
#: (`inplace.rebase_in_checkout`). A put-back that fails stops the item after
#: the task (`inplace.check_task`).
_RESOLVE_IN = {
    False: (
        " and the worktree is clean.",
        "{run} the rebase (`git rebase {new_base}`) and resolve the conflict "
        "yourself, then finish it (`git rebase --continue`).",
    ),
    True: (
        ". This is a person's own checkout: it may hold their uncommitted "
        "edits, which you must neither commit nor discard.",
        "{run} the rebase with `git -c rebase.updateRefs=false rebase "
        "--autostash {new_base}`: git sets their edits aside for the rebase and "
        "puts them back when it ends. That is the one exception to the rule "
        "against `git stash`; never stash, pop or drop anything yourself. "
        "Resolve the conflict by editing the conflicted files and `git add`-ing "
        "them by literal path, then finish it (`git rebase --continue`). If you "
        "cannot resolve it, `git rebase --abort` puts their edits back too. When "
        "the rebase ends and git says applying the autostash resulted in "
        "conflicts, the conflict markers in those files are the person's to "
        "resolve: leave those files and the stash alone, and report "
        "`done_with_concerns` naming them. If git refuses to start because an "
        "untracked file of theirs would be overwritten, do not move or remove "
        "it: report `failed`.",
    ),
}

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
    *,
    in_place: bool = False,
) -> str:
    log = git_read(worktree, "log", "--oneline", f"{old_base}..{new_base}") or "(no log)"
    diff = git_read(worktree, "diff", f"{old_base}..{new_base}") or "(no changes)"
    body = f"Commits:\n{log}\n\nDiff:\n{diff}"
    if len(body) > _REBASE_RESOLVE_DIFF_MAX:
        body = body[:_REBASE_RESOLVE_DIFF_MAX] + "\n... (truncated)"
    folder, rerun = _RESOLVE_IN[in_place]
    return _REBASE_RESOLVE_PROMPT.format(
        folder=folder,
        rerun=rerun.format(run="Re-run", new_base=new_base),
        branch=branch,
        old_base=old_base,
        new_base=new_base,
        conflict=conflict.strip(),
        diff=body,
        attachments=attachment_note(attachments, method_is_own=False),
    )


#: What the `on_merge_rejected` repair (`walk._repair_merge_rejection`) is
#: dispatched with: the drop the forge recorded (`merge_queue_dropped`), so it
#: does not have to rediscover why the train or queue rejected the merge
#: request, and what happened to it (`_MERGE_REJECTED_WHAT`).
_MERGE_REJECTED_PROMPT = (
    "A merge train or merge queue rejected this merge request. Branch {branch} "
    "{what}{folder}\n\n"
    "Reason: {reason}\n"
    "Pipeline: {url}\n"
    "Failed jobs:\n{jobs}\n\n"
    "{where}"
    "{landed}"
    "{rebase}"
    "Fetch and inspect origin/{base}, reproduce the failure locally where you "
    "can, and fix the code.\n"
    "Commit your fix on this branch. Do not push and do not merge: Kraft does both.\n"
)

#: What happened to the merge request, by the drop's cause: a merge request
#: that never reached the train never passed its own pipeline either.
_MERGE_REJECTED_WHAT = {
    "code": "passed its own pipeline, but failed once combined with {base} as it is now",
    "not_added": (
        "never reached the merge train or queue: its own pipeline failed after Kraft "
        "asked for the merge, checked against {base}"
    ),
    "conflict": "no longer merges cleanly with {base} as it is now",
}

#: Where the repair works: the item's own worktree, or in place a person's
#: checkout, worded as `_RESOLVE_IN` words it for the conflict resolver.
_MERGE_REJECTED_IN = {
    False: ".",
    True: _RESOLVE_IN[True][0],
}


def merge_rejected_note(
    branch: str,
    base: str,
    drop: Mapping,
    *,
    in_place: bool,
    landed: Sequence[str] = (),
    member: str | None = None,
) -> str:
    """What the merge-rejection repair is told: the drop's reason, the train's
    or queue's URL and each failed job. A branch Kraft's own forced rebase did
    not move is the repair's to rebase first. `landed` is a multi-repo item's
    targets already merged: the repair cannot change those, and asks instead.
    `member` is the workspace member checkout whose merge request was dropped,
    when it is not the item's own repository; `base` is then that member's."""
    jobs = "\n".join(f"- {job}" for job in drop.get("failed_jobs") or ()) or "- (none named)"
    landed_text = (
        "Already merged: "
        + ", ".join(landed)
        + ". These have already landed and cannot change here; if the fix belongs "
        "in one of them, report needs_context.\n\n"
        if landed
        else ""
    )
    rebase = ""
    if not drop.get("rebased"):
        rerun = _RESOLVE_IN[in_place][1].format(run="Run", new_base=f"origin/{base}")
        rebase = f"First rebase onto origin/{base} and resolve any conflict. {rerun}\n\n"
    where = f"Its merge request is the one in {member}; fix it there.\n\n" if member else ""
    return _MERGE_REJECTED_PROMPT.format(
        where=where,
        branch=branch,
        base=base,
        what=_MERGE_REJECTED_WHAT.get(drop.get("cause"), _MERGE_REJECTED_WHAT["code"]).format(
            base=base
        ),
        folder=_MERGE_REJECTED_IN[in_place],
        reason=drop.get("reason") or "no reason given",
        url=drop.get("url") or "(none given)",
        jobs=jobs,
        landed=landed_text,
        rebase=rebase,
    )


def merge_rewind_note(drop: Mapping, restart_from: str) -> str:
    """The seeded steer the rewound span starts with: why it went back."""
    return (
        f"Rewound to {restart_from} because the merge train or queue rejected the "
        f"merge request: {drop.get('reason') or 'no reason given'}"
        + (f" ({drop['url']})" if drop.get("url") else "")
        + (f". Failed jobs: {', '.join(drop['failed_jobs'])}." if drop.get("failed_jobs") else ".")
    )


def escalation_rewind_note(node_id: str, restart_from: str) -> str:
    """The same, for a stuck escalation that committed on merge node
    `node_id` (`ESCALATION_COMMITTED`)."""
    return (
        f"Rewound to {restart_from} because the escalation of node {node_id}, which "
        "merges the merge request, committed a change: it goes back through the "
        "final review before it can merge."
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
    artifact_kind: str | None,
    work_item_row,
    worktree,
    note: str,
    *,
    run_dirs,
    source: str = "human",
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
    wid = work_item_row["id"]
    name = (
        item_paths.document(run_dirs, wid, artifact_kind, worktree=worktree)
        if artifact_kind
        else None
    )
    if name is not None:
        path = item_paths.path_of(run_dirs, wid, name, worktree=worktree)
        return _REVISE_PROMPT.format(path=path, steer=note)
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
#: For a worker whose sandbox has no route to Kraft: the commit tag alone.
_PROGRESS_NOTE_NO_CHANNEL = (
    "\n\nThis plan has {total} tasks. Include `(task K)` in the subject of each commit for task K."
)


def progress_note(
    task: AgentTask, work_item_row, worktree, db=None, sandbox=None, *, run_dirs
) -> str:
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
    if db is not None and _progress.rework_run(db, work_item_row):
        # Kraft-hj2q9: a run a rejection bounced back follows the note, not the plan.
        return ""
    # In place, the plan on the item's branch: an escalation's or a reply's
    # prompt is built before the take, while the checkout may be the person's.
    in_place = _store.is_in_place(work_item_row)
    tasks = _progress.tasks_for(work_item_row, Path(worktree), run_dirs, in_place=in_place)
    note = _PROGRESS_NOTE if _callback.reachable(sandbox) else _PROGRESS_NOTE_NO_CHANNEL
    return note.format(total=len(tasks)) if tasks else ""


#: The item's unanswered review threads, framed by role: a working agent
#: (`task.skill is None`) is told to address and reply to each; a reviewer is
#: told to judge the change against them and never reply itself -- that is
#: the implementing agent's job, and a reviewer replying would blur who is
#: answering whom (review threads anywhere §1).
_THREADS_FOR_WORKER = (
    "\n\nA person reviewing this work left these threads. Address each one, then "
    'answer it: `kraft item reply <thread-id> --claim fixed --body "..."` when you '
    "changed the code, `--claim answered` when you are replying without a change.\n\n"
)
_THREADS_FOR_WORKER_NO_CHANNEL = (
    "\n\nA person reviewing this work left these threads. Address each one. "
    "This sandbox has no route to Kraft, so you cannot reply to them yourself: "
    "say in your result's summary what you did about each, by thread id.\n\n"
)
_THREADS_FOR_REVIEWER = (
    "\n\nA person reviewing this work left these threads. Check whether the change "
    "addresses each one. Report an unaddressed `must_fix` thread as a finding. Do not "
    "reply to the threads; that is the implementing agent's job.\n\n"
)


def review_threads_note(task: AgentTask, work_item_row, db, note: str | None, sandbox=None) -> str:
    """The item's unanswered review threads, for every agent launch, framed by
    role (review threads anywhere §1). A thread the note already lists -- the
    rejection note on a rework run -- is left out, so none appears twice."""
    threads = db.read(lambda c: _store.unanswered(c, work_item_row["id"]))
    threads = [t for t in threads if f"[{t['id']}]" not in (note or "")]
    body = _store.render_threads(threads)
    if not body:
        return ""
    if task.skill is not None:
        return _THREADS_FOR_REVIEWER + body
    return (
        _THREADS_FOR_WORKER if _callback.reachable(sandbox) else _THREADS_FOR_WORKER_NO_CHANNEL
    ) + body


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
#:
#: The table is what verification runs, never what the implementer runs: a
#: repo's scope command can be its whole suite (`just ci-test`), and an
#: implementer told to run it ran the suite twice before verification ran it
#: again (Kraft-jl7un). It picks its own tests by path instead.
_SCOPE_NOTE = (
    "\n\nBefore you finish, run the tests you choose yourself: the tests for "
    "the files you touched and for the code related to them, by path. Do not "
    "run the commands below: they run against your diff after you finish "
    "(verification, then CI), and a failure there costs a full review round. "
    "They say which kind of test gates which paths, so you know what to pick "
    "(an e2e spec for a UI change, say):\n{rows}\n"
    "A changed path matching no scope runs every scope."
)


def scope_note(task: AgentTask, repo_entry: RepoEntry | None) -> str:
    """The repo's path->command test mapping, for the task working from the
    brief only -- the same "no skill of its own" rule `progress_note` applies,
    and never a node id: a chain whose implementing node is called `build` must
    still get this (Kraft-s7c04.45).
    """
    if task.skill is not None or repo_entry is None:
        return ""
    scopes = [(s.paths, s.command) for s in repo_entry.test_scopes or ()]
    if not scopes and repo_entry.test_command:
        scopes = [(["**"], repo_entry.test_command)]
    if not scopes:
        return ""
    rows = "\n".join(f"  {', '.join(paths)}\n      {command}" for paths, command in scopes)
    return _SCOPE_NOTE.format(rows=rows)
