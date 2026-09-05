"""The one place that knows how to talk to a local Kraft server.

`kraft mcp` and the `kraft` subcommands are both dispatch tables over this
module; neither holds logic the other lacks. Validation is not duplicated here —
it lives in `api.py`, where the UI already exercises it.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from kraft import auth, config
from kraft.paths import RunDirs, default_run_dir, default_templates_dir


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


def _forbid_self_action(work_item_id: str | None) -> str:
    """Resolve the target of an act call, refusing a worker's own item.

    Design §6 rule 2. A worker session approving its own gate would collapse the
    human-gate model, so the refusal sits at the boundary the agent cannot route
    around rather than in a skill's prose. Client-side because `origin` is known
    only here: `api.py` sees an authenticated local caller either way.
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


def base_url() -> str:
    """Where the local server is listening.

    Mirrors `cli._bind` on host and port but not on its refusal to bind a LAN
    address without a password: that is a rule about *serving*, and this is a
    client. A wildcard bind is rewritten to loopback — `0.0.0.0` is an address to
    listen on, never one to connect to.
    """
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    access = config.load_access(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access["bind"]
    port = int(os.environ.get("KRAFT_PORT") or access["port"])
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def http() -> httpx.AsyncClient:
    """A configured client. Module-level so tests can point it at the ASGI app."""
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    headers = {}
    token = auth.read_mcp_token(run_dir)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=base_url(), headers=headers, timeout=30)


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text


async def _get(path: str, **params) -> dict | list:
    async with http() as session:
        response = await session.get(
            path, params={k: v for k, v in params.items() if v is not None}
        )
    if response.status_code >= 400:
        # An agent reads this string. "404: work item not found" is actionable;
        # an httpx traceback is not.
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
    return response.json()


async def list_work_items(status: str | None = None) -> list[dict]:
    """The board, trimmed to what a caller can act on.

    `GET /work-items` carries the full chain definition per row for the UI's
    progress rendering; forwarding that spends an agent's context on JSON it did
    not ask for.
    """
    payload = await _get("/work-items")
    keep = ("id", "title", "repo", "status", "current_node_id", "pending_gate")
    return [
        {k: item[k] for k in keep}
        for item in payload["items"]
        if status is None or item["status"] == status
    ]


async def get_work_item(work_item_id: str | None = None) -> dict:
    """One item. Defaults to the item this session is standing in."""
    if work_item_id is None:
        work_item_id, _origin = resolve_context()
    if work_item_id is None:
        worktrees = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).worktrees
        raise ValueError(
            f"no work item: pass an id, or run from a Kraft worktree under {worktrees}"
        )
    item = await _get(f"/work-items/{work_item_id}")
    keep = (
        "id",
        "title",
        "repo",
        "status",
        "current_node_id",
        "pending_gate",
        "worktree_path",
        "bead_id",
    )
    return {k: item[k] for k in keep if k in item}


async def search(q: str, limit: int = 20) -> dict:
    """Cross-repo search over specs, plans, and session summaries."""
    return await _get("/search", q=q, limit=limit)


async def _post(path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Status alongside the body: some callers treat a 4xx as a normal outcome
    (a 409 from POST /repos means the repo is already connected)."""
    async with http() as session:
        response = await session.post(path, json=payload or {})
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    return response.status_code, body


async def create_work_item(
    title: str, repo: str | None = None, chain_template: str = "quick-task"
) -> dict:
    """Create a work item. It lands paused: an agent files work, a human starts it.

    `repo` defaults to the repo of the work item this session is standing in,
    which is the common case for a worker filing follow-up work.
    """
    if repo is None:
        work_item_id, _origin = resolve_context()
        if work_item_id is not None:
            repo = (await get_work_item(work_item_id)).get("repo")
    if not repo:
        raise ValueError(
            "no repo: pass one, or run from a Kraft worktree so the repo can be resolved"
        )
    status, body = await _post(
        "/work-items",
        {"title": title, "repo": repo, "chain_template": chain_template, "autostart": False},
    )
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return {"id": body["id"], "status": body.get("status", "paused"), "title": title}


async def ensure_repo(path: str | None = None) -> dict:
    """Register a repo with Kraft if it is not already connected.

    Idempotent by construction: `POST /repos` 409s on a path it already holds,
    and "already connected" is the goal state, not a failure.
    """
    path = path or os.getcwd()
    status, body = await _post("/repos", {"path": path})
    if status == 409:
        probe_status, probed = await _post("/repos/probe", {"path": path})
        if probe_status >= 400:
            raise ValueError(f"kraft {probe_status}: {probed.get('detail', probed)}")
        return {**probed, "already_connected": True}
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return {**body, "already_connected": False}


async def _pending_gate_of(work_item_id: str) -> str:
    gate = (await get_work_item(work_item_id)).get("pending_gate")
    if not gate:
        raise ValueError(f"kraft: no gate is pending on {work_item_id}")
    return gate


async def _act(path: str, payload: dict | None = None) -> dict:
    status, body = await _post(path, payload)
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return body


async def approve_gate(gate: str | None = None, work_item_id: str | None = None) -> dict:
    """Approve the gate a work item is waiting on."""
    target = _forbid_self_action(work_item_id)
    gate = gate or await _pending_gate_of(target)
    return await _act(f"/work-items/{target}/gates/{gate}/approve")


async def reject_gate(note: str, gate: str | None = None, work_item_id: str | None = None) -> dict:
    """Reject the gate a work item is waiting on. The note is required — a
    rejection with no reason strands whoever picks the work up next."""
    if not note or not note.strip():
        raise ValueError("a reject note is required: say what is wrong")
    target = _forbid_self_action(work_item_id)
    gate = gate or await _pending_gate_of(target)
    return await _act(f"/work-items/{target}/gates/{gate}/reject", {"note": note.strip()})


async def pause(work_item_id: str | None = None) -> dict:
    """Stop the running node's sessions. Only an active item can be paused."""
    target = _forbid_self_action(work_item_id)
    return await _act(f"/work-items/{target}/pause")


async def resume(steer: str | None = None, work_item_id: str | None = None) -> dict:
    """Restart a paused item, optionally carrying a steer into the next attempt.

    This is also how a created-paused item is started for the first time: a NULL
    current_node_id resolves to node zero (design §6 rule 1).
    """
    target = _forbid_self_action(work_item_id)
    payload = {"steer": steer.strip()} if steer and steer.strip() else {}
    return await _act(f"/work-items/{target}/resume", payload)
