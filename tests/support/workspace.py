"""A workspace work item's target, and a filed workspace item, for tests
that file one."""

import dataclasses

from support import worktree as wtree
from support.harness import _git, make_repo, make_repo_with_submodule, v1_chain


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
    **materialize,
):
    """A root with one submodule `pkg` at `repos/pkg` (and `pkg2` at
    `repos/pkg2` when `second`), filed as a workspace item selecting them under
    root-pointer policy `pointer`, on one exec node of `tasks` (or the chain
    `nodes`); its checkout assembled. Returns `(row, node, worktree)`."""
    root, _ = make_repo_with_submodule(tmp_path)
    mounts = {"pkg": "repos/pkg"}
    if second:
        pkg2 = make_repo(tmp_path, name="pkg2")
        _git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(pkg2), "repos/pkg2")
        _git(root, "commit", "-qm", "add a second submodule")
        mounts["pkg2"] = "repos/pkg2"
    chain = v1_chain(
        nodes or [{"id": "n", "kind": "exec", "tasks": tasks}],
        repo=root,
        target=None
        if legacy
        else workspace_target(mounts, root_pointer_policy=pointer, base_branch=base_branch),
    )
    if materialize:
        # Built past `materialize`, as an item filed before Ruling 180 froze
        # it: `materialize` now refuses a sandbox over submodule mounts
        # (Kraft-dshto), and dispatch's per-repository resolution is what
        # this pins. The chain declares no policy, so no layer is skipped.
        chain = dataclasses.replace(
            chain,
            policy=materialize["effective_policy"],
            repository_policies=materialize["repository_policies"],
        )
    # `legacy`: the shape every item filed before Task 10 has -- a
    # single-repository target, its submodules and pointer policy in columns.
    columns = {"submodules": list(mounts.values()), "root_merge_policy": pointer} if legacy else {}
    if base_branch:
        # The base the item names has to be on origin before its checkout is
        # cut from it (Kraft-wz6vz).
        _git(root, "branch", base_branch)
        _git(tmp_path, "clone", "-q", "--bare", str(root), str(tmp_path / "root-origin.git"))
        _git(root, "remote", "add", "origin", str(tmp_path / "root-origin.git"))
    await wtree.make_item(database, root, materialized_chain=chain.to_json(), **columns)
    worktree = await wtree.ensure(database, run_dirs, root)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())
    return row, chain.chain.nodes[0], worktree
