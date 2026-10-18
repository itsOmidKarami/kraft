"""Around an in-place agent task: the mark before it, and after it the
check (a moved checkout, stray commits, the overlap with the person's
edits, an autostash its own rebase could not put back)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kraft import events, item_paths, store
from kraft.config import git_read
from kraft.inplace.base import (
    AutostashClash,
    CheckoutRefused,
    _git_lines,
    _operation,
    _rebase_file,
    _ref,
    _under_way,
)
from kraft.inplace.rebasing import _raise_clash
from kraft.vocab import LIVE, ChainEvent, StopKind
from kraft.vocab.sql import in_list

#: HEAD reflog subjects of an operation that may `--autostash`: a rebase's
#: start (or a pull's, `pull --rebase … (start):`; `rebase: checkout …` from
#: git 2.25 and older, and from `--apply` before 2.39), a pull, a merge.
_STARTS_AN_AUTOSTASH = (
    "rebase (start):",
    "rebase -i (start):",
    "rebase: checkout ",
    "pull",
    "merge",
)


#: A merge that conflicted writes no entry of its own: its autostash is put
#: back when its commit is made, and that commit's entry follows the HEAD it
#: started from.
_ENDS_A_MERGE = "commit (merge):"


#: A phased entry's phase: a `pull --rebase` names every entry `pull … (pick):`
#: and the like, and only its `(start)` started from the HEAD before it. A
#: search, not a match: a pull by URL or with a `src:dst` refspec puts a colon
#: before the phase.
_PHASE = re.compile(r" \(([\w -]+)\):")


def _head_reflog(checkout: Path) -> list[str]:
    """HEAD's reflog in `checkout`, newest first: `<sha> HEAD@{<unix>} <subject>`."""
    return _git_lines(checkout, "log", "-g", "--date=unix", "--format=%H %gd %gs", "HEAD")


def _put_back(
    checkout: Path, m: TaskMark, task: str, reflog: list[str] | None = None
) -> AutostashClash | None:
    """What a task's own `git rebase --autostash` (the conflict resolver's,
    `prompts._RESOLVE_IN`) could not put back since its mark: git exits 0 on
    it. Its autostash is on the HEAD one of this checkout's own rebases, pulls
    or merges since the mark started from (HEAD's reflog is this worktree's
    own): never just a commit another worktree may sit on too, such as the tip
    a fresh item starts at or the new base a rebase checks out.
    None mid-operation: its unmerged paths are its own, and its autostash is
    still in git's operation state, put back when the person finishes it.
    shortcut: another worktree whose own rebase started from the same commit
    as one of this checkout's during the task, and whose put-back clashed,
    still stops this item, even when this checkout's put-back was clean: `^1`
    cannot tell the two apart. With no HEAD reflog only `m.tip` counts, so a
    clash after a commit is left to the next take's refusal of its unmerged
    paths. Upgrade if either is seen in practice."""
    if _operation(checkout):
        return None
    since = int(datetime.fromisoformat(m.started_at).timestamp())
    entries = _head_reflog(checkout) if reflog is None else reflog
    held = set() if entries else {m.tip}
    for i, line in enumerate(entries):  # newest first
        _sha, selector, subject = line.split(" ", 2)
        # Not a `break`: an entry's time is its committer date's when one is set
        # (GIT_COMMITTER_DATE), so an older-looking entry may be newer.
        if int(selector.rsplit("{", 1)[1].rstrip("}")) < since:
            continue
        phase = _PHASE.search(subject)
        # The HEAD it started from: the older entry.
        if subject.startswith(_ENDS_A_MERGE) or (
            subject.startswith(_STARTS_AN_AUTOSTASH) and (phase is None or phase[1] == "start")
        ):
            held.add(entries[i + 1].split(" ", 1)[0] if i + 1 < len(entries) else m.tip)
    try:
        _raise_clash(checkout, "", None, since, task=task, held=held)
    except AutostashClash as exc:
        return exc
    return None


@dataclass(frozen=True)
class TaskMark:
    """The checkout as an agent task found it: the branch's tip and the paths
    already dirty that are the person's, not the item's."""

    tip: str
    dirty: frozenset[str]
    started_at: str  # ISO, whole seconds: the reflog's resolution


@dataclass(frozen=True)
class TaskCheck:
    moved_to: str | None
    stray_commits: list[str]
    overlap: list[str]
    leftovers: dict[str, str]
    #: Off the branch mid-operation (a rebase detaches HEAD): not `moved_to`.
    under_way: bool = False


#: How many paths a `task_marked` or `left_uncommitted` event names; the
#: whole set is in the item's folder (`item_paths.checkout_record`), since every event goes to
#: every client.
SAMPLE = 50


