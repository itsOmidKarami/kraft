"""`kraft.adapters.subprocess.run_task`'s commit identity for a session."""

import os

import pytest
from support.harness import _git, make_repo
from support.worktree import make_item

from kraft.adapters import subprocess as sp
from kraft.config import git_read


@pytest.mark.parametrize(
    ("cwd", "member_email"),
    [("root", None), ("member", "member@example.com")],
    ids=["in-the-root", "fanned-out-into-the-member"],
)
async def test_an_unsandboxed_session_commits_in_a_member_as_the_root(
    database, run_dirs, tmp_path, monkeypatch, cwd, member_email
):
    """Kraft-ju36l (J3): a workspace member is a worktree of its connected
    repository, and Kraft writes no identity into that repository's config.
    A session's commit there is the root's identity all the same, carried in
    the environment -- also for a run fanned out into the member, whose own
    repository's identity is not the item's -- and the member repository's
    config is left as it was."""
    for name in [k for k in os.environ if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))]:
        monkeypatch.delenv(name)
    (tmp_path / "empty.gitconfig").write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty.gitconfig"))
    root, member_repo = make_repo(tmp_path, "root"), make_repo(tmp_path, "member")
    _git(root, "config", "user.email", "root@example.com")
    if member_email:
        _git(member_repo, "config", "user.email", member_email)
    else:
        _git(member_repo, "config", "--unset", "user.email")
    _git(member_repo, "worktree", "add", "-q", "-b", "kraft/w1", str(root / "m"))
    before = (member_repo / ".git" / "config").read_text()
    await make_item(database, root)

    commit = "git commit -q --allow-empty -m work"
    status = await sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        cmd=["sh", "-c", commit if cwd == "member" else f"cd m && {commit}"],
        node_id="n",
        hook_point="on.test.run",
        cwd=root / "m" if cwd == "member" else root,
    )

    assert status == "done"
    authors = git_read(root / "m", "log", "-1", "--format=%ae %ce")
    assert authors == "root@example.com root@example.com"
    assert (member_repo / ".git" / "config").read_text() == before
