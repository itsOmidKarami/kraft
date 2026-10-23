"""Archive/restore round-trip (UI v2 · 03): the routes, the worktree
reclaim, and the list filter."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from support.api import _paused, _poll_events, _post_default, _set_status
from support.harness import git, write

from kraft.adapters.forge.git import PUSHED_REFS
from kraft.api.routes import lifecycle
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


@pytest.mark.parametrize("door", ["archive", "abandon"])
def test_archiving_or_abandoning_tells_the_machines_to_forget_the_item(
    client, repo, monkeypatch, door
):
    from kraft.remote import runs as runs_mod

    told = []
    monkeypatch.setattr(runs_mod.current(), "forget_soon", told.append)
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "completed")
    body = {"reason": "test"} if door == "abandon" else None
    assert client.post(f"/api/work-items/{wid}/{door}", json=body).status_code == 200
    assert told == [wid]


@pytest.mark.parametrize(
    "home", ["items/{wid}/sandbox-home", "sandbox-home/{wid}"], ids=["new-home", "old-home"]
)
def test_archiving_keeps_the_item_s_folder_but_its_sandbox_home(client, repo, home):
    """The documents, their versions, what was filed and the session files
    stay for the archived item's page; the sandbox home, wherever it is (an
    older item's in `sandbox-home/`), is a cache, and goes."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _set_status(wid, "completed")
    run = Path(os.environ["KRAFT_RUN_DIR"])
    parts = ("docs/spec.md", "history/spec.1.md", "filed/spec.md", "sessions/s1/session.log")
    kept = [write(run / "items" / wid, p, "x") for p in parts]
    cache = write(run, f"{home.format(wid=wid)}/.cache/x", "x")

    assert client.post(f"/api/work-items/{wid}/archive").status_code == 200

    assert [p.read_text() for p in kept] == ["x"] * len(kept)
    assert not cache.parent.parent.exists()


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
    git(repo, "tag", "so-far", branch)
    git(worktree, "commit", "--allow-empty", "-m", "work nobody pushed")
    if pushed_to:
        git(repo, "update-ref", f"{pushed_to}/{branch}", f"refs/heads/{branch}")
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


@pytest.mark.parametrize(
    "where",
    ["on-it", "elsewhere", "unpushed"],
    ids=["checked-out-kept", "deleted", "unpushed-kept"],
)
def test_archive_in_place_follows_the_same_rule(client, repo, where):
    """As abandon: the checkout stays, and so does its branch while the person
    is on it; archive's own guard keeps one with commits nothing else holds."""
    wid = _paused(client, repo, in_place=True)
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    assert branch
    git(repo, "branch", branch)
    if where == "unpushed":
        git(repo, "commit", "-q", "--allow-empty", "-m", "nobody pushed this")
        git(repo, "branch", "-f", branch)
        git(repo, "reset", "-q", "--hard", "HEAD~1")
    if where == "on-it":
        git(repo, "checkout", "-q", branch)
    _set_status(wid, "completed")

    r = client.post(f"/api/work-items/{wid}/archive")

    assert r.status_code == 200, r.text
    kept = {"on-it": "checked out", "elsewhere": None, "unpushed": "unpushed"}[where]
    assert (
        r.json().items()
        >= {
            "worktree_removed": False,
            "in_place": True,
            "branch_kept": kept,
        }.items()
    )
    assert bool(git_read(repo, "branch", "--list", branch)) is (kept is not None)
    assert repo.is_dir()
    events = client.get(f"/api/work-items/{wid}/events").json()
    payload = next(e["payload"] for e in events if e["type"] == "work_item_archived")
    assert payload.get("branch_kept") == kept


def test_archive_rescues_commits_on_a_detached_head(client, repo):
    """Commits made on a detached HEAD in the worktree are on no branch at
    all, so removing the worktree would orphan them. Archive gives them one,
    `kraft/rescued/<id>`, and says so."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    git(repo, "tag", "so-far", branch)
    git(worktree, "checkout", "-q", "--detach")
    git(worktree, "commit", "--allow-empty", "-m", "detached work")
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
    git(repo, "tag", "so-far", branch)
    git(repo, "branch", "kraft/rescued", branch)
    git(worktree, "checkout", "-q", "--detach")
    git(worktree, "commit", "--allow-empty", "-m", "detached work")
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


@pytest.mark.parametrize("action", ["archive", "abandon"])
def test_a_moved_repository_still_lets_the_worktree_go(client, repo, action):
    """Moving or deleting an old clone is normal. With no repository to run
    git in, archive and abandon remove the worktree directory themselves,
    since its `.git` link points into the missing repository and nothing
    else could reclaim it, and they name the path they could not find."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
    client.post(f"/api/work-items/{wid}/cancel", json={"reason": "later"})
    moved = repo.rename(repo.with_name("moved"))

    body = {"reason": "later"} if action == "abandon" else None
    r = client.post(f"/api/work-items/{wid}/{action}", json=body)

    assert r.status_code == 200, r.text
    assert r.json()["worktree_removed"] is True
    assert r.json()["repo_missing"] == str(repo)
    assert not worktree.exists()
    # The branch was in the repository, and moved with it.
    assert git_read(moved, "branch", "--list", branch)


def test_the_rescue_skips_a_repository_that_is_gone(repo, tmp_path):
    """The rescue names commits in the item's repository, so with that
    directory gone there is nothing to name them in, and no directory to run
    git from: it must not raise out of archive. The worktree here still
    reads (its git lives elsewhere), so the rescue gets as far as git."""
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    git(worktree, "commit", "--allow-empty", "-m", "detached work")

    assert lifecycle._rescue_detached_head(tmp_path / "gone", worktree, "w1") == {}


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
