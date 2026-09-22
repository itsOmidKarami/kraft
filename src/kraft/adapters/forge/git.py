"""The git side of the forge: what runs in the worktree itself, independent of
which CLI talks to the remote.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Collection
from pathlib import Path

from kraft.adapters.forge.models import ForgeError
from kraft.config import base_ignore_args, git_read
from kraft.worker import sandbox

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


#: Roots Kraft itself writes into and never means to commit. `.engineering/`
#: is session notes and gate artifacts (`agent.py:artifact_path`); `docs/
#: superpowers/` is the legacy convention the same content used to live under
#: (CLAUDE.md) -- both gitignored on `main`, both still landing in a spec/plan
#: attachment a stale worktree copies in (Kraft-vu26).
_KRAFT_ROOTS = (".engineering", "docs/superpowers")


async def _kraft_written_paths(repo: Path, base: str) -> list[str]:
    """Untracked paths under `_KRAFT_ROOTS` -- Kraft's own artifacts and
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
                *_KRAFT_ROOTS,
            ],
        )
    return [line[3:] for line in raw.splitlines() if line.startswith("??")]


async def work_product_pathspec(repo: Path, base: str) -> list[str]:
    """`.`, plus an exclusion for every path Kraft itself wrote into
    `_KRAFT_ROOTS` -- session summaries, spec/plan/chain_review/review_brief,
    and a spec/plan attachment copied in under `docs/superpowers/`. None of
    it is the agent's work product, so neither the clean check nor the
    straggler sweep may treat it as such: it is ingested straight into the
    index at gate approval (`Indexer.ingest_gate_artifact`), and never
    lands in the connected repo's git history at all.

    Computed per call rather than a fixed list: which paths are Kraft's own
    depends on what this repo already tracked before Kraft touched it
    (`_kraft_written_paths`), which no static pathspec can know.

    Individual paths rather than a `.gitignore` line or a blanket
    `:(exclude).engineering`: this repo ignores both `_KRAFT_ROOTS` for
    exactly this reason, but a repo Kraft was pointed at five minutes ago does
    not, and Kraft must not put its own bookkeeping into that repo's first
    merge request — or refuse to open one over it (Kraft-z8gj, widened: the
    same argument that carved out session summaries applies to
    spec/plan/chain_review/review_brief once none of them are committed
    either) — without also assuming every path under a Kraft root is ours.
    """
    return [".", *(f":(exclude){p}" for p in await _kraft_written_paths(repo, base))]


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
    repo: Path, *, base: str, message: str, mounts: Collection[str] = ()
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
        await run_git(repo, ["git", *ignore_args, "add", "-A", "--", *pathspec])
        try:
            await run_git(repo, ["git", "commit", "-m", message])
        except ForgeError:
            # A commit hook that reformats what it is given exits non-zero with the
            # files rewritten under it. Re-adding takes its edits; --no-verify then
            # refuses to let a second opinion cost us the work, which is the whole
            # point of this function. A hook that fails for any other reason loses
            # nothing either -- the commit is what keeps the work reachable.
            await run_git(repo, ["git", *ignore_args, "add", "-A", "--", *pathspec])
            await run_git(repo, ["git", "commit", "--no-verify", "-m", message])
    return True


async def push(repo: Path, branch: str) -> None:
    """`git push -u origin <branch>`, safe to call after a rebase.

    A plain push can only fast-forward. Any rebase -- a human resolving a
    real conflict against a moved `main`, or `mr_rebase`'s own auto-refresh
    -- moves the branch off of what origin last saw, and an ordinary push
    then dies `! [rejected] ... (non-fast-forward)` with no recourse but a
    human running a force push by hand: Kraft tells an escalation session
    not to push (Kraft owns the push), but Kraft's own push then structurally
    cannot publish the very rebase it asked for (Kraft-z6i8).

    `--force-with-lease=<branch>:<remote-sha>` against the remote-tracking
    ref this worktree last observed publishes the rewritten branch while
    still refusing if someone else moved the remote branch in between -- a
    genuine concurrent-writer race stays an error instead of being silently
    clobbered. The lease value is read locally, not re-fetched:
    `refs/remotes/origin/<branch>` reflects whatever this worktree's own last
    push or fetch saw, which is exactly the "last observed" state the lease
    is meant to protect -- fetching right before the push would instead adopt
    someone else's concurrent write as the expected value and defeat the
    check entirely.

    No local remote-tracking ref at all -- the branch has never been pushed
    from here -- needs no lease: origin has nothing yet for a lease to
    protect, and a plain push already does the right thing.

    Run with `sandbox.unhardened_git_env()`, not the inherited, pinned
    process env: by push time the commit is already made, so the pinned
    `core.hooksPath=/dev/null` no longer stops a worker from planting
    anything -- it only stops a real pre-push hook a human installed, like
    git-lfs's, from uploading the objects this push's pointers reference
    (Kraft-rki).
    """
    remote_sha = git_read(
        repo,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/remotes/origin/{branch}",
        expected_failure=True,
    )
    args = ["git", "push"]
    if remote_sha:
        args.append(f"--force-with-lease={branch}:{remote_sha}")
    args += ["-u", "origin", branch]
    await run_git(repo, args, env=sandbox.unhardened_git_env())


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
