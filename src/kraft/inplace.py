"""Items that run in their repository's own checkout instead of a worktree.

Whether an item runs in place is frozen on its chain snapshot at intake
(`WorkItemTarget.in_place`); everything here reads it from there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kraft import events, item_paths, node_runs, store
from kraft.adapters.forge import git
from kraft.config import ConfigError, git_read
from kraft.vocab import LIVE, ChainEvent, StopKind, WorkItemStatus
from kraft.vocab.sql import in_list

logger = logging.getLogger(__name__)

is_in_place = store.is_in_place


def item_folder(run_dirs, row) -> Path:
    """The folder the item's work happens in: its repository's checkout when
    it runs in place, else its worktree."""
    return folder_for(run_dirs, row, store.materialized_chain_of(row))


def folder_for(run_dirs, row, chain) -> Path:
    """`item_folder` for a caller that already parsed the row's snapshot."""
    in_place = chain is not None and chain.target.in_place
    return Path(row["repo"]) if in_place else run_dirs.worktrees / row["id"]


def head_ref(row) -> str:
    """What a reader that shows the item takes as its tip: `HEAD` in its
    worktree, or in place its branch, since the checkout's HEAD and working
    tree may be the person's."""
    return f"refs/heads/{store.branch_for(row)}" if is_in_place(row) else "HEAD"


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


class NotRunning(CheckoutRefused):
    """The walk's take for an item no longer `active`: a pause (a broken
    base's soft one) landed between its steps. Nothing switched; the step
    ends paused, never as a stop."""


class AutostashClash(RuntimeError):
    """An in-place rebase that git reported done, but whose autostash, put
    back, clashed with the newer base: `paths` are left unmerged in the
    checkout, and the person's original edits are the stash commit `stash`,
    which Kraft never pops or drops. A `checkout` stop, never a conflict."""

    def __init__(self, message: str, paths: list[str], stash: str | None):
        super().__init__(message)
        self.paths = paths
        self.stash = stash


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


#: One lock per checkout: co-tasks of one step take it at once, and git's
#: index lock would refuse the second.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(folder: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(folder.resolve()), threading.Lock())


@contextlib.asynccontextmanager
async def _held(folder: Path):
    """`_lock(folder)` from async code. A cancel while waiting for it still
    releases it once taken, so no cancel can leave it held."""
    lock = _lock(folder)
    got = asyncio.ensure_future(asyncio.to_thread(lock.acquire))
    try:
        await asyncio.shield(got)
    except asyncio.CancelledError:
        got.add_done_callback(lambda f: f.cancelled() or lock.release())
        raise
    try:
        yield
    finally:
        lock.release()


async def rebase(
    run_dirs,
    row,
    *,
    base: str,
    force: bool = False,
    timeout: float | None = None,
    identity=None,
) -> str | None:
    """`builtins.refresh_worktree_base` for an in-place item, never moving
    the person's checkout: when it is on the item's branch, git will not
    check that branch out anywhere else, so the rebase runs there with
    `--autostash` (unbounded: git is never killed in the person's checkout);
    otherwise in a throwaway worktree under `run_dirs.scratch`, with no setup
    and no carried files, removed whatever happens. Which one is decided
    under the checkout's lock, held to the end, so a take cannot switch the
    checkout in between."""
    from kraft import builtins

    if row["base_ref"] is None:  # never started: `prepare` forks from the newest base
        return None
    checkout = Path(row["repo"])
    branch = store.branch_for(row)
    async with _held(checkout):
        if _on(checkout, branch):
            await _finish(_settled, checkout)
            try:
                return await builtins.refresh_worktree_base(
                    checkout,
                    checkout,
                    branch,
                    base=base,
                    force=force,
                    identity=identity,
                    autostash=True,
                )
            except builtins.RebaseBlocked:
                raise
            except git.UnsafeWorktree:
                # Never a worktree's advice (delete its operation's state):
                # the person's checkout, as `_settled` tells it.
                raise _off(checkout, branch) from None
        # One a killed server left would pin the branch for good.
        await _finish(_drop_scratch, checkout, run_dirs.scratch.glob(f"{row['id']}-*"))
        run_dirs.scratch.mkdir(parents=True, exist_ok=True)
        scratch = run_dirs.scratch / f"{row['id']}-{secrets.token_hex(4)}"
        try:
            await _finish(_add_scratch, checkout, scratch, branch)
            return await builtins.refresh_worktree_base(
                scratch,
                checkout,
                branch,
                base=base,
                force=force,
                timeout=timeout,
                identity=identity,
            )
        finally:
            await _finish(_drop_scratch, checkout, [scratch])


