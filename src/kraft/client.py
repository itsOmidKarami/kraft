"""The one place that knows how to talk to a local Kraft server.

`kraft admin mcp` and the `kraft` subcommands are both dispatch tables over this
module; neither holds logic the other lacks. Validation is not duplicated here —
it lives in `api.py`, where the UI already exercises it.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
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


async def resolve_repo(cwd: Path | None = None) -> str | None:
    """The connected repo the cwd is inside, or None.

    Reads `GET /repos` rather than parsing `repos.yaml`: a local YAML read would
    work with the server down, but it puts a second reader on config the API
    already owns and shapes, and every verb that uses this answer needs the
    server anyway.

    Parents are walked so a submodule checkout resolves to the connected
    superproject.
    """
    here = cwd or Path.cwd()
    toplevel = config.git_read(here, "rev-parse", "--show-toplevel", expected_failure=True)
    if toplevel is None:
        return None
    payload = await _get("/repos")
    connected = {entry["path"] for entry in payload["repos"]}
    root = Path(toplevel)
    for candidate in (root, *root.parents):
        if str(candidate) in connected:
            return str(candidate)
    return None


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


async def _send(method: str, path: str, **kwargs) -> httpx.Response:
    """Every request goes through here, so a dead server reads the same at both
    front doors — an agent calling through MCP gets this sentence too, not a
    traceback it will try to reason about."""
    try:
        async with http() as session:
            return await session.request(method, path, **kwargs)
    except httpx.ConnectError as exc:
        raise ValueError(f"no Kraft server at {base_url()} — start one with `kraft`") from exc


async def _get(path: str, **params) -> dict | list:
    response = await _send("GET", path, params={k: v for k, v in params.items() if v is not None})
    if response.status_code >= 400:
        # An agent reads this string. "404: work item not found" is actionable;
        # an httpx traceback is not.
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
    return response.json()


async def _delete(url: str, **params) -> None:
    """No body on the way back: DELETE /repos answers 204. The error string is
    `_get`'s, because a 404 an agent reads must be one sentence either way."""
    response = await _send("DELETE", url, params=params)
    if response.status_code >= 400:
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")


async def list_work_items(
    status: str | None = None, *, include_abandoned: bool = False
) -> list[dict]:
    """The board, trimmed to what a caller can act on.

    `GET /work-items` carries the full chain definition per row for the UI's
    progress rendering; forwarding that spends an agent's context on JSON it did
    not ask for.

    Abandoned items are off the board by default. Without the flag there is no
    way to see one again from the CLI, which makes `kraft item abandon` look like a
    delete.
    """
    path = "/work-items?include_abandoned=true" if include_abandoned else "/work-items"
    payload = await _get(path)
    return trim_work_items(payload["items"], status)


def trim_work_items(items: list[dict], status: str | None = None) -> list[dict]:
    """The board rows, trimmed to the fields a caller can act on.

    Split out of `list_work_items` so a caller that needs the envelope too — the
    live board wants `cursor` alongside the rows — gets identical shaping rather
    than a second definition of what a work item is.
    """
    keep = ("id", "title", "repo", "status", "current_node_id", "pending_gate")
    return [
        {k: item[k] for k in keep} for item in items if status is None or item["status"] == status
    ]


def _next_node_id(item: dict) -> str | None:
    """The node after the current one, or None at the end of the chain.

    A NULL `current_node_id` is a work item nobody has started: node zero is what
    comes next, which is the same rule `resume` follows (design §6 rule 1).
    Saying "done" there would be a lie, and this field exists so an agent can say
    where work got to (Kraft-9rs).
    """
    ids = [node["id"] for node in (item.get("chain_definition") or {}).get("nodes") or []]
    current = item.get("current_node_id")
    if current is None:
        after = 0
    elif current in ids:
        after = ids.index(current) + 1
    else:
        after = len(ids)  # a node the chain no longer has: nothing to promise
    return ids[after] if after < len(ids) else None


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
        "gate_artifact",
        "worktree_path",
        "bead_id",
    )
    return {**{k: item[k] for k in keep if k in item}, "next_node_id": _next_node_id(item)}


async def resolve_work_item(work_item_id: str | None) -> str:
    """An explicit id, or the item this session is standing in.

    Not `_forbid_self_action`: reading your own logs is exactly what a worker
    session should be able to do. The guard is about acting, not looking.

    Public because `cli._cmd_events` needs the resolved id twice — once for the
    events call, once to filter the instance-wide bus — and `cli.py` must not
    become a second definition of what a work item is (Kraft-t5s9).
    """
    if work_item_id:
        return work_item_id
    resolved, _origin = resolve_context()
    if resolved is None:
        raise ValueError("no work item: pass an id, or run from a Kraft worktree")
    return resolved


async def worker_sessions(work_item_id: str | None = None) -> list[dict]:
    """The item's agent sessions, oldest first.

    Reads the untrimmed detail endpoint: `get_work_item` deliberately drops
    `worker_sessions` so an agent's context is not spent on it, which means the
    log verbs cannot reuse it.
    """
    item = await _get(f"/work-items/{await resolve_work_item(work_item_id)}")
    return item.get("worker_sessions", [])


