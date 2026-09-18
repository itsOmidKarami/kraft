from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from kraft import logs, store
from kraft.adapters.forge import git
from kraft.config import git_read, main_ignore_args
from kraft.worker_env import worker_env

logger = logging.getLogger(__name__)


class RebaseConflict(RuntimeError):
    """`refresh_worktree_base` could not replay this branch onto its new
    base. A `RuntimeError` subclass so every existing `except RuntimeError`
    around this call keeps behaving as it did (Kraft-s7c04.23); the type
    exists so the three callers that can now *act* on a conflict can tell
    one apart from any other git failure without matching on message text."""


def _commit_paths(worktree: Path, paths: list[str], message: str) -> None:
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
    worktree (and its submodules) via `_pin_identity` at creation (Kraft-cppp),
    which is where every other commit on this branch gets its author.

    Best effort. A failure logs a warning and returns: a document that did not
    commit becomes a dirty-tree failure at `open_mr` with git's own message
    already in the log, which is strictly better than failing worktree creation.

    `main_ignore_args`, the same override `forge._work_product_pathspec`
    relies on, rides along on the `add`: a spec/plan attachment copied to its
    original relative path can land under `docs/superpowers/` or
    `.engineering/`, and if `main` ignores that root but this worktree's own
    checked-out `.gitignore` predates the rule (Kraft-vu26), a plain `git add`
    stages it anyway. Staging nothing here is correct, not a failure -- the
    diff-cached check below already treats "nothing staged" as a no-op, and
    the attachment stays on disk for the agent to read either way.
    """
    if not paths:
        return

    with main_ignore_args(worktree) as ignore_args:

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


def restore_branch(worktree: Path, branch: str) -> None:
    """Force the worktree back onto `branch` if an agent task left it
    somewhere else, aborting any in-progress merge first (Kraft-v5qd).

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

    Best effort throughout, like `commit_stragglers`: a `restore_branch`
    that could not recover is a worktree already too broken for a log line
    to fix, and the failure it hides here surfaces the same way it always
    did -- the next node's own git command refuses on the same tree.
    """
    with main_ignore_args(worktree) as ignore_args:

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", *ignore_args, *args], cwd=str(worktree), capture_output=True, text=True
            )

        current = git("rev-parse", "--abbrev-ref", "HEAD")
        if current.returncode != 0 or current.stdout.strip() == branch:
            return
        logger.warning(
            "worktree %s left on %r instead of %r after an agent task; restoring",
            worktree,
            current.stdout.strip(),
            branch,
        )
        git("merge", "--abort")  # no-op, exit nonzero, if no merge is in progress
        checked_out = git("checkout", "-f", branch)
        if checked_out.returncode != 0:
            logger.warning(
                "could not restore %s to %r: %s", worktree, branch, checked_out.stderr.strip()
            )


def _copy_attachments(
    repo: Path, worktree: Path, attachments: list[dict], work_item_id: str
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
        if not src.is_file():
            # Not a skip. `source` is Kraft's own copy since Kraft-eqgn, so a
            # file missing here means Kraft lost it — and the gate this
            # document justified was trimmed at intake and cannot be put back.
            # Continuing would run the item without a document it promised, and
            # without a gate to notice. Should be unreachable; loud if not.
            raise FileNotFoundError(
                f"the {attachment['kind']} attachment for {work_item_id} is missing "
                f"from Kraft's storage at {src}; this work item's "
                f"{attachment['kind']} gate was trimmed at intake and cannot be "
                "restored — re-file the work item"
            )
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
    the worktree's own config is outranked by `main_ignore_args`' `-c` -- which
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
        # The worktree's own rules, not `main`'s: `main_ignore_args` widens what
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
    `worktree`'s own git config, plus every submodule's separate gitdir.

    `ensure_worktree` otherwise never establishes a commit identity, relying
    on the worktree inheriting the repo's config -- true until it isn't: a
    repo with no `[user]` block anywhere in its config chain resolves
    nothing, and the agent told to commit before it exits invents an identity
    to get unblocked (`kraft@local` was seen in the wild), which later nodes
    then read back off the branch and treat as sanctioned (Kraft-mxdx).

    Raised, not logged: a missing identity means every commit on this branch
    is about to either fail or fabricate one, so failing worktree creation
    with the missing key named beats a push rejection eight nodes later.

    A submodule's gitdir lives separately (under
    `.git/worktrees/<id>/modules/...`) and does not inherit config the way
    the main worktree does, so it needs the same pin repeated into it.
    """
    name = git_read(repo, "config", "--get", "user.name", expected_failure=True)
    email = git_read(repo, "config", "--get", "user.email", expected_failure=True)
    if not name or not email:
        missing = "user.name" if not name else "user.email"
        raise RuntimeError(f"no {missing} configured in {repo}; set it before Kraft can commit")

    def pin(target: Path) -> None:
        subprocess.run(
            ["git", "-C", str(target), "config", "user.name", name],
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(target), "config", "user.email", email],
            capture_output=True,
            text=True,
        )

    pin(worktree)
    submodules = git_read(
        worktree,
        "submodule",
        "foreach",
        "--quiet",
        "--recursive",
        "echo $sm_path",
        expected_failure=True,
    )
    for line in (submodules or "").splitlines():
        line = line.strip()
        if line:
            pin(worktree / line)


