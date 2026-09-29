"""Abandon and archive of a workspace item: each member's worktree and branch
in its connected repository (Kraft-ju36l). A sibling of test_lifecycle.py."""

from __future__ import annotations

from support.harness import _git, make_repo

from kraft.api.routes import lifecycle


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
