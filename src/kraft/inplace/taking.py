"""Taking the checkout: the item's branch made, the checkout switched onto
it right before a task, the first take's attachments, and the branch
dropped at the end."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from kraft import events, node_runs, store
from kraft.adapters.forge import git
from kraft.config import ConfigError, git_read
from kraft.inplace.base import (
    CheckoutRefused,
    NotRunning,
    _git_lines,
    _gone,
    _ignored_in_the_way,
    _lock,
    _on,
    _ref,
    _settled,
    _wait_out,
    item_folder,
    logger,
)
from kraft.vocab import ChainEvent, WorkItemStatus


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
    builtins.stored_attachments(run_dirs, row["id"], attachments_of(row), row["repo"])
    branch = store.branch_for(row)
    if _ref(folder, branch) is None:
        done = subprocess.run(
            ["git", "branch", branch, base_ref],
            cwd=folder,
            capture_output=True,
            text=True,
            start_new_session=True,
        )
        if done.returncode != 0:
            raise RuntimeError(f"could not make {branch} in {folder}: {done.stderr.strip()}")
    await db.write(lambda c: store.set_base_ref(c, row["id"], base_ref))
    return folder


async def take(db, run_dirs, row, *, attachments, walk: bool = False) -> None:
    """Put the checkout on the item's branch, right before a task that needs
    the folder: never on the walk's entry, which re-runs on every CI poll.
    Never forced: anything git would refuse, or that would clobber a file,
    raises `CheckoutRefused`. The person's uncommitted edits ride along. On
    the first take (the branch still at `base_ref`) the attachments are
    committed on the branch, by path. A switch is always recorded, even one a
    later step refused. The lease is looked at again in the writer: `walk`'s
    item must still be `active`, and no other item may hold the checkout."""
    from kraft import builtins

    def look(c):
        found = c.execute("SELECT * FROM work_items WHERE id = ?", (row["id"],)).fetchone()
        holder = store.folder_holder(c, found["repo"], found["id"])
        title = holder and store.checkout_reason(c, holder)["held_by_title"]
        return found, holder, title or holder

    row, holder, named = await db.write(look)
    folder = item_folder(run_dirs, row)
    if walk and row["status"] != WorkItemStatus.ACTIVE:
        raise NotRunning(f"{row['id']} is {row['status']}, no longer running: nothing to take")
    if holder is not None:
        raise CheckoutRefused(
            f"another item, {named}, is using the checkout {folder}: Kraft does not take it "
            "from under that item. Retry this one once that item stops"
        )
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
    running = asyncio.ensure_future(
        asyncio.to_thread(_take, folder, branch, row["base_ref"], attachments, row["id"], base)
    )
    # A cancel (a pause) cannot stop git in its thread, and a checkout left to
    # finish never loses the person's work: wait it out, however many cancels
    # arrive, so a switch it made is recorded; then honour the cancel.
    cancelled = await _wait_out(running)
    failed = running.exception()
    moved = failed.moved_from if isinstance(failed, CheckoutRefused) else None
    if failed is None:
        moved = running.result()
    if moved is not None:
        await db.write(
            lambda c: events.append(
                c, row["id"], ChainEvent.CHECKOUT_SWITCHED, {**moved, "to": branch}
            )
        )
    if cancelled:
        raise asyncio.CancelledError
    if failed is not None:
        raise failed


def _take(folder, branch, base_ref, attachments, wid, base) -> dict | None:
    """`take`'s git, under the checkout's lock. Returns `{"from": ...}` (and
    git's `output` when a hook failed after switching) when it switched."""
    with _lock(folder):
        _settled(folder)
        if _ref(folder, branch) is None:
            raise _gone(folder, branch)
        moved = None
        if git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True) != (
            f"refs/heads/{branch}"
        ):
            moved = _switch(folder, branch)
        if attachments and base_ref and _ref(folder, branch) == base_ref:
            try:
                _attach(folder, branch, attachments, wid, base)
            except CheckoutRefused as exc:
                exc.moved_from = moved
                raise
        return moved


