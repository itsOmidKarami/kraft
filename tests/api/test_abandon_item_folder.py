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
from support.harness import git, write


def test_abandoning_removes_the_item_s_folder_and_nothing_a_link_in_it_points_at(
    client, repo, tmp_path
):
    wid = _paused(client, repo)
    folder = Path(os.environ["KRAFT_RUN_DIR"]) / "items" / wid
    for part in (
        "docs/spec.md",
        "history/spec.1.md",
        "sessions/s1/session.log",
        "checkout/leftovers.json",
    ):
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


@pytest.mark.parametrize("body", [None, {}, {"reason": "  "}], ids=["no-body", "missing", "blank"])
def test_abandon_without_a_reason_still_abandons_and_records_none(client, repo, fake_beads, body):
    """A caller from before reasons were kept still abandons: no reason on the
    event or the detail, and the item's own bead is closed saying only that."""
    wid = client.post(
        "/api/work-items", json={"title": "t", "repo": str(repo), "autostart": False}
    ).json()["id"]
    own = client.get(f"/api/work-items/{wid}").json()["bead_id"]

    r = client.post(f"/api/work-items/{wid}/abandon", json=body)

    assert r.status_code == 200, r.text
    detail = client.get(f"/api/work-items/{wid}").json()
    assert (detail["status"], detail["ended_reason"]) == ("abandoned", None)
    events = client.get(f"/api/work-items/{wid}/events").json()
    assert [e["payload"] for e in events if e["type"] == "work_item_abandoned"] == [{}]
    assert fake_beads.all()[own]["close_reason"] == "abandoned"


@pytest.mark.parametrize(
    "where",
    ["on-it", "elsewhere", "rebasing", "other-worktree"],
    ids=["checked-out-kept", "deleted", "in-use-kept", "git-refused-kept"],
)
def test_abandon_in_place_deletes_the_branch_only_when_nothing_has_it(
    client, repo, tmp_path, where
):
    """The person's checkout on the item's branch keeps it, and so does a
    rebase holding the checkout's lock or git refusing the delete (another
    worktree has it out); the answer and the event say so. The item's refs
    under `refs/kraft/<id>/` go regardless."""
    from kraft import inplace
    from kraft.adapters.forge.git import PUSHED_REFS
    from kraft.config import git_read

    wid = _paused(client, repo, in_place=True)
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    assert branch
    git(repo, "branch", branch)
    git(repo, "update-ref", f"{PUSHED_REFS}/{branch}", "HEAD")
    git(repo, "update-ref", f"refs/kraft/{wid}/attempt-1", "HEAD")
    if where == "on-it":
        git(repo, "checkout", "-q", branch)
    if where == "other-worktree":
        git(repo, "worktree", "add", "-q", str(tmp_path / "elsewhere"), branch)
    lock = inplace._lock(repo)
    if where == "rebasing":
        lock.acquire()
    try:
        r = client.post(f"/api/work-items/{wid}/abandon", json={"reason": "test"})
    finally:
        if where == "rebasing":
            lock.release()

    assert r.status_code == 200, r.text
    kept = {
        "on-it": "checked out",
        "elsewhere": None,
        "rebasing": "in use",
        "other-worktree": "in use",
    }[where]
    assert r.json() | {"id": None} == {
        "id": None,
        "status": "abandoned",
        "worktree_removed": False,
        "in_place": True,
        "branch_kept": kept,
    }
    refs = git_read(repo, "for-each-ref", "--format=%(refname)").splitlines()
    assert (f"refs/heads/{branch}" in refs) is (kept is not None)
    assert (f"{PUSHED_REFS}/{branch}" in refs) is (kept is not None)
    assert f"refs/kraft/{wid}/attempt-1" not in refs
    assert repo.is_dir()
    events = client.get(f"/api/work-items/{wid}/events").json()
    payload = next(e["payload"] for e in events if e["type"] == "work_item_abandoned")
    assert payload == {"reason": "test"} | ({"branch_kept": kept} if kept else {})


def test_abandon_in_place_stops_krafts_own_session_and_nothing_of_the_persons(client, repo):
    """An escalation or a reply still running on a stopped item would go on
    committing in the person's checkout: its recorded process is stopped. A
    process of the person's in the same checkout is not Kraft's to touch, not
    even one a stale row names by a reused pid."""
    import sqlite3
    import subprocess
    import sys

    import psutil

    from kraft import store

    wid = _paused(client, repo, in_place=True)
    sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
    agent = subprocess.Popen(sleep, cwd=repo, start_new_session=True)
    theirs = subprocess.Popen(sleep, cwd=repo, start_new_session=True)
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    started = psutil.Process(agent.pid).create_time()
    # The second row's process died and its pid now belongs to the person's.
    rows = [("s1", agent.pid, started), ("s2", theirs.pid, started - 100)]
    try:
        for sid, pid, at in rows:
            store.create_session(
                conn,
                id=sid,
                work_item_id=wid,
                node_id="n",
                hook_point="escalation",
                log_path="l",
                result_path="r",
            )
            store.session_running(conn, sid, pid, at, work_item_id=wid)
        conn.commit()
    finally:
        conn.close()
    try:
        r = client.post(f"/api/work-items/{wid}/abandon", json={"reason": "test"})

        assert r.status_code == 200, r.text
        assert agent.wait(timeout=10) != 0
        assert theirs.poll() is None, "Kraft signalled a process of the person's"
    finally:
        for p in (agent, theirs):
            p.kill()
            p.wait()
