from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from kraft import logs, store
from kraft.config import git_read

logger = logging.getLogger(__name__)


def _copy_attachments(repo: Path, worktree: Path, attachments: list[dict]) -> None:
    """Intake attachments that are not committed do not exist in a fresh
    worktree (`git worktree add` branches from HEAD), so copy them in. A
    committed one arrived through git and is left exactly as git wrote it.

    The path was validated against the *repo's* working tree, not this
    worktree (checked out from HEAD, possibly a different tree). If HEAD has
    a symlink here, `dest.exists()` follows it, which for a dangling symlink
    reports False and `shutil.copyfile` would then write through it to
    wherever it points. So a symlinked destination, dangling or not, is
    always skipped, and the resolved destination must stay inside the
    worktree."""
    worktree_root = worktree.resolve()
    for attachment in attachments:
        dest = worktree / attachment["path"]
        src = repo / attachment["path"]
        if dest.is_symlink():
            continue
        resolved = dest.resolve()
        if resolved != worktree_root and worktree_root not in resolved.parents:
            continue
        if dest.exists() or not src.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


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
    clobber), and an existing `kraft/<id>` branch (a rejected gate removes the
    worktree but keeps the branch) is checked out rather than re-created.
    """
    worktree = run_dirs.worktrees / work_item_id
    if worktree.is_dir():
        return worktree
    # Pin the base before the worktree exists, so the early return above
    # guarantees a crashed-and-retried run never re-pins to a moved HEAD.
    row = db.read(
        lambda c: c.execute(
            "SELECT base_ref FROM work_items WHERE id = ?", (work_item_id,)
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
    branch = f"kraft/{work_item_id}"
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
    _copy_attachments(Path(repo), worktree, attachments or [])
    return worktree


async def start_session(
    db,
    run_dirs,
    *,
    session_id: str,
    work_item_id: str,
    node_id: str,
    hook_point: str,
    round: int,
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
    )
