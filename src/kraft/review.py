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

import re
from pathlib import Path
from typing import NamedTuple

from kraft import config as _config
from kraft.worker import sandbox as _sandbox

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
    worktree: Path,
    base: str,
    *,
    head: str | None = None,
    context: int | None = None,
    ignore_whitespace: bool = False,
) -> Change | None:
    """The change in `worktree` against `base`, or None if git itself failed.

    With `head` set the range is `base..head` and nothing uncommitted is in it,
    so `untracked` is `[]`: `git status --porcelain` describes the working tree
    and has no meaning for a committed range.

    None is not "no changes": returning an empty diff for a git failure is
    indistinguishable from a clean tree to whoever is about to approve it.
    """
    rev = [base] + ([head] if head else [])
    # Never `status` inside a nested repository: a worker's, planted behind a
    # gitlink, runs its own filters there (Kraft-nx4id). This is the diff
    # endpoint, readable while a sandboxed worker is still writing.
    unentered = _sandbox.SUBMODULES_UNENTERED
    # -w on both commands: git then drops a whitespace-only file from the patch and
    # the numstat alike, so `files` and `diff` cannot disagree.
    ws = ["-w"] if ignore_whitespace else []
    diff_args = ["diff", unentered, *ws] + ([f"-U{context}"] if context is not None else []) + rev
    # strip=False: a diff whose last line is blank context is still that diff
    body = _config.git_read(worktree, *diff_args, strip=False)
    numstat = _config.git_read(worktree, "diff", unentered, *ws, "--numstat", *rev)
    if body is None or numstat is None:
        return None
    untracked: list[str] = []
    if head is None:
        # Not Kraft's own untracked notes and artifacts: the commit path
        # (`forge.git.work_product_pathspec`) keeps them out of the merge
        # request, so a reviewer must not see them as part of the change
        # (Kraft-tugdf.22). A tracked file under a root still shows, via `diff`.
        roots = [f":(exclude){r}" for r in _config.KRAFT_ROOTS]
        status = _config.git_read(
            worktree, "status", unentered, "--porcelain", "-uall", "--", ".", *roots
        )
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


def _is_ancestor(worktree: Path, a: str, b: str) -> bool:
    return (
        _config.git_read(worktree, "merge-base", "--is-ancestor", a, b, expected_failure=True)
        is not None
    )


def touched_by(worktree: Path, runs, from_sha: str, to_sha: str) -> dict[str, list[str]]:
    """Which nodes changed each file inside `from_sha..to_sha`.

    A run is inside the window when its end is reachable from `to_sha` and not
    from `from_sha`; a run from before a rebase is reachable from neither, and
    drops out.
    # ponytail: file-level attribution, 3 git calls per run; per-run hunk diffs
    # if a shared file's mixed hunks ever matter.
    """
    out: dict[str, list[str]] = {}
    for r in runs:
        start, end = r["start_sha"], r["end_sha"]
        if not end or start == end:
            continue
        if not _is_ancestor(worktree, end, to_sha) or _is_ancestor(worktree, end, from_sha):
            continue
        names = _config.git_read(worktree, "diff", "--name-only", start, end) or ""
        for path in names.splitlines():
            if r["node_id"] not in out.setdefault(path, []):
                out[path].append(r["node_id"])
    return out


_RENAME = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


def new_path(path: str) -> str:
    """The path a `git diff --numstat` entry ends up at: numstat names a rename
    `old => new`, or `dir/{old => new}/f` for a shared prefix (Kraft-dl5fl)."""
    if " => " not in path:
        return path
    if "{" in path:
        return _RENAME.sub(lambda m: m.group(2), path).replace("//", "/")
    return path.split(" => ", 1)[1]


def last_toucher(worktree: Path, runs, path: str) -> str | None:
    """The node whose run most recently changed `path` (runs oldest first)."""
    for r in reversed(list(runs)):
        start, end = r["start_sha"], r["end_sha"]
        if not end or start == end:
            continue
        names = _config.git_read(worktree, "diff", "--name-only", start, end) or ""
        if path in names.splitlines():
            return r["node_id"]
    return None


def _working(n) -> bool:
    from kraft.templates.models import AgentTask

    return any(
        isinstance(t.task, AgentTask) and t.task.skill is None
        for s in getattr(n, "steps", ())
        for t in s.tasks
    )


