"""The merge request itself: its description, its title, and whether it can
land -- vocabulary both CLI backends share.
"""

from __future__ import annotations

import json

from kraft.adapters.forge.models import ForgeError, MRRef


def mr_body(work_item_id: str, branch: str, commits: tuple[str, ...]) -> str:
    """The merge request description, rebuilt from the branch as it stands.

    Rebuilt and not appended: `open_mr` runs before verify and mr_checks add
    their commits, so a description written once describes a branch that no
    longer exists (Kraft-c09h).
    """
    lines = [f"Opened by Kraft for work item {work_item_id}.", ""]
    if commits:
        lines.append("Commits on this branch:")
        lines.append("")
        lines += [f"- {c}" for c in commits]
        lines.append("")
    lines.append(f"Branch `{branch}`. Review the diff and the pipeline before merging.")
    return "\n".join(lines)


#: A forge title is a headline; a Kraft work item title is a paragraph (the
#: batch that fixed this bug had a 360-character one). First line, clipped.
MR_TITLE_MAX = 72


def mr_title(title: str) -> str:
    """The work item title, cut down to something a merge request can wear."""
    head = title.strip().splitlines()[0].strip() if title.strip() else ""
    if not head:
        return "Kraft work item"
    return head if len(head) <= MR_TITLE_MAX else head[: MR_TITLE_MAX - 1].rstrip() + "…"


#: Merge-request states that need a *code* change before anything can land:
#: glab's `detailed_merge_status`/`merge_status`, gh's `mergeable` and
#: `mergeStateStatus`.
_UNMERGEABLE = {
    "conflict",
    "need_rebase",
    "broken_status",
    "cannot_be_merged",
    "CONFLICTING",
    "DIRTY",
}
_MERGEABLE = {"mergeable", "can_be_merged", "MERGEABLE"}


def mergeable(*states: str) -> bool | None:
    """Can this merge request land, as far as the forge will say.

    Deliberately the opposite convention to glab's own pipeline vocabulary,
    where unknown is failure: `mr_checks` runs *before* the human_review gate,
    so `not_approved`, `ci_still_running`, `discussions_not_resolved`,
    `draft_status`, `checking`, `unchecked` and `UNKNOWN` are the ordinary
    states of a healthy merge request at this node, and mapping them to
    failure would fail the node on every repo with an approval rule. Only
    states that need a code change fail here. States that need a *person* are
    the gate's business, and the merge node reads the merge back rather than
    letting one through silently (Kraft-79x3).

    Variadic because GitHub answers in two fields and either can carry the bad
    news.
    """
    if any(s in _UNMERGEABLE for s in states):
        return False
    if any(s in _MERGEABLE for s in states):
        return True
    return None


def pick_mr(rows: list[MRRef]) -> MRRef | None:
    """The open merge request for a branch, else the first one the forge listed."""
    return next((r for r in rows if r.state == "open"), rows[0] if rows else None)


def parse_json(raw: str, what: str):
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ForgeError(f"{what} did not return JSON: {raw[:200]!r}") from exc
