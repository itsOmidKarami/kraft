"""POST /work-items/{id}/abandon and the item's own folder, `run/items/<id>/`:
abandoning removes it whole, and never what a link a worker planted in it
points at. In its own file because `test_lifecycle.py` is near its line
budget (`dev/check_tests.py`)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
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


@pytest.mark.parametrize(
    ("verb", "beads_fail"),
    [("abandon", False), ("abandon", True), ("cancel", False)],
    ids=["beads", "beads-down", "cancel"],
)
def test_abandon_records_why_and_settles_the_items_beads(
    client, repo, fake_beads, monkeypatch, verb, beads_fail
):
    """The reason lands on the event and the detail; the bead intake filed is
    closed with it, a bead the item implements gets a note and stays open, and
    a beads failure never fails the abandon."""
    cwd = os.environ["KRAFT_BD_CWD"]
    other = client.portal.call(lambda: fake_beads.intake("sub-task", cwd=cwd))
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "autostart": False, "implements_beads": [other]},
    ).json()["id"]
    own = client.get(f"/api/work-items/{wid}").json()["bead_id"]
    if beads_fail:

        async def down(*_a, **_k):
            raise RuntimeError("bd is down")

        monkeypatch.setattr("kraft.adapters.beads.complete", down)
        monkeypatch.setattr("kraft.adapters.beads.annotate", down)

    r = client.post(f"/api/work-items/{wid}/{verb}", json={"reason": "refiled as another item"})

    assert r.status_code == 200, r.text
    ended = "cancelled" if verb == "cancel" else "abandoned"
    event = [e for e in client.get(f"/api/work-items/{wid}/events").json()]
    recorded = [e["payload"]["reason"] for e in event if e["type"] == f"work_item_{ended}"]
    assert recorded == ["refiled as another item"]
    assert client.get(f"/api/work-items/{wid}").json()["ended_reason"] == "refiled as another item"
    beads = fake_beads.all()
    if beads_fail:
        assert beads[own]["status"] == "open" and "comments" not in beads[other]
        return
    assert beads[own]["status"] == "closed"
    assert beads[own]["close_reason"] == f"{ended}: refiled as another item"
    assert beads[other]["status"] == "open"
    assert beads[other]["comments"] == [
        f"Kraft work item {wid} was {ended}: refiled as another item"
    ]


@pytest.mark.parametrize("body", [{}, {"reason": "  "}], ids=["missing", "blank"])
def test_abandon_without_a_reason_is_refused_and_changes_nothing(client, repo, body):
    wid = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
    ).json()["id"]

    r = client.post(f"/api/work-items/{wid}/abandon", json=body)

    assert r.status_code == 422, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"
