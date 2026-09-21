"""The merge request itself: its description, its title, and whether it can
land -- vocabulary both CLI backends share.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kraft.adapters.forge.models import (
    MR,
    ApprovalState,
    ForgeError,
    MRRef,
    ReviewResult,
)
from kraft.automated_review import AutomatedReview
from kraft.index.ingest import split_front_matter
from kraft.worker.worktree_read import read_worktree_file

#: GitHub rejects a PR/MR body over 65 536 characters; GitLab's own limit is
#: far higher. The same body is built for both backends, so the cap is picked
#: from the smaller one, not from whichever forge this item happens to use.
MR_BODY_MAX_CHARS = 65_536

_TRUNCATION_MARKER = "\n\n*(truncated -- read the diff, this description did not fit)*"

#: One agent-authored scalar's ceiling. A label or a username is short; 200
#: chars is generous for both and still refuses a file pasted into a field.
_META_SCALAR_MAX = 200
#: How many labels/assignees/reviewers are plausible. Past this the agent is
#: not labelling, it is looping.
_META_LIST_MAX = 10
#: The metadata is one page of prose plus a few short fields.
_META_READ_MAX_BYTES = 200_000


@dataclass(frozen=True)
class MRMeta:
    """What an agent decided this merge request should say and carry.

    Every field is optional and independently droppable: this is
    agent-authored, so a value that does not survive validation must cost its
    own field only -- never the description, and never the merge request.
    """

    title: str | None = None
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    description: str = ""


#: The empty metadata every fallback path returns -- one shared instance so
#: mutable-default lint rules have a module-level singleton to point at.
_EMPTY_META = MRMeta()


def _clean_scalar(value) -> str | None:
    """One agent-authored value, or None if it must not reach argv.

    `git.run_git` passes a list, so there is no shell here -- but `glab` and
    `gh` read an argument beginning with `-` as a flag, which is the one way a
    label can become an option.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith("-") or len(text) > _META_SCALAR_MAX:
        return None
    if any(ch < " " or ch == "\x7f" for ch in text):
        return None
    return text


def _clean_list(value) -> tuple[str, ...]:
    """A front-matter list, cleaned entry by entry. A scalar where a list
    belongs is dropped whole rather than read as one entry: `labels: release`
    is a mistake about the contract, not a one-label MR."""
    if not isinstance(value, list):
        return ()
    cleaned = [c for v in value if (c := _clean_scalar(v))]
    return tuple(cleaned[:_META_LIST_MAX])


def read_mr_meta(worktree: Path, work_item_id: str) -> MRMeta:
    """The `mr_meta` artifact this item's `on.mr.describe` session wrote.

    Never raises and never partially fails: absent, outside the worktree,
    unreadable, malformed front matter, or a field of the wrong shape each
    cost exactly themselves. An MR must still open when the metadata is gone
    (the rule session-summary stitching followed, before it was deleted).
    """
    from kraft.adapters.agent import artifact_path

    result, _reason = read_worktree_file(
        worktree, artifact_path("mr_meta", work_item_id), _META_READ_MAX_BYTES
    )
    if result is None:
        return MRMeta()
    front, body = split_front_matter(result.text)
    raw_title = front.get("title")
    # `mr_title` clips first, the way a work item's own title already is
    # (multi-line -> first line, cut to 72 chars) -- only then does the clip's
    # *result* go through the same leading-dash/control-char/length gate every
    # other scalar does.
    title = (
        _clean_scalar(mr_title(raw_title))
        if isinstance(raw_title, str) and raw_title.strip()
        else None
    )
    return MRMeta(
        title=title,
        labels=_clean_list(front.get("labels")),
        assignees=_clean_list(front.get("assignees")),
        reviewers=_clean_list(front.get("reviewers")),
        description=body.strip(),
    )


def mr_title_for(work_item_title: str, meta: MRMeta) -> str:
    """The authored title if there is one, else the work item's own -- both
    through `mr_title`'s clip, so a title built straight from `MRMeta` (as a
    test does, bypassing `read_mr_meta`'s own clip) still gets it."""
    return mr_title(meta.title or work_item_title)


def meta_flags(meta: MRMeta) -> list[str]:
    """The `--label/--assignee/--reviewer` arguments both CLIs spell the same
    way. Omitted entirely when empty: `--label ""` is a real, empty value to
    both, not an absence."""
    flags: list[str] = []
    for flag, values in (
        ("--label", meta.labels),
        ("--assignee", meta.assignees),
        ("--reviewer", meta.reviewers),
    ):
        if values:
            flags += [flag, ",".join(values)]
    return flags


def _default_body(work_item_id: str, branch: str, commits: tuple[str, ...]) -> str:
    """Today's body: a fixed preamble and the commit list. What `mr_body`
    falls back to whenever there is no summary prose worth leading with --
    this must stay byte-for-byte what it always was (spec §5's "done when")."""
    lines = [f"Opened by Kraft for work item {work_item_id}.", ""]
    if commits:
        lines.append("Commits on this branch:")
        lines.append("")
        lines += [f"- {c}" for c in commits]
        lines.append("")
    lines.append(f"Branch `{branch}`. Review the diff and the pipeline before merging.")
    return "\n".join(lines)


