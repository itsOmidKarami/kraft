"""POST /work-items/{id}/abandon and the item's own folder, `run/items/<id>/`:
abandoning removes it whole, and never what a link a worker planted in it
points at. In its own file because `test_lifecycle.py` is near its line
budget (`dev/check_tests.py`)."""

from __future__ import annotations

import asyncio
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

    r = client.post(f"/api/work-items/{wid}/abandon", json={"reason": "test"})

    assert r.status_code == 200, r.text
    assert not folder.exists()
    assert victim.read_text() == "untouched"


def test_abandoning_removes_the_item_s_folder_off_the_event_loop(client, repo, monkeypatch):
    """A large folder takes a while to delete, and the server answers every
    request on one event loop."""
    from kraft import item_paths

    wid = _paused(client, repo)
    write(Path(os.environ["KRAFT_RUN_DIR"]) / "items" / wid, "docs/spec.md", "x")
    real = item_paths.remove_tree
    walked, on_loop = [], []

    def remove_tree(path):
        if Path(path).name == wid:
            walked.append(path)
            try:
                asyncio.get_running_loop()
                on_loop.append(path)
            except RuntimeError:
                pass
        return real(path)

    monkeypatch.setattr(item_paths, "remove_tree", remove_tree)

    assert client.post(f"/api/work-items/{wid}/abandon", json={"reason": "test"}).status_code == 200
    assert walked and on_loop == []
    assert not (Path(os.environ["KRAFT_RUN_DIR"]) / "items" / wid).exists()


def test_abandoning_takes_the_item_s_documents_off_the_storage_figure(client, repo):
    """Archiving keeps an item's documents, abandoning removes them: the
    cached figure drops at once, without waiting for the next walk."""
    from kraft import storage

    wid = _paused(client, repo)
    client.app.state.storage_usage = storage.Usage(
        "2026-01-01T00:00:00+00:00",
        100,
        {},
        {"documents": 100},
        {wid: 60, "other": 40},
    )

    assert client.post(f"/api/work-items/{wid}/abandon", json={"reason": "test"}).status_code == 200

    used = storage.usage(client.app.state)
    assert (used.governed, used.categories["documents"], used.documents) == (40, 40, {"other": 40})