async def latest_session(work_item_id: str | None = None) -> dict:
    """The most recent session — "what is it doing now", which is the question
    `kraft view logs` is asked."""
    sessions = await worker_sessions(work_item_id)
    if not sessions:
        raise ValueError("no worker session has run for this work item yet")
    return sessions[-1]


async def events(work_item_id: str | None = None, after_seq: int = 0) -> list[dict]:
    """The chain's own history: node transitions, gate decisions, escalations.

    This is the "why is it stopped" view, where the log is the "what is it
    saying" view.
    """
    target = await resolve_work_item(work_item_id)
    return await _get(f"/work-items/{target}/events", after_seq=after_seq)


async def log_backlog(session_id: str, limit: int | None = None) -> list[dict]:
    """The lines already written to one worker session's log, oldest first.

    Here rather than in `cli.py` because this module is the only one that is
    supposed to know an API path — and because the MCP door reads a backlog the
    same way, which it could not do without copying the request.

    `limit` is `None` for everything and `0` for none; `0` cannot be spelled as
    a falsy "no limit", which is the bug the slice invites.
    """
    payload = await _get(f"/worker-sessions/{session_id}/log", format="jsonl")
    lines = payload.get("lines", [])
    if limit is None:
        return lines
    return lines[-limit:] if limit > 0 else []