def mr_body(
    work_item_id: str,
    branch: str,
    commits: tuple[str, ...],
    meta: MRMeta = _EMPTY_META,
) -> str:
    """The merge request description, rebuilt from the branch as it stands.

    Rebuilt and not appended: `open_mr` runs before verify and mr_checks add
    their commits, so a description written once describes a branch that no
    longer exists (Kraft-c09h). An authored body goes stale the same way, for
    the same reason -- every call rebuilds from scratch.

    Falls back to today's fixed body when there is no authored description to
    lead with: this is the one path with no filesystem or database of its
    own, so a caller that could not resolve the artifact (missing node,
    missing file, a path that failed containment) hands in an empty `MRMeta`
    and gets exactly what `open_mr` has always produced.
    """
    if not meta.description:
        return _default_body(work_item_id, branch, commits)

    parts = [meta.description]
    if commits:
        commit_list = "\n".join(f"- {c}" for c in commits)
        parts.append(
            f"<details>\n<summary>Commits on this branch</summary>\n\n{commit_list}\n\n</details>"
        )
    parts.append(f"Branch `{branch}`. Review the diff and the pipeline before merging.")
    body = "\n\n".join(parts)

    if len(body) > MR_BODY_MAX_CHARS:
        cut = MR_BODY_MAX_CHARS - len(_TRUNCATION_MARKER)
        # Land on a newline rather than mid-line, if one is close enough to be
        # worth it -- cosmetic only, so a body with no newline in the last
        # stretch just cuts at `cut` rather than searching arbitrarily far back.
        newline = body.rfind("\n", 0, cut)
        if newline > cut - 200:
            cut = newline
        body = body[:cut] + _TRUNCATION_MARKER

    return body


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


#: An approval rule, not a code problem: GitLab's `detailed_merge_status`
#: (`not_approved`, `requested_changes`, `policies_denied`) and GitHub's
#: `reviewDecision` (`REVIEW_REQUIRED`, `CHANGES_REQUESTED`) all mean the
#: same thing -- someone has to approve or re-approve on the forge, and
#: nothing Kraft does to the branch changes that.
_NOT_APPROVED = {
    "not_approved",
    "requested_changes",
    "policies_denied",
    "REVIEW_REQUIRED",
    "CHANGES_REQUESTED",
}

#: GitLab's own draft indicator inside `detailed_merge_status`; GitHub
#: answers the same question as `mergeStateStatus: "DRAFT"`.
_DRAFT = {"draft_status", "DRAFT"}


def classify_block_reason(*states: str) -> Literal["draft", "conflict", "not_approved"] | None:
    """Why a merge request can't land, distinct from whether it can
    (`mergeable`, above): a draft, a conflict, and a missing approval are
    three different problems with three different fixes, and code that
    only reads `mergeable`'s bool can't tell them apart.

    Variadic for the same reason `mergeable` is: GitHub answers across
    two or three fields (`mergeable`, `mergeStateStatus`, `reviewDecision`),
    and any one of them can carry the reason. Conflict wins ties -- a
    state that is somehow both a conflict and pending approval is a code
    problem first, since nothing else is worth evaluating until the
    branch itself is fixed. `None` when nothing here recognises any state
    given, same as `mergeable`'s own `None`.
    """
    if any(s in _UNMERGEABLE for s in states):
        return "conflict"
    if any(s in _DRAFT for s in states):
        return "draft"
    if any(s in _NOT_APPROVED for s in states):
        return "not_approved"
    return None


def pick_mr(rows: list[MRRef]) -> MRRef | None:
    """The open merge request for a branch, else the first one the forge listed."""
    return next((r for r in rows if r.state == "open"), rows[0] if rows else None)


def parse_json(raw: str, what: str):
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ForgeError(f"{what} did not return JSON: {raw[:200]!r}") from exc


class CliWaits:
    """The two external-wait reads `gh` and `glab` answer the same way. A
    mixin rather than a function because `FakeForge` answers both from a
    script instead, and every backend is asked through the one `Forge`
    method."""

    async def approval_state(self, *, repo: Path, branch: str) -> ApprovalState:
        """Pending while an approval rule is unmet. Both CLIs already name
        that `block_reason == "not_approved"` (`classify_block_reason`), so
        this reads that one answer rather than parsing the fields again."""
        ci = await self.ci_status(repo=repo, mr=MR(number=0, url=""), branch=branch)
        return "pending" if ci.block_reason == "not_approved" else "approved"

    async def automated_review(
        self, *, repo: Path, branch: str, reviewer: AutomatedReview | None
    ) -> ReviewResult:
        """The repository's named reviewer, read off the merge request's
        current head (Ruling 171). No reviewer named: nothing is asked, and
        the review settles clean as not configured."""
        if reviewer is None:
            return ReviewResult(
                "clean", detail="no automated reviewer configured", configured=False
            )
        if reviewer.bot is not None:
            return await self._bot_review(repo, reviewer.bot)
        return await self._check_review(repo, reviewer.check)


def same_login(a: str, b: str) -> bool:
    """A forge login, as a repository names it and as the forge reports it:
    GitHub reports an app as `name[bot]`, and case never matters."""
    return a.lower().removesuffix("[bot]") == b.lower().removesuffix("[bot]")
