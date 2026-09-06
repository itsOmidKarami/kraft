"""One producer for "what changed", read by two consumers.

`GET /work-items/{wid}/diff` renders the change for the human at a gate;
`write_package` writes it to a file a review agent is handed by path. Sub-project
F spec §9 makes that one function on purpose: the human approving a review and
the reviewer that produced it must be looking at the same change, and two
separately-written producers are two things that eventually disagree about what
the change is.

Like the endpoint it was factored out of, the range is the working tree against
`base_ref` rather than `base_ref..HEAD`: an agent that wrote files without
committing them is the normal mid-chain state, and a committed-only diff would
show an empty change set while the work sat on disk. The commit list is the one
part that is genuinely `base_ref..HEAD`, because uncommitted work has no commit.
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


def read_change(worktree: Path, base: str, *, context: int | None = None) -> Change | None:
    """The change in `worktree` against `base`, or None if git itself failed.

    None is not "no changes": returning an empty diff for a git failure is
    indistinguishable from a clean tree to whoever is about to approve it.
    """
    diff_args = ["diff"] + ([f"-U{context}"] if context is not None else []) + [base]
    # strip=False: a diff whose last line is blank context is still that diff
    body = _config.git_read(worktree, *diff_args, strip=False)
    numstat = _config.git_read(worktree, "diff", "--numstat", base)
    status = _config.git_read(worktree, "status", "--porcelain", "-uall")
    if body is None or numstat is None or status is None:
        return None
    # A repo with no commits past `base` is normal, not a failure, so an empty
    # log is not folded in with the None checks above.
    log = _config.git_read(worktree, "log", "--oneline", f"{base}..HEAD") or ""

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
    return Change(
        commits=log.splitlines(),
        files=files,
        diff=body,
        untracked=[ln[3:] for ln in status.splitlines() if ln.startswith("?? ")],
    )


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