def _write(path: Path, data: dict) -> None:
    """Atomic, and safe for co-tasks writing the same file at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data))
    os.replace(tmp, path)


def save_mark(run_dirs, wid: str, task_path: str, m: TaskMark) -> None:
    data = {"tip": m.tip, "started_at": m.started_at, "dirty": sorted(m.dirty)}
    _write(item_paths.checkout_record(run_dirs, wid, f"{task_path}.mark.json"), data)


def load_mark(run_dirs, wid: str, task_path: str) -> TaskMark | None:
    try:
        d = json.loads(
            item_paths.checkout_record(run_dirs, wid, f"{task_path}.mark.json").read_text()
        )
    except FileNotFoundError:
        return None
    return TaskMark(tip=d["tip"], dirty=frozenset(d["dirty"]), started_at=d["started_at"])


def save_leftovers(run_dirs, wid: str, paths: Mapping[str, str]) -> None:
    _write(item_paths.checkout_record(run_dirs, wid, "leftovers.json"), dict(paths))


def leftovers(run_dirs, wid: str) -> dict[str, str]:
    """Path to blob sha of what the item's tasks last left uncommitted."""
    try:
        return json.loads(item_paths.checkout_record(run_dirs, wid, "leftovers.json").read_text())
    except FileNotFoundError:
        return {}


def still_left(run_dirs, wid: str, checkout: Path) -> bool:
    """Whether anything the item's tasks left uncommitted is still there as
    they left it: the item's own uncommitted work, never the person's."""
    left = leftovers(run_dirs, wid)
    now = [p for p in _dirty(checkout) if p in left] if left else []
    hashes = _hashes(checkout, now)
    return any(hashes[p] == left[p] for p in now)


