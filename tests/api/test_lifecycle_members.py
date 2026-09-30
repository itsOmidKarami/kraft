"""Abandon and archive of a workspace item: each member's worktree and branch
in its connected repository (Kraft-ju36l). A sibling of test_lifecycle.py."""

from __future__ import annotations

import os
from pathlib import Path

from support.api import _poll_events, _set_status
from support.harness import _git, make_repo, make_repo_with_submodule

from kraft.adapters.forge.git import PUSHED_REFS
from kraft.api.routes import lifecycle
from kraft.config import git_read


def test_abandon_prunes_each_members_worktree_and_branch(client, tmp_path):
    """Kraft-ju36l: a workspace member is a worktree of its connected
    repository, on the item's branch there. Abandon reclaims both, as it does
    the root's, so the operator's member repository keeps nothing of it --
    Kraft's record of pushing the branch included (Kraft-m7ppj)."""
    root = make_repo_with_submodule(tmp_path, submodule_path="libs/a")[0].resolve()
    member_repo = root / "libs" / "a"
    r = client.post("/api/repos", json={"path": str(root), "setup_command": ""})
    assert r.status_code == 201, r.text
    item = {"title": "t", "repo": str(root), "workspace": "ws", "members": ["a"]}
    wid = client.post(
        "/api/work-items", json=item | {"chain_template": "default", "autostart": True}
    ).json()["id"]
    _poll_events(client, wid, "gate_requested")
    branch = client.get(f"/api/work-items/{wid}").json()["branch"]
    worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid

    def worktrees():
        return len(git_read(member_repo, "worktree", "list").splitlines())

    assert worktree.is_dir() and worktrees() == 2, "fixture never made a member worktree"
    record = f"{PUSHED_REFS}/{branch}"
    for repo in (root, member_repo):
        _git(repo, "update-ref", record, "HEAD")
    _set_status(wid, "paused")

    r = client.post(f"/api/work-items/{wid}/abandon")

    assert r.status_code == 200, r.text
    assert not worktree.exists()
    assert worktrees() == 1
    assert not git_read(member_repo, "branch", "--list", branch)
    for repo in (root, member_repo):
        assert (
            git_read(repo, "rev-parse", "--verify", "--quiet", record, expected_failure=True)
            is None
        )


async def test_a_member_repository_gone_before_abandon_leaves_the_rest_of_the_teardown(
    tmp_path, monkeypatch
):
    """The row is abandoned already: a member repository moved or deleted
    since must not raise out and skip the refs and attachments after it."""
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "kraft/w1", str(worktree))
    dropped = []
    monkeypatch.setattr(lifecycle.node_runs, "drop_refs", lambda _r, wid: dropped.append(wid))

    removed = await lifecycle._remove_worktree(
        repo, worktree, "kraft/w1", "w1", [tmp_path / "gone"]
    )

    assert removed and not worktree.exists()
    assert dropped == ["w1"]
