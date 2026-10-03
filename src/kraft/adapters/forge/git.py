"""The git side of the forge: what runs in the worktree itself, independent of
which CLI talks to the remote.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Collection, Mapping
from pathlib import Path

from kraft.adapters.forge.models import ForgeError
from kraft.config import KRAFT_ROOTS, base_ignore_args, git_read
from kraft.worker import sandbox


class UnsafeWorktree(ForgeError):
    """A worktree Kraft will not act on (Kraft-xngty): an operation in progress
    or a HEAD off the item's branch, either of which a worker can plant. Never
    a conflict, which an agent is sent to resolve: a person looks first."""


#: What a git operation in progress leaves in a worktree's own gitdir. A worker
#: can write any of it, naming any branch or commit: `git rebase --abort` over
#: a planted `rebase-merge/` resets the branch it names, and a checkout or a
#: commit over a planted `MERGE_AUTOSTASH` stores it in the repository's shared
#: `refs/stash`, for the operator's next `git stash pop` (Kraft-xngty).
OPERATION_STATE = (
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "MERGE_AUTOSTASH",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "sequencer",
)


def assert_no_operation(worktree: Path, *, allow: Collection[str] = ()) -> None:
    """Raise if `worktree`'s gitdir holds any of `OPERATION_STATE` but `allow`,
    found by `lexists` so a planted symlink counts. Nothing to check when git
    cannot name the gitdir: no git that would act on it can run either."""
    gitdir = git_read(worktree, "rev-parse", "--absolute-git-dir")
    if gitdir is None:
        return
    for name in OPERATION_STATE:
        planted = Path(gitdir, name)
        if name not in allow and os.path.lexists(planted):
            # Never "abort it": aborting a planted rebase is the attack. Nor
            # "Kraft did not start it": Kraft's own timed-out abort can leave
            # a `rebase-merge/` behind too.
            raise UnsafeWorktree(
                f"{worktree} has a {name} in progress: inspect it, then delete {planted} "
                "(do not run git rebase/merge --abort or --continue) and retry"
            )


def assert_on_branch(worktree: Path, branch: str) -> None:
    """Raise unless `worktree` has no operation in progress and its HEAD is
    `refs/heads/<branch>` (Kraft-xngty). A worktree's HEAD sits in a gitdir
    its worker can write; pointed at one of the operator's branches, Kraft's
    next commit, rebase or push of HEAD would move that branch instead of the
    item's."""
    assert_no_operation(worktree)
    if git_read(worktree, "symbolic-ref", "--quiet", "HEAD") != f"refs/heads/{branch}":
        raise UnsafeWorktree(f"{worktree} is not on {branch}; check it out by hand, then retry")


#: Per-call cap, set from `policy.forge_cli_timeout_s` at startup. `subprocess.run`
#: with no timeout blocks its thread forever on a stalled `gh`, and no deadline
#: outside that thread reaches into it.
CLI_TIMEOUT_S = 120.0


