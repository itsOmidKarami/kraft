"""The git side of the forge: what runs in the worktree itself, independent of
which CLI talks to the remote.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Collection, Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from kraft import forges
from kraft.adapters.forge import git_auth
from kraft.adapters.forge.models import ForeignCommits, ForgeError
from kraft.config import KRAFT_ROOTS, base_ignore_args, git_read
from kraft.forges import ForgeAccount
from kraft.remote import lfs
from kraft.worker import sandbox

logger = logging.getLogger(__name__)


class UnsafeWorktree(ForgeError):
    """A worktree Kraft will not act on (Kraft-xngty): an operation in progress
    or a HEAD off the item's branch, either of which a worker can plant. Never
    a conflict, which an agent is sent to resolve: a person looks first."""


class CliUnavailable(ForgeError):
    """A forge CLI or git that never started: not installed, not executable,
    an argument list the OS refused, or a working directory that is gone.
    Nothing a commit could fix, so the forge task stops for a person."""


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


def assert_no_operation(
    worktree: Path, *, allow: Collection[str] = (), branch: str | None = None
) -> None:
    """Raise if `worktree`'s gitdir holds any of `OPERATION_STATE` but `allow`,
    found by `lexists` so a planted symlink counts. Nothing to check when git
    cannot name the gitdir: no git that would act on it can run either.
    `branch`, the item's, is named in a rebase's way back."""
    gitdir = git_read(worktree, "rev-parse", "--absolute-git-dir")
    if gitdir is None:
        return
    for name in OPERATION_STATE:
        planted = Path(gitdir, name)
        if name not in allow and os.path.lexists(planted):
            # Never "abort it": aborting a planted rebase is the attack. Nor
            # "Kraft did not start it": Kraft's own timed-out abort, or a
            # server killed mid-rebase, can leave a `rebase-merge/` behind too,
            # with HEAD detached at the base: the branch still has the work.
            back = (
                f"; if HEAD is left detached, `git checkout -f {branch}`, which still "
                "holds the item's commits"
                if branch is not None and name.startswith("rebase-")
                else ""
            )
            raise UnsafeWorktree(
                f"{worktree} has a {name} in progress: inspect it, then delete {planted} "
                f"(do not run git rebase/merge --abort or --continue){back}, and retry"
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


#: Per-call cap, set from `policy.forge_cli_timeout_s` at startup and on reload. `subprocess.run`
#: with no timeout blocks its thread forever on a stalled `gh`, and no deadline
#: outside that thread reaches into it.
CLI_TIMEOUT_S = 120.0


def _shown(args: list[str]) -> str:
    """The command as an error names it. An argument over 200 characters is
    clipped: a merge request body can be 100 KB, and the error lands in the
    node's log and events."""
    return " ".join(a if len(a) <= 200 else f"{a[:200]}… ({len(a)} chars)" for a in args)