def _add_scratch(checkout: Path, scratch: Path, branch: str) -> None:
    """`git worktree add` of the existing `branch`: never `-b`, so a branch
    the person deleted is never made again. (git itself would still make one
    from a remote's copy if it went in the instant between the read and the
    add.)"""
    from kraft import builtins

    if _ref(checkout, branch) is None:
        raise _gone(checkout, branch)
    try:
        done = builtins._run_git_group(
            ["git", "worktree", "add", "--quiet", str(scratch), branch],
            timeout=builtins.WORKTREE_ADD_TIMEOUT_S,
            cwd=checkout,
        )
    except subprocess.TimeoutExpired:
        said = f"timed out after {builtins.WORKTREE_ADD_TIMEOUT_S:.0f}s"
    else:
        if done.returncode == 0:
            return
        said = done.stderr.strip() or done.stdout.strip()
    if _ref(checkout, branch) is None:
        raise _gone(checkout, branch)
    raise CheckoutRefused(
        f"Kraft could not check {branch} out apart from {checkout} to rebase it "
        f"(git said: {said}): sort that out, then retry the item"
    )


def _drop_scratch(checkout: Path, scratches, git_dir: Path | None = None) -> None:
    """Remove throwaway worktrees (Kraft's own, never the person's) and
    prune what git still records of them. `git_dir`, the repository's own,
    when `checkout` is no checkout of it."""
    from kraft import builtins

    gitd = ["git", *(["--git-dir", str(git_dir)] if git_dir else [])]
    for scratch in list(scratches):
        try:
            builtins._run_git_group(
                [*gitd, "worktree", "remove", "--force", str(scratch)],
                timeout=builtins.WORKTREE_ADD_TIMEOUT_S,
                cwd=checkout,
            )
        except subprocess.TimeoutExpired:
            logger.warning("git worktree remove timed out for %s", scratch)
        shutil.rmtree(scratch, ignore_errors=True)
    subprocess.run(
        [*gitd, "worktree", "prune"],
        cwd=checkout,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def drop_stale_scratch(run_dirs) -> None:
    """At startup, before anything rebases: every throwaway worktree a killed
    server left under `run_dirs.scratch`, which would pin its item's branch
    until that item's next rebase. Never anything of the person's."""
    for scratch in list(run_dirs.scratch.iterdir()) if run_dirs.scratch.is_dir() else []:
        if scratch.is_symlink():  # never Kraft's: nothing is followed or removed
            continue
        common = None
        if (scratch / ".git").is_file():  # never a repository the scratch only sits in
            common = git_read(
                scratch,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                expected_failure=True,
            )
        if common:  # a submodule's, or one apart from its checkout, is no `.git` in one
            _drop_scratch(run_dirs.scratch, [scratch], git_dir=Path(common))
        else:
            shutil.rmtree(scratch, ignore_errors=True)


async def _wait_out(running: asyncio.Future) -> bool:
    """Wait for `running` (a thread, which a cancel cannot stop) to its end,
    however many cancels arrive; whether one did."""
    cancelled = False
    while not running.done():
        try:
            # Not `shield`: a shielded future that fails after a cancel is
            # reported to the loop as unhandled.
            await asyncio.wait({running})
        except asyncio.CancelledError:
            cancelled = True
    return cancelled


async def _finish(fn, *args, over_cancel: tuple[type[BaseException], ...] = ()):
    """`fn(*args)` in a thread, waited out to its end (what it leaves must be
    known); then a cancel is honoured, unless `fn` raised one of
    `over_cancel`, which is raised instead."""
    running = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    if await _wait_out(running):
        failed = running.exception()
        if isinstance(failed, over_cancel):
            raise failed
        raise asyncio.CancelledError
    return running.result()


async def rebase_in_checkout(checkout: Path, branch: str, head: str, base: str, env) -> None:
    """`git rebase --autostash <head>` in the person's checkout, for
    `refresh_worktree_base`. Never killed: a cancel waits for git to finish
    (and a conflict's abort, which puts the autostash back). Raises
    `RebaseConflict` (aborted) or `AutostashClash`; a clash is raised even
    over a cancel, so its stash is recorded rather than lost."""
    await _finish(
        _rebase_in_checkout, checkout, branch, head, base, env, over_cancel=(AutostashClash,)
    )


def refuse_ignored(checkout: Path, tip: str, onto: str, base: str) -> None:
    """Refuse a rebase of `tip` onto `onto` in `checkout` that would replace
    an ignored file, which git does without a word: whatever checking `onto`
    out writes, and whatever replaying `tip`'s commits on it does. Kraft's own
    rebase, and the conflict resolver's before it is dispatched."""
    incoming = _git_lines(checkout, "diff", "--name-only", "--no-renames", tip, onto)
    incoming += _git_lines(
        checkout, "log", "--name-only", "--no-renames", "--format=", f"{onto}..{tip}"
    )
    ignored = _ignored_in_the_way(checkout, sorted(set(incoming)))
    if ignored:
        raise CheckoutRefused(
            f"rebasing {checkout} onto the newer {base} would overwrite {', '.join(ignored)}, "
            f"ignored here but tracked on {base}. Move them aside yourself, then retry the item",
            ignored,
        )


def _rebase_in_checkout(checkout: Path, branch: str, head: str, base: str, env) -> None:
    from kraft import builtins

    def run(*args: str) -> subprocess.CompletedProcess:
        # Its own session: a terminal's Ctrl-C or a service stop's group
        # kill must not cut git off part way through the person's checkout.
        return subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            env=env,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    refuse_ignored(checkout, "HEAD", head, base)
    # The person may have switched since the lock was taken: never rebase
    # their branch. `updateRefs` off: no other branch of theirs moves along.
    if not _on(checkout, branch):
        raise _left(checkout, branch)
    stash, since = _stash(checkout), int(time.time())
    done = run("-c", "rebase.updateRefs=false", "rebase", "--autostash", head)
    if done.returncode != 0:
        onto = _rebase_file(checkout, "onto")
        if onto is not None and onto != head:
            raise CheckoutRefused(
                f"a rebase Kraft did not start is under way in {checkout}: finish or abort it "
                "yourself, then retry the item"
            )
        if onto is None:  # never started: git put the autostash back, or kept it
            _raise_clash(checkout, base, stash, since)
        builtins._refuse_unstarted_rebase(checkout, done)
        detail = "\n".join(filter(None, [done.stdout.strip(), done.stderr.strip()]))
        unmerged = _git_lines(checkout, "diff", "--name-only", "--diff-filter=U")
        # The abort puts the person's autostash back; nothing else is cleaned up.
        aborted = run("rebase", "--abort")
        if aborted.returncode != 0:
            said = (aborted.stderr.strip() or aborted.stdout.strip() or "no output").splitlines()
            detail += (
                f"\n`git rebase --abort` failed ({said[0]}); {checkout} was left mid-rebase "
                "for you to finish or abort"
            )
        else:
            _raise_clash(checkout, base, stash, since, conflict=detail)
        raise builtins.RebaseConflict(
            f"git rebase failed for {checkout}: {detail}", tuple(unmerged)
        )
    # git exits 0 when putting the autostash back clashed, or failed: never trust it.
    _raise_clash(checkout, base, stash, since)


def _rebase_file(checkout: Path, name: str) -> str | None:
    """A rebase under way in `checkout`'s record `name` (`onto`, the commit
    it is onto; `head-name`, the branch it rebases), or None."""
    gitdir = git_read(checkout, "rev-parse", "--absolute-git-dir")
    for state in ("rebase-merge", "rebase-apply") if gitdir else ():
        with contextlib.suppress(OSError):
            return Path(gitdir, state, name).read_text().strip()
    return None


def _stash(checkout: Path) -> str | None:
    return git_read(
        checkout, "rev-parse", "--verify", "--quiet", "refs/stash", expected_failure=True
    )


def _autostash(
    checkout: Path, before: str | None, since: int, held: set[str] | None = None
) -> str | None:
    """The autostash this rebase left in the stash: the newest entry since
    `before` (and since `since`, the rebase's start) whose reflog subject is
    git's own `autostash`. The stash is shared by every worktree of the
    repository: another's entry, pushed meanwhile, is never it. `held`, when
    no `before` is known: the commits this checkout's HEAD held, one of which
    an autostash made here is on."""
    if _stash(checkout) is None:
        return None
    for line in _git_lines(
        checkout, "log", "-g", "--date=unix", "--format=%H %gd %gs", "refs/stash"
    ):
        sha, selector, subject = line.split(" ", 2)
        if sha == before or int(selector.rsplit("{", 1)[1].rstrip("}")) < since:
            return None
        if subject == "autostash" and (
            held is None or git_read(checkout, "rev-parse", f"{sha}^1") in held
        ):
            return sha
    return None


def _raise_clash(
    checkout: Path,
    base: str,
    before: str | None,
    since: int,
    *,
    conflict: str | None = None,
    task: str | None = None,
    held: set[str] | None = None,
) -> None:
    """The autostash git could not put back: a clash leaves unmerged paths,
    but a put-back git refused outright (a hook or an editor wrote one of
    those files meanwhile) leaves none, and only the autostash's own stash
    entry says the person's edits are not in the checkout. `conflict`: the
    rebase was undone after one, which the person still needs to hear of.
    `task`: the rebase was that agent task's (the conflict resolver's), not
    Kraft's own onto `base`, and only its own autostash (`held`) says so:
    unmerged paths alone may be anyone's, and the next take refuses them."""
    unmerged = _git_lines(checkout, "diff", "--name-only", "--diff-filter=U")
    stash = _autostash(checkout, before, since, held)
    if stash is None and (task or not unmerged):
        return
    kept = f"as {stash}" if stash else "(`git stash list`)"
    undone = f" hit a conflict and was undone (git said: {conflict}), and" if conflict else ""
    onto = f"what {task} rebased onto" if task else f"the newer {base}"
    rebase = (
        f"the rebase {task} ran in {checkout}"
        if task
        else f"Kraft's rebase of {checkout} onto the newer {base}"
    )
    message = (
        f"your edits to {', '.join(unmerged)} clash with {onto}; your original "
        f"edits are safe in the stash {kept}; resolve the file, then retry the item"
        if unmerged
        else f"{rebase}{undone} could not put your "
        f"edits back (something wrote those files meanwhile); they are safe in the stash "
        f"{kept}: apply it yourself (`git stash apply {stash}`), then retry the item"
    )
    raise AutostashClash(message, unmerged, stash)


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


def _left(checkout: Path, branch: str) -> CheckoutRefused:
    return CheckoutRefused(
        f"{checkout} left {branch} before Kraft could rebase it, and Kraft does not "
        "rebase another branch: retry the item"
    )


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
            "and does not go on without it: fix that, then retry the item"
        )
    return [p for p in done.stdout.split("\0") if p]


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


