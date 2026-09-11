from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from kraft import logs, store
from kraft.config import git_read, main_ignore_args

logger = logging.getLogger(__name__)


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


async def ensure_worktree(
    db,
    run_dirs,
    *,
    repo: str,
    work_item_id: str,
    attachments: list[dict] | None = None,
) -> Path:
    """The item's worktree, created if it is not there yet, with intake
    attachments copied in.

    Called by the executor before the first node dispatches, not only by the
    `env_setup` builtin: `default.yaml` runs `spec` and `plan` ahead of
    `env_setup`, and an agent asked to write a file into a directory that does
    not exist fails in a way no chain can recover from (Kraft-bmp). The
    attachment copy has to move with it: `plan/SKILL.md` tells a headless
    session to fall back to an attached spec when the chain skipped the spec
    node, and that document was not there yet if the copy waited for
    `env_setup` (node 4) to run. `env_setup` still exists — it is this call,
    now unconditionally carrying attachments, plus its own session bookkeeping.

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
    # Pin the base before the worktree exists, so the early return above
    # guarantees a crashed-and-retried run never re-pins to a moved HEAD.
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref, branch, id FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    if row is not None and row["base_ref"] is None:
        head = git_read(Path(repo), "rev-parse", "HEAD")
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
    args += [str(worktree), branch] if exists else [str(worktree), "-b", branch]
    done = await asyncio.to_thread(subprocess.run, args, cwd=repo, capture_output=True, text=True)
    if done.returncode != 0:
        # Raised, not returned: this runs outside a session, so there is no
        # session status to carry the failure. `api._guard` turns it into
        # needs_human with the git stderr in the reason.
        detail = done.stderr.strip() or done.stdout.strip()
        raise RuntimeError(f"git worktree add failed for {work_item_id}: {detail}")
    await asyncio.to_thread(_pin_identity, Path(repo), worktree, work_item_id)
    await asyncio.to_thread(
        _copy_attachments, Path(repo), worktree, attachments or [], work_item_id
    )
    # Every node from `spec` on can commit, and the shared pre-commit hook
    # (`.beads/hooks/pre-commit`) needs `pre-commit` on PATH -- via `uv run
    # --no-sync` -- to catch a formatting slip before it reaches CI. A
    # freshly created worktree has no `.venv` yet, so the very first commit
    # an agent made hit `Failed to spawn: pre-commit` and fell back to
    # `--no-verify`, skipping the check it most needed for the doc it had
    # just written (Kraft-i047). Sync once here, before any node dispatches,
    # so the safety net is live for that first commit too. Best-effort: a
    # sync failure (offline, first-run download taking too long) logs and
    # falls back to today's `--no-verify` behavior rather than failing the
    # whole worktree over tooling, not content.
    if (worktree / "pyproject.toml").is_file():
        synced = await asyncio.to_thread(
            subprocess.run, ["uv", "sync"], cwd=worktree, capture_output=True, text=True
        )
        if synced.returncode != 0:
            logger.warning(
                "uv sync failed for %s: %s",
                work_item_id,
                synced.stderr.strip() or synced.stdout.strip(),
            )
    decl = db.read(
        lambda c: c.execute(
            "SELECT submodules FROM work_items WHERE id = ?", (work_item_id,)
        ).fetchone()
    )
    submodules = json.loads(decl["submodules"]) if decl and decl["submodules"] else []
    if submodules:
        await _setup_submodules(db, Path(repo), worktree, branch, work_item_id, submodules)
    return worktree


async def refresh_worktree_base(worktree: Path, repo: Path, branch: str) -> str | None:
    """Rebase `worktree`'s branch onto `repo`'s current HEAD, so a paused or
    retried item's next commit lands on top of whatever landed on `repo`
    while the item sat stopped, not the commit it forked from.

    Returns the new HEAD sha when the rebase moved the branch (the caller
    stores it as the item's `base_ref`), or None when there was nothing to
    do: no worktree yet, `repo`'s HEAD unreadable, the branch already
    contains that HEAD, the branch already has an `origin` remote-tracking
    ref, or the worktree has uncommitted changes -- all four are best-effort
    skips (logged), same posture as `ensure_worktree`'s other git steps.
    The dirty-tree case comes from a pause via SIGTERM (`api.py:_terminate`)
    catching an agent mid-edit with nothing committed yet, and `git rebase`
    itself refuses a dirty tree; the pushed-branch case is covered below.

    Raises RuntimeError, with the rebase already aborted (`git rebase
    --abort`), on a conflict -- that is for a human to resolve, not to
    dispatch an agent into.
    """
    if not worktree.is_dir():
        return None
    head = git_read(repo, "rev-parse", "HEAD")
    if not head:
        logger.warning("refresh_worktree_base: rev-parse HEAD failed in %s", repo)
        return None
    # Cheaper, purely local check first: a branch already pushed past a prior
    # open_mr gate must not be silently rewritten here -- a reviewer or a
    # pipeline may already be looking at those commits, and this is a quiet
    # auto-refresh on resume/retry, not a deliberate rebase someone asked
    # for. `forge._push` can publish a rewritten branch now (Kraft-z6i8), so
    # this skip is a policy choice about *when* to rewrite, not a workaround
    # for push being unable to.
    if git_read(
        worktree,
        "rev-parse",
        "--verify",
        "--quiet",
        f"refs/remotes/origin/{branch}",
        expected_failure=True,
    ):
        logger.info("refresh_worktree_base: %s already pushed to origin, skipping", branch)
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
        raise RuntimeError(f"git rebase failed for {worktree}: {detail}")
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
) -> str:
    """Rebase onto `repo`'s current HEAD right before `open_mr`, so an item
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
    """
    new_head = await refresh_worktree_base(Path(worktree), Path(repo), branch)
    if new_head:
        await db.write(lambda c: store.set_base_ref(c, work_item_id, new_head))
        log = f"rebased {branch} onto {new_head}\n"
    else:
        log = "nothing to rebase\n"
    return await _record_done(
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

    Runs right after `on.implementation.start` in the same node (see
    `default.yaml`), so a plain green `verify` never masks a change `open_mr`
    would reject three nodes later -- this is what would have rescued work
    item 9d0ab38ff3c9439b90506df0f6966660, which declared nothing.
    """
    log_path, result_path = await start_session(
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
) -> tuple[Path, Path]:
    """Create the session row before the in-process work starts, not after.

    `forge.run_task` can now sit in `_poll_ci` for up to `poll_timeout`
    (default 1800s); recording nothing until it returns left pause, abandon
    and reattach with no row to find for the whole wait -- pause silently
    no-op'd and the chain walked on into merge (Kraft-41b), and a restart
    mid-poll left nothing to reattach (Kraft-7xt). Splitting the row's
    creation from its exit is what `adapters.subprocess.run_task` already
    does for a spawned child; this gives an in-process task the same shape.
    """
    log_path = run_dirs.logs / f"{session_id}.log"
    result_path = run_dirs.results / f"{session_id}.json"
    await db.write(
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
        )
    )
    return log_path, result_path


async def finish_session(
    db,
    log_path: Path,
    result_path: Path,
    *,
    session_id: str,
    status: str,
    log: str,
) -> str:
    """Write the log and close out a session `start_session` already created."""
    log_path.write_text(log)
    # A subprocess session gets its per-line timestamps from the adapter's drain
    # thread (`adapters/subprocess._watch_log`), which stamps each line as it
    # appears. A builtin writes its whole log in one call and has no drain
    # thread, so without this its lines read back with a blank time column --
    # which `env_setup` used to have, before it stopped going through the
    # subprocess adapter.
    now = datetime.now(UTC).isoformat()
    try:
        logs.times_path(log_path).write_text(
            "".join(
                json.dumps({"n": n, "t": now}) + "\n" for n in range(len(logs.split_lines(log)))
            )
        )
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
    log_path, result_path = await start_session(
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
        db, run_dirs, repo=repo, work_item_id=work_item_id, attachments=attachments
    )
    return await _record_done(
        db,
        run_dirs,
        session_id=session_id,
        work_item_id=work_item_id,
        node_id=node_id,
        hook_point="on.env.prepare",
        round=round,
        log=f"worktree ready at {worktree}\n",
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