async def stream_log(session_id: str, after_line: int = 0) -> AsyncIterator[dict]:
    """Follow one worker session's log until the session stops.

    The server's `_tail` sends `data: {json}` frames and one terminal
    `event: end` when the session is no longer running, so the iterator ends on
    its own — a follow that outlives the agent is worse than no follow.

    `after_line` is applied here rather than sent: the follow endpoint replays
    from the top of the file and takes no offset, so skipping is the client's
    job. Cheap, and it keeps the resume semantics in one place.

    Not an MCP tool: a tool returns a value and a generator has none. An agent
    that wants history calls `events()`.
    """
    async with http() as session:
        try:
            async with session.stream(
                "GET",
                f"/worker-sessions/{session_id}/log",
                params={"format": "jsonl", "follow": "true"},
                timeout=None,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
                async for raw in response.aiter_lines():
                    if raw.startswith("event: end"):
                        return
                    if raw.startswith("data: "):
                        payload = raw[6:].strip()
                        if payload and payload != "{}":
                            line = json.loads(payload)
                            if line.get("n", 0) >= after_line:
                                yield line
        except httpx.ConnectError as exc:
            raise ValueError(f"no Kraft server at {base_url()} — start one with `kraft`") from exc
        except httpx.HTTPError as exc:
            # a follow outlives its request: a read error mid-stream is the
            # server going away, and reads as a sentence like any other failure
            raise ValueError(f"the Kraft server at {base_url()} closed the log stream") from exc


async def stream_events(after_seq: int = 0) -> AsyncIterator[dict]:
    """The live event bus — the same stream the board's UI redraws from.

    The bearer goes in a header: the websocket handler accepts it as of the
    /ws/events auth fix, because a CLI has no session cookie to offer.
    """
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed, InvalidHandshake

    url = base_url().replace("http://", "ws://", 1) + f"/ws/events?after_seq={after_seq}"
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    token = auth.read_mcp_token(run_dir)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with connect(url, additional_headers=headers) as socket:
            async for message in socket:
                yield json.loads(message)
    except (OSError, InvalidHandshake) as exc:
        raise ValueError(f"no Kraft server at {base_url()} — start one with `kraft`") from exc
    except ConnectionClosed as exc:
        # mid-stream: the server went away while we were watching, which is a
        # sentence like any other failure, not a traceback out of the event loop
        raise ValueError(f"the Kraft server at {base_url()} closed the event stream") from exc


async def diff(work_item_id: str | None = None) -> dict:
    """What the agent changed, against the item's `base_ref`.

    The payload is passed through untouched — `truncated` and `untracked` are
    the two fields a renderer must not drop, and passing the dict whole is how
    that is guaranteed rather than remembered.
    """
    return await _get(f"/work-items/{await resolve_work_item(work_item_id)}/diff")


async def artifact(work_item_id: str | None = None) -> dict:
    """The document the item's pending gate is a decision about.

    404s when there is no pending gate or the hook produced no document —
    reading a gate's artifact is only meaningful while the gate is open.
    """
    return await _get(f"/work-items/{await resolve_work_item(work_item_id)}/artifact")


async def documents(work_item_id: str | None = None) -> list[dict]:
    """The specs, plans and summaries the indexer linked to this item. No
    content: that is one `document()` call per id."""
    payload = await _get(f"/work-items/{await resolve_work_item(work_item_id)}/documents")
    return payload.get("documents", [])


async def document(doc_id: str) -> dict:
    return await _get(f"/documents/{doc_id}")


async def open_document(doc_id: str, editor: str | None = None) -> dict:
    """Hand the document to an editor on the server's machine.

    Reuses the server's editor table and its 501-when-headless answer rather
    than growing a second launcher here.
    """
    return await _act(f"/documents/{doc_id}/open", {"editor": editor} if editor else {})


async def repos() -> list[dict]:
    """Every connected repo, as the API shapes it.

    This and `resolve_repo` are the only readers of the repo list on the client
    side, and neither parses `repos.yaml`: a local YAML read would work with the
    server down, but would drift from the API's shaping (spec D §4).
    """
    return (await _get("/repos")).get("repos", [])


async def open_worktree(work_item_id: str | None = None, editor: str | None = None) -> dict:
    """Open the item's worktree in an editor on the server's machine — the same
    launch as the UI's "Open worktree", including its 501 when headless."""
    target = await resolve_work_item(work_item_id)
    return await _act(f"/work-items/{target}/open-worktree", {"editor": editor} if editor else {})


async def health() -> dict:
    """The server's own view of itself: invalid config, index state, reattach."""
    return await _get("/health")


async def reindex(repo: str | None = None) -> dict:
    """Rescan one repo's documents, or every repo's. Returns the change counts.

    Not through `_act`: `/index/rescan` takes `repo` as a query parameter, and
    `_post` only sends JSON bodies.
    """
    response = await _send("POST", "/index/rescan", params={"repo": repo} if repo else None)
    if response.status_code >= 400:
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
    return response.json()


async def search(q: str, limit: int = 20) -> dict:
    """Cross-repo search over specs, plans, and session summaries."""
    return await _get("/search", q=q, limit=limit)


async def _post(path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Status alongside the body: some callers treat a 4xx as a normal outcome
    (a 409 from POST /repos means the repo is already connected)."""
    response = await _send("POST", path, json=payload or {})
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
    if repo is None:
        # The cwd's connected repo, which is what `_repo_scope` already resolves
        # for the CLI door. Doing it here too means both doors agree, and that
        # the error below only fires when there really is no repo to find.
        repo = await resolve_repo()
    if not repo:
        raise ValueError(_no_repo_message())
    status, body = await _post(
        "/work-items",
        {"title": title, "repo": repo, "chain_template": chain_template, "autostart": False},
    )
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return {"id": body["id"], "status": body.get("status", "paused"), "title": title}


def _no_repo_message(cwd: Path | None = None) -> str:
    """Advice that matches the situation, not the API's field list.

    Standing in an ordinary git repo that is simply not connected is the common
    way to reach this, and "run from a Kraft worktree" is useless advice there —
    the fix is one `kraft repo connect` away (spec A §8).
    """
    toplevel = config.git_read(
        cwd or Path.cwd(), "rev-parse", "--show-toplevel", expected_failure=True
    )
    if toplevel:
        return (
            f"no repo: {toplevel} is a git repo but is not connected to Kraft — "
            f"connect it with `kraft repo connect {toplevel}`, or name a repo explicitly"
        )
    return (
        "no repo: name one explicitly, or run from a connected repo or a Kraft worktree "
        "so it can be resolved"
    )


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


async def disconnect_repo(path: str | None = None) -> dict:
    """Forget a repo. No repo file is touched and no work item is dropped.

    The path is resolved through the probe first, for the same reason
    `ensure_repo` probes on a 409: after Kraft-97e `POST /repos` stores the main
    checkout, and an agent standing in a worktree of that repo would otherwise
    send the worktree path and get a 404 — `api._connected` matches the raw path
    or its `resolve()` and knows nothing about worktrees. A probe that fails (not
    a git directory) falls back to the path as given, so the 404 still says what
    is wrong rather than being swallowed here.
    """
    path = path or os.getcwd()
    status, probed = await _post("/repos/probe", {"path": path})
    target = probed["path"] if status < 400 else path
    await _delete("/repos", path=target)
    return {"path": target}


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


async def abandon(work_item_id: str | None = None) -> dict:
    """Terminal. Removes the worktree, destroying anything uncommitted in it."""
    target = _forbid_self_action(work_item_id)
    return await _act(f"/work-items/{target}/abandon")


async def resume(steer: str | None = None, work_item_id: str | None = None) -> dict:
    """Restart a paused item, optionally carrying a steer into the next attempt.

    This is also how a created-paused item is started for the first time: a NULL
    current_node_id resolves to node zero (design §6 rule 1).
    """
    target = _forbid_self_action(work_item_id)
    payload = {"steer": steer.strip()} if steer and steer.strip() else {}
    return await _act(f"/work-items/{target}/resume", payload)


async def retry(steer: str | None = None, work_item_id: str | None = None) -> dict:
    """Re-run the node an item stopped on, optionally with a steer.

    The only door back onto a `needs_human` stop: resume wants `paused`, pause
    wants `running`, and approve/reject want a pending gate. `_forbid_self_action`
    rather than `resolve_work_item`, because a retry restarts the node that is
    running you.
    """
    target = _forbid_self_action(work_item_id)
    payload = {"steer": steer.strip()} if steer and steer.strip() else {}
    return await _act(f"/work-items/{target}/retry", payload)
