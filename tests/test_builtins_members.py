"""`kraft.builtins.ensure_worktree`'s workspace members (Kraft-ju36l): each
one a linked worktree of its connected repository, never a gitdir under the
root's worktree gitdir, which a sandboxed worker writes. A sibling of
test_builtins.py."""

from pathlib import Path

import pytest
from support import worktree as wtree
from support.harness import _git, entry_of, make_repo, make_repo_with_submodule, v1_chain
from support.workspace import workspace_target

from kraft import store
from kraft.config import git_read

_ONE_NODE = [
    {"id": "n", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


def _workspace_item(database, root, mounts):
    chain = v1_chain(_ONE_NODE, repo=root, target=workspace_target(mounts))
    return wtree.make_item(database, root, materialized_chain=chain.to_json())


def _members(sub):
    """`repositories` connecting member `pkg` at `sub`, as `LaunchContext` has them."""
    return {"pkg": entry_of({"id": "pkg", "path": str(sub)})}


def _common(cwd):
    return Path(git_read(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"))


async def test_a_workspace_member_is_a_linked_worktree_of_its_connected_repository(
    tmp_path, database, run_dirs
):
    """Kraft-ju36l: a member's gitdir is its connected repository's, never one
    under the root's worktree gitdir, which a sandboxed worker writes. It is
    on the item's branch at the commit the root records, that branch lives in
    the member's repository, and the root still counts it initialized."""
    root, sub = make_repo_with_submodule(tmp_path)
    # As in a clone that never ran `submodule init`: Kraft makes it active.
    _git(root, "config", "--remove-section", "submodule.repos/pkg")
    await _workspace_item(database, root, {"pkg": "repos/pkg"})
    gitlink = git_read(root, "rev-parse", "HEAD:repos/pkg")

    worktree = await wtree.ensure(database, run_dirs, root, repositories=_members(sub))

    member, branch = worktree / "repos" / "pkg", wtree.branch(database)
    assert _common(member) == _common(sub)
    assert git_read(member, "rev-parse", "HEAD") == gitlink
    assert git_read(sub, "rev-parse", f"refs/heads/{branch}") == gitlink
    # `-` would be an uninitialized member; `git_read` strips the leading space.
    assert git_read(worktree, "submodule", "status").startswith(f"{gitlink} repos/pkg")
    gitdir = Path(git_read(worktree, "rev-parse", "--path-format=absolute", "--git-dir"))
    assert not (gitdir / "modules").exists()


async def test_a_member_from_an_operators_submodule_checkout_is_a_worktree_of_it(
    tmp_path, database, run_dirs
):
    """The auto-connected member: its repository is the operator's own
    submodule checkout inside the root, whose gitdir sets `core.worktree`.
    The member is still the item's own checkout, and an edit in it never
    shows in the operator's."""
    root, _sub = make_repo_with_submodule(tmp_path)
    _git(root, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    operators = root / "repos" / "pkg"
    await _workspace_item(database, root, {"pkg": "repos/pkg"})

    worktree = await wtree.ensure(database, run_dirs, root, repositories=_members(operators))

    member = worktree / "repos" / "pkg"
    assert _common(member) == _common(operators)
    assert Path(git_read(member, "rev-parse", "--show-toplevel")) == member.resolve()
    (member / "edited.txt").write_text("the item's\n")
    assert git_read(operators, "status", "--porcelain") == ""


async def test_a_sandboxed_member_with_no_connected_repository_is_never_cloned_under_the_root(
    tmp_path, database, run_dirs
):
    """`submodule update --init` is the old layout: the member's gitdir under
    the root's worktree gitdir, which a sandboxed worker writes. A sandboxed
    item whose member has no connected repository stops instead, leaving no
    worktree for a retry to take as already set up."""
    root, _sub = make_repo_with_submodule(tmp_path)
    await _workspace_item(database, root, {"pkg": "repos/pkg"})

    with pytest.raises(RuntimeError, match="no connected repository is known for repos/pkg"):
        await wtree.ensure(database, run_dirs, root, sandbox={"kind": "docker", "image": "i"})

    assert not list((root / ".git" / "worktrees").glob("*/modules"))
    assert not (run_dirs.worktrees / "w1").exists()


async def test_a_missing_member_repository_stops_setup_and_leaves_no_worktree(
    tmp_path, database, run_dirs
):
    """A connected member whose path is gone is a stop, and the worktree goes
    with it: a retry would otherwise take the early return and run with an
    empty member directory."""
    root, _sub = make_repo_with_submodule(tmp_path)
    await _workspace_item(database, root, {"pkg": "repos/pkg"})

    with pytest.raises(RuntimeError, match="connected repository .* is missing"):
        await wtree.ensure(database, run_dirs, root, repositories=_members(tmp_path / "gone"))

    assert not (run_dirs.worktrees / "w1").exists()


async def test_a_retry_after_a_later_member_failed_records_each_member_once(
    tmp_path, database, run_dirs
):
    """An earlier member that checked out fine must leave no `work_item_repos`
    row behind when a later one fails: the discard removes the worktree, and
    the retry would record the earlier member a second time."""
    root, sub = make_repo_with_submodule(tmp_path)
    pkg2 = make_repo(tmp_path, name="pkg2")
    _git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(pkg2), "repos/pkg2")
    _git(root, "commit", "-qm", "a second member")
    await _workspace_item(database, root, {"pkg": "repos/pkg", "pkg2": "repos/pkg2"})
    paths = {"repos/pkg": sub, "repos/pkg2": pkg2}
    first, later = store.merge_rank_order(list(paths))
    ids = {"repos/pkg": "pkg", "repos/pkg2": "pkg2"}

    def connected(broken):
        return {
            ids[rel]: entry_of(
                {"id": ids[rel], "path": str(tmp_path / "gone" if broken == rel else p)}
            )
            for rel, p in paths.items()
        }

    with pytest.raises(RuntimeError):
        await wtree.ensure(database, run_dirs, root, repositories=connected(later))
    await wtree.ensure(database, run_dirs, root, repositories=connected(None))

    repos = database.read(lambda c: store.repos_for(c, "w1"))
    assert sorted(r["role"] for r in repos) == ["root", "submodule", "submodule"]


async def test_a_gitmodules_the_setup_command_rewrote_never_reaches_the_roots_config(
    tmp_path, database, run_dirs
):
    """The worktree's `.gitmodules` is the worker's to write, and a setup
    command runs before the members are checked out. Renamed, with an
    attacker's URL, it must not reach the operator's root repository config,
    where it would outlive the item."""
    root, sub = make_repo_with_submodule(tmp_path)
    await _workspace_item(database, root, {"pkg": "repos/pkg"})
    plant = (
        "git config -f .gitmodules --rename-section submodule.repos/pkg submodule.evil && "
        "git config -f .gitmodules submodule.evil.url https://attacker.invalid/x"
    )

    await wtree.ensure(
        database,
        run_dirs,
        root,
        repo_entry=entry_of({"setup_command": plant}),
        repositories=_members(sub),
    )

    config = (root / ".git" / "config").read_text()
    assert "attacker.invalid" not in config and '"evil"' not in config
