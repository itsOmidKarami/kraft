"""`GET /storage` and `POST /storage/preview`: what the worktrees use, item by
item, and what archiving some of them would do. Both read the measurement
`kraft.storage` keeps; neither changes anything."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import Request
from pydantic import BaseModel, Field

from kraft import storage, store
from kraft.api import api_router
from kraft.api.routes import lifecycle
from kraft.config import git_read

#: Only what the join reads: the rows carry the frozen chain, 30 KB apiece.
_ITEM_COLUMNS = "SELECT id, title, status, archived_at, updated_at FROM work_items"


class StoragePreview(BaseModel):
    ids: Annotated[list[str], Field(min_length=1, max_length=200)]


async def _measured(request: Request, force: bool = False) -> storage.Usage:
    """The cached measurement; a walk when there is none yet or `force`."""
    used = None if force else storage.usage(request.app.state)
    return used or await storage.refresh(request.app)


@api_router.get("/storage")
async def get_storage(request: Request, refresh: bool = False):
    """The disk the worktrees use, against the quota and limit when a limit is
    set, with one entry per measured worktree (largest first) and the folders
    that belong to no work item. It answers from the last measurement, and
    measures first when there is none yet or `refresh` is set."""
    st = request.app.state
    used = await _measured(request, refresh)
    rows = st.db.read(lambda c: c.execute(_ITEM_COLUMNS).fetchall())
    return storage.report(st.policy, used, {row["id"]: row for row in rows})


def _preview_one(st, wid: str, row, size: int, members: list[Path]) -> dict:
    """One id of the preview, with the check `lifecycle._archive_one` makes
    (`_branches_to_keep`) and a count of what its worktree would lose. Blocking
    git calls: run it in a thread."""
    why = storage.refusal(row)
    item = {
        "id": wid,
        "title": row["title"] if row is not None else None,
        "bytes": size,
        "archivable": why is None,
        "refusal": why,
        "uncommitted_files": None,
        "unpushed_commits": 0,
        "branch_kept": False,
    }
    if why is not None:
        return item
    worktree = st.run_dirs.worktrees / wid
    if worktree.is_dir():
        # null when git cannot say, as when there is no worktree
        status = git_read(worktree, "status", "--porcelain")
        item["uncommitted_files"] = None if status is None else len(status.splitlines())
    kept = lifecycle._branches_to_keep(Path(row["repo"]), store.branch_for(row), members)
    item["unpushed_commits"] = sum(kept.values())
    item["branch_kept"] = bool(kept)
    return item


@api_router.post("/storage/preview")
async def preview_storage_cleanup(body: StoragePreview, request: Request):
    """What archiving these items would do, without doing it: per item the
    size of its worktree, the uncommitted files that would be lost, the
    commits only its branch holds and whether the branch stays; and in total
    the bytes freed and the usage and state they would leave. An id that is
    unknown, not completed or abandoned, or already archived is answered with
    `archivable: false` and the reason, and frees nothing. An id given twice
    counts once. The bytes freed assume each worktree goes: an archive whose
    detached-HEAD rescue fails keeps it and frees nothing."""
    st = request.app.state
    used = await _measured(request)
    ids = list(dict.fromkeys(body.ids))
    rows = st.db.read(
        lambda c: {
            wid: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            for wid in ids
        }
    )
    # On the loop, as `_archive_one` does: it reads the launch cache.
    members = {
        wid: lifecycle._connected_members(st, row)
        for wid, row in rows.items()
        if storage.reclaimable(row)
    }
    items = await asyncio.to_thread(
        lambda: [
            _preview_one(st, wid, rows[wid], used.items.get(wid, 0), members.get(wid, []))
            for wid in ids
        ]
    )
    freed = sum(item["bytes"] for item in items if item["archivable"])
    left, state = storage.after(st.policy, used, freed)
    return {"freed_bytes": freed, "used_after_bytes": left, "state_after": state, "items": items}
