"""A sliced item on the board: children are not rows, their parent carries a summary."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from support.api import _paused

from kraft import store


def test_children_list_under_their_parent_not_as_rows(client, repo):

    parent = _paused(client, repo, chain_template="default")
    kids = [_paused(client, repo, chain_template="default") for _ in range(3)]
    state = store.GroupState(group="slices", phase="forked", generation=1)
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.execute("UPDATE work_items SET group_state = ? WHERE id = ?", (state.to_json(), parent))
    for key, (kid, status) in enumerate(
        zip(kids, ("completed", "needs_human", "paused"), strict=True), 1
    ):
        conn.execute(
            "UPDATE work_items SET parent_id = ?, slice_generation = 1, slice_key = ?, "
            "status = ? WHERE id = ?",
            (parent, key, status, kid),
        )
    conn.commit()
    conn.close()

    rows = {i["id"]: i for i in client.get("/api/work-items").json()["items"]}

    assert parent in rows
    assert not set(kids) & set(rows)
    assert rows[parent]["slices"] == {
        "done": 1, "needs_you": 1, "paused": 1, "running": 0, "queued": 0, "dropped": 0, "total": 3,
    }  # fmt: skip
    # A slice that needs a person puts its parent under Needs you.
    assert rows[parent]["display_status"] == "needs_you"
    detail = client.get(f"/api/work-items/{parent}").json()
    assert [(k["slice_key"], k["status"]) for k in detail["children"]] == [
        (1, "completed"),
        (2, "needs_human"),
        (3, "paused"),
    ]
    child = client.get(f"/api/work-items/{kids[1]}").json()
    assert (child["parent_id"], child["slice_key"]) == (parent, 2)
