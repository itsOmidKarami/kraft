"""Archive/restore round-trip (UI v2 · 03): the routes, the worktree
reclaim, and the list filter."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from support.api import _poll_events, _post_default, _set_status
from support.harness import _git

from kraft.adapters.forge.git import PUSHED_REFS
from kraft.config import git_read


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


@pytest.mark.parametrize(
    "pushed_to",
    [None, "refs/remotes/origin", PUSHED_REFS],
    ids=["unpushed", "remote-tracking", "push-record"],
)
def test_archive_keeps_a_branch_only_it_holds_commits_on(client, repo, pushed_to):
    """A cancelled item keeps its branch, and the item may never have pushed
    it. Archive (and the auto-archive poller, through the same function) then
    removes the worktree but leaves a branch with commits nothing else
    holds, and says so. A branch whose commits are all on a remote-tracking
    ref, or in Kraft's record of pushing it (a squash merge whose forge
    deleted the branch leaves only that), goes, as before."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    # A tag holds what the item made so far, so only the commit below counts.
    _git(repo, "tag", "so-far", branch)
    _git(worktree, "commit", "--allow-empty", "-m", "work nobody pushed")
    if pushed_to:
        _git(repo, "update-ref", f"{pushed_to}/{branch}", f"refs/heads/{branch}")
    client.post(f"/api/work-items/{wid}/cancel", json={"reason": "later"})

    r = client.post(f"/api/work-items/{wid}/archive")

    assert r.status_code == 200, r.text
    assert not worktree.exists()
    still_there = bool(git_read(repo, "branch", "--list", branch))
    events = client.get(f"/api/work-items/{wid}/events").json()
    payload = next(e["payload"] for e in events if e["type"] == "work_item_archived")
    if pushed_to:
        assert not still_there
        assert "kept_branch" not in r.json() and "kept_branch" not in payload
    else:
        assert still_there
        kept = {"kept_branch": branch, "unpushed_commits": 1}
        assert r.json().items() >= kept.items()
        assert payload == {"by": "you", **kept}


def test_archive_rescues_commits_on_a_detached_head(client, repo):
    """Commits made on a detached HEAD in the worktree are on no branch at
    all, so removing the worktree would orphan them. Archive gives them one,
    `kraft/rescued/<id>`, and says so."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    _git(repo, "tag", "so-far", branch)
    _git(worktree, "checkout", "-q", "--detach")
    _git(worktree, "commit", "--allow-empty", "-m", "detached work")
    head = git_read(worktree, "rev-parse", "HEAD")
    client.post(f"/api/work-items/{wid}/cancel", json={"reason": "later"})

    r = client.post(f"/api/work-items/{wid}/archive")

    assert r.status_code == 200, r.text
    assert not worktree.exists()
    rescued = f"kraft/rescued/{wid}"
    assert git_read(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{rescued}") == head
    assert r.json().items() >= {"rescued_branch": rescued, "rescued_commits": 1}.items()


def test_archive_keeps_the_worktree_when_the_rescue_fails(client, repo):
    """When the rescue branch cannot be made, the worktree is the only copy
    of the detached commits: archive still archives, but leaves it and says
    why. A branch named `kraft/rescued` makes git refuse the ref under it."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    _git(repo, "tag", "so-far", branch)
    _git(repo, "branch", "kraft/rescued", branch)
    _git(worktree, "checkout", "-q", "--detach")
    _git(worktree, "commit", "--allow-empty", "-m", "detached work")
    client.post(f"/api/work-items/{wid}/cancel", json={"reason": "later"})

    r = client.post(f"/api/work-items/{wid}/archive")

    assert r.status_code == 200, r.text
    assert r.json()["worktree_removed"] is False
    assert r.json()["worktree_kept"].startswith("rescue failed: ")
    assert worktree.is_dir()
    assert client.get(f"/api/work-items/{wid}").json()["archived_at"]
    events = client.get(f"/api/work-items/{wid}/events").json()
    payload = next(e["payload"] for e in events if e["type"] == "work_item_archived")
    assert payload["worktree_kept"] == r.json()["worktree_kept"]


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