async def run_cli(
    repo: Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stdin: str | None = None,
    errors: str = "strict",
) -> subprocess.CompletedProcess[str]:
    """One forge CLI call, returned whatever its exit code.

    A CLI that never started (FileNotFoundError, E2BIG, a binary that is not
    executable, a working directory that is gone) becomes `CliUnavailable`, so
    it reads as the configuration problem it is rather than as a traceback from
    three frames up. A timeout is a `ForgeError`: no output can exist.

    `env` defaults to `None`, meaning inherit the process env unchanged --
    which carries `sandbox.harden_host_git_env`'s pinned `core.hooksPath`.
    `push` and the reviewed-head fetch pass a hardened copy of their own.
    """
    if timeout is None:
        timeout = CLI_TIMEOUT_S
    try:
        # `to_thread` can't be cancelled, so the kill has to be subprocess.run's
        # own. Every call here is a read or idempotent, so abandoning one is safe.
        return await asyncio.to_thread(
            subprocess.run,
            args,
            cwd=repo,
            input=stdin,
            capture_output=True,
            text=True,
            errors=errors,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ForgeError(f"{_shown(args)} timed out after {timeout:g}s") from exc
    except FileNotFoundError as exc:
        # The same error for a missing working directory as for a missing binary.
        if not Path(repo).is_dir():
            raise CliUnavailable(f"{args[0]} cannot run in {repo}: it does not exist") from exc
        raise CliUnavailable(f"{args[0]} is not installed or not on PATH") from exc
    except OSError as exc:
        # E2BIG and the like: the CLI never started.
        raise CliUnavailable(f"{args[0]} could not start: {exc.strerror or exc}") from exc


async def run_git(
    repo: Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stdin: str | None = None,
) -> str:
    """`run_cli`, raising a `ForgeError` on a non-zero exit."""
    done = await run_cli(repo, args, env=env, timeout=timeout, stdin=stdin)
    if done.returncode != 0:
        detail = done.stderr.strip() or done.stdout.strip()
        raise ForgeError(f"{_shown(args)} failed: {detail}")
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


#: Lockfiles a package manager writes by itself where a repo commits none,
#: each with the command that makes one to commit: `uv sync` in a pyproject
#: with no `uv.lock`, as an entry connected before the probe stopped
#: proposing it for one still runs, and `cargo fetch` in a crate with no
#: `Cargo.lock`, which the probe proposes.
LOCK_COMMANDS = {"uv.lock": "uv lock", "Cargo.lock": "cargo generate-lockfile"}
WRITTEN_LOCKFILES = frozenset(LOCK_COMMANDS)

#: In the worktree's own git dir (`git rev-parse --absolute-git-dir`): a JSON
#: object naming the untracked `WRITTEN_LOCKFILES` its setup command wrote,
#: each with the sha256 of what the setup left in it. Only these, as the
#: setup left them, are left out of a commit; a lockfile the agent made, or
#: an edit the agent made to the setup's, is its work.
SETUP_WROTE = "kraft-setup-wrote"

#: What a package manager leaves at the top of a `node_modules` it installed:
#: npm 7 and later, pnpm (`.modules.yaml`, and its `.pnpm` store), Yarn 1 and
#: Yarn 2+'s node-modules linker.
_NODE_MODULES_MARKERS = (
    ".package-lock.json",
    ".modules.yaml",
    ".pnpm",
    ".yarn-integrity",
    ".yarn-state.yml",
)


def is_environment(path: Path) -> bool:
    """Whether untracked directory `path` is one a setup installs into: a
    virtualenv (`pyvenv.cfg` beside its `python`) or a `node_modules` a
    package manager made (its marker inside). A directory that only has the
    name, a bare `pyvenv.cfg`, or a `package.json` beside it is something the
    agent wrote, such as a resolver's test fixture, and is its work.
    `lexists`, not `exists`: a virtualenv's `python` is a link to an
    interpreter the host may not have, when a sandbox made it."""
    if path.name == "node_modules":
        return any(os.path.lexists(path / m) for m in _NODE_MODULES_MARKERS)
    return (path / "pyvenv.cfg").is_file() and any(
        os.path.lexists(path / p) for p in ("bin/python", "Scripts/python.exe")
    )


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


def _digest(path: Path) -> str | None:
    """The sha256 of the lockfile at `path`. A link is hashed as the name it
    points at, never followed: the record sits where the worker can read
    it, and a followed link would put a digest of any host file in it."""
    try:
        if path.is_symlink():
            return hashlib.sha256(b"symlink:" + os.fsencode(os.readlink(path))).hexdigest()
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


#: Directories a setup installs into, recorded by path (with a trailing `/`)
#: when the setup made them: bun leaves no marker inside its `node_modules`,
#: so `is_environment` cannot tell its install from an agent's fixture.
INSTALL_DIRS = frozenset({"node_modules"})


async def untracked_installs(repo: Path) -> set[str]:
    """The untracked `INSTALL_DIRS` in `repo`, as git lists them (`dir/`)."""
    try:
        raw = await run_git(
            repo,
            [
                "git",
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=normal",
                sandbox.SUBMODULES_UNENTERED,
            ],
        )
    except ForgeError:
        return set()
    return {
        entry[3:]
        for entry in raw.split("\0")
        if entry.startswith("?? ") and entry.endswith("/") and Path(entry[3:]).name in INSTALL_DIRS
    }


async def lockfile_digests(repo: Path) -> dict[str, str | None]:
    """`untracked_lockfiles`, each with the sha256 of its content now, and
    `untracked_installs`, each with None: what `_prepare` reads before a
    setup runs, for `record_setup_writes`."""
    return {
        **{p: _digest(repo / p) for p in await untracked_lockfiles(repo)},
        **dict.fromkeys(await untracked_installs(repo)),
    }


async def _setup_wrote_path(repo: Path) -> Path:
    return Path((await run_git(repo, ["git", "rev-parse", "--absolute-git-dir"])).strip()) / (
        SETUP_WROTE
    )


async def _setup_record(repo: Path) -> dict[str, str | None]:
    """The record as written, every path in it; `setup_wrote` and
    `setup_installs` read it through their filters."""
    try:
        path = await _setup_wrote_path(repo)
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except (OSError, ForgeError) as exc:
        logger.warning("ignoring the setup record of %s: %s", repo, exc)
        return {}
    try:
        text = raw.decode("utf-8")
        # Before digests (1.5.0rc14): one path per line.
        data = json.loads(text) if text.lstrip()[:1] in ("{", "[") else dict.fromkeys(text.split())
    except ValueError as exc:  # UnicodeDecodeError and JSONDecodeError included
        logger.warning("ignoring an unreadable setup record %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("ignoring a setup record that is not an object: %s", path)
        return {}
    return {p: d if isinstance(d, str) else None for p, d in data.items()}


async def setup_wrote(repo: Path) -> dict[str, str | None]:
    """What `record_setup_writes` recorded for this worktree: each lockfile's
    path, with the sha256 the setup left it at (None in a record written
    before digests were kept, which counts as unchanged).

    Read through the filter it was written with: only a `WRITTEN_LOCKFILES`
    name. The file sits in a git dir a worker can write, so any other path
    in it was never the setup's and is ignored, not left out of a commit.

    A record that cannot be read or decoded counts as empty, and says so in
    the log: never a failed task. The setup's lockfile then reads as the
    agent's, and the sweep commits it, which a person can see and undo."""
    return {
        p: d
        for p, d in (await _setup_record(repo)).items()
        if Path(p).name in WRITTEN_LOCKFILES and not p.endswith("/")
    }


async def setup_installs(repo: Path) -> set[str]:
    """The `INSTALL_DIRS` the setup made, as recorded (`dir/`). Only that
    name: any other directory in the record was never the setup's. A forged
    one only leaves the worker's own files out of its commit, and the sweep
    names it (`sweep_left_out`)."""
    return {
        p for p in await _setup_record(repo) if p.endswith("/") and Path(p).name in INSTALL_DIRS
    }


async def record_setup_writes(repo: Path, before: Mapping[str, str | None]) -> None:
    """Record the lockfiles a setup command just wrote, with what it wrote in
    them. `before` is `lockfile_digests` from before it ran. A lockfile is the
    setup's when it was not there before, or was recorded as the setup's and
    nobody has changed it since. One that was there before this setup and
    not recorded, or that changed after the last setup recorded it, is the
    agent's. An `INSTALL_DIRS` directory is the setup's the same way: made
    by this setup, or recorded by the last one. Written whole or not at all
    (a temporary file, then a rename)."""
    after = await lockfile_digests(repo)
    prior = await _setup_record(repo)
    wrote = {
        p: d
        for p, d in after.items()
        if p not in before or (p in prior and prior[p] in (None, before[p]))
    }
    try:
        target = await _setup_wrote_path(repo)
        partial = target.with_name(f"{SETUP_WROTE}.partial")
        partial.write_text(json.dumps(wrote, sort_keys=True), encoding="utf-8")
        os.replace(partial, target)
    except (OSError, ForgeError):
        # Not a git checkout: nothing commits from it, so nothing to keep out.
        return


async def setup_lockfiles(repo: Path, *, unknown: bool = True) -> set[str]:
    """The lockfiles this worktree's setup wrote that are still untracked and
    still hold what the setup left in them: the ones no commit takes. One the
    agent has since edited is its work, and is committed. One recorded with
    no digest (before they were kept) counts as unchanged, unless `unknown`
    is False."""
    wrote = await setup_wrote(repo)
    if not wrote:
        return set()
    return {
        p
        for p in await untracked_lockfiles(repo)
        if p in wrote and (wrote[p] == _digest(repo / p) or (unknown and wrote[p] is None))
    }


async def environment_paths(repo: Path, base: str) -> list[str]:
    """Untracked paths that are an install, not work: a virtualenv or
    `node_modules` (`is_environment`) -- a setup's `.venv/` in a repo that
    never ignored it is a thousand files -- and a lockfile this worktree's
    setup command wrote and nobody has changed since (`setup_lockfiles`),
    which would otherwise land in every work item's merge request. No merge
    request wants either. Only a path git lists as untracked: one the repo
    tracks is the repo's own, edits and all. Asked for as `normal`, since
    `status.showUntrackedFiles=all` in a user's or the repo's config would
    list a directory's files one by one instead."""
    wrote = await setup_lockfiles(repo)
    installs = await setup_installs(repo)
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
        and (
            (entry.endswith("/") and (is_environment(repo / entry[3:]) or entry[3:] in installs))
            or entry[3:] in wrote
        )
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


def _lfsconfig_moved(repo: Path, rev: str = "HEAD", trusted: str | None = None) -> bool:
    """Whether the branch's own work changed the `.lfsconfig` git-lfs would
    read: a commit of `rev` that is on no `origin/*` branch, nor on `trusted`
    (the base, for a repository with no remote), touched it, or, for HEAD,
    the staged or on-disk copy differs from HEAD's (git-lfs reads disk, then
    the index, then HEAD). Commits on a remote branch are the base's, a
    person's or ones already pushed, and a sandboxed worker cannot write
    `refs/remotes/` or the base: its refs land in its own store.

    shortcut: a branch that changes `.lfsconfig` and changes it back still
    counts as touching it; compare against the boundary commits if that bites.
    """

    def blob(spec: str) -> str | None:
        # `or None`: real git prints the id of anything it finds, so empty is absent.
        return (
            git_read(repo, "rev-parse", "--verify", "--quiet", spec, expected_failure=True) or None
        )

    if rev == "HEAD":
        disk = None
        if os.path.lexists(repo / ".lfsconfig"):
            # `--no-filters`: the worker's `.gitattributes` must not reshape it first.
            disk = (
                git_read(
                    repo, "hash-object", "--no-filters", "--", ".lfsconfig", expected_failure=True
                )
                or "?"
            )
        if {blob(":.lfsconfig"), disk} != {blob("HEAD:.lfsconfig")}:
            return True
    besides = [trusted] if trusted and blob(f"{trusted}^{{commit}}") else []
    touched = git_read(
        repo,
        "log",
        "--format=%H",
        "-1",
        rev,
        "--not",
        "--remotes",
        *besides,
        "--",
        ".lfsconfig",
        expected_failure=True,
    )
    return touched is None or touched != ""


def assert_lfsconfig_unmoved(
    repo: Path, step: str, *, rev: str = "HEAD", trusted: str | None = None
) -> None:
    """Raise `UnsafeWorktree` before a host git in `repo` that runs git-lfs
    (a push's `git lfs pre-push`, a checkout's smudge) when the branch's own work
    changed `.lfsconfig`: git-lfs would talk to whatever server it names from
    this machine, outside any sandbox. `step` says what Kraft will not do.
    The dangerous git never runs; whether the item stops at once or later,
    at its push, is the caller's handling of an UnsafeWorktree.
    """
    if _lfsconfig_moved(repo, rev, trusted):
        raise UnsafeWorktree(
            f"the item's work changes .lfsconfig, which tells git-lfs where this "
            f"repository's objects live, so Kraft will not {step} from your machine, "
            "outside the sandbox. Review the change: drop the commits that make it "
            "from the branch, or push the branch yourself if it is meant, and Kraft "
            "carries on"
        )


async def push(repo: Path, branch: str, *, account: tuple[str, ForgeAccount] | None = None) -> None:
    """`git push origin <branch>`, safe to call after a rebase. Never `-u`:
    the push names its branch, so an upstream buys nothing, and it would be
    written into the repository's config, in place the person's own.

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

    Kraft dying between its push and the record leaves the record one push
    behind (Kraft-mre20), so origin's tip also counts as Kraft's own when the
    local branch once pointed at it (its reflog): a person's commit pushed
    from elsewhere never was. With no reflog the push refuses, as before.

    It runs no hook, even a pre-push the person installed in the git
    directory: such a hook may read the working tree, which the worker writes,
    and the push runs on the host, outside any sandbox. pre-commit's pre-push
    runs whatever `.pre-commit-config.yaml` names. git-lfs's pre-push was the
    one Kraft needed, so the push uploads the LFS objects itself first.

    `account` is the API account the item pinned, as `(name, account)`: its token
    authenticates every remote call here (`git_auth`), and a push URL off its
    origin is refused. A CLI backend passes none, so git uses its own credential.
    """
    # git-lfs reads its server from `.lfsconfig`, which the worker writes: a
    # changed one would send this push's LFS traffic from the host, outside
    # any sandbox, to a URL the branch chose.
    assert_lfsconfig_unmoved(repo, "push it")
    auth = await git_auth.auth_for_account(repo, *account, push=True) if account else None
    try:
        await _push(repo, branch, auth)
    except ForgeError as exc:
        if auth:
            auth.stamp(exc)
        raise


async def _push(repo: Path, branch: str, auth: git_auth.GitAuth | None) -> None:
    plain = sandbox.hardened_git_env()
    env = git_auth.git_env(plain, auth)
    pushed_ref = f"{PUSHED_REFS}/{branch}"
    head = git_read(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
    # Where the push goes, which a `pushurl` or `pushInsteadOf` can make
    # somewhere other than where `origin` fetches from.
    target = git_read(repo, "remote", "get-url", "--push", "origin") or "origin"
    if auth:
        # Never dial by URL with the header: git applies a `[remote "<url>"]` section of the
        # repo's config to such a call, and none of it is pinned. `origin` reads the fetch URL.
        if target != git_read(repo, "remote", "get-url", "origin"):
            raise ForgeError(
                f"forge account {auth.name}: origin's push URL differs from its fetch URL; "
                "Kraft sends its token only through the remote's own name, so make them "
                "the same, then retry"
            )
        target = "origin"
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
        and not _was_local_tip(repo, branch, remote)
    ):
        raise ForeignCommits(
            f"origin/{branch} is at {remote[:12]}, which Kraft did not push and this "
            "worktree does not have -- likely a person's commits on the merge request. "
            f"Kraft will not overwrite them: pull them into the item's worktree (git pull "
            f"--rebase origin {branch}), then retry",
            remote=remote,
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
    await _push_lfs_objects(repo, branch, head, remote, target, env, auth)
    args = ["git", "push", "--no-verify"]
    if remote:
        args.append(f"--force-with-lease={branch}:{remote}")
    args += ["origin", branch]
    await run_git(repo, args, env=env)
    await run_git(repo, ["git", "update-ref", pushed_ref, head], env=plain)


_LFS_ENDPOINT_KEYS = (
    "lfs.url",
    "lfs.pushurl",
    "remote.origin.lfsurl",
    "remote.origin.lfspushurl",
)


def _lfs_refusal(repo: Path, name: str) -> str | None:
    """Why a header-carrying `git lfs pre-push` must not run, in words that quote no value, or
    None: another endpoint named by `lfs.url`, `remote.origin.lfsurl` or `remote.origin.lfspushurl`
    in the repo's own config, or in the `.lfsconfig` on disk (the branch cannot have moved it,
    `assert_lfsconfig_unmoved`) unless that is origin's own endpoint, the fetch URL with or
    without `.git` plus `/info/lfs`; or a custom transfer agent, which runs with the header env."""
    if git_read(
        repo,
        "config",
        "--name-only",
        "--get-regexp",
        r"^lfs\..*(customtransfer\.|standalonetransferagent$)",
        expected_failure=True,
    ):
        return (
            f"forge account {name}: git-lfs runs a custom transfer agent here "
            "(lfs.customtransfer or lfs.standalonetransferagent); Kraft sends its token only "
            "to git-lfs's own transfer, so unset it and retry"
        )
    if any(git_read(repo, "config", "--get", k, expected_failure=True) for k in _LFS_ENDPOINT_KEYS):
        return (
            f"forge account {name}: git-lfs is pointed elsewhere by lfs.url, "
            "lfs.pushurl or remote.origin.lfsurl; Kraft sends its token only to origin's own "
            "LFS endpoint, so unset it and retry"
        )
    origin = urlsplit(git_read(repo, "remote", "get-url", "origin", expected_failure=True) or "")
    stem = origin.path.rstrip("/").removesuffix(".git")
    own = {f"{stem}/info/lfs", f"{stem}.git/info/lfs"}
    for key in _LFS_ENDPOINT_KEYS:
        value = git_read(
            repo, "config", "--file", str(repo / ".lfsconfig"), "--get", key, expected_failure=True
        )
        if not value:
            continue
        named = urlsplit(value)
        same = (
            named.scheme == "https"
            and "@" not in named.netloc
            and forges.origin(value) == forges.origin(origin.geturl())
            and named.path.rstrip("/") in own
            and not named.query
        )
        if not same:
            return (
                f"forge account {name}: the base branch's .lfsconfig sends LFS to another "
                "endpoint; push this branch yourself, or move LFS to origin, then retry"
            )
    return None


async def _push_lfs_objects(
    repo: Path,
    branch: str,
    head: str,
    remote: str,
    target: str,
    env: dict[str, str],
    auth: git_auth.GitAuth | None = None,
) -> None:
    """What git-lfs's pre-push did, run as git would run it: `git lfs
    pre-push` told this push's one ref, so it uploads the objects the new
    commits' pointers name and skips what origin's real tip already has (a
    person's commits brought in without their objects included). Commits
    with no LFS pointer, read with plain git, never run git-lfs; ones with
    pointers, on a machine without git-lfs, refuse, as the hook did."""
    # A failed listing raises rather than reading as "no pointers", which would
    # push pointers without their objects.
    listing = await run_git(
        repo,
        [
            "git",
            "rev-list",
            "--objects",
            head,
            "--not",
            "--remotes=origin",
            *([remote] if remote else []),
        ],
        env=env,
    )
    blobs = (line.split(" ", 1)[0] for line in listing.splitlines())
    if not await asyncio.to_thread(lfs.pointers_among, repo, blobs):
        return
    # Not `lfs.available()`: it is cached, and the message says install, then retry.
    if shutil.which("git-lfs") is None:
        raise ForgeError(
            "the branch adds Git LFS files and this machine has no git-lfs, so Kraft "
            "cannot upload them or push the branch: install git-lfs, then retry"
        )
    if auth and (why := _lfs_refusal(repo, auth.name)):
        raise ForgeError(why)
    ref = f"refs/heads/{branch}"
    await run_git(
        repo,
        ["git", "lfs", "pre-push", "origin", target],
        env=env,
        stdin=f"{ref} {head} {ref} {remote or '0' * len(head)}\n",
    )


def _was_local_tip(repo: Path, branch: str, sha: str) -> bool:
    """Whether `refs/heads/<branch>` ever pointed at `sha`, by its reflog. A
    linked worktree's branch reflog lives in the main repository, and git
    reads it from there."""
    log = git_read(
        repo, "reflog", "show", "--format=%H", f"refs/heads/{branch}", expected_failure=True
    )
    return sha in (log or "").split()


async def _assert_pushed(repo: Path, branch: str) -> None:
    """Refuse to merge a head the remote has never seen.

    If `origin/<branch>` does not exist, `run_git` raises ForgeError of its own
    and the node fails loudly — the right answer for a merge with no pushed
    branch.
    """
    raw = await run_git(
        repo, ["git", "rev-list", "--count", f"origin/{branch}..refs/heads/{branch}"]
    )
    ahead = int(raw.strip() or 0)
    if ahead:
        raise ForgeError(
            f"local branch is ahead of origin/{branch} by {ahead} commit(s); "
            "merging would merge a head the forge has never seen"
        )


async def _head_sha(repo: Path, branch: str = "") -> str:
    """`branch`'s tip (HEAD when none is named), or empty when git will not say.

    Empty means "do not compare": a repo git cannot read is no reason to
    report a pipeline that was read perfectly well as missing. The branch, not
    HEAD: an in-place item's checkout may be on any branch.
    """
    rev = f"refs/heads/{branch}" if branch else "HEAD"
    try:
        return (await run_git(repo, ["git", "rev-parse", rev])).strip()
    except ForgeError:
        return ""


async def committed_at(repo: Path, sha: str) -> datetime:
    """`sha`'s committer time: when a head Kraft has no push record of (one an
    older Kraft pushed, or a person) is taken to have reached the forge."""
    raw = await run_git(repo, ["git", "log", "-1", "--format=%cI", sha])
    return datetime.fromisoformat(raw.strip())


async def commits_ahead(repo: Path, branch: str, base: str) -> int | None:
    """How many commits `branch` adds to `origin/<base>`, the branch its
    merge request targets; None when git cannot say (no origin), which is no
    evidence the branch is empty.

    `origin/<base>` and not the local one: a Kraft worktree is cut from
    whatever the local checkout happened to be at, which may be behind.
    """
    try:
        raw = await run_git(repo, ["git", "rev-list", "--count", f"origin/{base}..{branch}"])
    except ForgeError:
        return None
    return int(raw.strip() or 0)


async def source_changed(repo: Path, branch: str, *, base: str, exclude: set[str]) -> bool:
    """Whether `branch` changes any path of `repo` outside `exclude` -- a
    workspace root's member mount paths, so a commit that only moves a
    member's pointer is not a source change. Against `origin/<base>`, as
    `commits_ahead` reads; False when git cannot say (no origin), like it."""
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
