"""Which work item a session is standing in, and the self-action guard that
reads it."""

from __future__ import annotations

import os
from pathlib import Path

from kraft import config
from kraft.client import transport
from kraft.paths import RunDirs, default_run_dir


def resolve_context(cwd: Path | None = None) -> tuple[str | None, str]:
    """`(work_item_id, origin)` for the session calling us.

    `origin` is `"worker"` only when `$KRAFT_WORK_ITEM_ID` is set, which the
    executor injects into sessions it starts. The self-action guard keys on it,
    so a worker that lost the variable would read as a human — which is why the
    guard and the injection have to ship together (design §4).

    A human-started session is identified by its directory instead: the executor
    lays worktrees out as `run_dirs.worktrees / work_item_id`.
    """
    wid = os.environ.get("KRAFT_WORK_ITEM_ID")
    if wid:
        return wid, "worker"
    worktrees = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).worktrees
    try:
        worktrees = worktrees.resolve()
        here = (cwd or Path.cwd()).resolve()
    except OSError:
        return None, "user"
    for candidate in (here, *here.parents):
        if candidate.parent == worktrees:
            return candidate.name, "user"
    return None, "user"


async def resolve_repo(cwd: Path | None = None) -> str | None:
    """The connected repo the cwd is inside, or None.

    Reads `GET /repos` rather than parsing `repos.yaml`: a local YAML read would
    work with the server down, but it puts a second reader on config the API
    already owns and shapes, and every verb that uses this answer needs the
    server anyway.

    Parents are walked so a submodule checkout resolves to the connected
    superproject. `config.normalized_repo_root`, not a bare `--show-toplevel`,
    so a linked worktree ($KRAFT_HOME worktree or any other) resolves to its
    main checkout instead of having no connected ancestor at all (Kraft-tc33).
    """
    here = cwd or Path.cwd()
    root = config.normalized_repo_root(here)
    if root is None:
        return None
    payload = await transport._get("/repos")
    connected = {entry["path"] for entry in payload["repos"]}
    for candidate in (root, *root.parents):
        if str(candidate) in connected:
            return str(candidate)
    return None


def _forbid_self_action(work_item_id: str | None) -> str:
    """Resolve the target of an act call, refusing a worker's own item.

    Design §6 rule 2. A worker session approving its own gate would collapse the
    human-gate model, so the refusal sits at the boundary the agent cannot route
    around rather than in a skill's prose. Client-side because `origin` is known
    only here: `kraft.api.perimeter` sees an authenticated local caller either way.
    """
    resolved, origin = resolve_context()
    target = work_item_id or resolved
    if target is None:
        raise ValueError("no work item: pass an id, or run from a Kraft worktree")
    if origin == "worker" and target == resolved:
        raise PermissionError(
            f"a worker session cannot act on its own work item ({target}). "
            "Gates are where a human decides; report what you found instead."
        )
    return target


async def resolve_work_item(work_item_id: str | None) -> str:
    """An explicit id, or the item this session is standing in.

    Not `_forbid_self_action`: reading your own logs is exactly what a worker
    session should be able to do. The guard is about acting, not looking.

    Public because `cli.view._cmd_events` needs the resolved id twice — once for the
    events call, once to filter the instance-wide bus — and `cli.py` must not
    become a second definition of what a work item is (Kraft-t5s9).
    """
    if work_item_id:
        return work_item_id
    resolved, _origin = resolve_context()
    if resolved is None:
        raise ValueError("no work item: pass an id, or run from a Kraft worktree")
    return resolved
