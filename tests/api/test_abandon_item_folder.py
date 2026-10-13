"""POST /work-items/{id}/abandon and the item's own folder, `run/items/<id>/`:
abandoning removes it whole, and never what a link a worker planted in it
points at. In its own file because `test_lifecycle.py` is near its line
budget (`dev/check_tests.py`)."""

from __future__ import annotations

import os
from pathlib import Path

from support.api import _paused
from support.harness import write


def test_abandoning_removes_the_item_s_folder_and_nothing_a_link_in_it_points_at(
    client, repo, tmp_path
):
    wid = _paused(client, repo)
    folder = Path(os.environ["KRAFT_RUN_DIR"]) / "items" / wid
    for part in ("docs/spec.md", "history/spec.1.md", "sessions/s1/session.log"):
        write(folder, part, "x")
    victim = write(tmp_path, "victim/keep.md", "untouched")
    (folder / "docs" / "elsewhere").symlink_to(victim.parent)
    (folder / "sandbox-home").mkdir()
    (folder / "sandbox-home" / ".cache").symlink_to(victim.parent)

    r = client.post(f"/api/work-items/{wid}/abandon")

    assert r.status_code == 200, r.text
    assert not folder.exists()
    assert victim.read_text() == "untouched"