async def run_git(
    repo: Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    """One forge CLI call.

    FileNotFoundError becomes ForgeError so a binary that is missing, or a
    backend name that was never installed, reads as the configuration problem it
    is rather than as a traceback from three frames up.

    `env` defaults to `None`, meaning inherit the process env unchanged --
    which carries `sandbox.harden_host_git_env`'s pinned `core.hooksPath`.
    `push` is the one caller that overrides it.
    """
    if timeout is None:
        timeout = CLI_TIMEOUT_S
    try:
        # `to_thread` can't be cancelled, so the kill has to be subprocess.run's
        # own. Every call here is a read or idempotent, so abandoning one is safe.
        done = await asyncio.to_thread(
            subprocess.run,
            args,
            cwd=repo,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ForgeError(f"{' '.join(args)} timed out after {timeout:g}s") from exc
    except FileNotFoundError as exc:
        raise ForgeError(f"{args[0]} is not installed or not on PATH") from exc
    if done.returncode != 0:
        detail = done.stderr.strip() or done.stdout.strip()
        raise ForgeError(f"{' '.join(args)} failed: {detail}")
    return done.stdout


async def _kraft_written_paths(repo: Path, base: str) -> list[str]:
    """Untracked paths under `KRAFT_ROOTS` -- Kraft's own artifacts and
    session notes, which hooks write straight to disk and never `git add`.

    Deliberately not a static pathspec: a path this repo already tracked
    under one of these roots before this worktree existed shows as modified
    ('M'), not untracked ('??'), so it is never in this list. An edit to that
    file is the repo's own content and real work product, not Kraft's
    bookkeeping -- excluding it outright, the way a blanket
    `:(exclude).engineering` pathspec used to, silently dropped it from every
    merge request (caught in review: a worker's edit to a pre-existing,
    already-committed `.engineering/specs/x.md` would otherwise vanish).

    `base_ignore_args` rides along so a root the base branch ignores but this
    worktree's own stale `.gitignore` does not yet is treated the same as one
    it always knew about, instead of showing up here as merely untracked.
    """
    with base_ignore_args(repo, base) as ignore_args:
        raw = await run_git(
            repo,
            [
                "git",
                *ignore_args,
                "status",
                "--porcelain",
                sandbox.SUBMODULES_UNENTERED,
                "--",
                *KRAFT_ROOTS,
            ],
        )
    return [line[3:] for line in raw.splitlines() if line.startswith("??")]


#: Lockfiles a package manager writes by itself where a repo commits none:
#: `uv sync` in a pyproject with no `uv.lock`, as an entry connected before
#: the probe stopped proposing it for one still runs.
WRITTEN_LOCKFILES = frozenset({"uv.lock"})

#: In the worktree's own git dir (`git rev-parse --absolute-git-dir`): the
#: untracked `WRITTEN_LOCKFILES` its setup command wrote, one per line. Only
#: these are left out of a commit; a lockfile the agent made is its work.
SETUP_WROTE = "kraft-setup-wrote"


def is_environment(path: Path) -> bool:
    """Whether untracked `path` is a directory a setup installs into: a
    virtualenv (it holds `pyvenv.cfg`) or a `node_modules`."""
    return path.name == "node_modules" or (path / "pyvenv.cfg").is_file()


async def untracked_lockfiles(repo: Path) -> set[str]:
    """The untracked `WRITTEN_LOCKFILES` in `repo`, by path; none where
    `repo` is not a git checkout."""
    try:
        raw = await run_git(
            repo,
            ["git", "status", "--porcelain", "-z", "--untracked-files=all", "--", "."],
        )
    except ForgeError:
        return set()
    return {
        entry[3:]
        for entry in raw.split("\0")
        if entry.startswith("?? ") and Path(entry[3:]).name in WRITTEN_LOCKFILES
    }


async def _setup_wrote_path(repo: Path) -> Path:
    return Path((await run_git(repo, ["git", "rev-parse", "--absolute-git-dir"])).strip()) / (
        SETUP_WROTE
    )


async def setup_wrote(repo: Path) -> set[str]:
    """What `record_setup_writes` recorded for this worktree."""
    try:
        return set((await _setup_wrote_path(repo)).read_text().split())
    except (OSError, ForgeError):
        return set()


async def record_setup_writes(repo: Path, before: set[str]) -> None:
    """Record the lockfiles a setup command just wrote: untracked now and not
    before it ran, plus those it wrote before that are still untracked. One
    that was there before this setup and not recorded is the agent's."""
    after = await untracked_lockfiles(repo)
    wrote = (after - before) | (await setup_wrote(repo) & after)
    try:
        (await _setup_wrote_path(repo)).write_text("".join(f"{p}\n" for p in sorted(wrote)))
    except (OSError, ForgeError):
        # Not a git checkout: nothing commits from it, so nothing to keep out.
        return


async def environment_paths(repo: Path, base: str) -> list[str]:
    """Untracked paths that are an install, not work: a virtualenv or
    `node_modules` (`is_environment`) -- a setup's `.venv/` in a repo that
    never ignored it is a thousand files -- and a lockfile this worktree's
    setup command wrote (`record_setup_writes`), which would otherwise land
    in every work item's merge request. No merge request wants either. Only
    a path git lists as untracked: one the repo tracks is the repo's own,
    edits and all. Asked for as `normal`, since `status.showUntrackedFiles=
    all` in a user's or the repo's config would list a directory's files one
    by one instead."""
    wrote = await setup_wrote(repo)
    with base_ignore_args(repo, base) as ignore_args:
        raw = await run_git(
            repo,
            [
                "git",
                *ignore_args,
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=normal",
                sandbox.SUBMODULES_UNENTERED,
            ],
        )
    return [
        entry[3:].rstrip("/")
        for entry in raw.split("\0")
        if entry.startswith("?? ")
        and ((entry.endswith("/") and is_environment(repo / entry[3:])) or entry[3:] in wrote)
    ]


async def work_product_pathspec(repo: Path, base: str) -> list[str]:
    """`.`, plus an exclusion for every path Kraft itself wrote into
    `KRAFT_ROOTS` -- session summaries, spec/plan/chain_review/review_brief,
    and a spec/plan attachment copied in under `docs/superpowers/`. None of
    it is the agent's work product, so neither the clean check nor the
    straggler sweep may treat it as such: it is ingested straight into the
    index at gate approval (`Indexer.ingest_gate_artifact`), and never
    lands in the connected repo's git history at all.

    Computed per call rather than a fixed list: which paths are Kraft's own
    depends on what this repo already tracked before Kraft touched it
    (`_kraft_written_paths`), which no static pathspec can know.

    Individual paths rather than a `.gitignore` line or a blanket
    `:(exclude).engineering`: this repo ignores both `KRAFT_ROOTS` for
    exactly this reason, but a repo Kraft was pointed at five minutes ago does
    not, and Kraft must not put its own bookkeeping into that repo's first
    merge request — or refuse to open one over it (Kraft-z8gj, widened: the
    same argument that carved out session summaries applies to
    spec/plan/chain_review/review_brief once none of them are committed
    either) — without also assuming every path under a Kraft root is ours.

    An untracked virtualenv or `node_modules`, or a lockfile the setup
    command wrote (`environment_paths`), is left out too: what a setup
    installed is never work product, whether or not
    the repo thought to ignore it.
    """
    return [
        ".",
        *(f":(exclude){p}" for p in await _kraft_written_paths(repo, base)),
        *(f":(exclude,literal){p}" for p in await environment_paths(repo, base)),
    ]


async def assert_clean(repo: Path, base: str) -> None:
    """Refuse to open a merge request over a worktree with uncommitted work.

    Untracked files are included on purpose: a source or test file the agent
    never `git add`ed is what went missing on work item 5163dd1b. Ignored files
    are excluded by git itself, so `.pytest_cache/` does not trip it, and
    `work_product_pathspec` drops Kraft's own session notes and artifacts in
    a repo that has not ignored them.

    `SUBMODULES_UNENTERED` (`--ignore-submodules=dirty`) deliberately
    overrides the repo's own `submodule.<path>.ignore` config. A human
    setting `ignore = all` on a workspace with several submodules to stop
    pointer churn in every `git status` is reasonable; it is not permission
    for Kraft to open a merge request over a submodule holding commits that
    request will not carry (Kraft-qlsf — this is what let work item
    9d0ab38ff3c9439b90506df0f6966660 push a submodule commit nowhere while
    every guard reported clean).

    `dirty`, not `none`: `none` also runs `git status` *inside* each
    submodule to look for uncommitted edits, and that child git reads the
    submodule's own config -- which, for a repository a sandboxed worker
    nested in its worktree and committed a gitlink to, is the worker's,
    filters and all (Kraft-nx4id). A declared member's uncommitted edits are
    still caught: publication runs this same check in each member.
    """
    pathspec = await work_product_pathspec(repo, base)
    with base_ignore_args(repo, base) as ignore_args:
        raw = await run_git(
            repo,
            [
                "git",
                *ignore_args,
                "status",
                "--porcelain",
                sandbox.SUBMODULES_UNENTERED,
                "--",
                *pathspec,
            ],
        )
    dirty = [line[3:] for line in raw.splitlines() if line.strip()]
    if dirty:
        shown = ", ".join(dirty[:5])
        more = f" (+{len(dirty) - 5} more)" if len(dirty) > 5 else ""
        raise ForgeError(
            f"{len(dirty)} uncommitted path(s) in the worktree, which would not "
            f"reach the merge request: {shown}{more}"
        )


async def commit_stragglers(
    repo: Path,
    *,
    branch: str,
    base: str,
    message: str,
    mounts: Collection[str] = (),
    identity: Mapping[str, str] | None = None,
) -> bool:
    """Commit whatever an agent left behind in the worktree. True if it did.

    A worker is told to commit everything it changes before it exits, and one
    that does not leaves work `assert_clean` refuses two nodes later and a
    worktree prune destroys — with `verify` passing in between, because the
    files are on disk (Kraft-7fip). Kraft owns the worktree and the branch is
    throwaway, so there is nothing to protect by refusing: commit it, and let
    the merge request carry it.

    Ignored files stay out, the same exclusion `assert_clean` relies on, so a
    `.pytest_cache/` left behind is not mistaken for work — and
    `work_product_pathspec` keeps Kraft's own session notes and artifacts out
    of the merge request even in a repo that has never heard of them.

    So does every nested repository but the item's declared `mounts`
    (Kraft-nx4id): `git add` runs `git status` inside each gitlink it
    matches, whatever `--ignore-submodules` says, and that child git runs
    whatever filter the nested repository's config names. A pointer the
    agent moved in a submodule nobody declared is left uncommitted for
    `assert_clean` to name, rather than swept into the merge request.
    """
    # The commit is Kraft's, as `identity` (the root's): a member's connected
    # repository may have none, and Kraft writes none there (Kraft-ju36l).
    env = {**os.environ, **identity} if identity else None
    nested = await asyncio.to_thread(sandbox.nested_repos, repo) or {}
    pathspec = [
        *await work_product_pathspec(repo, base),
        *(f":(exclude,literal){p}" for p in nested if p not in mounts),
    ]
    with base_ignore_args(repo, base) as ignore_args:
        status = await run_git(
            repo,
            [
                "git",
                *ignore_args,
                "status",
                "--porcelain",
                sandbox.SUBMODULES_UNENTERED,
                "--",
                *pathspec,
            ],
        )
        if not status.strip():
            return False
        assert_on_branch(repo, branch)
        await run_git(repo, ["git", *ignore_args, "add", "-A", "--", *pathspec])
        try:
            await run_git(repo, ["git", "commit", "-m", message], env=env)
        except ForgeError:
            # A commit hook that reformats what it is given exits non-zero with the
            # files rewritten under it. Re-adding takes its edits; --no-verify then
            # refuses to let a second opinion cost us the work, which is the whole
            # point of this function. A hook that fails for any other reason loses
            # nothing either -- the commit is what keeps the work reachable.
            await run_git(repo, ["git", *ignore_args, "add", "-A", "--", *pathspec])
            await run_git(repo, ["git", "commit", "--no-verify", "-m", message], env=env)
    return True


#: Where `push` records the commit it last pushed for each branch.
PUSHED_REFS = "refs/kraft/pushed"


async def push(repo: Path, branch: str) -> None:
    """`git push -u origin <branch>`, safe to call after a rebase.

    A plain push can only fast-forward. Any rebase -- a human resolving a
    real conflict against a moved `main`, or `mr_rebase`'s own auto-refresh
    -- moves the branch off of what origin last saw, and an ordinary push
    then dies `! [rejected] ... (non-fast-forward)` with no recourse but a
    human running a force push by hand: Kraft tells an escalation session
    not to push (Kraft owns the push), but Kraft's own push then structurally
    cannot publish the very rebase it asked for (Kraft-z6i8).

    What origin has for the branch is read fresh (`ls-remote`, which leaves
    the shared remote-tracking ref alone), and the push goes ahead only when
    it loses nothing: origin has no such branch, origin's tip is already in
    the local branch (a fast-forward, including a person's commits the
    worktree has since pulled), or origin's tip is the commit Kraft itself
    last pushed, recorded in `refs/kraft/pushed/<branch>` (a rebase of
    Kraft's own work). Anything else is someone else's commits, and the push
    refuses rather than overwrite them. The lease on the tip just read keeps
    a write landing between the read and the push an error too.

    The remote-tracking ref cannot stand in for Kraft's record: the worktree
    shares refs with the connected checkout, so a person's plain `git fetch`
    there moves it onto the commits they pushed to the MR branch, and a
    lease on it then overwrote them (Kraft-m7ppj). A branch Kraft pushed
    before the record existed falls back to the remote-tracking ref, as
    every branch did before.

    Run with `sandbox.unhardened_git_env()`, not the inherited, pinned
    process env: by push time the commit is already made, so the pinned
    `core.hooksPath=/dev/null` no longer stops a worker from planting
    anything -- it only stops a real pre-push hook a human installed, like
    git-lfs's, from uploading the objects this push's pointers reference
    (Kraft-rki).
    """
    env = sandbox.unhardened_git_env()
    pushed_ref = f"{PUSHED_REFS}/{branch}"
    head = git_read(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    # Where the push goes, which a `pushurl` or `pushInsteadOf` can make
    # somewhere other than where `origin` fetches from.
    target = git_read(repo, "remote", "get-url", "--push", "origin") or "origin"
    listed = await run_git(repo, ["git", "ls-remote", target, f"refs/heads/{branch}"], env=env)
    remote = listed.split()[0] if listed.split() else ""
    ours = next(
        (
            sha
            for ref in (pushed_ref, f"refs/remotes/origin/{branch}")
            if (
                sha := git_read(
                    repo, "rev-parse", "--verify", "--quiet", ref, expected_failure=True
                )
            )
        ),
        None,
    )
    # Empty output: every commit of `remote` is already in `head`. None: git
    # does not even have `remote`, so it certainly is not.
    if (
        remote
        and remote != ours
        and git_read(repo, "rev-list", "-n1", remote, f"^{head}", expected_failure=True) != ""
    ):
        raise ForgeError(
            f"origin/{branch} is at {remote[:12]}, which Kraft did not push and this "
            "worktree does not have -- likely a person's commits on the merge request. "
            f"Kraft will not overwrite them: pull them into the item's worktree (git pull "
            f"--rebase origin {branch}), then retry"
        )
    if not remote and ours:
        # Merged, or deleted with its merge request: recreating it would
        # reopen work someone closed (Kraft-7itv).
        raise ForgeError(
            f"origin no longer has {branch}, which Kraft pushed; it was merged or "
            "deleted. To publish this item's branch again, run `git update-ref -d "
            f"{pushed_ref}` and `git update-ref -d refs/remotes/origin/{branch}` in "
            "the item's worktree, then retry"
        )
    args = ["git", "push"]
    if remote:
        args.append(f"--force-with-lease={branch}:{remote}")
    args += ["-u", "origin", branch]
    await run_git(repo, args, env=env)
    await run_git(repo, ["git", "update-ref", pushed_ref, head])


async def _assert_pushed(repo: Path, branch: str) -> None:
    """Refuse to merge a head the remote has never seen.

    If `origin/<branch>` does not exist, `run_git` raises ForgeError of its own
    and the node fails loudly — the right answer for a merge with no pushed
    branch.
    """
    raw = await run_git(repo, ["git", "rev-list", "--count", f"origin/{branch}..HEAD"])
    ahead = int(raw.strip() or 0)
    if ahead:
        raise ForgeError(
            f"local branch is ahead of origin/{branch} by {ahead} commit(s); "
            "merging would merge a head the forge has never seen"
        )


async def _head_sha(repo: Path) -> str:
    """The worktree's HEAD, or empty when git will not say.

    Empty means "do not compare": a repo git cannot read is no reason to
    report a pipeline that was read perfectly well as missing.
    """
    try:
        return (await run_git(repo, ["git", "rev-parse", "HEAD"])).strip()
    except ForgeError:
        return ""


async def commits_on(repo: Path, branch: str, base: str) -> tuple[str, ...]:
    """Subjects of the commits this branch adds to `base`, the branch its
    merge request targets, newest last.

    `origin/<base>` and not the local one: a Kraft worktree is cut from
    whatever the local checkout happened to be at, which may be behind.
    Returns empty rather than raising — a description is not worth failing a
    node over.
    """
    try:
        raw = await run_git(
            repo, ["git", "log", "--reverse", "--format=%s", f"origin/{base}..{branch}"]
        )
    except ForgeError:
        return ()
    return tuple(line for line in raw.splitlines() if line.strip())


async def commits_ahead(repo: Path, branch: str, base: str) -> int | None:
    """How many commits `branch` adds to `origin/<base>`, as `commits_on`
    reads them; None when git cannot say (no origin), which is no evidence
    the branch is empty."""
    try:
        raw = await run_git(repo, ["git", "rev-list", "--count", f"origin/{base}..{branch}"])
    except ForgeError:
        return None
    return int(raw.strip() or 0)


async def source_changed(repo: Path, branch: str, *, base: str, exclude: set[str]) -> bool:
    """Whether `branch` changes any path of `repo` outside `exclude` -- a
    workspace root's member mount paths, so a commit that only moves a
    member's pointer is not a source change. Against `origin/<base>`, as
    `commits_on` reads; False when git cannot say (no origin), like it."""
    try:
        raw = await run_git(repo, ["git", "diff", "--name-only", f"origin/{base}...{branch}"])
    except ForgeError:
        return False
    return any(p and p not in exclude for p in raw.splitlines())


async def _assert_submodules_covered(repo: Path, covered: set[Path]) -> None:
    """Refuse to publish a workspace item while an initialized submodule
    holds commits no `work_item_repos` row will carry anywhere.

    Membership is typed: the item's frozen target selects the members it may
    change, and only those get a branch and a merge request. A submodule the
    agent changed without it being selected must stop the chain rather than
    silently drop the change, exactly as it did on work item
    9d0ab38ff3c9439b90506df0f6966660 -- select it when filing the item.
    """
    raw = await run_git(repo, ["git", "submodule", "status"])
    for line in raw.splitlines():
        if not line or line[0] != "+":
            continue
        parts = line[1:].split()
        if len(parts) < 2:
            continue
        path = (repo / parts[1]).resolve()
        if path not in covered:
            raise ForgeError(
                f"submodule {parts[1]} has commits not covered by any declared or "
                "discovered repo — it will not reach a merge request"
            )


async def default_branch(repo: Path) -> str:
    """origin's default branch, or `main` when the forge doesn't say.

    Not the answer to "which branch does this item's work target": that is
    `builtins.base_branch`, which reads this only for an item that named no
    branch, and for a workspace member."""
    try:
        raw = await run_git(repo, ["git", "symbolic-ref", "refs/remotes/origin/HEAD"])
        return raw.strip().rsplit("/", 1)[-1] or "main"
    except ForgeError:
        return "main"