async def _setup_submodules(
    db, repo: Path, worktree: Path, branch: str, work_item_id: str, paths: list[str]
) -> None:
    """`git submodule update --init` only the declared paths (design 3 step 2
    -- never blanket), check the item's branch out inside each one, and write
    one `work_item_repos` row per repo -- deepest submodule first, root last
    (3a) -- so the forge nodes later know what to open a merge request
    against and in what order.

    A submodule is a regular working copy once initialized, not a bare repo,
    so getting the item's branch into it is a plain `checkout`/`checkout -b`
    -- design 06's "git worktree add inside each submodule" is shorthand for
    "this submodule ends up on the item's branch", not a second linked
    worktree, which a submodule path does not support the way the root does.
    """
    ordered = store.merge_rank_order(paths)
    init = await asyncio.to_thread(
        subprocess.run,
        # `-c protocol.file.allow=always`: git 2.38+ refuses a `file://`
        # submodule URL by default (a supply-chain hardening default, not
        # something specific to this repo's own submodules). A real remote is
        # https/ssh and is unaffected; the fixtures this plan's own tests
        # build (`make_repo_with_submodule`) use plain filesystem paths.
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "--init",
            "--",
            *ordered,
        ],
        cwd=str(worktree),
        capture_output=True,
        text=True,
    )
    if init.returncode != 0:
        detail = init.stderr.strip() or init.stdout.strip()
        raise RuntimeError(f"git submodule update --init failed for {work_item_id}: {detail}")
    for rank, rel in enumerate(ordered, start=1):
        sub = worktree / rel
        exists = git_read(
            sub, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", expected_failure=True
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
        await db.write(
            lambda c, p=str(sub), r=rel, rk=rank: store.add_repo(
                c,
                work_item_id=work_item_id,
                repo_path=p,
                role="submodule",
                submodule_path=r,
                merge_rank=rk,
            )
        )
    root_rank = len(ordered) + 1
    await db.write(
        lambda c, p=str(worktree), rk=root_rank: store.add_repo(
            c, work_item_id=work_item_id, repo_path=p, role="root", merge_rank=rk
        )
    )


async def _discard_worktree(repo: Path, worktree: Path) -> str | None:
    """Remove a worktree whose setup failed, keeping its branch. Returns a
    reason when the directory survives, or None.

    `ensure_worktree` returns early when the directory exists, so a worktree
    left behind by a failed setup would make the next attempt skip setup
    entirely. The branch stays: a rejected gate already removes a worktree and
    keeps its branch, and the commits on it are not this failure's business.
    Not best-effort like the abandon path -- a survivor is reported into the
    error the caller is about to raise, because silence here is the bug.
    """
    for args in (
        ["git", "worktree", "remove", "--force", str(worktree)],
        ["git", "worktree", "prune"],
    ):
        await asyncio.to_thread(subprocess.run, args, cwd=repo, capture_output=True, text=True)
    return None if not worktree.exists() else f"{worktree} still present"


async def ensure_worktree(
    db,
    run_dirs,
    *,
    repo: str,
    work_item_id: str,
    repo_entry: dict | None,
    attachments: list[dict] | None = None,
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
    # poisoned entry (malformed repos.yaml) raises ConfigError on `.get`, and
    # reading it only after the worktree exists would leave that worktree
    # behind for a later `kraft item retry` to find via the early return
    # above -- skipping setup_command entirely and dispatching into an
    # unprepared worktree.
    entry = repo_entry or {}
    local_files = entry.get("local_files") or []
    # Pin the base before the worktree exists, so the early return above
    # guarantees a crashed-and-retried run never re-pins to a moved HEAD.
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref, branch, id FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    head = None
    if row is not None and row["base_ref"] is None:
        head = await upstream_head(Path(repo))
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
    exists = git_read(
        Path(repo),
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}",
        expected_failure=True,
    )
    # A worktree directory deleted out from under git leaves a stale
    # administrative entry that makes `worktree add` refuse the same path.
    await asyncio.to_thread(
        subprocess.run, ["git", "worktree", "prune"], cwd=repo, capture_output=True, text=True
    )
    args = ["git", "worktree", "add"]
    # A sha, not `origin/<default>`: a remote-tracking start point would make
    # git set it as the new branch's upstream.
    if exists:
        args += [str(worktree), branch]
    else:
        args += [str(worktree), "-b", branch] + ([head] if head else [])
    done = await asyncio.to_thread(subprocess.run, args, cwd=repo, capture_output=True, text=True)
    if done.returncode != 0:
        # Raised, not returned: this runs outside a session, so there is no
        # session status to carry the failure. `kraft.api.deps.guard` turns it into
        # needs_human with the git stderr in the reason.
        detail = done.stderr.strip() or done.stdout.strip()
        raise RuntimeError(f"git worktree add failed for {work_item_id}: {detail}")
    await asyncio.to_thread(_pin_identity, Path(repo), worktree, work_item_id)
    await asyncio.to_thread(
        _copy_attachments, Path(repo), worktree, attachments or [], work_item_id
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
        await run_setup_command(worktree, Path(repo), repo_entry)
    except RuntimeError as exc:
        # Only the creating caller may throw away a worktree other nodes are
        # already using, so the discard lives here and not in the helper.
        left = await _discard_worktree(Path(repo), worktree)
        suffix = f" (and its worktree could not be removed: {left})" if left else ""
        raise RuntimeError(f"{exc}{suffix}") from exc
    decl = db.read(
        lambda c: c.execute(
            "SELECT submodules FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    submodules = json.loads(decl["submodules"]) if decl and decl["submodules"] else []
    if submodules:
        await _setup_submodules(db, Path(repo), worktree, branch, work_item_id, submodules)
    return worktree


async def run_setup_command(worktree: Path, repo: Path, repo_entry: dict | None) -> str:
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
    """
    cmd = (repo_entry or {}).get("setup_command")
    if cmd is None:
        raise RuntimeError(
            f"no setup_command declared for {repo} in repos.yaml, so {worktree.name}'s "
            'worktree cannot be prepared. Declare one (use "" for a repo that '
            "deliberately needs no preparation)."
        )
    if not cmd:
        return ""
    done = await asyncio.to_thread(
        subprocess.run,
        cmd,
        shell=True,
        cwd=worktree,
        env=worker_env(repo_entry),
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        detail = done.stderr.strip() or done.stdout.strip()
        raise RuntimeError(f"setup command failed for {worktree.name}: {cmd!r}: {detail}")
    return f"$ {cmd}\n{done.stdout}{done.stderr}"


async def upstream_head(repo: Path) -> str | None:
    """The tip of origin's default branch, fetched now; the last fetched tip
    when the fetch fails; `repo`'s own HEAD only when there is no `origin`
    or it was never fetched.

    An item's MR targets origin's branch, and the connected checkout is only
    as fresh as its owner's last pull -- Kraft merges on the forge, so nothing
    here ever moves it (Kraft-k647). Forking and rebasing onto that checkout
    started items behind and made every `open_mr` rebase answer "nothing to
    rebase". A failed fetch (offline, credentials the server process cannot
    reach, a concurrent fetch holding the ref lock) still prefers the stale
    remote-tracking ref over the checkout, which may be on another branch or
    carry unpushed commits that would then ride into the MR unseen.

    The refspec is explicit because a `--single-branch` clone's configured
    one may not cover the default branch, leaving its ref unmoved. No prompt
    may reach a foreground `kraft`'s terminal: `GIT_TERMINAL_PROMPT=0` for
    git's own, ssh `BatchMode` for passphrases and unknown hosts -- unless the
    user already chose an ssh command, which an env var would override.
    """
    if git_read(repo, "remote", "get-url", "origin", expected_failure=True):
        default = await git.default_branch(repo)
        ref = f"refs/remotes/origin/{default}"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        if "GIT_SSH_COMMAND" not in env and not git_read(
            repo, "config", "core.sshCommand", expected_failure=True
        ):
            env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
        try:
            done = await asyncio.to_thread(
                subprocess.run,
                ["git", "fetch", "-q", "origin", f"+refs/heads/{default}:{ref}"],
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
            logger.warning("could not fetch origin/%s in %s: %s", default, repo, detail)
        tip = git_read(repo, "rev-parse", "--verify", "--quiet", ref, expected_failure=True)
        if tip:
            return tip
    return git_read(repo, "rev-parse", "HEAD")


async def refresh_worktree_base(
    worktree: Path, repo: Path, branch: str, *, force: bool = False
) -> str | None:
    """Rebase `worktree`'s branch onto origin's default branch (`upstream_head`),
    so a paused or retried item's next commit lands on top of whatever landed
    while the item sat stopped, not the commit it forked from.

    Returns the new HEAD sha when the rebase moved the branch (the caller
    stores it as the item's `base_ref`), or None when there was nothing to
    do: no worktree yet, `repo`'s HEAD unreadable, the branch already
    contains that HEAD, the branch already has an `origin` remote-tracking
    ref (unless `force`), or the worktree has uncommitted changes -- all are
    best-effort skips (logged), same posture as `ensure_worktree`'s other git
    steps. The dirty-tree case comes from a pause via SIGTERM
    (`kraft.api.routes.lifecycle._terminate`) catching an agent mid-edit with
    nothing committed yet, and `git rebase` itself refuses a dirty tree; the
    pushed-branch case is covered below.

    Raises `RebaseConflict` (a `RuntimeError` subclass), with the rebase
    already aborted (`git rebase --abort`), on a conflict -- the worktree is
    left clean and consistent either way; what happens next is the caller's
    call (Kraft-s7c04.23 reverses the "not to dispatch an agent into" stance
    this docstring used to take here, by the human's own call at the design
    gate).

    `force=True` (only `mr_checks`'s conflict-triggered rebase passes this,
    via `mr_rebase_forced` below) skips the "already pushed, don't touch it"
    guard: the caller already read the merge request back and confirmed it
    cannot land as-is, so rewriting a branch a reviewer is looking at is
    exactly what was asked for, not an accident.
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
    head = await upstream_head(repo)
    if not head:
        logger.warning("refresh_worktree_base: rev-parse HEAD failed in %s", repo)
        return None
    # Already up to date: exit 0 means head is already an ancestor of the tip.
    if (
        git_read(worktree, "merge-base", "--is-ancestor", head, "HEAD", expected_failure=True)
        is not None
    ):
        return None
    if git_read(worktree, "status", "--porcelain"):
        logger.warning("refresh_worktree_base: %s has uncommitted changes, skipping", worktree)
        return None
    done = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(worktree), "rebase", head],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", str(worktree), "rebase", "--abort"],
            capture_output=True,
            text=True,
        )
        # git prints the "CONFLICT (content): Merge conflict in <file>" line to
        # stdout; stderr only ever carries the generic "could not apply"/"hint:
        # Resolve all conflicts" text. Join both rather than preferring one, so
        # the conflicting file's name survives into the raised message and the
        # needs_human reason a human reads.
        detail = "\n".join(filter(None, [done.stdout.strip(), done.stderr.strip()]))
        raise RebaseConflict(f"git rebase failed for {worktree}: {detail}")
    return head


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
) -> str:
    """Rebase onto origin's default branch right before `open_mr`, so an item
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

    When the node declares `rebase_bounce_to` and the rebase moved the base,
    that is reported (`BASE_MOVED`) rather than swallowed, so the node stops
    before its later steps run against a base nobody re-verified.
    """
    from kraft.executor.context import BASE_MOVED  # executor imports this module

    new_head = await refresh_worktree_base(Path(worktree), Path(repo), branch)
    if new_head:
        await db.write(lambda c: store.set_base_ref(c, work_item_id, new_head))
        log = f"rebased {branch} onto {new_head}\n"
    else:
        log = "nothing to rebase\n"
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
    return BASE_MOVED if (new_head and has_rebase_bounce) else recorded


async def mr_rebase_forced(worktree: Path, repo: Path, branch: str) -> str | None:
    """`refresh_worktree_base` with the pushed-branch guard off, for
    `ci_poll`'s conflict path (Kraft-9h7v) -- the one caller that has already
    confirmed, from the forge's own re-fetched read, that this branch cannot
    land as it stands."""
    return await refresh_worktree_base(worktree, repo, branch, force=True)


async def scan_submodules(
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
    head_sha: str | None = None,
) -> str:
    """Design 3a: catch a submodule the agent touched but the item never
    declared, give it a `work_item_repos` row and a pinned identity, before
    `open_mr` would otherwise have to refuse over it.

    Runs in its own node right after `implementation` (see `default.yaml`),
    so a plain green `verify` never masks a change `open_mr`
    would reject three nodes later -- this is what would have rescued work
    item 9d0ab38ff3c9439b90506df0f6966660, which declared nothing.
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
    wt = Path(worktree)
    known = {
        r["submodule_path"]
        for r in db.read(
            lambda c: c.execute(
                "SELECT submodule_path FROM work_item_repos "
                "WHERE work_item_id = ? AND role = 'submodule'",
                (work_item_id,),
            ).fetchall()
        )
        if r["submodule_path"]
    }
    # '+' means the submodule's checked-out commit no longer matches what the
    # superproject's index records -- new commits sitting in the worktree,
    # exactly the shape that went unreported before this plan.
    raw = git_read(wt, "submodule", "status", expected_failure=True) or ""
    touched = set()
    for line in raw.splitlines():
        if not line or line[0] != "+":
            continue
        parts = line[1:].split()
        if len(parts) >= 2:
            touched.add(parts[1])

    undeclared = sorted(touched - known)
    if not undeclared:
        return await finish_session(
            db,
            log_path,
            result_path,
            session_id=session_id,
            status="done",
            log="no undeclared submodule changes\n",
        )

    existing = db.read(
        lambda c: c.execute(
            "SELECT COUNT(*) AS n FROM work_item_repos WHERE work_item_id = ?", (work_item_id,)
        ).fetchone()
    )
    next_rank = existing["n"] + 1
    log = ""
    for rel in undeclared:
        sub = wt / rel
        await asyncio.to_thread(_pin_identity, Path(repo), sub, work_item_id)
        rank = next_rank
        await db.write(
            lambda c, p=str(sub), r=rel, rk=rank: store.add_repo(
                c,
                work_item_id=work_item_id,
                repo_path=p,
                role="submodule",
                submodule_path=r,
                merge_rank=rk,
            )
        )
        log += f"found undeclared submodule change: {rel}\n"
        next_rank += 1
    # The root row (written by ensure_worktree, if this item declared any
    # submodule at all) now has to merge after these too.
    final_rank = next_rank
    await db.write(
        lambda c, rk=final_rank: c.execute(
            "UPDATE work_item_repos SET merge_rank = ? WHERE work_item_id = ? AND role = 'root'",
            (rk, work_item_id),
        )
    )
    return await finish_session(
        db, log_path, result_path, session_id=session_id, status="done", log=log
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


async def env_setup(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    repo: str,
    repo_entry: dict | None = None,
    round: int = 0,
    attachments: list[dict] | None = None,
    head_sha: str | None = None,
) -> str:
    # `ensure_worktree` copies attachments itself now (Kraft-pqu fallout: the
    # copy has to exist before `spec`/`plan`, which run ahead of this node in
    # `default.yaml`). By the time this node dispatches, `executor.run`/
    # `resume` have already called `ensure_worktree` with the same attachments,
    # so this call is the early-return path and does no git or copy work in
    # the ordinary case — it only does real work when a test or a future chain
    # calls `env_setup` without that prior call having happened.
    worktree = await ensure_worktree(
        db,
        run_dirs,
        repo=repo,
        work_item_id=work_item_id,
        attachments=attachments,
        repo_entry=repo_entry,
    )
    # No entry, nothing declared to re-run: `ensure_worktree` already refused a
    # repo without a `setup_command` when it cut this worktree.
    setup_log = (
        await run_setup_command(worktree, Path(repo), repo_entry) if repo_entry is not None else ""
    )
    missing = await asyncio.to_thread(_uncarried_local_files, Path(repo), worktree)
    report = f"worktree ready at {worktree}\n{setup_log}"
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
    return await _record_done(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point="on.env.prepare",
        round=round,
        log=report,
        head_sha=head_sha,
    )


async def noop(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int = 0,
    head_sha: str | None = None,
) -> str:
    """Placeholder task for a hook with no plugin yet: records a done session, does no work."""
    return await _record_done(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point=hook_point,
        round=round,
        log=f"noop placeholder for {hook_point}\n",
        head_sha=head_sha,
    )