def mark(
    checkout: Path,
    branch: str,
    leftovers: Mapping[str, str],
    siblings: Iterable[frozenset[str]] = (),
) -> TaskMark:
    """Before an agent task: the tip and the person's dirty paths. A leftover
    of the item's own whose content is unchanged is the item's, so a later
    task committing it is no overlap. `siblings`: the person's paths as each
    co-task still running marked them; a path dirtied since one started is
    taken for that co-task's work in flight, not the person's."""
    tip = _ref(checkout, branch)
    if tip is None:
        raise CheckoutRefused(f"the branch {branch} is gone from {checkout}")
    dirty = _dirty(checkout)
    mine = [p for p in dirty if p in leftovers]
    hashes = _hashes(checkout, mine)
    theirs = {p for p in dirty if p not in mine or hashes[p] != leftovers[p]}
    for earlier in siblings:
        theirs &= earlier
    return TaskMark(
        tip=tip,
        dirty=frozenset(theirs),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


#: Reflog subjects of a commit made in the checkout, not merged or replayed
#: into it (a merge's result is the tree diff's, a rebase's replays were made
#: before the task).
_MADE_HERE = (
    *("commit:", "commit (amend):", "commit (cherry-pick):"),
    *("revert:", "cherry-pick:", "am:"),
)


def check(
    checkout: Path, branch: str, mark: TaskMark, reflog: list[str] | None = None
) -> TaskCheck:
    """After an agent task: never a repair, only what a stop needs to say.
    `reflog`: `_head_reflog`, when the caller already read it."""
    head = git_read(checkout, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    since = datetime.fromisoformat(mark.started_at).timestamp()
    entries = []
    for entry in _head_reflog(checkout) if reflog is None else reflog:
        sha, selector, subject = entry.split(" ", 2)
        if int(selector.rsplit("{", 1)[1].rstrip("}")) >= since:
            entries.append((sha, subject))
    # Oldest first, following which branch HEAD was on (the take left it on
    # the item's): only what HEAD reached while it was elsewhere is stray, so
    # an amend, reset or rebase on the item's branch never is.
    on, made, ours = branch, [], []
    for sha, subject in reversed(entries):
        if subject.startswith("checkout: moving from "):
            on = subject.rsplit(" to ", 1)[1]
        elif subject.startswith("rebase (finish): returning to refs/heads/"):
            on = subject.removeprefix("rebase (finish): returning to refs/heads/")
        elif on != branch and sha not in made:
            made.append(sha)
        elif on == branch and subject.startswith(_MADE_HERE):
            ours.append(sha)
    made.reverse()  # newest first, as named
    operation = _operation(checkout)
    # An operation begun on the item's branch (a rebase detaches HEAD, a merge
    # does not) is the person's to finish; one begun elsewhere is a move. A
    # rebase says which branch it is rebasing, whichever one it began from.
    rebasing = _rebase_file(checkout, "head-name")
    mine = rebasing == f"refs/heads/{branch}" if rebasing is not None else on == branch
    under_way = operation and mine
    moved_to = None
    if head != f"refs/heads/{branch}" and not under_way:
        moved_to = (head or "").removeprefix("refs/heads/") or (
            f"detached at {git_read(checkout, 'rev-parse', 'HEAD')}"
        )
    off = (
        set(_git_lines(checkout, "log", "--format=%H", *made, "--not", f"refs/heads/{branch}"))
        if made
        else set()
    )
    # Every path the branch's tree gained since the mark, whatever the merge
    # shape (a merged side branch's files count), and every path a commit made
    # here on the branch records unlike the mark, so one a later commit or a
    # reset undid still counts. Each against the mark, never its parent: a
    # reworded amend, a squash or a replay committed by hand only carries the
    # item's earlier work, and a commit from before the mark (the reflog has
    # whole seconds) is already in it. Never main's commits or a rebase's
    # replays: those are not made here. Git merges or rebases nothing over a
    # dirty path, so an upstream change lands on one of the person's only if
    # their edit was set aside (a stash) during the task, worth a look too.
    touched: list[str] = []
    for sha in [f"refs/heads/{branch}", *ours]:
        touched += _git_lines(checkout, "diff", "--name-only", "--no-renames", mark.tip, sha)
    # Mid-operation, the dirty paths are the operation's, not the task's.
    new = [] if operation else [p for p in _dirty(checkout) if p not in mark.dirty]
    return TaskCheck(
        moved_to=moved_to,
        stray_commits=[s for s in made if s in off],
        overlap=sorted(mark.dirty.intersection(touched)),
        leftovers=_hashes(checkout, new),
        under_way=under_way,
    )


def _dirty(checkout: Path) -> list[str]:
    """Modified, staged or untracked, never ignored: one path an entry."""
    entries = _git_lines(
        checkout, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames"
    )
    return [e[3:] for e in entries]


def _hashes(checkout: Path, paths: list[str]) -> dict[str, str]:
    """Each path's blob sha; "" for one that is gone or is not a file."""
    out = dict.fromkeys(paths, "")
    files = [p for p in paths if (checkout / p).is_file()]
    for i in range(0, len(files), 500):
        chunk = files[i : i + 500]
        shas = subprocess.run(
            ["git", "hash-object", "--", *chunk],
            cwd=checkout,
            capture_output=True,
            text=True,
            errors="surrogateescape",
        ).stdout.split()
        if len(shas) == len(chunk):
            out.update(zip(chunk, shas, strict=True))
        elif len(chunk) > 1:
            # One unreadable file fails the whole call: the rest one by one.
            for p in chunk:
                out.update(_hashes(checkout, [p]))
    return out


async def mark_task(db, run_dirs, row, folder: Path, node_id: str, task_path: str) -> None:
    """Before an in-place agent or command task: what `check_task` compares against,
    kept in the item's folder so a restart's reattach reads the same mark."""
    wid = row["id"]
    # The lease keeps every other item out: a live session of this node here
    # is a co-task.
    running = db.read(
        lambda c: c.execute(
            "SELECT DISTINCT hook_point FROM worker_sessions WHERE work_item_id = ? "
            f"AND node_id = ? AND hook_point != ? AND status IN ({in_list(LIVE)})",
            (wid, node_id, task_path),
        ).fetchall()
    )
    siblings = [s.dirty for r in running if (s := load_mark(run_dirs, wid, r[0])) is not None]
    m = await asyncio.to_thread(
        mark, folder, store.branch_for(row), leftovers(run_dirs, wid), siblings
    )
    save_mark(run_dirs, wid, task_path, m)
    dirty = sorted(m.dirty)
    payload = {"node_id": node_id, "task": task_path, "tip": m.tip, "started_at": m.started_at}
    payload |= {"dirty_count": len(dirty), "dirty": dirty[:SAMPLE]}
    await db.write(lambda c: events.append(c, wid, ChainEvent.TASK_MARKED, payload))


async def check_task(
    db, run_dirs, row, folder: Path, node_id: str, task_path: str, *, command: bool = False
):
    """After an in-place agent task, in place of the sweep: record what it
    left uncommitted, and stop the item as `checkout` when the checkout moved
    or the task committed a path the person had changed, or its rebase could not
    put their autostashed edits back (named with the stash). Never a repair: no
    switch back, no commit, no undo. Answers the stop's reason, or None.
    After a `command` the stop reruns it on a retry, since its verdict was
    measured on whatever the checkout held, and only paths no earlier task
    left as they are now are reported left uncommitted."""
    wid = row["id"]
    m = load_mark(run_dirs, wid, task_path)
    if m is None:  # launched before marks were recorded
        return None
    branch = store.branch_for(row)
    stray: list[str] = []
    clash: AutostashClash | None = None
    try:
        reflog = await asyncio.to_thread(_head_reflog, folder)
        found = await asyncio.to_thread(check, folder, branch, m, reflog)
        clash = await asyncio.to_thread(_put_back, folder, m, task_path, reflog)
    except CheckoutRefused as exc:
        reason = str(exc)
    else:
        stray = found.stray_commits
        before = leftovers(run_dirs, wid) if command else {}
        left = sorted(p for p, sha in found.leftovers.items() if before.get(p) != sha)
        if left:
            save_leftovers(run_dirs, wid, found.leftovers)
            payload = {"node_id": node_id, "task": task_path, "count": len(left)}
            payload["paths"] = left[:SAMPLE]
            await db.write(lambda c: events.append(c, wid, ChainEvent.LEFT_UNCOMMITTED, payload))
        reasons = []
        made = (
            f"commits made or moved onto during it that are not on {branch}: " + ", ".join(stray)
            if stray
            else ""
        )
        if found.moved_to:
            reasons.append(
                f"the checkout moved to {found.moved_to} during the run of {task_path} in "
                f"{folder}{'; ' + made if made else ''}. Kraft did not switch it back"
                f"{', nor move those commits' if made else ''}: retry the item once {folder} "
                f"may go back to {branch}"
            )
        elif found.under_way:
            reasons.append(str(_under_way(folder, f" at the end of the run of {task_path}")))
        if stray and not found.moved_to:
            # Switched away, committed and switched back: the work may be elsewhere.
            reasons.append(
                f"{task_path} left {made}. Kraft did not move them: check where that work "
                "belongs, then retry the item"
            )
        if clash is not None:
            reasons.append(str(clash))
        if clash is not None and found.overlap:
            # The put-back merged upstream into them, or a commit took them: either may hold
            # the person's edits, so the overlap is said, in words that blame no commit.
            reasons.append(
                f"the branch now changes {', '.join(found.overlap)}, which you had edited "
                "before the task started; check that no commit holds your edits, "
                "then retry the item"
            )
        elif found.overlap:
            reasons.append(
                f"the agent committed {', '.join(found.overlap)}, which already had uncommitted "
                "changes before it started; check that the commit holds only the item's work, "
                "then retry the item"
            )
        reason = "; ".join(reasons)
    if not reason:
        return None
    last = db.read(lambda c: store.latest_session_per_task(c, wid, node_id, [task_path]))
    # A retry goes on past the task, unless its work may be on another branch.
    facts = {"task": task_path, "rerun": command or bool(stray)}
    if clash is not None:  # as `stop_refused` records Kraft's own
        facts |= {"paths": clash.paths, "stash": clash.stash}
    if last:
        facts |= {"session": last[0]["id"], "round": last[0]["round"]}

    def stop(c) -> None:
        if facts["rerun"] and last:
            # Its work or its verdict may be about another branch: a retry
            # runs it again, never reuses it at the branch's unmoved tip.
            c.execute(
                "UPDATE worker_sessions SET head_sha = NULL WHERE work_item_id = ? "
                "AND node_id = ? AND hook_point = ? AND round = ?",
                (wid, node_id, task_path, last[0]["round"]),
            )
        # Never `stuck`: an escalation would take the checkout straight back.
        store.mark_needs_human(c, wid, node_id, reason, kind=StopKind.CHECKOUT, facts=facts)

    await db.write(stop)
    return reason


async def stop_refused(db, wid: str, node_id: str, exc: CheckoutRefused | AutostashClash) -> None:
    """A person's request for an agent (an escalation, a reply) whose
    checkout could not be taken, or a rebase that left it clashing: the item
    stops for them, as a walk task's refused take does. Never `stuck`: an
    escalation would only take it again. A clash records its stash's commit,
    since the stash list is shared and its `stash@{n}` moves."""
    facts = {"paths": exc.paths, "stash": exc.stash} if isinstance(exc, AutostashClash) else None
    await db.write(
        lambda c: store.mark_needs_human(
            c, wid, node_id, str(exc), kind=StopKind.CHECKOUT, facts=facts
        )
    )


def stopped(db, wid: str) -> bool:
    """Whether a session's after-check (`check_task`) has stopped the item: what
    a gate's reviewer or a walk at its gate must not go on past."""
    row = db.read(lambda c: c.execute("SELECT stop_kind FROM work_items WHERE id = ?", (wid,)))
    found = row.fetchone()
    return found is not None and found["stop_kind"] == StopKind.CHECKOUT
