"""A workspace work item's target, and a filed workspace item, for tests
that file one."""

import os
from pathlib import Path
from types import SimpleNamespace

from support import worktree as wtree
from support.harness import (
    ALLOW_FILE,
    entry_of,
    git,
    make_repo,
    make_repo_with_submodule,
    v1_chain,
    v1_resolved,
)


def workspace_target(
    mounts: dict[str, str], *, root_pointer_policy: str = "ignore", base_branch: str | None = None
):
    """A workspace `WorkItemTarget` selecting every member of `mounts` (member
    id -> mount path in the root), each member its own repository id."""
    from kraft.templates.environment import WorkItemTarget, Workspace, WorkspaceMember

    workspace = Workspace(
        id="ws",
        root="ws",
        members={m: WorkspaceMember(repository=m, path=p) for m, p in mounts.items()},
    )
    return WorkItemTarget.from_selection(
        workspace,
        members=list(mounts),
        root_pointer_policy=root_pointer_policy,
        base_branch=base_branch,
    )


def repositories(tmp_path, *members: str) -> dict:
    """`launch.repositories` for `workspace_item`'s `members`: each one's
    connected repository, the operator's clone of the repository the root's
    `.gitmodules` names -- so that one is what a member's pushes reach."""
    return {m: entry_of({"id": m, "path": str(tmp_path / f"{m}-connected")}) for m in members}


def nested_repositories(root: Path, mounts: dict[str, str]) -> dict:
    """`launch.repositories` connecting each member at its submodule checkout
    in the root: the auto-connected case, whose common gitdir lies inside the
    root's (`.git/modules/<path>`)."""
    return {m: entry_of({"id": m, "path": str(root / p)}) for m, p in mounts.items()}


async def workspace_item(
    database,
    run_dirs,
    tmp_path,
    tasks,
    *,
    pointer="ignore",
    second=False,
    legacy=False,
    nodes=None,
    base_branch=None,
    nested=False,
    **materialize,
):
    """A root with one submodule `pkg` at `repos/pkg` (and `pkg2` at
    `repos/pkg2` when `second`), filed as a workspace item selecting them under
    root-pointer policy `pointer`, on one exec node of `tasks` (or the chain
    `nodes`); its checkout assembled. Each member's connected repository is a
    clone of its own (`repositories`), or when `nested` the root's own
    submodule checkout (`nested_repositories`). `materialize` (an
    `effective_policy`, `repository_policies`) goes to `materialize`, as intake
    passes them. Returns `(row, node, worktree)`."""
    root, pkg = make_repo_with_submodule(tmp_path)
    mounts = {"pkg": "repos/pkg"}
    connected = {"pkg": pkg}
    if second:
        pkg2 = make_repo(tmp_path, name="pkg2")
        git(root, *ALLOW_FILE, "submodule", "add", str(pkg2), "repos/pkg2")
        git(root, "commit", "-qm", "add a second submodule")
        mounts["pkg2"] = "repos/pkg2"
        connected["pkg2"] = pkg2
    target = (
        None
        if legacy
        else workspace_target(mounts, root_pointer_policy=pointer, base_branch=base_branch)
    )
    nodes = nodes or [{"id": "n", "kind": "exec", "tasks": tasks}]
    chain = (
        v1_resolved(nodes).materialize(target=target, **materialize)
        if materialize
        else v1_chain(nodes, repo=root, target=target)
    )
    # `legacy`: the shape every item filed before Task 10 has -- a
    # single-repository target, its submodules and pointer policy in columns.
    columns = {"submodules": list(mounts.values()), "root_merge_policy": pointer} if legacy else {}
    if base_branch:
        # The base the item names has to be on origin before its checkout is
        # cut from it (Kraft-wz6vz).
        git(root, "branch", base_branch)
        git(tmp_path, "clone", "-q", "--bare", str(root), str(tmp_path / "root-origin.git"))
        git(root, "remote", "add", "origin", str(tmp_path / "root-origin.git"))
    await wtree.make_item(database, root, materialized_chain=chain.to_json(), **columns)
    # Each member checked out of its connected repository, as the walk does
    # with `launch.repositories` (Kraft-ju36l); a legacy item has no ids.
    for m, origin in ({} if legacy or nested else connected).items():
        git(tmp_path, "clone", "-q", str(origin), str(tmp_path / f"{m}-connected"))
    connect = nested_repositories(root, mounts) if nested else repositories(tmp_path, *connected)
    worktree = await wtree.ensure(
        database, run_dirs, root, repositories=None if legacy else connect
    )
    row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
    return row, chain.chain.nodes[0], worktree


#: What `only_the_root_has_an_identity` sets the root's email to.
ROOT_EMAIL = "root@example.com"


def only_the_root_has_an_identity(monkeypatch, tmp_path, row) -> None:
    """No commit identity anywhere git would look -- environment, global or
    system config, a member's connected repository -- but in the item's root
    repository, whose email becomes `ROOT_EMAIL` (Kraft-ju36l, J3)."""
    git(Path(row["repo"]), "config", "user.email", ROOT_EMAIL)
    for name in [k for k in os.environ if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))]:
        monkeypatch.delenv(name)
    (tmp_path / "no-identity.gitconfig").write_text("[user]\n\tuseConfigOnly = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-identity.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def member_checkout(tmp_path, branch: str, *, nested: bool = False) -> SimpleNamespace:
    """A root worktree `wt` on `branch`, and its member `repos/pkg` laid out
    as `ensure_worktree` lays one out: a linked worktree, on `branch`, of the
    member's connected repository `m` -- a repository of its own, or when
    `nested` the root's own submodule checkout, whose common gitdir lies
    inside the root's. `checkout` is what `stops.sandbox_checkout` hands a
    sandboxed launch of it (Kraft-ju36l)."""
    from kraft.worker.sandbox import Checkout, member_gitdirs

    root, sub = make_repo_with_submodule(tmp_path)
    wt = tmp_path / "wt"
    git(root, "worktree", "add", "-q", "-b", branch, str(wt))
    m = root / "repos" / "pkg" if nested else sub
    git(m, "worktree", "add", "-q", "-b", branch, str(wt / "repos" / "pkg"))
    members = {"repos/pkg": member_gitdirs(m, wt, "repos/pkg")}
    return SimpleNamespace(root=root, m=m, wt=wt, checkout=Checkout(wt, members))
