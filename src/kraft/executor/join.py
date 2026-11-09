"""The join: replaying a group's completed children onto the parent's branch.

Runs in the parent's worktree, holding a slot, once every child of the current
generation has ended. Each completed child's pinned range (`docs commit..pinned
head`) is replayed with `git cherry-pick -x --allow-empty
--keep-redundant-commits`, slice by slice in slice order. Linear replay, not
merge commits: the plain `git rebase` that `refresh_worktree_base` and
`mr_rebase` run later flattens a merge commit and would bring a resolved
conflict back where nothing handles it.

Host git here trusts nothing a child's agent wrote:

* the range is the child's pinned head, not its branch as it stands now;
* a child commit that already holds a `(cherry picked from commit ...)` line is
  refused before any pick, since the applied-check reads those lines;
* a merge commit, a head that does not descend from the fork commit or from the
  child's docs commit (a branch reset or squashed would replay its slice plan)
  and a moved `.lfsconfig` or planted repository each stop the join naming the
  child;
* a resume never trusts the worktree's git folder, which a worker can write: it
  deletes the operation files itself, with `lstat`, and moves the branch by
  name (`put_back`). It never runs `cherry-pick --abort`.

A conflict puts the parent back and hands the slice off. The group's
`on_conflict` handler (the library's `resolve_join_conflict`), when it has one,
then gets a turn in the parent's worktree, run as any recovery handler is
(`dispatch.run_recovery`), with no leftover sweep after it: the join itself
removes what it left untracked and not ignored (`_clear_leftovers`), then the
slice is joined only if the applied-check finds it whole, else the parent is put
back again and a person is asked. Tracked changes it left are never discarded:
they mean the slice is not joined.

While the handler runs the slice is `handling`; its verdict makes it
`handed_off`, a person's from then on. A pause cancels the walk mid-handler and
a crash kills it, both leaving `handling` and whatever the handler left. The
join re-entering a `handling` slice clears the leftovers and joins the slice if
the applied-check finds it whole; otherwise it puts the parent back and picks
the slice again, so the handler gets a fresh turn (`join_handler_resumed`).
It gets one such turn (`_FRESH_TURNS`): cut short again, the slice is
`handed_off` and the parent stops for a person.

The applied-check is bookkeeping, not a security boundary: what the code *does*
is judged by `verification` and the human gates after the join.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
from pathlib import Path

from kraft import events, store
from kraft.adapters.forge import git
from kraft.adapters.forge.git import ForgeError, UnsafeWorktree, run_git
from kraft.config import base_ignore_args, git_read
from kraft.executor import stops
from kraft.executor.context import LaunchContext
from kraft.store.slices import GroupState
from kraft.templates.models import ResolvedNode
from kraft.vocab import ENDED, ChainEvent, StopKind, WorkItemEvent, WorkItemStatus

logger = logging.getLogger(__name__)

__all__ = ["JoinRefused", "join", "missing", "put_back", "recorded_range"]

DONE = "ok"

#: Every git the join runs: no rerere, whatever the config says.
_GIT = ["git", "-c", "rerere.enabled=false"]
_PICKED = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,64})\)")
#: `--allow-empty` for a commit the child made empty, `--keep-redundant-commits`
#: for one the replay empties (a change an earlier slice made too).
_PICK = ["cherry-pick", "-x", "--allow-empty", "--keep-redundant-commits"]


class JoinRefused(RuntimeError):
    """A child the join will not replay. The message names the child, so the
    stop tells a person what to look at."""


def recorded_range(repo: Path, docs_commit: str, pinned_head: str, fork_commit: str) -> list[str]:
    """The child's commits `docs_commit..pinned_head`, oldest first. Raises
    `JoinRefused` for a head that does not descend from the fork commit or from
    the docs commit, a merge commit in the range, and a commit message that
    already holds a `cherry picked from commit` line. (The moved-branch check
    needs the child's row: `_check_child`.)"""
    if (
        git_read(
            repo, "merge-base", "--is-ancestor", fork_commit, pinned_head, expected_failure=True
        )
        is None
        and git_read(repo, "rev-parse", "--verify", "--quiet", f"{pinned_head}^{{commit}}") is None
    ):
        raise JoinRefused(f"its head {pinned_head[:12]} is not a commit this repository has")
    if not _is_ancestor(repo, fork_commit, pinned_head):
        raise JoinRefused(
            f"its head {pinned_head[:12]} does not descend from {fork_commit[:12]}, the "
            "commit the slices were cut from"
        )
    if not _is_ancestor(repo, fork_commit, docs_commit):
        raise JoinRefused(
            f"the commit that attached its slice documents ({docs_commit[:12]}) does not "
            f"descend from {fork_commit[:12]}, the commit the slices were cut from"
        )
    if not _is_ancestor(repo, docs_commit, pinned_head):
        # A branch reset or squashed past its first commit: the range would
        # replay the slice's own spec and plan into the parent.
        raise JoinRefused(
            f"its head {pinned_head[:12]} does not descend from the commit that attached "
            f"its slice documents ({docs_commit[:12]}): its branch was reset or squashed, "
            "so the replay would carry those documents into this item"
        )
    span = f"{docs_commit}..{pinned_head}"
    merges = git_read(repo, "rev-list", "--merges", span) or ""
    if merges.strip():
        raise JoinRefused(f"its range holds a merge commit ({merges.split()[0][:12]})")
    commits = (git_read(repo, "rev-list", "--reverse", span) or "").split()
    for sha in commits:
        message = git_read(repo, "show", "-s", "--format=%B", sha, strip=False) or ""
        if _PICKED.search(message):
            raise JoinRefused(
                f"commit {sha[:12]} already holds a 'cherry picked from commit' line, which "
                "only the join may write"
            )
    return commits


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return subprocess_ok(repo, ["merge-base", "--is-ancestor", ancestor, descendant]) == 0


def subprocess_ok(repo: Path, args: list[str]) -> int | None:
    import subprocess

    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def missing(repo: Path, at_commit: str, recorded: list[str]) -> list[str]:
    """Which of `recorded` the branch does not hold yet: the commits since
    `at_commit` mapped one to one, in order, to `recorded` by the last `cherry
    picked from` line git added to each. Everything in `recorded` that is not
    matched is returned, in order; an unexpected extra commit is no match."""
    if not _is_ancestor(repo, at_commit, "HEAD"):
        return list(recorded)
    since = git_read(repo, "rev-list", "--reverse", "--first-parent", f"{at_commit}..HEAD") or ""
    picked = []
    for sha in since.split():
        message = git_read(repo, "show", "-s", "--format=%B", sha, strip=False) or ""
        found = _PICKED.findall(message)
        picked.append(found[-1] if found else None)
    applied = 0
    for want, got in zip(recorded, picked, strict=False):
        if got is None or not want.startswith(got[:7]) and not got.startswith(want[:7]):
            break
        applied += 1
    return list(recorded[applied:]) if len(picked) <= len(recorded) else list(recorded)


def picks_of(repo: Path, since: str, recorded: list[str], *, head: str = "HEAD") -> list[str]:
    """The commits in `since..head` that git picked from one of `recorded`, by
    the last `cherry picked from` line of each, oldest first: a joined slice's
    work on its parent's branch, which outlives the slice's own branch."""
    log = git_read(
        repo,
        "log",
        "--reverse",
        "--first-parent",
        "--format=%H%x00%B%x1e",
        f"{since}..{head}",
        strip=False,
    )
    out = []
    for entry in (log or "").split("\x1e"):
        sha, _, message = entry.strip("\n").partition("\x00")
        found = _PICKED.findall(message)
        got = found[-1] if found else None
        if sha and got and any(w.startswith(got) or got.startswith(w) for w in recorded):
            out.append(sha)
    return out


def put_back(worktree: Path, branch: str, at_commit: str) -> None:
    """Put the parent back at `at_commit` without trusting its git folder: the
    operation files go first (by `lstat`, never following a symlink), since
    `assert_on_branch` itself refuses any operation in progress; then nothing
    else may be in progress; then the branch moves by name and the tree resets."""
    gitdir = git_read(worktree, "rev-parse", "--absolute-git-dir")
    if gitdir is None:
        raise UnsafeWorktree(f"{worktree} has no git folder Kraft can read")
    for name in ("CHERRY_PICK_HEAD", "sequencer"):
        _remove(Path(gitdir, name))
    git.assert_no_operation(worktree)
    git.assert_on_branch(worktree, branch)
    import subprocess

    for args in (["update-ref", f"refs/heads/{branch}", at_commit], ["reset", "--hard", "-q"]):
        done = subprocess.run(
            ["git", *args], cwd=worktree, capture_output=True, text=True, timeout=300
        )
        if done.returncode != 0:
            raise ForgeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")


def _remove(path: Path) -> None:
    """Delete `path` if there is anything there, a symlink as the link itself."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _steps(child: dict, docs: str, head: str, branch: str) -> str:
    return (
        f"Replay slice {child['slice_key']} by hand in the parent's worktree: "
        f"`git cherry-pick -x --allow-empty --keep-redundant-commits {docs[:12]}..{head[:12]}`, "
        "resolve the "
        "conflicts, then `git cherry-pick --continue` (or `git commit --allow-empty "
        "--no-edit` when the resolution leaves nothing). Never `git commit -m` (it drops the "
        "-x line) and never `--skip`. Then retry this item; it checks every commit is there "
        f"before it goes on. The parent's branch is {branch}."
    )


async def join(
    db,
    run_dirs,
    row,
    worktree: Path,
    node: ResolvedNode,
    *,
    launch: LaunchContext | None = None,
    policy=None,
) -> str:
    """Replay every completed child of the group's current generation, in slice
    order, resuming from the stored per-slice state. Answers `ok`, or `needs_human`
    after stopping the parent for a person."""
    wid = row["id"]
    state = db.read(lambda c: store.group_state(c, wid))
    children = db.read(lambda c: store.children_of(c, wid, state.generation))
    branch = store.branch_for(db.read(lambda c: _item(c, wid)))
    done: list[int] = []
    dropped: list[dict] = []
    completed = [c for c in children if c["status"] == WorkItemStatus.COMPLETED]
    if not completed:
        await _stop(
            db,
            wid,
            node,
            "every slice was dropped, so there is nothing to join: send the plan back "
            "(a rewind to the plan builds the slices again) or abandon this item",
            StopKind.FAILED,
        )
        return "needs_human"
    for child in children:
        key = child["slice_key"]
        if child["status"] != WorkItemStatus.COMPLETED:
            if child["status"] in ENDED:
                dropped.append(child)
                if key > state.joined_through:
                    await db.write(lambda c, ch=child: _record_dropped(c, wid, ch))
            continue
        if key <= state.joined_through:
            done.append(key)
            continue
        if db.read(lambda c, ch=child: _completed_before_it_started(c, ch)):
            await db.write(lambda c, ch=child: _record_unstarted(c, wid, ch))
            await _mark_joined(db, wid, key, worktree)
            done.append(key)
            continue
        state = db.read(lambda c: store.group_state(c, wid))
        stopped = await _replay_one(
            db, run_dirs, row, worktree, node, state, child, branch, launch, policy
        )
        if stopped is not None:
            return stopped
        await db.write(lambda c, ch=child: _record_notes(c, wid, ch))
        done.append(key)
    state = db.read(lambda c: store.group_state(c, wid))
    await _finish(db, run_dirs, wid, state, children)
    await db.write(
        lambda c: events.append(
            c,
            wid,
            WorkItemEvent.SLICES_JOINED,
            {
                "generation": state.generation,
                "joined": sorted(done),
                "dropped": [d["slice_key"] for d in dropped],
            },
        )
    )
    return DONE


def _item(conn, wid: str):
    return conn.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()


def _record_notes(conn, wid: str, child: dict) -> None:
    """What the child skipped or had completed by hand, for the work brief."""
    notes = []
    for e in events.read_after(conn, 0, child["id"]):
        if e["type"] == ChainEvent.NODE_SKIPPED:
            notes.append(f"skipped {e['payload'].get('node_id') or e['payload'].get('path')}")
        elif e["type"] == WorkItemEvent.MANUALLY_COMPLETED:
            notes.append("completed by hand, untested")
    if not notes:
        return
    state = store.group_state(conn, wid)
    key = child["slice_key"]
    state.skipped[key] = list(dict.fromkeys([*state.skipped.get(key, []), *notes]))
    store.set_group_state(conn, wid, state)


def _completed_before_it_started(conn, child: dict) -> bool:
    """A child a person completed before it had a worktree: no docs commit and
    no head were ever recorded, so it has nothing to replay. Both missing on a
    child the walk completed is a broken pin, which `_check_child` refuses."""
    if child["docs_commit"] or child["pinned_head"]:
        return False
    return any(
        e["type"] == WorkItemEvent.MANUALLY_COMPLETED
        for e in events.read_after(conn, 0, child["id"])
    )


def _record_unstarted(conn, wid: str, child: dict) -> None:
    state = store.group_state(conn, wid)
    state.skipped[child["slice_key"]] = ["completed by hand before it started: nothing replayed"]
    store.set_group_state(conn, wid, state)


def _record_dropped(conn, wid: str, child: dict) -> None:
    state = store.group_state(conn, wid)
    state.skipped.setdefault(child["slice_key"], [])
    if "dropped" not in state.skipped[child["slice_key"]]:
        state.skipped[child["slice_key"]].append("dropped")
        store.set_group_state(conn, wid, state)
        events.append(
            conn,
            wid,
            WorkItemEvent.SLICE_DROPPED,
            {
                "child": child["id"],
                "slice_key": child["slice_key"],
                "reason": child["status"],
            },
        )


async def _stop(db, wid: str, node: ResolvedNode, why: str, kind: StopKind) -> None:
    await db.write(lambda c: store.mark_needs_human(c, wid, node.id, why, kind=kind))


async def _replay_one(
    db, run_dirs, row, worktree, node, state, child: dict, branch: str, launch, policy=None
) -> str | None:
    """Replay one completed child. None when it is joined, else the status the
    parent stopped with."""
    wid, key = row["id"], child["slice_key"]
    docs, head, fork = child["docs_commit"], child["pinned_head"], state.fork_commit
    repo = Path(row["repo"])
    # The child's own branch must still be where it completed.
    try:
        _check_child(repo, run_dirs, child, launch, docs, head, fork)
        range_ = recorded_range(repo, docs, head, fork)
    except (JoinRefused, UnsafeWorktree, RuntimeError) as exc:
        await _stop(
            db, wid, node, f"slice {key} ({child['id']}) cannot be joined: {exc}", StopKind.FAILED
        )
        return "needs_human"
    if state.slice_phase == "handed_off" and state.joined_through < key and state.recorded.get(key):
        # Never resets: a person or a handler may have resolved it.
        wrong = _not_joined(worktree, branch, state.at_commit or fork, state.recorded[key])
        if wrong is not None:
            await _stop(
                db,
                wid,
                node,
                f"slice {key} ({child['id']}) is not in the parent yet: {wrong}. "
                + _steps(child, docs, head, branch),
                StopKind.CONFLICT,
            )
            return "needs_human"
        return await _mark_joined(db, wid, key, worktree)
    if state.slice_phase == "handling" and state.joined_through < key and state.recorded.get(key):
        # Its conflict handler was cut short (a pause, a restart), its session ended since.
        at = state.at_commit or fork
        await _clear_leftovers(db, row, child, worktree)
        if _not_joined(worktree, branch, at, state.recorded[key]) is None:
            await _handler_resumed(db, wid, child, "joined")
            return await _mark_joined(db, wid, key, worktree)
        steps = _steps(child, docs, head, branch)
        try:
            put_back(worktree, branch, at)
        except (UnsafeWorktree, ForgeError) as exc:
            await _stop(
                db, wid, node, _left_unputbackable(exc, at, branch, steps), StopKind.CHECKOUT
            )
            return "needs_human"
        if db.read(lambda c: _reruns(c, wid, child)) >= _FRESH_TURNS:
            await db.write(lambda c: _set_phase(c, wid, "handed_off"))
            await _handler_resumed(db, wid, child, "handed_off")
            await _stop(
                db,
                wid,
                node,
                f"slice {key} ({child['id']}): its conflict handler was cut short twice, "
                "so the slice is yours now. " + steps,
                StopKind.CONFLICT,
            )
            return "needs_human"
        await _handler_resumed(db, wid, child, "rerun")
    if state.slice_phase == "picking" and state.at_commit:
        # Kraft's own pick was running when the server went down.
        try:
            put_back(worktree, branch, state.at_commit)
        except (UnsafeWorktree, ForgeError) as exc:
            await _stop(db, wid, node, f"could not put the parent back: {exc}", StopKind.CHECKOUT)
            return "needs_human"
    try:
        git.assert_on_branch(worktree, branch)
    except UnsafeWorktree as exc:
        await _stop(db, wid, node, str(exc), StopKind.CHECKOUT)
        return "needs_human"
    start = git_read(worktree, "rev-parse", "HEAD") or fork
    resumed = state.slice_phase in ("picking", "handling") and state.at_commit
    at = state.at_commit if resumed else start
    await db.write(lambda c: _picking(c, wid, key, at, range_))
    if range_:
        try:
            await run_git(worktree, [*_GIT, *_PICK, f"{docs}..{head}"])
        except ForgeError as exc:
            return await _conflict(
                db, run_dirs, row, node, worktree, branch, at, child, range_, str(exc), launch,
                policy,
            )  # fmt: skip
    left = missing(worktree, at, range_)
    if left or git_read(worktree, "status", "--porcelain", "--untracked-files=no"):
        await _stop(
            db,
            wid,
            node,
            f"slice {key} ({child['id']}) did not apply cleanly; {len(left)} commit(s) missing",
            StopKind.CONFLICT,
        )
        return "needs_human"
    return await _mark_joined(db, wid, key, worktree)


def _not_joined(worktree: Path, branch: str, at: str, recorded: list[str]) -> str | None:
    """Why a handed-off slice is not in the parent yet, or None: the applied-
    check, on a worktree settled enough to read. Reads only; never resets."""
    wrong = _unsettled(worktree, branch, at)
    if wrong is None and (left := missing(worktree, at, recorded)):
        wrong = f"{len(left)} commit(s) missing ({', '.join(c[:12] for c in left)})"
    return wrong


def _unsettled(worktree: Path, branch: str, at: str) -> str | None:
    """Why a handed-off slice's worktree is not one the applied-check can read,
    or None: something still in progress, HEAD off the parent's branch, a dirty
    tree, or a HEAD that does not descend from `at`. Reads only; never resets."""
    try:
        git.assert_no_operation(worktree)
    except UnsafeWorktree:
        return (
            "a git operation is still in progress in its worktree; finish the cherry-pick "
            "(`git cherry-pick --continue`) or clear it"
        )
    try:
        git.assert_on_branch(worktree, branch)
    except UnsafeWorktree as exc:
        return str(exc)
    if git_read(worktree, "status", "--porcelain", "--untracked-files=no"):
        return "its worktree has uncommitted changes; commit them as the steps below say"
    if not _is_ancestor(worktree, at, "HEAD"):
        return f"its HEAD does not descend from {at[:12]}, where the join had reached"
    return None


def _check_child(repo: Path, run_dirs, child: dict, launch, docs, head, fork) -> None:
    if not docs or not head or not fork:
        raise JoinRefused("it was never pinned: it has no recorded head or docs commit")
    branch = store.branch_for(child)
    now = git_read(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if now is not None and now != head:
        raise JoinRefused(
            f"its branch {branch} has moved since it completed ({head[:12]} -> {now[:12]})"
        )
    git.assert_lfsconfig_unmoved(repo, "replay it", rev=head, trusted=fork)
    stops.refuse_planted_repos(child, launch, run_dirs.worktrees / child["id"])


def _picking(conn, wid: str, key: int, at: str, range_: list[str]) -> None:
    state = store.group_state(conn, wid)
    state.slice_phase = "picking"
    state.at_commit = at
    state.recorded[key] = list(range_)
    store.set_group_state(conn, wid, state)


async def _conflict(
    db, run_dirs, row, node, worktree, branch, at, child, range_, detail, launch, policy
) -> str | None:
    """Put the parent back at `at` and hand the slice off; then the group's
    conflict handler, if it has one, gets a turn. None when it joined the
    slice, else the status the parent stopped with."""
    wid, key = row["id"], child["slice_key"]
    docs, head = child["docs_commit"], child["pinned_head"]
    files = (git_read(worktree, "diff", "--name-only", "--diff-filter=U") or "").split()
    try:
        put_back(worktree, branch, at)
    except (UnsafeWorktree, ForgeError) as exc:
        await _stop(db, wid, node, f"could not put the parent back: {exc}", StopKind.CHECKOUT)
        return "needs_human"

    await db.write(lambda c: _set_phase(c, wid, "handling" if node.on_conflict else "handed_off"))
    where = f" in {', '.join(files)}" if files else ""
    what = f"slice {key} ({child['id']}) conflicts with what is already joined{where}. "
    steps = _steps(child, docs, head, branch)
    if node.on_conflict:
        note = what + steps + _LEAVE_NOTHING
        stopped, ok = await _handle(db, run_dirs, row, node, worktree, note, launch, policy)
        wrong, removed = None, []
        if stopped is None:  # a verdict: a person's turn from here, never the handler's again
            await db.write(lambda c: _set_phase(c, wid, "handed_off"))
        if stopped is None and ok:
            removed = await _clear_leftovers(db, row, child, worktree)
            if (wrong := _not_joined(worktree, branch, at, range_)) is None:
                return await _mark_joined(db, wid, key, worktree)
        elif stopped is None:
            wrong = "it reported a failure"
        # Whatever it left, a person (or the handler's next turn after a stop)
        # starts from where the join stopped: the handed-off check never resets.
        try:
            put_back(worktree, branch, at)
        except (UnsafeWorktree, ForgeError) as exc:
            await _stop(
                db, wid, node, _left_unputbackable(exc, at, branch, steps), StopKind.CHECKOUT
            )
            return "needs_human"
        if stopped is not None:
            return stopped
        if removed:
            wrong += f" (it left {', '.join(removed)}, which the join removed)"
        detail = f"the conflict handler did not join it: {wrong}; {detail}"
    await _stop(db, wid, node, what + steps + f" ({detail[:300]})", StopKind.CONFLICT)
    return "needs_human"


def _set_phase(conn, wid: str, phase) -> None:
    state = store.group_state(conn, wid)
    state.slice_phase = phase
    store.set_group_state(conn, wid, state)


def _left_unputbackable(exc: Exception, at: str, branch: str, steps: str) -> str:
    """The stop for a handler that left something else in progress, its picks
    maybe on the branch."""
    return (
        f"the conflict handler left the parent where it cannot be put back: {exc}. "
        f"Clear that, then `git reset --hard {at[:12]}` on {branch}, where the join "
        "stopped, before the steps below. " + steps
    )


#: Fresh turns a conflict handler gets after being cut short: a handler cut
#: short again is likely to be again, and each turn costs a full agent run.
_FRESH_TURNS = 1


def _reruns(conn, wid: str, child: dict) -> int:
    return sum(
        e["type"] == WorkItemEvent.JOIN_HANDLER_RESUMED
        and e["payload"].get("child") == child["id"]
        and e["payload"].get("outcome") == "rerun"
        for e in events.read_after(conn, 0, wid)
    )


async def _handler_resumed(db, wid: str, child: dict, outcome: str) -> None:
    payload = {"child": child["id"], "slice_key": child["slice_key"], "outcome": outcome}
    await db.write(lambda c: events.append(c, wid, WorkItemEvent.JOIN_HANDLER_RESUMED, payload))


_LEAVE_NOTHING = (
    " Commit only through `git cherry-pick --continue` (or `git commit --allow-empty "
    "--no-edit` for an empty resolution), and leave nothing else behind: no uncommitted "
    "change and no untracked file (a `.orig`, a scratch file)."
)


async def _clear_leftovers(db, row, child: dict, worktree: Path) -> list[str]:
    """Remove what the conflict handler left in the parent's worktree untracked
    and not ignored, and name it in an event and the slice's join notes. `git
    clean -fd`: never `-x` (ignored files stay) and never `-ff` (a nested
    repository stays); a symlink goes as the link. What the straggler sweep
    would have left out stays too (`work_product_pathspec`: Kraft's own notes,
    what the setup installed). Tracked changes are not touched: `_unsettled`
    reports them."""
    from kraft import builtins

    wid = row["id"]
    try:
        base = await builtins.base_branch(db, wid, Path(row["repo"]))
        spec = await git.work_product_pathspec(worktree, base)
        with base_ignore_args(worktree, base) as ignore:
            clean = [*_GIT, *ignore, "-c", "core.quotePath=false", "clean", "-fd", "--", *spec]
            out = await run_git(worktree, clean)
    except RuntimeError:  # ForgeError included
        # Nothing sweeps them into a commit now, so the applied-check can still pass.
        logger.warning("could not clear the conflict handler's leftovers", exc_info=True)
        return []
    paths = [line[len("Removing ") :] for line in out.splitlines() if line.startswith("Removing ")]
    if not paths:
        return []
    key = child["slice_key"]

    def record(c):
        events.append(
            c,
            wid,
            WorkItemEvent.JOIN_LEFTOVERS_REMOVED,
            {"child": child["id"], "slice_key": key, "paths": paths},
        )
        state = store.group_state(c, wid)
        note = f"its conflict handler left {', '.join(paths)}, which the join removed"
        state.skipped[key] = [*state.skipped.get(key, []), note]
        store.set_group_state(c, wid, state)

    await db.write(record)
    return paths


async def _handle(
    db, run_dirs, row, node, worktree, note, launch, policy
) -> tuple[str | None, bool]:
    """Run the group's conflict handler as any recovery handler runs, with
    nothing more than an `on_failure` handler gets: the stop a pause, a rate
    limit or the like named (or None), and whether it answered `ok`."""
    from kraft import policy as _policy
    from kraft.executor import dispatch, walk

    budget = store.effective_budget(row, policy.budget if policy else _policy.NO_BUDGET)
    verdict, failed, _excs = await dispatch.run_recovery(
        db,
        run_dirs,
        row["id"],
        node,
        row,
        worktree,
        handler=node.on_conflict,
        scope="conflict",
        failed=[],
        note=note,
        steer=None,
        launch=launch,
        budget=budget,
        measured_round=0,
        round=0,
    )
    stop = await walk._sentinel_stop(db, row["id"], node, verdict, failed, budget)
    return stop, verdict == "ok"


async def _mark_joined(db, wid: str, key: int, worktree: Path) -> None:
    sha = git_read(worktree, "rev-parse", "HEAD")

    def joined(c):
        s = store.group_state(c, wid)
        s.joined_through = key
        s.at_commit = sha
        s.slice_phase = "idle"
        store.set_group_state(c, wid, s)

    await db.write(joined)
    return None


async def _finish(db, run_dirs, wid: str, state: GroupState, children: list[dict]) -> None:
    """After the last slice: the replayed children's branches go (the parent holds
    their commits, and archive would keep a branch nothing else holds), and the
    group is `joined`. A dropped child's branch stays, so work a cancel kept
    survives its archive."""
    for child in children:
        if child["status"] != WorkItemStatus.COMPLETED:
            continue
        repo = Path(child["repo"])
        branch = store.branch_for(child)
        # The child's worktree still has the branch checked out until it is
        # archived, so `branch -D` would refuse: delete the ref itself.
        if git_read(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
            try:
                await run_git(repo, ["git", "update-ref", "-d", f"refs/heads/{branch}"])
            except ForgeError:
                logger.warning("could not delete the replayed branch %s", branch, exc_info=True)

    def joined(c):
        s = store.group_state(c, wid)
        s.phase = "joined"
        s.slice_phase = "idle"
        store.set_group_state(c, wid, s)

    await db.write(joined)
