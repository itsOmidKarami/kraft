"""Pin the commit every node run started and ended on
(docs/superpowers/specs/2026-09-27-review-flow-backend-design.md §1).

Wraps the three store writers that mark a node's life -- `enter_node`,
`complete_node`, `request_gate` -- so the git reads happen *before* the
`db.write`, never inside it, and the ref that keeps an old attempt alive
through a rebase is written after the row commits.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from kraft import config as _config
from kraft import store
from kraft.worker import sandbox as _sandbox

logger = logging.getLogger(__name__)

REF_ROOT = "refs/kraft"


def head(worktree: Path) -> str | None:
    return _config.git_read(worktree, "rev-parse", "HEAD")


def is_dirty(worktree: Path) -> bool:
    out = _config.git_read(worktree, "status", _sandbox.SUBMODULES_UNENTERED, "--porcelain")
    return bool(out)


def _ref(wid: str, node_id: str, attempt: int) -> str:
    return f"{REF_ROOT}/{wid}/{node_id}/{attempt}"


def pin_ref(worktree: Path, wid: str, node_id: str, attempt: int, sha: str) -> None:
    """Best-effort: a missing ref costs one old attempt's diff, never the walk.
    Bounded like `config.git_read` (Kraft-dl5fl); the async callers run it off
    the event loop."""
    try:
        done = subprocess.run(
            ["git", "update-ref", _ref(wid, node_id, attempt), sha],
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not pin %s: %s", _ref(wid, node_id, attempt), exc)
        return
    if done.returncode != 0:
        logger.warning("could not pin %s: %s", _ref(wid, node_id, attempt), done.stderr.strip())


def drop_refs(repo: Path, wid: str) -> None:
    listed = _config.git_read(repo, "for-each-ref", "--format=%(refname)", f"{REF_ROOT}/{wid}/")
    for ref in (listed or "").splitlines():
        subprocess.run(["git", "update-ref", "-d", ref], cwd=repo, capture_output=True)


def _live(worktree: Path | None) -> Path | None:
    return worktree if worktree is not None and Path(worktree).is_dir() else None


def _base(c, wid: str, fallback: str) -> str:
    row = c.execute("SELECT base_ref FROM work_items WHERE id = ?", (wid,)).fetchone()
    return (row["base_ref"] if row else None) or fallback


async def entered(db, worktree, wid: str, node_id: str) -> None:
    wt = _live(worktree)
    sha = head(wt) if wt else None

    def write(c):
        store.enter_node(c, wid, node_id)
        if sha:
            store.start_run(c, wid, node_id, start_sha=sha, base_sha=_base(c, wid, sha))

    await db.write(write)


async def completed(db, worktree, wid: str, node_id: str) -> None:
    wt = _live(worktree)
    sha = head(wt) if wt else None
    dirty = is_dirty(wt) if wt else False

    def write(c):
        store.complete_node(c, wid, node_id)
        return store.finish_run(c, wid, node_id, end_sha=sha, dirty=dirty) if sha else None

    attempt = await db.write(write)
    if attempt is not None:
        await asyncio.to_thread(pin_ref, wt, wid, node_id, attempt, sha)


async def gate_requested(db, worktree, wid: str, gate: str) -> None:
    wt = _live(worktree)
    sha = head(wt) if wt else None
    dirty = is_dirty(wt) if wt else False
    # The ref goes first: `gate_requested` ends the walk, and anything still
    # awaited after it keeps the walk's task live past the point a person can
    # already see the gate. The walk is this item's only writer, so the attempt
    # read here is the one `pin_gate` writes.
    attempt = db.read(lambda c: store.next_gate_attempt(c, wid, gate, sha)) if sha else None
    if attempt is not None:
        await asyncio.to_thread(pin_ref, wt, wid, gate, attempt, sha)

    def write(c):
        store.request_gate(c, wid, gate, gate)
        if sha:
            store.pin_gate(c, wid, gate, sha=sha, base_sha=_base(c, wid, sha), dirty=dirty)

    await db.write(write)
