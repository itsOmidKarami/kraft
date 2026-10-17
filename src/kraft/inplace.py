"""Items that run in their repository's own checkout instead of a worktree.

Whether an item runs in place is frozen on its chain snapshot at intake
(`WorkItemTarget.in_place`); everything here reads it from there.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

from kraft import events, store
from kraft.adapters.forge import git
from kraft.config import ConfigError, git_read
from kraft.vocab import ChainEvent

is_in_place = store.is_in_place


def item_folder(run_dirs, row) -> Path:
    """The folder the item's work happens in: its repository's checkout when
    it runs in place, else its worktree."""
    return folder_for(run_dirs, row, store.materialized_chain_of(row))


def folder_for(run_dirs, row, chain) -> Path:
    """`item_folder` for a caller that already parsed the row's snapshot."""
    in_place = chain is not None and chain.target.in_place
    return Path(row["repo"]) if in_place else run_dirs.worktrees / row["id"]


class CheckoutRefused(RuntimeError):
    """The checkout cannot be taken without forcing it: a `checkout` stop
    (`StopKind.CHECKOUT`), never a failure and never auto-escalated. The
    message names the files or branch and what the person does next; `paths`
    are the files. `moved_from` is set when the checkout was switched before
    the refusal, so the switch is still recorded."""

    def __init__(self, message: str, paths: list[str] | None = None):
        super().__init__(message)
        self.paths = paths or []
        self.moved_from: dict | None = None


async def prepare(db, run_dirs, row, *, base: str) -> Path:
    """`ensure_worktree` for an in-place item: on its first call, pin
    `base_ref` and make the item's branch ref there. The checkout itself is
    untouched: no switch, no setup command, no carried files and no git
    config written. A branch deleted later is never made again: the take
    stops on it."""
    from kraft import builtins

    folder = item_folder(run_dirs, row)
    if not folder.is_dir():
        raise ConfigError(
            f"{folder}, the checkout this item runs in, is gone: put it back, or abandon the item"
        )
    if row["base_ref"] is not None:
        return folder
    base_ref = await builtins.upstream_head(folder, base)
    if not base_ref:
        return folder
    # Before the first take commits them: a missing stored copy fails the
    # item here, as `ensure_worktree` does, not as a failed task.
    builtins.stored_attachments(run_dirs, row["id"], _attachments_of(row), row["repo"])
    branch = store.branch_for(row)
    if _ref(folder, branch) is None:
        done = subprocess.run(
            ["git", "branch", branch, base_ref], cwd=folder, capture_output=True, text=True
        )
        if done.returncode != 0:
            raise RuntimeError(f"could not make {branch} in {folder}: {done.stderr.strip()}")
    await db.write(lambda c: store.set_base_ref(c, row["id"], base_ref))
    return folder


async def take(db, run_dirs, row, *, attachments) -> None:
    """Put the checkout on the item's branch, right before a task that needs
    the folder: never on the walk's entry, which re-runs on every CI poll.
    Never forced: anything git would refuse, or that would clobber a file,
    raises `CheckoutRefused`. The person's uncommitted edits ride along. On
    the first take (the branch still at `base_ref`) the attachments are
    committed on the branch, by path. A switch is always recorded, even one a
    later step refused."""
    from kraft import builtins

    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (row["id"],)).fetchone()
    )
    folder = item_folder(run_dirs, row)
    branch = store.branch_for(row)
    first = (
        bool(attachments)
        and row["base_ref"] is not None
        and _ref(folder, branch) == row["base_ref"]
    )
    attachments = (
        builtins.stored_attachments(run_dirs, row["id"], attachments, row["repo"]) if first else []
    )
    base = await builtins.base_branch(db, row["id"], folder) if attachments else ""
    moved = None
    running = asyncio.ensure_future(
        asyncio.to_thread(_take, folder, branch, row["base_ref"], attachments, row["id"], base)
    )
    try:
        moved = await asyncio.shield(running)
    except CheckoutRefused as exc:
        moved = exc.moved_from
        raise
    except asyncio.CancelledError:
        # A cancel (a pause) cannot stop the thread: let git finish, and
        # record a switch it made before giving the task up.
        with contextlib.suppress(Exception):
            moved = await running
        raise
    finally:
        if moved is not None:
            await db.write(
                lambda c: events.append(
                    c, row["id"], ChainEvent.CHECKOUT_SWITCHED, {**moved, "to": branch}
                )
            )


#: One lock per checkout: co-tasks of one step take it at once, and git's
#: index lock would refuse the second.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(folder: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(folder.resolve()), threading.Lock())


def _ref(folder: Path, branch: str) -> str | None:
    return git_read(
        folder, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", expected_failure=True
    )