def _switch(folder: Path, branch: str) -> dict:
    """A plain `git checkout <branch>`: never `-f`, and refused before it
    would overwrite an ignored file the branch tracks, which git itself
    would replace without a word."""
    head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    moved_from = (head or "").removeprefix("refs/heads/") or git_read(folder, "rev-parse", "HEAD")
    # Detached: what only HEAD holds would be left to the reflog.
    held = "--branches", "--remotes", "--tags"
    only = (
        []
        if head
        else _git_lines(folder, "log", "--max-count=6", "--format=%H", "HEAD", "--not", *held)
    )
    if only:
        raise CheckoutRefused(
            f"{folder} is on a detached HEAD with commits no branch holds "
            f"({', '.join(c[:12] for c in only[:5])}{', ...' if len(only) > 5 else ''}): "
            "switching away would leave them only in the reflog. Put a branch on them "
            "(`git branch <name>`), then retry the item"
        )
    changed = _git_lines(folder, "diff", "--name-only", "--no-renames", "HEAD", branch)
    ignored = _ignored_in_the_way(folder, changed)
    if ignored:
        raise CheckoutRefused(
            f"switching {folder} from {moved_from} to {branch} would overwrite "
            f"{', '.join(ignored)}, ignored here but tracked on {branch}. Move them aside "
            "yourself, then retry the item",
            ignored,
        )
    # Run to the end, never killed: git refuses before it would overwrite an
    # edit, so only a checkout stopped part way could leave a tree half one
    # branch and half the other. A hung filter or hook holds this checkout's
    # lock alone.
    done = subprocess.run(
        ["git", "-c", "core.quotePath=false", "checkout", branch, "--"],
        cwd=folder,
        capture_output=True,
        text=True,
        errors="surrogateescape",
        env={**os.environ, "LC_ALL": "C"},
        stdin=subprocess.DEVNULL,
        # Its own session: a terminal's Ctrl-C or a service stop's group kill
        # must not cut it off either.
        start_new_session=True,
    )
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


def drop_branch(repo: Path, branch: str, wid: str, *, keep: bool) -> dict:
    """Abandon's and archive's end of an in-place item's branch, which leaves
    the checkout and whatever runs in it alone. The branch, and Kraft's record
    of pushing it, stay when the checkout is on it (`checked out`), when a take
    or rebase holds the checkout right now or git refuses the delete, as for a
    branch another worktree has out (`in use`), or when `keep` (archive found
    commits only it holds: `unpushed`). The item's per-attempt refs go
    regardless. Returns `branch_kept`: the reason, or None when it went. Never
    raises on git: the caller may already have flipped the row."""
    if not repo.is_dir():
        return {"branch_kept": None, "repo_missing": str(repo)}
    try:
        kept = _drop_branch(repo, branch, wid, keep)
        node_runs.drop_refs(repo, wid)
    except OSError as exc:
        logger.warning("drop %s: git failed: %s", wid, exc)
        return {"branch_kept": "git failed"}
    return {"branch_kept": kept}


def _drop_branch(repo: Path, branch: str, wid: str, keep: bool) -> str | None:
    lock = _lock(repo)
    if not lock.acquire(blocking=False):
        return "in use"
    try:
        if _on(repo, branch):
            return "checked out"
        if keep:
            return "unpushed"
        if _ref(repo, branch):
            done = subprocess.run(
                ["git", "branch", "-D", "--", branch],
                cwd=repo,
                capture_output=True,
                text=True,
                start_new_session=True,
            )
            if done.returncode != 0:
                logger.warning("drop %s: branch kept: %s", wid, done.stderr.strip())
                return "in use"
        # Absent (a never-started item never pushed) is fine.
        subprocess.run(
            ["git", "update-ref", "-d", f"{git.PUSHED_REFS}/{branch}"],
            cwd=repo,
            capture_output=True,
            start_new_session=True,
        )
        return None
    finally:
        lock.release()


def _attach(folder: Path, branch: str, attachments: list[dict], wid: str, base: str) -> None:
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
    # A post-checkout hook may have moved it on: never commit on their branch.
    if not _on(folder, branch):
        raise CheckoutRefused(
            f"{folder} left {branch} right after Kraft switched to it, so Kraft did not commit "
            "the item's attachments there: retry the item"
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


def attachments_of(row) -> list[dict]:
    from kraft.executor import entry

    return entry.attachments_of(row)
