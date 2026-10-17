"""Items that run in their repository's own checkout instead of a worktree.

Whether an item runs in place is frozen on its chain snapshot at intake
(`WorkItemTarget.in_place`); everything here reads it from there.
"""

from __future__ import annotations

import asyncio
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
    are the files."""

    def __init__(self, message: str, paths: list[str] | None = None):
        super().__init__(message)
        self.paths = paths or []


async def prepare(db, run_dirs, row, *, base: str) -> Path:
    """`ensure_worktree` for an in-place item: pin `base_ref`, and make the
    item's branch ref at it if missing. The checkout itself is untouched: no
    switch, no setup command, no carried files and no git config written."""
    from kraft import builtins

    folder = item_folder(run_dirs, row)
    if not folder.is_dir():
        raise ConfigError(
            f"{folder}, the checkout this item runs in, is gone: put it back, or abandon the item"
        )
    base_ref = row["base_ref"]
    if base_ref is None:
        base_ref = await builtins.upstream_head(folder, base)
        if base_ref:
            await db.write(lambda c: store.set_base_ref(c, row["id"], base_ref))
    branch = store.branch_for(row)
    if base_ref and _ref(folder, branch) is None:
        done = subprocess.run(
            ["git", "branch", branch, base_ref], cwd=folder, capture_output=True, text=True
        )
        if done.returncode != 0:
            raise RuntimeError(f"could not make {branch} in {folder}: {done.stderr.strip()}")
        # Before the first take commits them: a missing stored copy fails the
        # item here, as `ensure_worktree` does, not as a failed task.
        builtins.stored_attachments(run_dirs, row["id"], _attachments_of(row), row["repo"])
    return folder


async def take(db, run_dirs, row, *, attachments) -> None:
    """Put the checkout on the item's branch, right before a task that needs
    the folder: never on the walk's entry, which re-runs on every CI poll.
    Never forced: anything git would refuse, or that would clobber a file,
    raises `CheckoutRefused` with the checkout as it was. The person's
    uncommitted edits ride along. On the first take (the branch still at
    `base_ref`) the attachments are committed on the branch, by path."""
    from kraft import builtins

    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (row["id"],)).fetchone()
    )
    folder = item_folder(run_dirs, row)
    attachments = builtins.stored_attachments(run_dirs, row["id"], attachments, row["repo"])
    base = await builtins.base_branch(db, row["id"], folder) if attachments else ""
    moved_from = await asyncio.to_thread(
        _take, folder, store.branch_for(row), row["base_ref"], attachments, row["id"], base
    )
    if moved_from is not None:
        await db.write(
            lambda c: events.append(
                c,
                row["id"],
                ChainEvent.CHECKOUT_SWITCHED,
                {"from": moved_from, "to": store.branch_for(row)},
            )
        )


#: Co-tasks of one step take the same checkout at once; git's index lock
#: would refuse the second.
_TAKING = threading.Lock()


def _ref(folder: Path, branch: str) -> str | None:
    return git_read(
        folder, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", expected_failure=True
    )


def _take(folder, branch, base_ref, attachments, wid, base) -> str | None:
    """`take`'s git, under `_TAKING`. Returns what HEAD was when it switched."""
    with _TAKING:
        try:
            git.assert_no_operation(folder)
        except git.UnsafeWorktree:
            raise CheckoutRefused(
                f"{folder} has a git operation in progress (a rebase, merge, cherry-pick, "
                "revert or bisect): finish or abort it yourself, then retry the item"
            ) from None
        unmerged = (git_read(folder, "diff", "--name-only", "--diff-filter=U") or "").splitlines()
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
        head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
        moved_from = None
        if head != f"refs/heads/{branch}":
            moved_from = (head or "").removeprefix("refs/heads/") or git_read(
                folder, "rev-parse", "HEAD"
            )
            # Never `-f`: git refuses a switch that would overwrite an edit.
            done = subprocess.run(
                ["git", "checkout", branch, "--"],
                cwd=folder,
                capture_output=True,
                text=True,
                env={**os.environ, "LC_ALL": "C"},
            )
            if done.returncode != 0:
                # git lists each file it would overwrite on a tab-indented line.
                paths = [ln.strip() for ln in done.stderr.splitlines() if ln.startswith("\t")]
                said = ", ".join(paths) or done.stderr.strip()
                raise CheckoutRefused(
                    f"git would not switch {folder} from {moved_from} to {branch}: it would "
                    f"overwrite {said}. Commit, stash or move those changes yourself, then "
                    "retry the item",
                    paths,
                )
        if attachments and base_ref and _ref(folder, branch) == base_ref:
            _attach(folder, attachments, wid, base)
        return moved_from


def _attach(folder: Path, attachments: list[dict], wid: str, base: str) -> None:
    """Copy the attachments in and commit them on the branch, by path. A file
    already there with other content is the person's, and refuses."""
    from kraft import builtins

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
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    builtins._commit_paths(
        folder,
        [a["path"] for a in attachments],
        f"chore: attach {'+'.join(a['kind'] for a in attachments)} for {wid}",
        base,
    )


def _attachments_of(row) -> list[dict]:
    from kraft.executor import entry

    return entry.attachments_of(row)
