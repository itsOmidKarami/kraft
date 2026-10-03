from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from kraft import caps as _caps
from kraft import events, logs, store
from kraft import harness as _harness
from kraft.adapters import subprocess as _subprocess
from kraft.adapters.forge import git
from kraft.config import ConfigError, RepoEntry, base_ignore_args, git_read
from kraft.paths import RunDirs, default_run_dir
from kraft.worker import backends as _backends
from kraft.worker import ca as _ca
from kraft.worker import inject as _inject
from kraft.worker import sandbox as _sandbox
from kraft.worker.egress import PhaseLists
from kraft.worker.env import worker_env
from kraft.worker.worktree_read import open_dir_no_symlinks

logger = logging.getLogger(__name__)


class RebaseConflict(RuntimeError):
    """`refresh_worktree_base` could not replay this branch onto its new
    base. A `RuntimeError` subclass so every existing `except RuntimeError`
    around this call keeps behaving as it did (Kraft-s7c04.23); the type
    exists so the three callers that can now *act* on a conflict can tell
    one apart from any other git failure without matching on message text.

    `paths` are the files git left unmerged, read before the abort."""

    def __init__(self, message: str, paths: tuple[str, ...] = ()):
        super().__init__(message)
        self.paths = paths


class RebaseTimedOut(RuntimeError):
    """`refresh_worktree_base` aborted a rebase that ran past its `timeout`
    (Kraft-3llig): a hanging pre-rebase hook or smudge/LFS filter must not
    hold the worker slot forever. The worktree is left clean either way, the
    same posture as `RebaseConflict` (unless the abort itself times out too:
    `_abort_rebase`). Only `mr_rebase` passes a `timeout`, so
    only it can see this; the other three `refresh_worktree_base` callers
    keep today's unbounded behaviour."""


class BaseBranchMissing(git.ForgeError):
    """The item named a base branch origin no longer has, and there is no
    copy of it to fall back on that is still true (Kraft-wz6vz). A stop for a
    person naming the branch, never a silent fork from whatever the connected
    checkout has on it. A `ForgeError` (so a `RuntimeError`): every door that
    already stops on a failed refresh or a failed forge call stops on this."""


def _commit_paths(worktree: Path, paths: list[str], message: str, base: str) -> None:
    """Commit exactly `paths` in `worktree`, or commit nothing.

    Kraft's own commit primitive. A document Kraft put in the worktree is not a
    change any agent made, so no agent is told to commit it, and it sits
    untracked until `forge._assert_clean` refuses to open the merge request over
    it (Kraft-8iw6). Kraft-xwen needs the same primitive so a document node can
    be given an allowlist with no Bash, which is why the pathspec is explicit
    and an unrelated dirty file is left exactly as it was.

    `--no-verify`: this runs before `ensure_worktree`'s `uv sync`, so the repo's
    `pre-commit` hook has no environment to spawn from yet (Kraft-i047 is the
    same failure seen from the other side), and the content is a file Kraft
    copied unmodified — there is nothing here for the hook to catch. No `-c
    user.email` override either: `ensure_worktree` pins identity into the
    worktree via `_pin_identity` at creation (Kraft-cppp),
    which is where every other commit on this branch gets its author.

    Best effort. A failure logs a warning and returns: a document that did not
    commit becomes a dirty-tree failure at `open_mr` with git's own message
    already in the log, which is strictly better than failing worktree creation.

    `base_ignore_args`, the same override `forge._work_product_pathspec`
    relies on, rides along on the `add`: a spec/plan attachment copied to its
    original relative path can land under `docs/superpowers/` or
    `.engineering/`, and if the base (`base`) ignores that root but this worktree's own
    checked-out `.gitignore` predates the rule (Kraft-vu26), a plain `git add`
    stages it anyway. Staging nothing here is correct, not a failure -- the
    diff-cached check below already treats "nothing staged" as a no-op, and
    the attachment stays on disk for the agent to read either way.
    """
    if not paths:
        return

    with base_ignore_args(worktree, base) as ignore_args:

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", *ignore_args, *args], cwd=str(worktree), capture_output=True, text=True
            )

        added = git("add", "--", *paths)
        if added.returncode != 0:
            logger.warning("could not stage %s in %s: %s", paths, worktree, added.stderr.strip())
            return
        # Exit 0 means no staged difference for these paths — an ignored or an
        # unchanged path stages nothing, and `git commit` on an empty commit exits
        # non-zero. Skip it rather than log a failure that is really a no-op.
        if git("diff", "--cached", "--quiet", "--", *paths).returncode == 0:
            return
        done = git("commit", "--no-verify", "-m", message, "--", *paths)
        if done.returncode != 0:
            logger.warning(
                "could not commit %s in %s: %s",
                paths,
                worktree,
                done.stderr.strip() or done.stdout.strip(),
            )


def restore_branch(worktree: Path, branch: str, base: str) -> None:
    """Force the worktree back onto `branch` if an agent task left it
    somewhere else, clearing any in-progress merge with it (Kraft-v5qd).

    An agent has a real shell and can check out whatever it likes to
    investigate something -- a diagnostic test-merge against `main` to look
    at a conflict, say. Nothing enforces that it checks back out afterward,
    and a merge left mid-conflict when the agent's turn just ends (budget,
    a crash, the one-pass skills that only run once) strands the worktree:
    every task after it resolves the merge request from *this* branch, and a
    diagnostic branch has none. `commit_stragglers` runs right after this in
    the agent-kind dispatch, precisely so any straggler ends up on the
    branch the item actually owns rather than committed onto whatever the
    agent happened to leave checked out.

    Best effort, except the checkout: an unreadable HEAD is left for the
    next node's own git command to refuse, but a checkout that fails, or
    that would act on operation state other than a merge, raises, since the
    straggler commit after it would land on whatever branch HEAD names
    (Kraft-xngty).
    """
    with base_ignore_args(worktree, base) as ignore_args:

        def run(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", *ignore_args, *args], cwd=str(worktree), capture_output=True, text=True
            )

        current = run("rev-parse", "--abbrev-ref", "HEAD")
        if current.returncode != 0 or current.stdout.strip() == branch:
            return
        logger.warning(
            "worktree %s left on %r instead of %r after an agent task; restoring",
            worktree,
            current.stdout.strip(),
            branch,
        )
        # `checkout -f` clears an agent's half-done merge itself (Kraft-v5qd),
        # so that state is allowed. Any other is refused: over a planted
        # `MERGE_AUTOSTASH` the checkout would store the worker's tree in the
        # repository's shared `refs/stash` (Kraft-xngty).
        git.assert_no_operation(worktree, allow=("MERGE_HEAD",))
        checked_out = run("checkout", "-f", branch)
        if checked_out.returncode != 0:
            # Raised, not logged: the straggler commit that follows would land
            # on whatever branch HEAD names (Kraft-xngty).
            raise RuntimeError(
                f"could not restore {worktree} to {branch!r}: {checked_out.stderr.strip()}"
            )


def _copy_attachments(
    repo: Path, worktree: Path, attachments: list[dict], work_item_id: str, base: str
) -> None:
    """Intake attachments that are not committed do not exist in a fresh
    worktree (`git worktree add` branches from HEAD), so copy them in. A
    committed one arrived through git and is left exactly as git wrote it.

    The path was validated against the *repo's* working tree, not this
    worktree (checked out from HEAD, possibly a different tree). If HEAD has
    a symlink here, `dest.exists()` follows it, which for a dangling symlink
    reports False and `shutil.copyfile` would then write through it to
    wherever it points. So a symlinked destination, dangling or not, is
    always skipped, and the resolved destination must stay inside the
    worktree.

    `source`, when the validator set it, is the absolute path the document was
    actually found at — a working tree of the repo that is not `repo` itself
    (Kraft-85wk). Without it the copy would look under `repo`, find nothing,
    and skip.

    Whatever actually gets copied is committed once, here, so an uncommitted
    attachment does not sit untracked and trip `open_mr`'s dirty-worktree guard
    (Kraft-8iw6) — no agent is told to commit a file it never wrote."""
    worktree_root = worktree.resolve()
    written: list[dict] = []
    for attachment in attachments:
        dest = worktree / attachment["path"]
        src = Path(attachment["source"]) if attachment.get("source") else repo / attachment["path"]
        if dest.is_symlink():
            continue
        resolved = dest.resolve()
        if resolved != worktree_root and worktree_root not in resolved.parents:
            continue
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        written.append(attachment)

    if written:
        # One commit for the whole intake, not one per document, and no commit
        # at all when every copy was skipped.
        _commit_paths(
            worktree,
            [a["path"] for a in written],
            f"chore: attach {'+'.join(a['kind'] for a in written)} for {work_item_id}",
            base,
        )