def _git_lines(folder: Path, command: str, *args: str) -> list[str]:
    """`git <command> -z *args`, one path a line, unquoted. Fails closed: a
    read git refuses is a `CheckoutRefused`, never an empty answer."""
    done = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "--literal-pathspecs"]
        + [command, "-z", *args],
        cwd=folder,
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )
    if done.returncode != 0:
        raise CheckoutRefused(
            f"Kraft could not read {folder} (`git {command}` said: {done.stderr.strip()}), "
            "so it did not switch it: fix that, then retry the item"
        )
    return [p for p in done.stdout.split("\0") if p]


def _take(folder, branch, base_ref, attachments, wid, base) -> dict | None:
    """`take`'s git, under the checkout's lock. Returns `{"from": ...}` (and
    git's `output` when a hook failed after switching) when it switched."""
    with _lock(folder):
        try:
            git.assert_no_operation(folder)
        except git.UnsafeWorktree:
            raise CheckoutRefused(
                f"{folder} has a git operation in progress (a rebase, merge, cherry-pick, "
                "revert or bisect): finish or abort it yourself, then retry the item"
            ) from None
        unmerged = _git_lines(folder, "diff", "--name-only", "--diff-filter=U")
        if unmerged:
            raise CheckoutRefused(
                f"{folder} has unmerged paths: {', '.join(unmerged)}; resolve them yourself, "
                "then retry the item",
                unmerged,
            )
        if _ref(folder, branch) is None:
            raise CheckoutRefused(
                f"the branch {branch} is gone from {folder}, and Kraft does not make it again: "
                "restore it (`git reflog` may still have its commits) and retry, or abandon "
                "the item"
            )
        moved = None
        if git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True) != (
            f"refs/heads/{branch}"
        ):
            moved = _switch(folder, branch)
        if attachments and base_ref and _ref(folder, branch) == base_ref:
            try:
                _attach(folder, attachments, wid, base)
            except CheckoutRefused as exc:
                exc.moved_from = moved
                raise
        return moved


def _switch(folder: Path, branch: str) -> dict:
    """A plain `git checkout <branch>`: never `-f`, and refused before it
    would overwrite an ignored file the branch tracks, which git itself
    would replace without a word. A checkout stopped part way (a hung hook or
    filter) is put back to exactly what it was (`_put_back`)."""
    from kraft import builtins

    head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    moved_from = (head or "").removeprefix("refs/heads/") or git_read(folder, "rev-parse", "HEAD")
    changed = _git_lines(folder, "diff", "--name-only", "HEAD", branch)
    ignored = _ignored_in_the_way(folder, changed)
    if ignored:
        raise CheckoutRefused(
            f"switching {folder} from {moved_from} to {branch} would overwrite "
            f"{', '.join(ignored)}, ignored here but tracked on {branch}. Move them aside "
            "yourself, then retry the item",
            ignored,
        )
    before = _Before.of(folder, changed)
    try:
        done = builtins._run_git_group(
            ["git", "-c", "core.quotePath=false", "checkout", branch, "--"],
            timeout=builtins.WORKTREE_ADD_TIMEOUT_S,
            env={**os.environ, "LC_ALL": "C"},
            cwd=folder,
        )
    except builtins._GroupTimedOut:
        stopped = f"stopped after {builtins.WORKTREE_ADD_TIMEOUT_S:.0f}s"
        if _on(folder, branch):
            # Only a post-checkout hook was left: the switch itself finished.
            return {"from": moved_from, "output": f"a checkout hook was {stopped}"}
        left = _put_back(folder, before)
        what = (
            "Kraft put back the files it had already switched: the checkout is as it was"
            if not left
            else f"Kraft could not put back {', '.join(left)}, which it had written"
        )
        raise CheckoutRefused(
            f"Kraft's switch of {folder} to {branch} was {stopped} (a hook or a filter such "
            f"as an LFS smudge hung), and {what}. Find what hung, then retry the item",
            left,
        ) from None
    said = done.stderr.strip() or done.stdout.strip()
    if done.returncode == 0:
        return {"from": moved_from}
    if _on(folder, branch):
        # A post-checkout hook failed after git had switched.
        return {"from": moved_from, "output": said}
    # git lists each file it would overwrite on a tab-indented line.
    paths = [ln.strip() for ln in done.stderr.splitlines() if ln.startswith("\t")]
    what = f"it would overwrite {', '.join(paths)}" if paths else f"git said: {said}"
    advice = "Commit, stash or move those changes" if paths else "Sort that out"
    raise CheckoutRefused(
        f"git would not switch {folder} from {moved_from} to {branch}: {what}. "
        f"{advice} yourself, then retry the item",
        paths,
    )


