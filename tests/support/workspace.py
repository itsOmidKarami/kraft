"""A workspace work item's target, for tests that file one."""


def workspace_target(mounts: dict[str, str], *, root_pointer_policy: str = "ignore"):
    """A workspace `WorkItemTarget` selecting every member of `mounts` (member
    id -> mount path in the root), each member its own repository id."""
    from kraft.templates.environment import WorkItemTarget, Workspace, WorkspaceMember

    workspace = Workspace(
        id="ws",
        root="ws",
        members={m: WorkspaceMember(repository=m, path=p) for m, p in mounts.items()},
    )
    return WorkItemTarget.from_selection(
        workspace, members=list(mounts), root_pointer_policy=root_pointer_policy
    )
