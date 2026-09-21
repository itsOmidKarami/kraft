"""Archive/restore round-trip (UI v2 · 03): the routes, the worktree
reclaim, and the list filter."""

from __future__ import annotations

import os
from pathlib import Path

from support.api import _poll_events, _post_default, _set_status


def test_archive_reclaims_the_worktree_and_keeps_status(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "completed")
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    assert worktree.is_dir()

    r = client.post(f"/api/work-items/{wid}/archive")

    assert r.status_code == 200, r.text
    assert r.json()["archived_by"] == "you"
    assert not worktree.exists()
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["status"] == "completed"
    assert detail["archived_at"]


def test_archive_refuses_an_active_item(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "active")

    r = client.post(f"/api/work-items/{wid}/archive")

    assert r.status_code == 409, r.text


def test_restore_puts_it_back_and_refuses_a_non_archived_item(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "abandoned")
    client.post(f"/api/work-items/{wid}/archive")

    r = client.post(f"/api/work-items/{wid}/restore")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/work-items/{wid}").json()["archived_at"] is None

    r2 = client.post(f"/api/work-items/{wid}/restore")
    assert r2.status_code == 409, r2.text


def test_list_excludes_archived_by_default_and_archived_true_returns_only_them(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "completed")
    client.post(f"/api/work-items/{wid}/archive")

    default_list = client.get("/api/work-items").json()["items"]
    assert wid not in {i["id"] for i in default_list}

    archived_list = client.get("/api/work-items?archived=true").json()["items"]
    assert {i["id"] for i in archived_list} == {wid}
    assert archived_list[0]["archived_by"] == "you"


def test_archived_list_needs_include_abandoned_for_abandoned_items(client, repo):
    """An archived abandoned item (auto-archive poller, or Archive from the
    Done group) must still be reachable to restore -- the SPA's
    listArchivedWorkItems sends include_abandoned=true for exactly this."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "abandoned")
    client.post(f"/api/work-items/{wid}/archive")

    without = client.get("/api/work-items?archived=true").json()["items"]
    assert wid not in {i["id"] for i in without}

    with_it = client.get("/api/work-items?archived=true&include_abandoned=true").json()["items"]
    assert {i["id"] for i in with_it} == {wid}