def _on(folder: Path, branch: str) -> bool:
    head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    return head == f"refs/heads/{branch}"


def _ignored_in_the_way(folder: Path, changed: list[str]) -> list[str]:
    """The ignored files on disk a switch writing `changed` would replace: a
    changed path itself, or a file where it needs a directory."""
    if not changed:
        return []
    ignored = [
        e.rstrip("/")
        for e in _git_lines(
            folder, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory"
        )
    ]
    hits = []
    for c in changed:
        blocker = _first_on_disk(folder, c)
        if blocker and any(blocker == e or blocker.startswith(e + "/") for e in ignored):
            hits.append(blocker)
    return sorted(set(hits))


def _first_on_disk(folder: Path, path: str) -> str | None:
    """`path` when it is on disk, or the file (not directory) standing where
    one of its parents should be; None when nothing is in the way."""
    parts = path.split("/")
    for i in range(1, len(parts) + 1):
        p = "/".join(parts[:i])
        full = folder / p
        if not os.path.lexists(full):
            return None
        if i == len(parts) or full.is_symlink() or not full.is_dir():
            return p
    return None


class _Before:
    """What the checkout held, before a switch, of the paths it writes."""

    def __init__(self, changed, dirty, existed, lock, gitdir):
        self.changed, self.dirty, self.existed = changed, dirty, existed
        self.lock, self.gitdir = lock, gitdir

    @classmethod
    def of(cls, folder: Path, changed: list[str]) -> _Before:
        gitdir = Path(git_read(folder, "rev-parse", "--absolute-git-dir") or folder / ".git")
        dirty = set(_git_lines(folder, "diff", "--name-only", "HEAD"))
        dirty |= set(_git_lines(folder, "ls-files", "--others", "--exclude-standard"))
        existed = {c for c in changed if os.path.lexists(folder / c)}
        return cls(changed, dirty, existed, (gitdir / "index.lock").exists(), gitdir)


def _put_back(folder: Path, before: _Before) -> list[str]:
    """Undo a killed checkout, and only it: its `index.lock`, and each path it
    writes that was clean before, from HEAD, or gone if HEAD lacks it and it
    was not there. A path that was the person's (dirty) is never touched.
    Returns what still differs, `index.lock` included."""
    if not before.lock:
        (before.gitdir / "index.lock").unlink(missing_ok=True)
    ours = [c for c in before.changed if c not in before.dirty]
    # Whole listings, intersected here: a pathspec per path could pass ARG_MAX.
    tracked = set(_git_lines(folder, "ls-tree", "-r", "--name-only", "HEAD")) & set(ours)
    restore = [c for c in ours if c in tracked]
    if restore:
        subprocess.run(
            ["git", "checkout", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul"],
            cwd=folder,
            input="\0".join(restore),
            capture_output=True,
            text=True,
        )
    for c in ours:
        if c not in tracked and c not in before.existed and os.path.lexists(folder / c):
            (folder / c).unlink()
    left = set(_git_lines(folder, "diff", "--name-only", "HEAD")) & set(ours)
    left |= {c for c in ours if c not in tracked and os.path.lexists(folder / c)}
    if not before.lock and (before.gitdir / "index.lock").exists():
        left.add(str(before.gitdir / "index.lock"))
    return sorted(left)


def _attach(folder: Path, attachments: list[dict], wid: str, base: str) -> None:
    """Copy the attachments in and commit them on the branch, by path. A file
    already there with other content is the person's, and refuses; so does a
    checkout with no identity to commit as."""
    from kraft import builtins

    for key in ("user.name", "user.email"):
        if not git_read(folder, "config", "--get", key, expected_failure=True):
            raise CheckoutRefused(
                f"no {key} configured in {folder}, so Kraft cannot commit the item's "
                "attachments there: set it, then retry the item"
            )
    root = folder.resolve()
    for a in attachments:
        dest = folder / a["path"]
        src = Path(a["source"])
        if dest.is_symlink() or not dest.resolve().is_relative_to(root):
            clash = True
        else:
            clash = dest.exists() and dest.read_bytes() != src.read_bytes()
        if clash:
            raise CheckoutRefused(
                f"{a['path']} in {folder} is not the {a['kind']} this item was filed with, "
                "and Kraft will not overwrite it: move it aside, then retry the item",
                [a["path"]],
            )
    for a in attachments:
        dest = folder / a["path"]
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(a["source"], dest)
    builtins._commit_paths(
        folder,
        [a["path"] for a in attachments],
        f"chore: attach {'+'.join(a['kind'] for a in attachments)} for {wid}",
        base,
    )


def _attachments_of(row) -> list[dict]:
    from kraft.executor import entry

    return entry.attachments_of(row)
