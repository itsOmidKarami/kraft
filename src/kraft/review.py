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
nodes finished and committed. The gate viewer shows both, apart (Kraft-nceo);
`write_package` still hands a review agent the one combined range.
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


def render_package(change: Change, base: str) -> str:
    stat = "\n".join(f"{f['path']} | +{f['insertions']} -{f['deletions']}" for f in change.files)
    return (
        f"# Review package for {base}..working tree\n\n"
        f"## Commits\n{chr(10).join(change.commits) or '(none yet — uncommitted work)'}\n\n"
        f"## Files changed\n{stat or '(none)'}\n\n"
        f"## Untracked\n{chr(10).join(change.untracked) or '(none)'}\n\n"
        f"## Diff\n{change.diff}"
    )


def write_package(results_dir: Path, worktree: Path, base: str, session_id: str) -> Path | None:
    """Write the package into `results_dir` and return its path, or None.

    Named by the session, the way the result file beside it already is.
    Naming it by the `base..HEAD` range instead looks like it distinguishes
    more, and distinguishes less: `results_dir` is one directory for every work
    item, and uncommitted work -- the normal mid-chain state this module exists
    to show -- leaves HEAD equal to base, so two items branched from the same
    commit collide, and one reviewer is handed the other item's diff. A session
    id is unique per dispatch, so a re-measure after a fix cycle also keeps the
    evidence the previous cycle was reviewed against.
    """
    change = read_change(worktree, base, context=PACKAGE_CONTEXT)
    if change is None:
        return None
    path = results_dir / f"{session_id}.review.md"
    path.write_text(render_package(change, base))
    return path
