from __future__ import annotations

from pathlib import Path

from kraft import findings as _findings
from kraft import progress as _progress
from kraft import review as _review
from kraft.adapters import agent as _agent
from kraft.config import git_read
from kraft.templates import Registry

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

#: Handed to fix cycle N > 1: cycle N-1's own result file, by path, not by
#: content -- pasting the file costs tokens on every cycle of every work item,
#: and the agent can already read a path itself (spec §4).
_FIX_PREVIOUS_RESULT = "\n\nYour previous attempt's result file is at {result_path}."
_FIX_PREVIOUS_SUMMARY = " Its session summary is at {summary_ref}."


def format_findings(found: list[_findings.Finding], repeats: set[str]) -> str:
    lines = []
    for f in found:
        where = f"{f.file}:{f.line}" if f.file and f.line else (f.file or "—")
        tag = "REPEAT " if f.fingerprint in repeats else ""
        lines.append(f"- {tag}[{f.severity}] {where} — {f.message} ({f.source_plugin})")
    return "\n".join(lines)


#: What a retry with no explicit steer and no rejection to fall back on leads
#: with (Kraft-7sec, second half): the same per-finding bullets a fix cycle's
#: own FIX_FINDINGS already uses -- not a second findings format to keep in
#: sync with, just a different lead-in sentence, because no human wrote this one.
_SEEDED_FINDINGS_STEER = "Findings the last review of this node left unresolved:\n{findings}"


def seeded_findings_note(found: list[_findings.Finding]) -> str:
    return _SEEDED_FINDINGS_STEER.format(findings=format_findings(found, repeats=set()))


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
    "Kraft carried this forward from the last review of this node; no human typed it:\n\n{note}\n\n"
)


def steer_prefix(binding: dict, work_item_row, worktree, note: str, *, human: bool = True) -> str:
    """What a steered agent launch leads with.

    Keys on the artifact being on disk rather than on which gate was rejected,
    so it covers the spec gate and any future `artifact:` binding for free.

    `human=False` is Kraft's own seeded note: it gets neither template that
    claims an author, and no revision framing either -- the note is about
    findings in the code, not about a document a human read.
    """
    if not human:
        return _SEEDED_PROMPT.format(note=note)
    artifact_kind = binding.get("artifact")
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


def attachment_note(attachments: list[dict]) -> str:
    if not attachments:
        return ""
    lines = "\n".join(f"{a['kind'].capitalize()}: {a['path']}" for a in attachments)
    return _ATTACHMENT_PROMPT.format(lines=lines)


# How the implementer reports where it is in the plan, so the board can say
# "Task 3 of 6" instead of leaving a human to read the log. The commit tag is
# the fallback Kraft reads when a report was never made.
_PROGRESS_NOTE = (
    "\n\nThis plan has {total} tasks. When you start task K, run "
    "`kraft item progress K`. Include `(task K)` in the subject of each "
    "commit for that task."
)


def progress_note(task_hook: str, work_item_row, worktree) -> str:
    """Only on the implementation hook, and only for a plan with `## Task N`
    headings to count."""
    if task_hook != _progress.IMPLEMENTATION_HOOK:
        return ""
    tasks = _progress.tasks_for(work_item_row, Path(worktree))
    return _PROGRESS_NOTE.format(total=len(tasks)) if tasks else ""


#: The hooks whose job is to judge a change rather than make one. They are the
#: only ones handed a review package: everything else is working *in* the diff.
REVIEW_HOOKS = frozenset({"on.review.local.run", "on.review.mr.run"})


def review_package(
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


def named_with_kind(hook: str, registry: Registry) -> str:
    """`hook`, with its binding's `kind` alongside for a failure reason
    (Kraft-5m7t) -- `[forge]`/`[builtin]`/`[subprocess]` reads as "no agent
    here to steer" without a human having to open registry.yaml to check."""
    kind = registry.hooks.get(hook, {}).get("kind")
    return f"{hook} [{kind}]" if kind else hook


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
            text = "; ".join(f"[{f.severity}] {f.fingerprint} {f.message}" for f in found)
        else:
            text = "clean"
        fix_note = (
            f" (that round's fix: {h['fix_result_path']})" if h.get("fix_result_path") else ""
        )
        lines.append(f"round {h['round']}: {text}{fix_note}")
    return "\n".join(lines)