def changes_target(worktree: Path, nodes, current: int, threads, runs) -> tuple[int, str]:
    """Where a gateless `request_changes` re-runs (review threads anywhere §1):
    the earliest node, at or before `current`, that wrote what the threads are
    about; else the current working node. Raises ValueError when no working
    node exists at or before `current`."""
    index = {n.id: i for i, n in enumerate(nodes)}
    hits = []
    for t in threads:
        nid = t.get("node_id") or (
            last_toucher(worktree, runs, t["file_path"]) if t.get("file_path") else None
        )
        if nid in index and index[nid] <= current:
            hits.append((index[nid], t.get("file_path") or nid))
    if hits:
        i, what = min(hits)
        while i >= 0 and not _working(nodes[i]):
            i -= 1
        if i >= 0:
            return i, f"threads on {what}"
    i = current
    while i >= 0 and not _working(nodes[i]):
        i -= 1
    if i < 0:
        raise ValueError("no node at or before the current one runs a working agent")
    return i, "current node"


def filter_diff(diff: str, keep: set[str]) -> str:
    """Only the file sections of `diff` whose old or new path is in `keep`."""
    chunks = diff.split("\ndiff --git ")
    chunks = chunks[:1] + ["diff --git " + c for c in chunks[1:]]
    out = []
    for c in chunks:
        header = c.split("\n", 1)[0]
        if not header.startswith("diff --git a/"):
            continue
        old, _, new = header[len("diff --git a/") :].rpartition(" b/")
        if old in keep or new in keep:
            out.append(c)
    return "\n".join(out)


#: At most this many lines are quoted with a comment, as the review page sends
#: (`Comments.tsx`'s `QUOTE_LINES`); past it, `…` stands for the rest.
QUOTE_LINES = 200

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
#: A commit as `quote_range` takes one: never anything git could read as an option.
_SHA = re.compile(r"[0-9a-f]{7,64}")


class _DiffLine(NamedTuple):
    kind: str  # " ", "+" or "-"
    old: int | None
    new: int | None
    text: str
    hunk: int


def _diff_lines(diff: str) -> list[_DiffLine]:
    """One file's unified diff as its lines, each with its line number on
    each side it is on: `review/range.ts`'s `lineIndex`, in Python."""
    out: list[_DiffLine] = []
    old = new = 0
    hunk = -1
    for line in diff.splitlines():
        if m := _HUNK.match(line):
            old, new, hunk = int(m.group(1)), int(m.group(2)), hunk + 1
            continue
        if hunk < 0 or not line or line[0] not in " +-":
            continue
        kind, text = line[0], line[1:]
        out.append(
            _DiffLine(
                kind,
                old if kind != "+" else None,
                new if kind != "-" else None,
                text,
                hunk,
            )
        )
        old += kind != "+"
        new += kind != "-"
    return out


def _clip_quote(lines: list[str]) -> str | None:
    if not lines:
        return None
    return "\n".join(lines[:QUOTE_LINES] + (["…"] if len(lines) > QUOTE_LINES else []))


def quote_range(
    worktree: Path,
    base: str,
    head: str,
    path: str,
    side: str,
    start: int,
    end: int,
    start_side: str | None = None,
) -> str | None:
    """The lines a comment's range covers in `base..head`, each led by its
    diff mark, as the review page quotes them (`range.ts`'s `quoteOf`), or
    None when they cannot be read. A one-side range is that side's lines
    `start` to `end`, a line the diff leaves alone read from the file; a range
    across sides is every diff line between its two ends, `…` between hunks.

    For a comment made where no diff is drawn -- `kraft item comment`, MCP's
    `add_review_comment` -- so that the agent reading the thread gets the
    lines it is about, as it does for one made on the review page."""
    if not (_SHA.fullmatch(base) and _SHA.fullmatch(head)):
        return None
    unentered = _sandbox.SUBMODULES_UNENTERED
    diff = _config.git_read(worktree, "diff", unentered, base, head, "--", path, strip=False)
    if diff is None:
        return None
    lines = _diff_lines(diff)
    if start_side is not None and start_side != side:
        at = {
            (side_, n): i
            for i, d in enumerate(lines)
            for side_, n in (("old", d.old), ("new", d.new))
            if n is not None
        }
        lo, hi = at.get((start_side, start)), at.get((side, end))
        if lo is None or hi is None or lo > hi:
            return None
        out: list[str] = []
        for i in range(lo, hi + 1):
            if i > lo and lines[i].hunk != lines[i - 1].hunk:
                out.append("…")
            out.append(lines[i].kind + lines[i].text)
        return _clip_quote(out)
    rev = base if side == "old" else head
    content = _config.git_read(worktree, "show", f"{rev}:{path}", strip=False)
    if content is None:
        return None
    text = content.splitlines()
    if not 1 <= start <= end <= len(text):
        return None
    changed = {d.old if side == "old" else d.new for d in lines if d.kind != " "} - {None}
    mark = "-" if side == "old" else "+"
    return _clip_quote(
        [(mark if n in changed else " ") + text[n - 1] for n in range(start, end + 1)]
    )
