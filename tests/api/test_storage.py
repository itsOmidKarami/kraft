"""`GET /storage` and `POST /storage/preview`: the worktrees' disk use item by
item, and what archiving some of them would do."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest
from support.api import _hold_storage, _paused, _poll_events, _post_default, _set_status
from support.harness import git

from kraft import storage
from kraft.config import git_read

_AT = "2026-01-01T00:00:00+00:00"


def test_storage_joins_the_cached_measurement_to_the_items(client, repo):
    done, live = _paused(client, repo), _paused(client, repo)
    _set_status(done, "completed")
    client.app.state.storage_usage = storage.Usage(
        _AT, 50, {done: 30, live: 50, "ghost": 70}, {"worktrees": 50}
    )

    got = client.get("/api/storage").json()

    assert [(i["id"], i["title"], i["status"], i["reclaimable"]) for i in got["items"]] == [
        (live, "t", "paused", False),
        (done, "t", "completed", True),
    ]
    assert got["orphans"] == [{"name": "ghost", "bytes": 70}]
    assert (got["measured_at"], got["state"], got["used_bytes"]) == (_AT, None, 50)
    assert (got["quota_bytes"], got["limit_bytes"], got["reclaimable_bytes"]) == (None, None, 30)


def test_storage_reports_the_state_against_the_limit(client):
    _hold_storage(client, used=120, limit=100)

    got = client.get("/api/storage").json()

    assert (got["state"], got["used_bytes"], got["quota_bytes"], got["limit_bytes"]) == (
        "held",
        120,
        80,
        100,
    )


def test_storage_measures_when_nothing_is_cached_and_when_asked(client, monkeypatch):
    walks = []

    def walk(base, stop=None):
        walks.append(base)
        return storage.Usage(_AT, len(walks), {}, {})

    monkeypatch.setattr(storage, "measure", walk)
    client.app.state.storage_usage = None

    used = [
        client.get(url).json()["used_bytes"]
        for url in ("/api/storage", "/api/storage", "/api/storage?refresh=1")
    ]

    assert used == [1, 1, 2]


@pytest.mark.api_client(peer=("10.0.0.5", 54321))
@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/storage"), ("post", "/api/storage/preview")],
    ids=["storage", "preview"],
)
def test_storage_needs_a_session(client, monkeypatch, method, path):
    st = client.app.state
    monkeypatch.setattr(st, "access", {**st.access, "password_hash": "x"}, raising=False)

    assert getattr(client, method)(path).status_code == 401


def test_preview_counts_what_archive_would_lose_and_keep(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    # A tag holds what the item made so far, so only the commit below is unpushed.
    git(repo, "tag", "so-far", branch)
    git(worktree, "commit", "--allow-empty", "-m", "work nobody pushed")
    already = len(git_read(worktree, "status", "--porcelain").splitlines())
    (worktree / "scratch-1.txt").write_text("x")
    (worktree / "scratch-2.txt").write_text("y")
    _set_status(wid, "completed")
    client.app.state.storage_usage = storage.Usage(_AT, 1000, {wid: 400}, {})

    r = client.post("/api/storage/preview", json={"ids": [wid]})

    assert r.status_code == 200, r.text
    got = r.json()
    assert got["items"] == [
        {
            "id": wid,
            "title": "make the failing test pass",
            "bytes": 400,
            "archivable": True,
            "refusal": None,
            "uncommitted_files": already + 2,
            "unpushed_commits": 1,
            "branch_kept": True,
        }
    ]
    assert (got["freed_bytes"], got["used_after_bytes"], got["state_after"]) == (400, 600, None)
    # It only looked.
    assert worktree.is_dir()
    assert client.get(f"/api/work-items/{wid}").json()["archived_at"] is None
    assert client.app.state.storage_usage.items == {wid: 400}


def test_preview_refuses_what_archive_would_and_counts_the_rest_once(client, repo):
    done, live, archived = (_paused(client, repo) for _ in range(3))
    for wid in (done, archived):
        _set_status(wid, "completed")
    assert client.post(f"/api/work-items/{archived}/archive").status_code == 200
    _hold_storage(client, used=140, limit=100)
    st = client.app.state
    st.storage_usage = dataclasses.replace(
        st.storage_usage, items={done: 40, live: 30, archived: 20}
    )

    r = client.post("/api/storage/preview", json={"ids": [done, live, archived, "nope", done]})

    assert r.status_code == 200, r.text
    got = r.json()
    assert [(i["id"], i["archivable"], i["refusal"], i["bytes"]) for i in got["items"]] == [
        (done, True, None, 40),
        (live, False, "only a completed or abandoned item can be archived", 30),
        (archived, False, "already archived", 20),
        ("nope", False, "unknown work item", 0),
    ]
    # A never-started item has no worktree to look in.
    assert got["items"][0]["uncommitted_files"] is None
    assert (got["freed_bytes"], got["used_after_bytes"], got["state_after"]) == (
        40,
        100,
        "over_quota",
    )


def test_preview_needs_at_least_one_id(client):
    assert client.post("/api/storage/preview", json={"ids": []}).status_code == 422