def _carry_local_files(repo: Path, worktree: Path, rels: list[str]) -> tuple[list[str], list[str]]:
    """Copy `rels` from `repo` into `worktree`. Returns `(carried, refused)`.

    `git worktree add` checks out tracked content at HEAD, so a machine-local
    file the developer never committed does not exist in the worktree. A
    `.python-version` left behind this way is not a missing convenience: uv
    falls through to `requires-python`, and `~=3.11` means `>=3.11, <4`, so it
    picks whatever interpreter PATH offers first and the chain runs on it
    silently -- 3.14 on work item 1e2e6b45898e42298d16232c9cbfb768, ten retries
    deep (Kraft-gxcmy).

    A file the worktree would not ignore is refused rather than carried. Kraft
    has no way to hold an unignored file out of a commit: a per-worktree
    `info/exclude` is read from the *common* dir, so an entry there would leak
    to every other worktree of the same repo, and a `core.excludesFile` set in
    the worktree's own config is outranked by `base_ignore_args`' `-c` -- which
    is live during `_commit_paths`, exactly where it would have mattered.
    Refusing keeps a carried file clear of `open_mr`'s dirty-worktree guard by
    construction, and an unignored machine-local file is one `git add -A` from
    being committed with or without Kraft.

    Never raises: a refusal or a missing source is reported to the caller, not
    turned into a failed worktree. The destination guards are
    `_copy_attachments`' (Kraft-85wk) -- a symlinked destination is skipped
    whether or not it dangles, and the resolved destination must stay inside
    the worktree.
    """
    worktree_root = worktree.resolve()
    carried: list[str] = []
    refused: list[str] = []
    for rel in rels:
        src = repo / rel
        dest = worktree / rel
        if not src.is_file():
            # config.py only rejects a directory entry that ends in `/`
            # (`.venv/`), so a bare `.venv` passes validation and lands here.
            # Silently skipping it -- indistinguishable from a source that was
            # simply never created -- hides exactly the typo an operator most
            # needs to see; refusing it, like an unignored file, at least says
            # something went wrong. A genuinely absent source (no file, no
            # directory) stays a silent no-op, per the spec.
            if src.exists():
                refused.append(rel)
            continue
        if dest.is_symlink() or dest.exists():
            continue
        resolved = dest.resolve()
        if resolved != worktree_root and worktree_root not in resolved.parents:
            continue
        # The worktree's own rules, not the base's: `base_ignore_args` widens what
        # counts as ignored for Kraft's own commits, but a plain `git add -A`
        # from an agent sees only what is checked out here.
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", rel],
            cwd=str(worktree),
            capture_output=True,
            text=True,
        )
        if ignored.returncode != 0:
            refused.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        carried.append(rel)
    return carried, refused


def _uncarried_local_files(repo: Path, worktree: Path) -> list[str]:
    """Root-level files present in `repo` but absent from `worktree`.

    `local_files` is opt-in, which means an unconfigured repo behaves exactly
    as it did before -- including the part where it is silently wrong. Nobody
    knew to configure the repo on work item 1e2e6b45898e42298d16232c9cbfb768
    until after it had burned ten retries (Kraft-gxcmy), so the gap says its own
    name here instead of waiting to be discovered.

    No `--exclude-standard`: an *ignored* root file is the likeliest carry
    candidate of all, so the listing has to include it. `--directory` collapses
    an untracked directory to a single entry with a trailing slash, which the
    filter then drops -- `.venv/` and `node_modules/` are artifacts to rebuild,
    never files to carry.

    Stateless by design: a carried file exists in the worktree, so nothing needs
    to be told what Task 3 copied, and this reads correctly on
    `ensure_worktree`'s early-return path where no copy happened at all.
    """
    listed = git_read(repo, "ls-files", "--others", "--directory", expected_failure=True) or ""
    names = [n for n in listed.splitlines() if n and "/" not in n]
    return sorted(n for n in names if not (worktree / n).exists())


def _pin_identity(repo: Path, worktree: Path, work_item_id: str) -> None:
    """Resolve `user.name`/`user.email` from `repo` and write them into
    `worktree`'s own git config.

    `ensure_worktree` otherwise never establishes a commit identity, relying
    on the worktree inheriting the repo's config -- true until it isn't: a
    repo with no `[user]` block anywhere in its config chain resolves
    nothing, and the agent told to commit before it exits invents an identity
    to get unblocked (`kraft@local` was seen in the wild), which later nodes
    then read back off the branch and treat as sanctioned (Kraft-mxdx).

    Raised, not logged: a missing identity means every commit on this branch
    is about to either fail or fabricate one, so failing worktree creation
    with the missing key named beats a push rejection eight nodes later.

    Never a member made by `_setup_submodules`: its config is the operator's
    member repository's own, and Kraft writes nothing there (Kraft-ju36l, J3).
    A session gets the root's identity as `GIT_AUTHOR_*`/`GIT_COMMITTER_*`
    instead (`adapters.subprocess.run_task`). An old-layout member's separate
    gitdir (`modules/<rel>`) is still pinned this way.
    """
    name = git_read(repo, "config", "--get", "user.name", expected_failure=True)
    email = git_read(repo, "config", "--get", "user.email", expected_failure=True)
    if not name or not email:
        missing = "user.name" if not name else "user.email"
        raise RuntimeError(f"no {missing} configured in {repo}; set it before Kraft can commit")
    for key, value in (("user.name", name), ("user.email", email)):
        subprocess.run(
            ["git", "-C", str(worktree), "config", key, value], capture_output=True, text=True
        )


def _git_ok(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _submodule_name(worktree: Path, base: str | None, rel: str) -> str | None:
    """The name `.gitmodules` gives the submodule at `rel`, read from the
    `base` commit, never the worktree's copy, which a worker or a setup
    command can rewrite (Kraft-ju36l)."""
    if not base:
        return None
    listed = git_read(
        worktree,
        "config",
        "--blob",
        f"{base}:.gitmodules",
        "--get-regexp",
        r"^submodule\..*\.path$",
        expected_failure=True,
    )
    for line in (listed or "").splitlines():
        key, _, path = line.partition(" ")
        if path == rel:
            return key.removeprefix("submodule.").removesuffix(".path")
    return None


def _add_member(
    worktree: Path, rel: str, member_repo: Path, branch: str, base: str | None = None
) -> None:
    """Check member `rel` out as a linked worktree of its connected
    repository `member_repo` (Kraft-ju36l, spec 3.1), on `branch` at the
    commit the root's gitlink records.

    Never `submodule update --init`: that puts the member's gitdir, config
    and hooks under the root's worktree gitdir, which a sandboxed worker
    writes. A linked worktree of `member_repo` reads config, hooks and
    attributes only from `member_repo`'s common gitdir, the operator's.
    `submodule status` needs `submodule.<name>.active` to count the member
    as initialized. Kraft sets that alone, with the name from the base
    commit's `.gitmodules`: `submodule init` would copy a name and URL out of
    the worktree's copy into the root repository's config, where a worker's
    rewrite would outlive the item."""
    if not member_repo.is_dir():
        raise RuntimeError(
            f"the connected repository {member_repo} for {rel} is missing; reconnect "
            "it, or fix its path in repos.yaml, then retry"
        )
    if not _sandbox.repository_top(member_repo):
        raise RuntimeError(
            f"the connected repository {member_repo} for {rel} is not the top of a git "
            "repository, so git there would act on the repository around it; fix its "
            "path in repos.yaml, then retry"
        )
    if name := _submodule_name(worktree, base, rel):
        _git_ok(worktree, "config", f"submodule.{name}.active", "true")
    sha = git_read(worktree, "rev-parse", f"HEAD:{rel}", expected_failure=True)
    if not sha:
        raise RuntimeError(f"{rel} is not a gitlink in the root's HEAD")
    have = ("cat-file", "-e", f"{sha}^{{commit}}")
    for fetch in ((), ("fetch", "-q", "origin"), ("fetch", "-q", "origin", sha)):
        if fetch:
            _git_ok(member_repo, *fetch)
        if _git_ok(member_repo, *have).returncode == 0:
            break
    else:
        raise RuntimeError(
            f"{member_repo} does not have {sha}, the commit {rel} points at, "
            "and its origin would not give it"
        )
    target = worktree / rel
    if not _sandbox.inside(worktree, rel) or not target.is_dir() or any(target.iterdir()):
        raise RuntimeError(
            f"{rel} in {worktree} is not the empty directory the checkout made; "
            "Kraft checks a member out only into that"
        )
    # A branch of this name already in the member repository (a rejected gate
    # kept it, or a person made it) is taken only where the root points.
    existing = git_read(
        member_repo,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}^{{commit}}",
        expected_failure=True,
    )
    if existing and existing != sha:
        raise RuntimeError(
            f"{member_repo} already has a branch {branch} at {existing[:12]}, not at "
            f"{sha[:12]}, the commit {rel} points at; move or delete that branch by "
            "hand, then retry"
        )
    _add_worktree(member_repo, target, branch, sha)


