"""One producer for "what changed", read by two consumers.

`GET /work-items/{wid}/diff` renders the change for the human at a gate;
`write_package` writes it to a file a review agent is handed by path. Sub-project
F spec §9 makes that one function on purpose: the human approving a review and
the reviewer that produced it must be looking at the same change, and two
separately-written producers are two things that eventually disagree about what
the change is.

Two ranges, not one. `read_change(wt, base)` is the working tree against `base`
-- an agent that wrote files without committing them is the normal mid-chain
state, and a committed-only diff would show an empty change set while the work
sat on disk. `read_change(wt, base, head="HEAD")` is `base..HEAD`, what earlier
nodes finished and committed. The gate viewer shows both, apart (Kraft-nceo).

`write_package` hands a review agent one range, and from the second round of a
fix loop onward that range starts at the head the previous review was taken at
rather than at `base` (`since`, Kraft-s7c04.1). Re-reading the whole branch
every round is what made `verify` half of all Kraft spend: 43717ee6 measured
the same 38-file diff seven times. The package says which range it is showing
and names the git command for the rest, so nothing is hidden -- only unpasted.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from kraft import config as _config

#: Wider than git's default 3. A reviewer holding ten lines of context per hunk
#: can judge most changes without opening the file: "the diff's context lines
#: ARE the changed files". Only the package uses it -- the gate viewer renders
#: in a browser, where the extra context is scrolling, not evidence.
PACKAGE_CONTEXT = 10


class Change(NamedTuple):
    commits: list[str]
    files: list[dict]
    diff: str
    untracked: list[str]


def read_change(
    worktree: Path, base: str, *, head: str | None = None, context: int | None = None
) -> Change | None:
    """The change in `worktree` against `base`, or None if git itself failed.

    With `head` set the range is `base..head` and nothing uncommitted is in it,
    so `untracked` is `[]`: `git status --porcelain` describes the working tree
    and has no meaning for a committed range.

    None is not "no changes": returning an empty diff for a git failure is
    indistinguishable from a clean tree to whoever is about to approve it.
    """
    rev = [base] + ([head] if head else [])
    diff_args = ["diff"] + ([f"-U{context}"] if context is not None else []) + rev
    # strip=False: a diff whose last line is blank context is still that diff
    body = _config.git_read(worktree, *diff_args, strip=False)
    numstat = _config.git_read(worktree, "diff", "--numstat", *rev)
    if body is None or numstat is None:
        return None
    untracked: list[str] = []
    if head is None:
        status = _config.git_read(worktree, "status", "--porcelain", "-uall")
        if status is None:
            return None
        untracked = [ln[3:] for ln in status.splitlines() if ln.startswith("?? ")]
    # A repo with no commits past `base` is normal, not a failure, so an empty
    # log is not folded in with the None checks above.
    log = _config.git_read(worktree, "log", "--oneline", f"{base}..{head or 'HEAD'}") or ""

    files = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            ins, dels, path = parts
            files.append(
                {
                    "path": path,
                    "insertions": int(ins) if ins.isdigit() else 0,
                    "deletions": int(dels) if dels.isdigit() else 0,
                }
            )
    return Change(commits=log.splitlines(), files=files, diff=body, untracked=untracked)


#: What a narrowed package tells the reviewer, in place of the plain header.
#: Both halves matter: which range it is looking at, and that the rest of the
#: branch is still reachable. Without the second half a reviewer that obeys
#: "read that file first" has been quietly cut off from the change as a whole,
#: which is the failure this narrowing must not cause (Kraft-s7c04.1).
_SINCE_HEADER = (
    "# Review package for {since}..working tree\n\n"
    "This is the change **since your last review of this branch**, not the whole "
    "branch. Everything before {since} was reviewed in an earlier round, and the "
    "findings that review left are listed in your instruction.\n\n"
    "For the full branch diff, run `git diff {base}...HEAD` in this worktree. Do "
    "that when you need to judge the change as a whole -- whether it does what "
    "the work item asked, whether it breaks a caller outside these hunks -- and "
    "not otherwise.\n"
)


def render_package(change: Change, base: str, *, since: str | None = None) -> str:
    stat = "\n".join(f"{f['path']} | +{f['insertions']} -{f['deletions']}" for f in change.files)
    header = (
        _SINCE_HEADER.format(since=since, base=base)
        if since
        else f"# Review package for {base}..working tree\n"
    )
    return (
        f"{header}\n"
        f"## Commits\n{chr(10).join(change.commits) or '(none yet — uncommitted work)'}\n\n"
        f"## Files changed\n{stat or '(none)'}\n\n"
        f"## Untracked\n{chr(10).join(change.untracked) or '(none)'}\n\n"
        f"## Diff\n{change.diff}"
    )


def write_package(
    results_dir: Path, worktree: Path, base: str, session_id: str, *, since: str | None = None
) -> Path | None:
    """Write the package into `results_dir` and return its path, or None.

    `since`, when given, narrows the range to `since..working tree` -- the head
    the previous review of this hook was taken at (Kraft-s7c04.1). `Commits` and
    `Files changed` narrow with it: a package describing one range and listing
    another is a trap for whoever reads it next. `None` is the whole branch,
    exactly as before, and is what round 0 of every work item gets.

    Named by the session, the way the result file beside it already is.
    Naming it by the `base..HEAD` range instead looks like it distinguishes
    more, and distinguishes less: `results_dir` is one directory for every work
    item, and uncommitted work -- the normal mid-chain state this module exists
    to show -- leaves HEAD equal to base, so two items branched from the same
    commit collide, and one reviewer is handed the other item's diff. A session
    id is unique per dispatch, so a re-measure after a fix cycle also keeps the
    evidence the previous cycle was reviewed against.
    """
    change = read_change(worktree, since or base, context=PACKAGE_CONTEXT)
    if change is None:
        return None
    path = results_dir / f"{session_id}.review.md"
    path.write_text(render_package(change, base, since=since))
    return path