def _gone(folder: Path, branch: str) -> CheckoutRefused:
    return CheckoutRefused(
        f"the branch {branch} is gone from {folder}, and Kraft does not make it again: "
        "restore it (`git reflog` may still have its commits) and retry, or abandon the item"
    )


def _settled(folder: Path) -> None:
    """Refuse a checkout with the person's own git operation under way, or
    paths they have left unmerged: Kraft never finishes, aborts or works
    over either."""
    if _operation(folder):
        raise _under_way(folder)
    unmerged = _git_lines(folder, "diff", "--name-only", "--diff-filter=U")
    if unmerged:
        raise CheckoutRefused(
            f"{folder} has unmerged paths: {', '.join(unmerged)}; resolve them yourself, "
            "then retry the item",
            unmerged,
        )


def _operation(folder: Path) -> bool:
    try:
        git.assert_no_operation(folder)
    except git.UnsafeWorktree:
        return True
    return False


def _under_way(folder: Path, when: str = "") -> CheckoutRefused:
    held = (
        "; your uncommitted edits are held by its autostash and come back when it "
        "finishes or is aborted"
        if _rebase_file(folder, "autostash") is not None
        else ""
    )
    return CheckoutRefused(
        f"{folder} has a git operation in progress (a rebase, merge, cherry-pick, "
        f"revert or bisect){when}: finish or abort it yourself, then retry the item{held}"
    )


def _off(folder: Path, branch: str) -> CheckoutRefused:
    """Why git would not act on `branch` in `folder`: a rebase of the
    person's detaches HEAD, and is theirs to finish, not a switch away."""
    gone = not _on(folder, branch) and not _operation(folder)
    return _left(folder, branch) if gone else _under_way(folder)


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


def _on(folder: Path, branch: str) -> bool:
    head = git_read(folder, "symbolic-ref", "--quiet", "HEAD", expected_failure=True)
    return head == f"refs/heads/{branch}"


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
    # On a case-insensitive filesystem `Gen.txt` on the branch is `gen.txt` here.
    insensitive = (
        git_read(folder, "config", "--bool", "core.ignorecase", expected_failure=True) == "true"
    )
    fold = str.casefold if insensitive else str
    hits = []
    for c in changed:
        blocker = _first_on_disk(folder, c)
        for e in ignored if blocker else []:
            if fold(blocker) == fold(e):
                hits.append(e)  # the person's own spelling
            elif fold(blocker).startswith(fold(e) + "/"):
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