async def _setup_submodules(
    db,
    repo: Path,
    worktree: Path,
    branch: str,
    work_item_id: str,
    members: list[tuple[str, str | None]],
    repositories: Mapping[str, RepoEntry],
    *,
    sandboxed: bool,
    base: str | None = None,
) -> None:
    """Check out only the declared members (design 3 step 2 -- never
    blanket), each on the item's branch, and write one `work_item_repos` row
    per repo -- deepest submodule first, root last (3a) -- so the forge nodes
    later know what to open a merge request against and in what order.

    A member naming a connected repository is a linked worktree of it
    (`_add_member`), for every item, sandboxed or not. One with none -- an
    item filed before workspaces (Kraft-zvqwl), or a member whose repository
    is no longer connected -- keeps `submodule update --init` and a checkout
    inside it, which leaves its gitdir in the root's worktree gitdir. A
    sandboxed worker writes that, so a sandboxed item refuses the old way.
    """
    ids = dict(members)
    ordered = store.merge_rank_order(list(ids))
    legacy = [rel for rel in ordered if ids[rel] is None or ids[rel] not in repositories]
    if legacy and sandboxed:
        raise RuntimeError(
            f"{work_item_id} runs sandboxed, and no connected repository is known for "
            f"{', '.join(legacy)}: Kraft checks a sandboxed item's members out only "
            "from their connected repositories. Connect them and retry"
        )
    if legacy:
        init = await asyncio.to_thread(
            subprocess.run,
            # `-c protocol.file.allow=always`: git 2.38+ refuses a `file://`
            # submodule URL by default (a supply-chain hardening default, not
            # something specific to this repo's own submodules). A real remote is
            # https/ssh and is unaffected; the fixtures this plan's own tests
            # build (`make_repo_with_submodule`) use plain filesystem paths.
            ["git", "-c", "protocol.file.allow=always", "submodule", "update", "--init"]
            + ["--", *legacy],
            cwd=str(worktree),
            capture_output=True,
            text=True,
        )
        if init.returncode != 0:
            detail = init.stderr.strip() or init.stdout.strip()
            raise RuntimeError(f"git submodule update --init failed for {work_item_id}: {detail}")
    for rel in ordered:
        sub = worktree / rel
        if rel in legacy:
            exists = git_read(
                sub,
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
                expected_failure=True,
            )
            checkout = await asyncio.to_thread(
                subprocess.run,
                ["git", "checkout", branch] if exists else ["git", "checkout", "-b", branch],
                cwd=str(sub),
                capture_output=True,
                text=True,
            )
            if checkout.returncode != 0:
                detail = checkout.stderr.strip() or checkout.stdout.strip()
                raise RuntimeError(
                    f"checkout of {branch!r} failed in submodule {rel} for {work_item_id}: {detail}"
                )
            await asyncio.to_thread(_pin_identity, repo, sub, work_item_id)
        else:
            member_repo = Path(repositories[ids[rel]].path)
            try:
                await asyncio.to_thread(_add_member, worktree, rel, member_repo, branch, base)
            except RuntimeError as exc:
                raise RuntimeError(f"member {rel} of {work_item_id}: {exc}") from exc

    # Only once every member is checked out: a failure above discards the
    # worktree, and its retry must not record an earlier member twice.
    def record(c) -> None:
        for rank, rel in enumerate(ordered, start=1):
            store.add_repo(
                c,
                work_item_id=work_item_id,
                repo_path=str(worktree / rel),
                role="submodule",
                submodule_path=rel,
                merge_rank=rank,
            )
        store.add_repo(
            c,
            work_item_id=work_item_id,
            repo_path=str(worktree),
            role="root",
            merge_rank=len(ordered) + 1,
        )

    await db.write(record)


async def _discard_worktree(repo: Path, worktree: Path, members=()) -> str | None:
    """Remove a worktree whose setup failed, keeping its branch, and prune
    each of `members` -- the connected repositories its members were checked
    out from, which removing the root leaves with a stale entry. Returns a
    reason when the directory survives, or None.

    `ensure_worktree` returns early when the directory exists, so a worktree
    left behind by a failed setup would make the next attempt skip setup
    entirely. The branch stays: a rejected gate already removes a worktree and
    keeps its branch, and the commits on it are not this failure's business.
    Not best-effort like the abandon path -- a survivor is reported into the
    error the caller is about to raise, because silence here is the bug.
    """
    for cwd, args in (
        (repo, ["git", "worktree", "remove", "--force", str(worktree)]),
        (repo, ["git", "worktree", "prune"]),
        *((m, ["git", "worktree", "prune"]) for m in members if m.is_dir()),
    ):
        await asyncio.to_thread(subprocess.run, args, cwd=cwd, capture_output=True, text=True)
    return None if not worktree.exists() else f"{worktree} still present"


def _add_worktree(repo: Path, path: Path, branch: str, start: str | None) -> None:
    """`git worktree add` of `branch` at `path` in `repo`: the branch checked
    out if `repo` already has it (a rejected gate removes a worktree and keeps
    its branch), else created at `start`. Raises `RuntimeError` with git's
    stderr. The root and every workspace member are made by this one call.

    A sha for `start`, not `origin/<default>`: a remote-tracking start point
    would make git set it as the new branch's upstream. The prune first: a
    worktree directory deleted out from under git leaves a stale
    administrative entry that makes `worktree add` refuse the same path.
    A repository with no commit is refused rather than given an orphan."""
    exists = git_read(
        repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", expected_failure=True
    )
    if (
        not exists
        and not start
        and not git_read(
            repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", expected_failure=True
        )
    ):
        # git would make the branch an empty orphan, without one of the
        # repository's files, and the work item would run in it.
        raise RuntimeError(
            f"{repo} has no commit to branch from: commit its files, then retry the work item"
        )
    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True, text=True)
    args = ["git", "worktree", "add", str(path)]
    args += [branch] if exists else ["-b", branch, *([start] if start else [])]
    done = subprocess.run(args, cwd=repo, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or done.stdout.strip())


async def ensure_worktree(
    db,
    run_dirs,
    *,
    repo: str,
    work_item_id: str,
    repo_entry: RepoEntry | None,
    attachments: list[dict] | None = None,
    sandbox: dict | None = None,
    repositories: Mapping[str, RepoEntry] | None = None,
) -> Path:
    """The item's worktree, created if it is not there yet, with intake
    attachments copied in.

    Called by the executor before the first node dispatches, not only by the
    `env_setup` builtin: `default.yaml` runs `spec` and `plan` before
    the first `on.env.prepare`, and an agent asked to write a file into a directory that does
    not exist fails in a way no chain can recover from (Kraft-bmp). The
    attachment copy has to move with it: `plan/SKILL.md` tells a headless
    session to fall back to an attached spec when the chain skipped the spec
    node, and that document was not there yet if the copy waited for
    `env_setup` to run. `env_setup` is this call, unconditionally carrying
    attachments, plus its own session bookkeeping.

    Idempotent in both directions: an existing worktree is returned untouched
    *and uncopied-into* (attachments were copied whenever this worktree was
    created; that also covers a rejected gate re-entering with the same
    attachments — the worktree is gone but the copy already landed on the
    branch's prior commits, and `_copy_attachments`'s own `dest.exists()` guard
    makes a second copy onto a survived worktree a no-op rather than a
    clobber), and an existing branch — the one on the row (a rejected gate
    removes the worktree but keeps the branch) is checked out rather than
    re-created.
    """
    worktree = run_dirs.worktrees / work_item_id
    if worktree.is_dir():
        return worktree
    # Read from repo_entry before `git worktree add` runs, not after: a
    # poisoned entry (malformed repos.yaml) raises ConfigError on any read, and
    # reading it only after the worktree exists would leave that worktree
    # behind for a later `kraft item retry` to find via the early return
    # above -- skipping setup_command entirely and dispatching into an
    # unprepared worktree.
    local_files = repo_entry.local_files if repo_entry is not None else []
    # Pin the base before the worktree exists, so the early return above
    # guarantees a crashed-and-retried run never re-pins to a moved HEAD.
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref, branch, id, materialized_chain, submodules "
            "FROM work_items WHERE id = ?",
            (work_item_id,),
        ).fetchone()
    )
    head = None
    base = await base_branch(db, work_item_id, Path(repo))
    if row is not None and row["base_ref"] is None:
        head = await upstream_head(Path(repo), base)
        if head:
            await db.write(lambda c: store.set_base_ref(c, work_item_id, head))
        else:
            # Without a base_ref the diff endpoint answers "no diff available"
            # for the rest of the item's life; the reason belongs in the log
            # rather than in a reviewer's guesswork.
            logger.warning("no base_ref for %s: rev-parse HEAD failed in %s", work_item_id, repo)
    # One stored value, not a third derivation of it (Kraft-nhps). The row is
    # the truth here; it is None only when the caller asked for a worktree
    # before intake committed the row — tests do, and `env_setup` is reachable
    # that way — and then the id is all there is to name a branch with.
    branch = store.branch_for(row) if row is not None else f"kraft/{work_item_id}"
    # Not a skip, and before `worktree add`: `source` is Kraft's own copy since
    # Kraft-eqgn, so a missing one means Kraft lost it, and the gate it justified
    # is trimmed. A worktree made anyway would take the early return above on a
    # retry and run without the document (Kraft-s7c04.29).
    for a in attachments or []:
        src = Path(a["source"]) if a.get("source") else Path(repo) / a["path"]
        if not src.is_file():
            raise FileNotFoundError(
                f"the {a['kind']} attachment for {work_item_id} is missing from Kraft's "
                f"storage at {src}; its gate was trimmed at intake, so put the "
                f"{a['kind']} back at {src} and retry the work item"
            )
    try:
        await asyncio.to_thread(_add_worktree, Path(repo), worktree, branch, head)
    except RuntimeError as exc:
        # Raised, not returned: this runs outside a session, so there is no
        # session status to carry the failure. `kraft.api.deps.guard` turns it into
        # needs_human with the git stderr in the reason.
        raise RuntimeError(f"git worktree add failed for {work_item_id}: {exc}") from exc
    await asyncio.to_thread(_pin_identity, Path(repo), worktree, work_item_id)
    await asyncio.to_thread(
        _copy_attachments, Path(repo), worktree, attachments or [], work_item_id, base
    )
    # Before the sync, not after: uv chooses an interpreter when it runs, so a
    # pin that lands later is a pin that changed nothing (Kraft-gxcmy).
    _, refused = await asyncio.to_thread(_carry_local_files, Path(repo), worktree, local_files)
    if refused:
        logger.warning(
            "not carried into %s (the worktree would not ignore them, so Kraft "
            "cannot keep them out of a commit): %s",
            work_item_id,
            ", ".join(refused),
        )
    # Every node from `spec` on can commit, and the shared pre-commit hook
    # needs its tooling on PATH, so the worktree must be a working environment
    # before the first node dispatches (Kraft-i047). There is no default and no
    # marker sniffing: the repo declares how it is prepared, or the chain stops
    # (Kraft-kji8w). A failure here used to be a log warning, which dispatched
    # a node into a known-broken environment and let the verify node retry a
    # deterministic failure ten times over (Kraft-s0w2l).
    try:
        # Before the members are checked out: there are none to mount yet.
        await _prepare(
            worktree,
            Path(repo),
            repo_entry,
            sandbox=sandbox,
            checkout=_sandbox.Checkout(worktree, {}) if sandbox else None,
        )
    except RuntimeError as exc:
        # Only the creating caller may throw away a worktree other nodes are
        # already using, so the discard lives here and not in the helper.
        left = await _discard_worktree(Path(repo), worktree)
        suffix = f" (and its worktree could not be removed: {left})" if left else ""
        raise RuntimeError(f"{exc}{suffix}") from exc
    members = item_members(row) if row is not None else []
    if members:
        try:
            await _setup_submodules(
                db,
                Path(repo),
                worktree,
                branch,
                work_item_id,
                members,
                repositories or {},
                sandboxed=sandbox is not None,
                base=(row["base_ref"] if row is not None else None) or head,
            )
        except (RuntimeError, OSError) as exc:
            # As for a failed setup command: a retry must find no worktree, or
            # it would skip the members entirely.
            connected = [m for m in member_repositories(row, repositories or {}).values() if m]
            left = await _discard_worktree(Path(repo), worktree, connected)
            suffix = f" (and its worktree could not be removed: {left})" if left else ""
            raise RuntimeError(f"{exc}{suffix}") from exc
    return worktree


