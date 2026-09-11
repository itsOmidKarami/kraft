from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kraft.store import _now as _now  # test seam for wall-clock checks

#: Root merge policies (design 1g). What happens to the root repo's submodule
#: pointer once the submodule MRs land.
ROOT_MERGE_POLICIES = ("bump", "skip", "bump_no_mr")


def merge_rank_order(paths: list[str]) -> list[str]:
    """Deepest path first, root last (design 3a): a submodule must merge
    before the parent whose pointer names it."""
    return sorted(paths, key=lambda p: (-p.count("/"), p))


def add_repo(
    conn: sqlite3.Connection,
    *,
    work_item_id: str,
    repo_path: str,
    role: str,
    merge_rank: int,
    submodule_path: str | None = None,
    bead_id: str | None = None,
) -> int:
    """Register one repo (root or submodule) a work item will merge into.

    Callers compute `merge_rank` -- `ensure_worktree` for declared submodules,
    the §3a scan for ones it discovers -- because only they know the whole set
    at the moment they write a row.
    """
    now = _now()
    cur = conn.execute(
        "INSERT INTO work_item_repos (work_item_id, repo_path, role, submodule_path, "
        "merge_rank, bead_id, merge_state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (work_item_id, repo_path, role, submodule_path, merge_rank, bead_id, now, now),
    )
    return cur.lastrowid


def repos_for(conn: sqlite3.Connection, work_item_id: str) -> list[dict]:
    """The item's repos, deepest submodule first, root last. Empty on a
    single-repo item -- no `work_item_repos` rows were ever written for it.
    """
    rows = conn.execute(
        "SELECT * FROM work_item_repos WHERE work_item_id = ? ORDER BY merge_rank",
        (work_item_id,),
    ).fetchall()
    return [
        {
            "repo": Path(r["repo_path"]).name,
            "path": r["repo_path"],
            "role": r["role"],
            "merge_rank": r["merge_rank"],
            "state": r["merge_state"],
            "mr_ref": json.loads(r["mr_ref"]) if r["mr_ref"] else None,
        }
        for r in rows
    ]


def update_repo_state(
    conn: sqlite3.Connection, repo_row_id: int, *, merge_state: str, mr_ref: dict | None = None
) -> None:
    """Record what a forge call just learned about one repo's merge request."""
    conn.execute(
        "UPDATE work_item_repos SET merge_state = ?, mr_ref = ?, updated_at = ? WHERE id = ?",
        (merge_state, json.dumps(mr_ref) if mr_ref else None, _now(), repo_row_id),
    )
