"""Completing a slice's child by hand still pins its head for the join."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from support.api import _paused


def test_completing_a_child_by_hand_pins_its_head(client, repo):
    wid = _paused(client, repo, chain_template="default")
    worktree = client.app.state.run_dirs.worktrees / wid
    subprocess.run(
        ["git", "worktree", "add", "-b", f"kraft/{wid}", str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, capture_output=True, text=True
    ).stdout.strip()
    with closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")) as c, c:
        c.execute(
            "UPDATE work_items SET parent_id = 'p1', slice_generation = 1, slice_key = 1 "
            "WHERE id = ?",
            (wid,),
        )

    r = client.post(f"/api/work-items/{wid}/complete", json={"reason": "done by hand"})

    assert r.status_code == 200, r.text
    with closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")) as c:
        assert (
            c.execute("SELECT pinned_head FROM work_items WHERE id = ?", (wid,)).fetchone()[0]
            == head
        )