def item_members(row) -> list[tuple[str, str | None]]:
    """`(mount path, repository id)` for each submodule `row`'s checkout
    assembles: exactly the members its frozen target selected, at their
    frozen mount paths (`workspace-tasks-have-an-assembled-checkout`) --
    typed membership, never whatever `.gitmodules` lists or the agent later
    touches. `row` carries `materialized_chain` and `submodules`.

    Kraft-zvqwl: an item keeps the checkout shape it was born with. One filed
    before workspaces has a single-repository target and its submodules in the
    `submodules` column, with no repository id; a worktree rebuilt for it
    after the upgrade (a retry, a rejected gate re-entering) assembles them
    still. A V1 repository target never writes the column."""
    snapshot = store.materialized_chain_of(row)
    members: list[tuple[str, str | None]] = (
        [(m.path, m.repository) for m in snapshot.target.mounts.values()]
        if snapshot is not None
        else []
    )
    if row["submodules"] and (snapshot is None or snapshot.target.kind == "repository"):
        members = [(p, None) for p in json.loads(row["submodules"])]
        logger.info("%s: filed before workspaces; its submodules are %s", row["id"], members)
    return members


def member_repositories(row, repositories: Mapping[str, RepoEntry]) -> dict[str, Path | None]:
    """Each declared member's connected repository, by mount path: read from
    repos.yaml by id, never from the member's gitfile, which a worker writes
    (Kraft-ju36l). None for a member with none -- an old-layout or
    pre-workspace member, a repository no longer connected, or a repos.yaml
    that cannot be read."""
    found: dict[str, Path | None] = {}
    for rel, rid in item_members(row):
        try:
            entry = repositories.get(rid) if rid is not None else None
        except ConfigError:
            entry = None
        found[rel] = Path(entry.path) if entry is not None else None
    return found


def item_mounts(row) -> list[str]:
    """The mount paths of `item_members(row)`."""
    return [p for p, _ in item_members(row)]


