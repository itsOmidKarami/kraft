"""Rebasing an in-place item's branch: in the checkout with `--autostash`
when it is on the branch, else in a throwaway worktree; and the autostash
git could not put back."""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time
from pathlib import Path

from kraft import store
from kraft.adapters.forge import git
from kraft.config import git_read
from kraft.inplace.base import (
    AutostashClash,
    CheckoutRefused,
    _finish,
    _git_lines,
    _gone,
    _held,
    _ignored_in_the_way,
    _left,
    _off,
    _on,
    _rebase_file,
    _ref,
    _settled,
    logger,
)


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