async def run_setup_command(
    worktree: Path,
    repo: Path,
    repo_entry: RepoEntry | None,
    *,
    sandbox: dict | None = None,
    checkout: _sandbox.Checkout | None = None,
) -> str:
    """Prepare `worktree` the way its repo declares, and say what happened.

    Extracted from `ensure_worktree` so it can run more than once per item
    (Kraft-zlsuk). `ensure_worktree` still calls it at creation -- every node
    from `spec` on can commit and the shared pre-commit hook needs its tooling
    on PATH before the first node dispatches (Kraft-i047) -- and `env_setup`
    calls it on every dispatch, because a rebase can land a new lockfile and
    nothing else rebuilds the environment.

    There is no default and no marker sniffing: the repo declares how it is
    prepared, or the chain stops (Kraft-kji8w). Raises `RuntimeError` when the
    repo declares no `setup_command` at all, or when the command fails.

    `sandbox` is the item's (`dispatch.item_sandbox`): given one, the command
    runs inside it or not at all.
    """
    cmd = repo_entry.setup_command if repo_entry is not None else None
    if cmd is None:
        raise RuntimeError(
            f"no setup_command declared for {repo} in repos.yaml, so {worktree.name}'s "
            'worktree cannot be prepared. Declare one (use "" for a repo that '
            "deliberately needs no preparation), or tick No setup needed under "
            "Templates › Repos."
        )
    if not cmd:
        return ""
    client_env = worker_env(repo_entry)
    withheld: set[str] = set()
    if sandbox and checkout is None:
        # Fails closed for a caller that forgot, as `run_task` does: a member
        # left unmounted has a `.git` the container can rewrite (Kraft-ju36l).
        raise RuntimeError(
            f"setup command for {worktree.name} cannot run: its sandbox was not given "
            "the checkout to mount, workspace members included"
        )
    if sandbox:
        # Never on the host for a sandboxed item (Kraft-p8nem): the worktree
        # is the worker's to write, so `uv sync` or `npm ci` there runs a build
        # backend or package script the worker may have written. The docker
        # client runs on the host with the worker env, like `run_task`'s; the
        # container gets the entry's `env` and passthrough names, bare.
        # It mounts the worktree's ref store as it stands -- one of the item's
        # sessions may have it mounted -- and publishes nothing from it: only
        # a session's store names the branch Kraft moves.
        run_base = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
        backend = _backends.for_sandbox(sandbox)
        # No harness here: an entry naming just `env` holds its sentinel
        # and nothing injects it; a repository's own credential is injected
        # where the install phase names its host. One scoped to `runtime`
        # alone is not here at all, and its value stays out all the same.
        # A bound credential's `source` is withheld as its `env` is: it holds
        # the daemon's real value under that name.
        every = _harness.sandbox_credentials(sandbox)
        credentials = _harness.manage(every, phase="install")
        sentinels = {c.env: c.sentinel for c in credentials}
        withheld = {n for c in every for n in (c.env, c.source) if n}
        try:
            kraft_ca = _ca.ensure_ca(RunDirs(run_base))[0] if credentials else None
            ca_bundle = await backend.prepare(sandbox, kraft_ca=kraft_ca)
        except _sandbox.SandboxNotReady as exc:
            raise RuntimeError(f"setup command for {worktree.name} cannot run: {exc}") from exc
        # Every member it can see is mounted like the root (Kraft-ju36l):
        # `checkout`, once the members exist.
        refs = await asyncio.to_thread(
            backend.code_in,
            run_base,
            worktree,
            None,
            members=checkout.members,
            work_item_id=worktree.name,
        )
        # Named like a session, so the sandbox can be asked whether its
        # memory limit killed the command, and closed after.
        setup_id = f"setup-{uuid.uuid4().hex[:12]}"
        network = bool(sandbox.get("network"))
        try:
            # The install phase's lists, never the runtime's or the harness's
            # hosts (spec §1). No session row: nothing reattaches a setup
            # command. Its worktree is named for its work item.
            proxy_env = (
                await _subprocess.open_egress(
                    None,
                    backend,
                    setup_id,
                    worktree.name,
                    sandbox,
                    PhaseLists.of(sandbox["network"], "install"),
                    _inject.rules(credentials, client_env),
                )
                if network
                else {}
            )
            argv = backend.wrap(
                ["sh", "-c", cmd],
                worktree,
                sandbox,
                None,
                env=proxy_env,
                session_id=setup_id,
                refs=refs,
                # The entry's `env:` by name too: its values are in the
                # client's own env (`worker_env` below), never on argv.
                passthrough=(
                    *(repo_entry.env_passthrough if repo_entry is not None else ()),
                    *(repo_entry.env if repo_entry is not None else {}),
                ),
                ca_bundle=ca_bundle,
                sentinels=sentinels,
            )
        except BaseException as exc:
            if network:
                await _subprocess.close_egress(backend, setup_id)
            if isinstance(exc, _sandbox.SandboxNotReady):
                raise RuntimeError(f"setup command for {worktree.name} cannot run: {exc}") from exc
            raise
        run = dict(args=argv, cwd=backend.client_cwd(setup_id) or worktree)
    else:
        run = dict(args=cmd, shell=True, cwd=worktree)
    oom = None
    try:
        done = await asyncio.to_thread(
            _run_setup,
            **run,
            # A managed credential's value is the egress proxy's alone.
            env={k: v for k, v in client_env.items() if k not in withheld},
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        if not sandbox:
            raise
        # No docker means no setup, never a host fallback.
        raise RuntimeError(
            f"setup command for {worktree.name} must run in its sandbox, but "
            f"{argv[0]!r} could not be started: {exc}"
        ) from exc
    finally:
        if sandbox:
            try:
                oom = await backend.oom_killed(setup_id)
            finally:
                # The command, then its route out (spec §4).
                try:
                    await backend.close(setup_id)
                finally:
                    if network:
                        await _subprocess.close_egress(backend, setup_id)
    # Kraft never stops a setup command itself, so an unconfirmed kill counts.
    if oom is not None and done.returncode != 0:
        raise RuntimeError(
            f"setup command for {worktree.name} failed: a process in the sandbox was killed "
            f"{_subprocess.oom_cause(oom)}: {cmd!r}; raise the sandbox's resources.memory"
        )
    if done.returncode != 0:
        detail = done.stderr.strip() or done.stdout.strip()
        raise RuntimeError(f"setup command failed for {worktree.name}: {cmd!r}: {detail}")
    return f"$ {cmd}\n{done.stdout}{done.stderr}"


#: The process groups of the setup commands running now, by leader pid.
_RUNNING_SETUPS: set[int] = set()


def _run_setup(args, *, capture_output: bool = False, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run` for a setup command, run as the leader of its own
    process group, which `end_running_setups` can end with everything it
    started. The seam a test replaces to see or stub the setup's spawn."""
    if capture_output:
        kwargs |= {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, start_new_session=True, **kwargs) as proc:
        _RUNNING_SETUPS.add(proc.pid)
        try:
            out, err = proc.communicate()
        finally:
            _RUNNING_SETUPS.discard(proc.pid)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def end_running_setups() -> None:
    """End every setup command still running, its children included: the
    server is shutting down. One left running outlived `kraft admin stop`
    and ran beside the next server's own run of the same setup in the same
    worktree, where two `npm ci` or `pip install` runs can corrupt it."""
    for pid in list(_RUNNING_SETUPS):
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


async def _prepare(
    worktree: Path,
    repo: Path,
    repo_entry: RepoEntry | None,
    *,
    sandbox: dict | None = None,
    checkout: _sandbox.Checkout | None = None,
) -> str:
    """`run_setup_command`, recording the lockfiles it wrote (a `uv sync` with
    no `uv.lock`) so the straggler sweep can tell them from one the agent made
    (`forge.git.record_setup_writes`). Through the git adapter on the host's
    worktree, before and after, and only for a command that will run: no
    declared command runs nothing, and a refusal records nothing."""
    if repo_entry is None or not repo_entry.setup_command:
        return await run_setup_command(
            worktree, repo, repo_entry, sandbox=sandbox, checkout=checkout
        )
    before = await git.lockfile_digests(worktree)
    said = await run_setup_command(worktree, repo, repo_entry, sandbox=sandbox, checkout=checkout)
    await git.record_setup_writes(worktree, before)
    return said


async def base_branch(db, work_item_id: str, repo: Path, *, member: bool = False) -> str:
    """The branch `work_item_id`'s work in `repo` -- its own repository, or a
    workspace's root -- starts from, rebases onto, and merges into: the one
    frozen on its target at intake (Kraft-v9gbi), else `repo`'s default
    branch, which is every item filed without one and every item filed
    before an item could name one.

    The one accessor every "which branch is the base" question goes through.
    A workspace `member` (`repo` is its checkout) is not the item's
    repository: it keeps its own default branch, as it always has, because
    one branch name means nothing across repositories that need not share it.
    """
    if member:
        return await git.default_branch(repo)
    row = db.read(
        lambda c: c.execute(
            "SELECT materialized_chain, run_chain FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    snapshot = store.materialized_chain_of(row) if row is not None else None
    frozen = snapshot.target.base_branch if snapshot is not None else None
    return frozen or await git.default_branch(repo)


def promptless_git_env(repo: Path) -> dict[str, str]:
    """The process env for a git call that talks to origin from the daemon:
    no prompt may reach a foreground `kraft`'s terminal. `GIT_TERMINAL_PROMPT=0`
    for git's own, ssh `BatchMode` for passphrases and unknown hosts -- unless
    the user already chose an ssh command, which an env var would override."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if "GIT_SSH_COMMAND" not in env and not git_read(
        repo, "config", "core.sshCommand", expected_failure=True
    ):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


async def upstream_head(repo: Path, branch: str) -> str | None:
    """The tip of origin's `branch` -- the item's `base_branch` -- fetched now;
    the last fetched tip when the fetch fails; `repo`'s own HEAD only for the
    default branch when there is no `origin` or it was never fetched. A named
    branch origin cannot give -- gone from it, or never fetched and no origin
    to fetch from -- raises `BaseBranchMissing` instead (Kraft-wz6vz).

    An item's MR targets origin's branch, and the connected checkout is only
    as fresh as its owner's last pull -- Kraft merges on the forge, so nothing
    here ever moves it (Kraft-k647). Forking and rebasing onto that checkout
    started items behind and made every `open_mr` rebase answer "nothing to
    rebase". A failed fetch (offline, credentials the server process cannot
    reach, a concurrent fetch holding the ref lock) still prefers the stale
    remote-tracking ref over the checkout, which may be on another branch or
    carry unpushed commits that would then ride into the MR unseen.

    The refspec is explicit because a `--single-branch` clone's configured
    one may not cover the base branch, leaving its ref unmoved. No prompt
    may reach a foreground `kraft`'s terminal (`promptless_git_env`).
    """
    detail = "there is no origin remote"
    if git_read(repo, "remote", "get-url", "origin", expected_failure=True):
        ref = f"refs/remotes/origin/{branch}"
        env = promptless_git_env(repo)
        try:
            done = await asyncio.to_thread(
                subprocess.run,
                ["git", "fetch", "-q", "origin", f"+refs/heads/{branch}:{ref}"],
                cwd=repo,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            detail = (done.stderr.strip() or "failed") if done.returncode else None
        except subprocess.TimeoutExpired:
            detail = "timed out"
        if detail is not None:
            logger.warning("could not fetch origin/%s in %s: %s", branch, repo, detail)
        # git's own words for a branch origin does not have. A last-fetched
        # copy of a branch that is gone is no base to build on either.
        gone = detail is not None and "couldn't find remote ref" in detail
        tip = git_read(repo, "rev-parse", "--verify", "--quiet", ref, expected_failure=True)
        if tip and not gone:
            return tip
    # The checkout's HEAD stands in only for the default branch, as it always
    # has. A named base branch has no stand-in: the checkout may be on any
    # branch at all.
    if branch != await git.default_branch(repo):
        raise BaseBranchMissing(
            f"base branch {branch!r} cannot be read from origin of {repo} ({detail}); "
            "push it back, or retry the work item on a branch origin has"
        )
    return git_read(repo, "rev-parse", "HEAD")


def item_identity(db, work_item_id: str) -> dict[str, str]:
    """The identity Kraft's own host commits for `work_item_id` are made as:
    its root's (`sandbox.git_identity` of the item's repository). A member is
    a worktree of its connected repository, whose config Kraft never writes
    an identity into (Kraft-ju36l, J3), so every host commit or rebase in a
    member is handed this, per command."""
    row = db.read(
        lambda c: c.execute("SELECT repo FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    return _sandbox.git_identity(Path(row["repo"])) if row is not None else {}


def _committer_env(identity: Mapping[str, str] | None) -> dict[str, str] | None:
    """The process env with `identity`'s committer half: a rebase replays each
    commit under its own author, and only the committer is Kraft's."""
    if not identity:
        return None
    return {**os.environ, **{k: v for k, v in identity.items() if k.startswith("GIT_COMMITTER_")}}


async def refresh_worktree_base(
    worktree: Path,
    repo: Path,
    branch: str,
    *,
    base: str,
    force: bool = False,
    timeout: float | None = None,
    identity: Mapping[str, str] | None = None,
) -> str | None:
    """Rebase `worktree`'s branch onto origin's `base` (`upstream_head`) -- the
    item's `base_branch` -- so a paused or retried item's next commit lands on
    top of whatever landed while the item sat stopped, not the commit it
    forked from.

    Returns the upstream HEAD sha when the rebase moved the branch, or when
    the branch already contains it (the caller stores it as the item's
    `base_ref` either way -- Kraft-jypzx, a rebase done by hand outside Kraft
    must not leave `base_ref` pointing at a commit the branch has moved past).
    Returns None when there was nothing to do: no worktree yet, `repo`'s HEAD
    unreadable, the branch already has an `origin` remote-tracking
    ref (unless `force`), or the worktree has uncommitted changes to tracked
    files -- all are best-effort skips (logged), same posture as
    `ensure_worktree`'s other git steps. The dirty-tree case comes from a
    pause via SIGTERM (`kraft.api.routes.lifecycle._terminate`) catching an
    agent mid-edit with nothing committed yet, and `git rebase` itself
    refuses a dirty tree; the pushed-branch case is covered below. Untracked
    files do not skip it: the lockfiles the setup wrote are set aside for the
    rebase and put back after it (`_set_aside_setup_lockfiles`), and any
    other untracked file the incoming commits would overwrite makes git
    refuse to start, which raises `RebaseBlocked` (an `UnsafeWorktree`: a
    stop for a person, not a conflict).

    Raises `RebaseConflict` (a `RuntimeError` subclass), with the rebase
    already aborted (`git rebase --abort`), on a conflict -- the worktree is
    left clean and consistent either way (unless the abort itself runs past
    `REBASE_ABORT_TIMEOUT_S`, which the message then says); what happens next is the caller's
    call (Kraft-s7c04.23 reverses the "not to dispatch an agent into" stance
    this docstring used to take here, by the human's own call at the design
    gate).

    `force=True` (only `mr_checks`'s conflict-triggered rebase passes this,
    via `mr_rebase_forced` below) skips the "already pushed, don't touch it"
    guard: the caller already read the merge request back and confirmed it
    cannot land as-is, so rewriting a branch a reviewer is looking at is
    exactly what was asked for, not an accident.

    `timeout` (seconds) bounds only the `git rebase` subprocess itself --
    only `mr_rebase` passes one, from its own time cap; the other three
    callers (`/retry`, `/resume`, gate self-retry, `mr_rebase_forced`) pass
    none and keep today's unbounded run. Past it, `git rebase --abort` runs
    the same as a conflict's, and `RebaseTimedOut` (a `RuntimeError`
    subclass, distinct from `RebaseConflict`) is raised instead: a hook or a
    smudge/LFS filter that hangs mid-rebase must not hold the worker's slot
    forever (Kraft-3llig). `upstream_head`'s own fetch has its own fixed 60s
    and is not covered by this -- it is a separate subprocess, bounded before
    this one ever starts.
    """
    if not worktree.is_dir():
        return None
    # Cheaper, purely local check first -- ahead of `upstream_head`'s fetch: a
    # branch already pushed past a prior open_mr gate must not be silently
    # rewritten here -- a reviewer or a pipeline may already be looking at
    # those commits, and this is a quiet auto-refresh on resume/retry, not a
    # deliberate rebase someone asked for. `forge._push` can publish a
    # rewritten branch now (Kraft-z6i8), so this skip is a policy choice about
    # *when* to rewrite, not a workaround for push being unable to.
    if not force and git_read(
        worktree,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/remotes/origin/{branch}",
        expected_failure=True,
    ):
        logger.info("refresh_worktree_base: %s already pushed to origin, skipping", branch)
        return None
    head = await upstream_head(repo, base)
    if not head:
        logger.warning("refresh_worktree_base: rev-parse HEAD failed in %s", repo)
        return None
    # Already up to date: exit 0 means head is already an ancestor of the tip.
    # No rebase needed, but base_ref may still be stale (e.g. a human rebased
    # the branch by hand) -- report head so the caller advances it (Kraft-jypzx).
    if (
        git_read(worktree, "merge-base", "--is-ancestor", head, "HEAD", expected_failure=True)
        is not None
    ):
        return head
    # Tracked changes only. An untracked file is always there in a repo that
    # does not ignore Kraft's own session notes, or the setup's `.venv` and
    # `uv.lock`, so counting one skipped every rebase in such a repo: the
    # merge request opened on a stale base and `on_base_changed` never fired
    # (R10E-01). An untracked file the incoming commits would overwrite
    # makes `git rebase` refuse to start, which is `RebaseBlocked` below.
    if git_read(
        worktree, "status", _sandbox.SUBMODULES_UNENTERED, "--porcelain", "--untracked-files=no"
    ):
        logger.warning("refresh_worktree_base: %s has uncommitted changes, skipping", worktree)
        return None
    # Kraft never rebases over, or aborts, an operation it did not start.
    git.assert_on_branch(worktree, branch)
    aside = await _set_aside_setup_lockfiles(worktree)
    try:
        try:
            done = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", str(worktree), "rebase", head],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_committer_env(identity),
            )
        except subprocess.TimeoutExpired:
            # `subprocess.run` already killed the `git rebase` process on the
            # timeout; its abort left mid-operation, the same clean-up a
            # conflict's abort below does.
            await _abort_rebase(
                worktree,
                RebaseTimedOut(f"git rebase timed out after {timeout:.0f}s for {worktree}"),
            )
        if done.returncode != 0:
            _refuse_unstarted_rebase(worktree, done)
            # git prints the "CONFLICT (content): Merge conflict in <file>" line to
            # stdout; stderr only ever carries the generic "could not apply"/"hint:
            # Resolve all conflicts" text. Join both rather than preferring one, so
            # the conflicting file's name survives into the raised message and the
            # needs_human reason a human reads.
            detail = "\n".join(filter(None, [done.stdout.strip(), done.stderr.strip()]))
            unmerged = git_read(worktree, "diff", "--name-only", "--diff-filter=U") or ""
            await _abort_rebase(
                worktree,
                RebaseConflict(
                    f"git rebase failed for {worktree}: {detail}", tuple(unmerged.splitlines())
                ),
            )
    except BaseException:
        # The rebase's own error is the one to report: a put-back that fails
        # too is logged, never raised in its place.
        try:
            _put_back_setup_lockfiles(worktree, aside)
        except Exception:
            logger.exception("could not put the setup's lockfiles back in %s", worktree)
        raise
    _put_back_setup_lockfiles(worktree, aside)
    return head


class RebaseBlocked(git.UnsafeWorktree):
    """`git rebase` refused to start, so nothing was rebased and there is no
    conflict for an agent to resolve: most often an untracked file the
    incoming commits would overwrite. A stop for a person, never a skip."""


def _refuse_unstarted_rebase(worktree: Path, done: subprocess.CompletedProcess) -> None:
    """Raise `RebaseBlocked` when a failed `git rebase` never started one (it
    left no state behind: `assert_on_branch` saw none before it ran)."""
    gitdir = git_read(worktree, "rev-parse", "--absolute-git-dir")
    if gitdir is None or any(
        os.path.lexists(Path(gitdir, d)) for d in ("rebase-merge", "rebase-apply")
    ):
        return
    detail = "\n".join(filter(None, [done.stdout.strip(), done.stderr.strip()]))
    # The paths first: a stop reason is read by its first line. git lists
    # each one tab-indented under its "would be overwritten" line.
    paths = [line.strip() for line in detail.splitlines() if line.startswith("\t")]
    first = (
        f"untracked {', '.join(paths)} would be overwritten by the base branch; "
        f"move or delete {'it' if len(paths) == 1 else 'them'}, then retry"
        if paths
        else "git would not start the rebase onto the base branch; fix what it names, then retry"
    )
    raise RebaseBlocked(
        f"{first}\ngit rebase could not start in {worktree}, so nothing was rebased: {detail}"
    )


#: Under Kraft's own run dir: where `_set_aside_setup_lockfiles` keeps the
#: setup's lockfiles while a rebase runs, one directory per worktree.
SET_ASIDE = "set-aside"


def set_aside_dir(worktree: Path) -> Path:
    """Where `worktree`'s set-aside lockfiles wait out a rebase: under Kraft's
    run dir, never the worktree's git dir. A sandboxed worker can write its
    git dir, and a link it planted there was followed by the host-side move,
    so the setup's lockfile went into, and came back out of, any directory
    the server user can write. Named after the worktree and a digest of its
    path, so a member's checkout never shares its root's."""
    run_base = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    key = hashlib.sha256(os.fsencode(worktree.resolve())).hexdigest()[:16]
    return run_base / SET_ASIDE / f"{worktree.name}-{key}"


def _move_no_symlinks(src_root: Path, dst_root: Path, rel: str) -> None:
    """Rename `src_root/rel` to `dst_root/rel`, making `dst_root/rel`'s
    parents as needed. Every directory on both sides is opened without
    following a link (`open_dir_no_symlinks`), and the rename itself never
    follows one, so a worker that swaps a component for a link while this
    runs gets an `OSError`, not a write outside its worktree."""
    parts = Path(rel).parts
    src = open_dir_no_symlinks(src_root, parts[:-1])
    try:
        dst = open_dir_no_symlinks(dst_root, parts[:-1], create=True)
        try:
            os.replace(parts[-1], parts[-1], src_dir_fd=src, dst_dir_fd=dst)
        finally:
            os.close(dst)
    finally:
        os.close(src)


def _exists_no_symlinks(root: Path, rel: str) -> bool:
    """Whether `root/rel` exists, as a link or anything else, reached without
    following a link on the way. A parent that is a link counts as there:
    nothing is written through it."""
    parts = Path(rel).parts
    try:
        fd = open_dir_no_symlinks(root, parts[:-1])
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        os.lstat(parts[-1], dir_fd=fd)
    except FileNotFoundError:
        return False
    finally:
        os.close(fd)
    return True


async def _set_aside_setup_lockfiles(worktree: Path) -> list[str]:
    """Move the untouched lockfiles this worktree's setup wrote
    (`forge.git.setup_lockfiles`) into `set_aside_dir`, out of the rebase's
    way: a base that has since committed its own `uv.lock`, as Kraft advises,
    could not be checked out over the setup's untracked one. Returns what
    was moved, for `_put_back_setup_lockfiles`. A path that cannot be moved
    without following a link stays where it is, for the rebase to judge."""
    store_dir = set_aside_dir(worktree)
    # What a server killed mid-rebase left: the setup's next run rewrites it.
    # Kraft's own directory, and `rmtree` unlinks a link inside it rather
    # than following it.
    shutil.rmtree(store_dir, ignore_errors=True)
    # Only a lockfile known to be as the setup left it: one with no digest (a
    # record from before them) may hold the agent's edit, and moving it out
    # of the way of a base's copy would lose that.
    lockfiles = await git.setup_lockfiles(worktree, unknown=False)
    if not lockfiles:
        return []
    store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    moved = []
    for rel in sorted(lockfiles):
        try:
            _move_no_symlinks(worktree, store_dir, rel)
        except OSError as exc:
            logger.warning("not setting %s aside in %s: %s", rel, worktree, exc)
            continue
        moved.append(rel)
    return moved


def _put_back_setup_lockfiles(worktree: Path, moved: list[str]) -> None:
    """Undo `_set_aside_setup_lockfiles`. A path the rebase brought a tracked
    copy to keeps the base's: the setup's is dropped, and its next run (every
    walk entry runs it) works from the committed one. So is one whose way
    back into the worktree passes through a link: the setup writes it again."""
    if not moved:
        return
    store_dir = set_aside_dir(worktree)
    for rel in moved:
        if _exists_no_symlinks(worktree, rel):
            continue
        try:
            _move_no_symlinks(store_dir, worktree, rel)
        except OSError as exc:
            logger.warning("not putting %s back in %s: %s", rel, worktree, exc)
    shutil.rmtree(store_dir, ignore_errors=True)


#: Seconds `git rebase --abort` gets (Kraft-ujep9). Fixed, not the item's time
#: cap: the abort runs *after* that cap may already have fired, and a hook that
#: also fires on the abort (`reference-transaction`, say) must not hold the
#: worker's slot forever in its place.
REBASE_ABORT_TIMEOUT_S = 30.0


async def _abort_rebase(worktree: Path, error: RebaseConflict | RebaseTimedOut) -> NoReturn:
    """`git rebase --abort` in `worktree`, then raise `error`. An abort that
    runs past `REBASE_ABORT_TIMEOUT_S` still raises `error`'s own class, so the
    caller's handling is unchanged, but says the worktree was left mid-rebase:
    the one case where `refresh_worktree_base` does not leave it clean."""
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", str(worktree), "rebase", "--abort"],
            capture_output=True,
            text=True,
            timeout=REBASE_ABORT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise type(error)(
            f"{error}\n`git rebase --abort` also timed out after "
            f"{REBASE_ABORT_TIMEOUT_S:.0f}s; {worktree} was left mid-rebase for a human"
        ) from None
    raise error


async def mr_rebase(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int,
    repo: str,
    worktree: str,
    branch: str,
    head_sha: str | None = None,
    has_rebase_bounce: bool = False,
    #: The tightest time cap over this task (`caps.at_launch`), the same one
    #: `adapters.subprocess.run_task` kills a launched process at. Threaded
    #: into `refresh_worktree_base`'s own `git rebase` subprocess as its
    #: `timeout=` -- a hanging pre-rebase hook or smudge/LFS filter must not
    #: hold the worker's slot forever (Kraft-3llig). `None` (a chain that
    #: predates this, or a task with no cap resolved) means unbounded, the
    #: same as every other `refresh_worktree_base` caller.
    time_cap: _caps.Deadline | None = None,
) -> str:
    """Rebase onto the item's base branch right before `open_mr`, so an item
    that ran straight through the chain -- no pause, no `/retry` -- doesn't
    open its MR however many commits behind (Kraft-4bgg). A thin wrapper:
    `refresh_worktree_base` is already the whole implementation, shared with
    `/resume` and `/retry`.

    A conflict's `RuntimeError` is deliberately left to propagate rather than
    caught here: `_measure_node` already folds a raised exception into this
    node's ordinary failure path, the same `needs_human` outcome `/resume`
    and `/retry` reach by catching it and calling `mark_needs_human`
    themselves -- one behavior, this call site doesn't need its own copy of
    that catch.

    When the node declares `on_base_changed` and the rebase moved the base,
    that is reported (`BASE_MOVED`) rather than swallowed, so the node stops
    before its later steps run against a base nobody re-verified.

    A workspace item's changed members are rebased first, onto their own
    default branches (`_rebase_members`, Kraft-ei38e), then the root, all
    under the same time cap. A member's conflict, timeout or missing base
    ends the task exactly as the root's would; its message names the member.
    """
    from kraft.executor.context import BASE_MOVED  # executor imports this module

    base = await base_branch(db, work_item_id, Path(repo))
    prior_base_ref = db.read(
        lambda c: c.execute(
            "SELECT base_ref FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )["base_ref"]

    def remaining() -> float | None:
        return max(0.0, time_cap.at - _caps.monotonic()) if time_cap is not None else None

    try:
        moved = await _rebase_members(db, work_item_id, Path(worktree), branch, base, remaining)
        try:
            new_head = await refresh_worktree_base(
                Path(worktree), Path(repo), branch, base=base, timeout=remaining()
            )
            # `refresh_worktree_base` now reports the upstream head even when
            # the branch already contained it (Kraft-jypzx), so `base_ref` can
            # catch up after a hand-rebase -- but that is not a rebase *this*
            # call performed, and must not trip `on_base_changed`'s bounce.
            if new_head == prior_base_ref:
                new_head = None
        except RebaseConflict as exc:
            raise _explain_gitlink_conflict(exc, {rel for rel, _ in moved}, branch, base) from None
    except (BaseBranchMissing, git.UnsafeWorktree) as exc:
        # Nothing an agent could fix: `config_error` stops the item for a
        # person rather than sending a fix loop round against a missing base.
        return await _record_done(
            db,
            run_dirs,
            session_id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            round=round,
            log=f"{exc}\n",
            status="config_error",
            head_sha=head_sha,
        )
    except RebaseTimedOut as exc:
        # Recorded the same way a time cap stops any other task
        # (`dispatch.time_capped_session`, `adapters.subprocess.run_task`'s
        # own mid-run kill): the session exits `capped_out`, `caps.REACHED`
        # names the scope and cap, and the walk stops for a human -- not a
        # new stop kind, the existing one.
        assert time_cap is not None  # only a passed time_cap can raise this
        await _record_done(
            db,
            run_dirs,
            session_id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            round=round,
            log=f"{exc}\n",
            status="capped_out",
            head_sha=head_sha,
        )
        await db.write(
            lambda c: events.append(
                c,
                work_item_id,
                _caps.REACHED,
                time_cap.hit.payload(node_id=node_id, task=hook_point, session_id=session_id),
            )
        )
        return _caps.TIME_CAPPED
    # A member's move is never persisted: `base_ref` is the root's (Kraft-puqxq).
    log = "".join(f"[{rel}] rebased {branch} onto {head}\n" for rel, head in moved)
    if new_head:
        await db.write(lambda c: store.set_base_ref(c, work_item_id, new_head))
        log += f"rebased {branch} onto {new_head}\n"
    log = log or "nothing to rebase\n"
    recorded = await _record_done(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        log=log,
        head_sha=head_sha,
    )
    # The session is `done` either way -- the rebase itself succeeded.
    return BASE_MOVED if ((new_head or moved) and has_rebase_bounce) else recorded


async def _rebase_members(
    db, work_item_id: str, root: Path, branch: str, base: str, remaining
) -> list[tuple[str, str]]:
    """Rebase each changed member of a workspace item onto its own default
    branch before its draft opens (Kraft-ei38e): a member's branch is cut from
    the root's pinned gitlink, which can lag the member's origin by any amount.
    "Changed" is `open_mr`'s own test -- still pending, commits beyond its
    base -- so a member that gets no merge request is left alone. Returns
    `(mount path, new base)` per member that moved; none for any other item.

    A moved member leaves the root's committed gitlink at its old head, and
    `assert_clean` would then refuse the root's own draft. So when the root
    had committed the member's old head, the pointer update is committed in
    the root -- one commit, only those mounts -- the same pointer move the
    straggler sweep already commits after every task. `mr_rebase` rebases the
    root after this, so its rebase carries that commit."""
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM work_item_repos WHERE work_item_id = ? AND role = 'submodule' "
            "AND merge_state = 'pending' ORDER BY merge_rank",
            (work_item_id,),
        ).fetchall()
    )
    if rows:
        # The repoint below commits in the root, onto whatever its HEAD names.
        git.assert_on_branch(root, branch)
    moved: list[tuple[str, str]] = []
    repoint: list[str] = []
    try:
        for r in rows:
            sub, rel = Path(r["repo_path"]), r["submodule_path"]
            sub_base = await base_branch(db, work_item_id, sub, member=True)
            if await git.commits_ahead(sub, branch, sub_base) == 0:
                continue
            old = git_read(sub, "rev-parse", "HEAD")
            head = await refresh_worktree_base(
                sub,
                sub,
                branch,
                base=sub_base,
                timeout=remaining(),
                identity=item_identity(db, work_item_id),
            )
            # `refresh_worktree_base` now reports `sub_base`'s head even when
            # the member's branch already contained it as an ancestor
            # (Kraft-jypzx) -- that is not a move (`old` unchanged), and must
            # not be reported as one: it would fire `mr_rebase`'s
            # `on_base_changed` bounce on every ordinary run of a workspace
            # item with any pending member.
            if head and git_read(sub, "rev-parse", "HEAD") != old:
                moved.append((rel, head))
                if git_read(root, "rev-parse", f"HEAD:{rel}", expected_failure=True) == old:
                    repoint.append(rel)
    finally:
        # Even when a later member raises: a member already moved has nothing
        # left to rebase on the retry, so this is its only repoint, and a
        # root left at its old head would fail `assert_clean` for good.
        _commit_paths(root, repoint, "chore: repoint members after pre-MR rebase", base)
    return moved


def _explain_gitlink_conflict(
    exc: RebaseConflict, moved: set[str], branch: str, base: str
) -> RebaseConflict:
    """`exc`, with a sentence saying how to resolve it when the only paths the
    root's rebase left unmerged are pointers `_rebase_members` just moved: the
    root's base moved the same pointer (Kraft-xvwye). git's own text is kept."""
    if not exc.paths or not set(exc.paths) <= moved:
        return exc
    how = " ".join(
        f"The base branch {base} also moved member {rel}'s pointer: rebase {branch} in "
        f"the root onto {base}, take the member's rebased head for {rel} "
        f"(`git -C {rel} checkout {branch}`, then `git add {rel}`), continue the rebase, "
        "and retry the item."
        for rel in exc.paths
    )
    return RebaseConflict(f"{exc}\n{how}", exc.paths)


async def mr_rebase_forced(
    worktree: Path,
    repo: Path,
    branch: str,
    base: str,
    identity: Mapping[str, str] | None = None,
) -> str | None:
    """`refresh_worktree_base` with the pushed-branch guard off, for
    `ci_poll`'s conflict path (Kraft-9h7v) -- the one caller that has already
    confirmed, from the forge's own re-fetched read, that this branch cannot
    land as it stands."""
    return await refresh_worktree_base(
        worktree, repo, branch, base=base, force=True, identity=identity
    )


async def start_session(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int,
    head_sha: str | None = None,
    reuse_if_waiting: bool = False,
) -> tuple[str, Path, Path]:
    """Create the session row before the in-process work starts, not after.

    `forge.run_task` can now sit in `_poll_ci` for up to `poll_timeout`
    (default 1800s); recording nothing until it returns left pause, abandon
    and reattach with no row to find for the whole wait -- pause silently
    no-op'd and the chain walked on into merge (Kraft-41b), and a restart
    mid-poll left nothing to reattach (Kraft-7xt). Splitting the row's
    creation from its exit is what `adapters.subprocess.run_task` already
    does for a spawned child; this gives an in-process task the same shape.

    Returns `(id, log_path, result_path)`. Normally `id == session_id`; with
    `reuse_if_waiting=True` (Kraft-ivh1) a still-`'waiting'` row for this
    exact (item, node, hook point, round) is reused instead, and the
    returned id/paths are that row's, not `session_id`'s -- the caller must
    use the returned id, not `session_id`, for the rest of its work.
    """
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = run_dirs.results / f"{session_id}.json"
    actual_id, log_str, result_str = await db.write(
        lambda c: store.create_session(
            c,
            id=session_id,
            work_item_id=work_item_id,
            node_id=node_id,
            hook_point=hook_point,
            log_path=str(log_path),
            result_path=str(result_path),
            round=round,
            head_sha=head_sha,
            reuse_if_waiting=reuse_if_waiting,
        )
    )
    return actual_id, Path(log_str), Path(result_str)


async def finish_session(
    db,
    log_path: Path,
    result_path: Path,
    *,
    session_id: str,
    status: str,
    log: str,
    findings: list[dict] | None = None,
    reused: bool = False,
) -> str:
    """Write the log and close out a session `start_session` already created.

    `findings` is None for every caller that predates this (an agent/
    subprocess task writes its own result file, and every existing builtin
    has nothing to report) -- writing `result_path` only when it is given
    keeps `_findings.parse`'s "missing file -> no findings" behaviour for
    every one of them.

    `reused` (Kraft-ivh1): a session `create_session(reuse_if_waiting=True)`
    handed back instead of inserting fresh already has a log on disk from an
    earlier poll of the same wait episode -- appended to here, not
    overwritten, so on.ci.poll's repeated "pipeline pending" reads land in
    one running log instead of replacing each other. The `.times` sidecar
    is extended the same way: only the newly-added lines get a fresh
    timestamp; ones already on disk keep theirs.
    """
    previous = log_path.read_text() if reused and log_path.exists() else ""
    log_path.write_text(previous + log if previous else log)
    if findings is not None:
        result_path.write_text(json.dumps({"findings": findings}))
    # A subprocess session gets its per-line timestamps from the adapter's drain
    # thread (`adapters/subprocess._watch_log`), which stamps each line as it
    # appears. A builtin writes its whole log in one call and has no drain
    # thread, so without this its lines read back with a blank time column --
    # which `env_setup` used to have, before it stopped going through the
    # subprocess adapter.
    now = datetime.now(UTC).isoformat()
    try:
        times_path = logs.times_path(log_path)
        previous_line_count = len(logs.split_lines(previous)) if previous else 0
        new_entries = [
            json.dumps({"n": previous_line_count + n, "t": now}) + "\n"
            for n in range(len(logs.split_lines(log)))
        ]
        if reused and times_path.exists():
            with times_path.open("a") as fh:
                fh.writelines(new_entries)
        else:
            times_path.write_text("".join(new_entries))
    except OSError:
        # Best-effort, the same call `_watch_log` makes about its own sidecar: a
        # missing time column is cosmetic, not a reason to fail the node.
        logger.warning("no log time sidecar for session %s", session_id)
    await db.write(lambda c: store.session_exited(c, session_id, status))
    return status


async def _record_done(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int,
    log: str,
    status: str = "done",
    head_sha: str | None = None,
) -> str:
    """A session row for a builtin that did its work in-process, before and
    after in one call. Shared so a builtin's bookkeeping cannot drift from
    `noop`'s.

    `status` defaults to "done" because every original caller succeeded by
    construction. A forge task can genuinely fail — a red pipeline — and
    recording that as done would let the chain walk into the merge node.
    """
    _, log_path, result_path = await start_session(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        head_sha=head_sha,
    )
    return await finish_session(
        db, log_path, result_path, session_id=session_id, status=status, log=log
    )


async def prepare_runtime(
    worktree: Path,
    repo: Path,
    repo_entry: RepoEntry | None,
    *,
    sandbox: dict | None = None,
    checkout: _sandbox.Checkout | None = None,
) -> str:
    """Re-prepare an existing worktree and say what happened.

    What the `env_setup` node used to do, minus the session bookkeeping: V1 has
    no builtin to bind it to (`BuiltinAction` names one action, and it is not
    this), so it is implicit runtime preparation run by `walk.run_once` right
    after `ensure_worktree`, before the first node dispatches.

    Called on every entry into the walk, not only at worktree creation, for the
    reason `run_setup_command` was extracted in the first place: a rebase can
    land a new lockfile and nothing else rebuilds the environment.

    Raises `RuntimeError` exactly where `run_setup_command` does, so the
    caller's own "the node that was about to dispatch could not start"
    handling covers it.
    """
    # No entry, nothing declared to re-run: `ensure_worktree` already refused a
    # repo without a `setup_command` when it cut this worktree.
    setup_log = (
        await _prepare(worktree, repo, repo_entry, sandbox=sandbox, checkout=checkout)
        if repo_entry is not None
        else ""
    )
    # Stateless like `_uncarried_local_files`: a configured entry whose source
    # exists but whose copy does not was refused, root-level or nested -- and
    # it is the operator's own config, so it is named apart from the noise
    # below rather than only in the server's logger (Kraft-hro48).
    local_files = repo_entry.local_files if repo_entry is not None else []
    refused = [r for r in local_files if (repo / r).exists() and not (worktree / r).exists()]
    missing = [
        n
        for n in await asyncio.to_thread(_uncarried_local_files, repo, worktree)
        if n not in refused
    ]
    report = f"worktree ready at {worktree}\n{setup_log}"
    if refused:
        report += (
            "\nlocal_files entries NOT carried into this worktree -- only a file the "
            "worktree's .gitignore covers is copied, so ignore these or drop them:\n"
            + "".join(f"  {r}\n" for r in refused)
        )
    if missing:
        # Informational, not a to-do list: on most repos this names things
        # like `.DS_Store` or `.testmondata` that nobody would ever carry.
        # No hardcoded skip list for those, though -- a per-name filter here
        # is exactly the per-repo maintenance treadmill `local_files` was
        # built to avoid, and it would just be wrong on the next repo.
        report += (
            "\nuntracked root-level files present in the repo but not in this "
            "worktree (git worktree add only checks out tracked content) -- "
            "most of these are irrelevant noise, worth a glance only if the "
            "build actually needs one of them:\n" + "".join(f"  {n}\n" for n in missing)
        )
    return report
