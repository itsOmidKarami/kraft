"""Everything that only reads: the board, one item, its documents, its
streams, cross-repo search."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from kraft import auth
from kraft.client import context, transport
from kraft.paths import RunDirs, default_run_dir


async def board(
    status: str | None = None, *, include_abandoned: bool = False
) -> tuple[list[dict], int]:
    """The trimmed board rows, and the event cursor they reflect.

    The cursor comes back with the rows so a live view can start its stream at
    *now*: connecting at seq 0 replays every event the server ever committed,
    redrawing the board once per historical row. `GET /work-items` has always
    carried it in the envelope; `list_work_items` drops it, which is why
    `_cmd_watch` used to reach for `_get` directly (Kraft-8okl).

    A tuple rather than a `Board` dataclass, and a second function rather than a
    `with_cursor=True` flag on `list_work_items` that returns two different
    types: two fields, one caller that needs both.
    """
    path = "/work-items?include_abandoned=true" if include_abandoned else "/work-items"
    payload = await transport._get(path)
    return trim_work_items(payload["items"], status), payload["cursor"]


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
    return (await board(status, include_abandoned=include_abandoned))[0]


def trim_work_items(items: list[dict], status: str | None = None) -> list[dict]:
    """The board rows, trimmed to the fields a caller can act on.

    Split out of `list_work_items` so a caller that needs the envelope too — the
    live board wants `cursor` alongside the rows — gets identical shaping rather
    than a second definition of what a work item is.
    """
    keep = ("id", "title", "repo", "status", "current_node_id", "pending_gate")
    return [
        {**{k: item[k] for k in keep}, "progress": item.get("progress")}
        for item in items
        if status is None or item["status"] == status
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


async def get_work_item(work_item_id: str | None = None, *, full: bool = False) -> dict:
    """One item. Defaults to the item this session is standing in.

    Trimmed so an agent's context is not spent on the chain and its sessions;
    `full` keeps the detail endpoint's whole payload, which is what
    `kraft view show --json` prints (Kraft-w17d)."""
    if work_item_id is None:
        work_item_id, _origin = context.resolve_context()
    if work_item_id is None:
        worktrees = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())).worktrees
        raise ValueError(
            f"no work item: pass an id, or run from a Kraft worktree under {worktrees}"
        )
    item = await transport._get(f"/work-items/{work_item_id}")
    if full:
        return {**item, "next_node_id": _next_node_id(item)}
    keep = (
        "id",
        "title",
        "description",
        "repo",
        "status",
        "current_node_id",
        "pending_gate",
        "gate_artifact",
        "worktree_path",
        "bead_id",
        # what an attached spec/plan trimmed, and the trim itself — an agent
        # confirming a handoff landed needs to see both (Kraft-82gz).
        "attachments",
        # where the implementer is in its plan, "3 of 6 · title" (None off that node)
        "progress",
    )
    return {
        **{k: item[k] for k in keep if k in item},
        # The item's own policy override (Kraft-ab1bh), only when it has one.
        **({"policy_override": item["policy_override"]} if item.get("policy_override") else {}),
        "next_node_id": _next_node_id(item),
    }


async def worker_sessions(work_item_id: str | None = None) -> list[dict]:
    """The item's agent sessions, oldest first.

    Reads the untrimmed detail endpoint: `get_work_item` deliberately drops
    `worker_sessions` so an agent's context is not spent on it, which means the
    log verbs cannot reuse it.
    """
    item = await transport._get(f"/work-items/{await context.resolve_work_item(work_item_id)}")
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
    target = await context.resolve_work_item(work_item_id)
    return await transport._get(f"/work-items/{target}/events", after_seq=after_seq)


async def log_backlog(session_id: str, limit: int | None = None) -> list[dict]:
    """The lines already written to one worker session's log, oldest first.

    Here rather than in `cli.py` because this module is the only one that is
    supposed to know an API path — and because the MCP door reads a backlog the
    same way, which it could not do without copying the request.

    `limit` is `None` for everything and `0` for none; `0` cannot be spelled as
    a falsy "no limit", which is the bug the slice invites.

    The server bounds what it returns and leads with a `truncated` marker row
    when it had to. `limit` is the reader's own cut, the marker the server's:
    it stays whenever the slice reaches back to it, so the cap is never silent.
    """
    payload = await transport._get(f"/worker-sessions/{session_id}/log", format="jsonl")
    lines = payload.get("lines", [])
    if limit is None:
        return lines
    rows = [line for line in lines if "truncated" not in line]
    kept = rows[-limit:] if limit > 0 else []
    if kept and len(kept) == len(rows):
        return [line for line in lines if "truncated" in line] + kept
    return kept


async def stream_log(session_id: str, after_line: int = 0) -> AsyncIterator[dict]:
    """Follow one worker session's log until the session stops.

    The server's `_tail` sends `data: {json}` frames and one terminal
    `event: end` when the session is no longer running, so the iterator ends on
    its own — a follow that outlives the agent is worse than no follow.

    `after_line` is applied here rather than sent: the follow endpoint replays
    the log's bounded tail (a `truncated` marker row, `n` -1, first when it
    had to cut) and takes no offset, so skipping is the client's
    job. Cheap, and it keeps the resume semantics in one place.

    Not an MCP tool: a tool returns a value and a generator has none. An agent
    that wants history calls `events()`.
    """
    async with transport.http() as session:
        try:
            async with session.stream(
                "GET",
                transport._api(f"/worker-sessions/{session_id}/log"),
                params={"format": "jsonl", "follow": "true"},
                timeout=None,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise ValueError(f"kraft {response.status_code}: {transport._detail(response)}")
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
            raise ValueError(
                f"no Kraft server at {transport.base_url()} — start one with `kraft`"
            ) from exc
        except httpx.HTTPError as exc:
            # a follow outlives its request: a read error mid-stream is the
            # server going away, and reads as a sentence like any other failure
            raise ValueError(
                f"the Kraft server at {transport.base_url()} closed the log stream"
            ) from exc


async def permission_request(tool_name: str, input: dict, tool_use_id: str | None = None) -> dict:
    """Ask Kraft whether this worker session may use `tool_name` (Kraft-oor).

    Fails closed, and returns rather than raises: the caller is an MCP tool the
    agent CLI is waiting on, and every failure has to come back in the CLI's own
    contract shape. An unreachable server is a denial -- exactly what an
    unanswered ask is today, where failing open would make a Kraft outage a
    permission grant.
    """
    sid = os.environ.get("KRAFT_SESSION_ID")
    if not sid:
        return {"behavior": "deny", "message": "not a Kraft worker session"}
    try:
        status, body = await transport._post(
            f"/worker-sessions/{sid}/permission",
            {"tool_name": tool_name, "input": input, "tool_use_id": tool_use_id},
        )
    except ValueError as exc:  # `_send` raises this for a server that is not there
        return {"behavior": "deny", "message": str(exc)}
    if status >= 400 or not isinstance(body, dict) or "behavior" not in body:
        detail = body.get("detail") if isinstance(body, dict) else body
        return {"behavior": "deny", "message": f"kraft {status}: {detail}"}
    return body


async def stream_events(after_seq: int = 0) -> AsyncIterator[dict]:
    """The live event bus — the same stream the board's UI redraws from.

    The bearer goes in a header: the websocket handler accepts it as of the
    /ws/events auth fix, because a CLI has no session cookie to offer.
    """
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed, InvalidHandshake

    url = transport.base_url().replace("http://", "ws://", 1) + transport._api(
        f"/ws/events?after_seq={after_seq}"
    )
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    token = auth.read_mcp_token(run_dir)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with connect(url, additional_headers=headers) as socket:
            async for message in socket:
                yield json.loads(message)
    except (OSError, InvalidHandshake) as exc:
        raise ValueError(
            f"no Kraft server at {transport.base_url()} — start one with `kraft`"
        ) from exc
    except ConnectionClosed as exc:
        # mid-stream: the server went away while we were watching, which is a
        # sentence like any other failure, not a traceback out of the event loop
        raise ValueError(
            f"the Kraft server at {transport.base_url()} closed the event stream"
        ) from exc


async def diff(work_item_id: str | None = None) -> dict:
    """What the agent changed, against the item's `base_ref`.

    The payload is passed through untouched — `truncated` and `untracked` are
    the two fields a renderer must not drop, and passing the dict whole is how
    that is guaranteed rather than remembered.
    """
    return await transport._get(f"/work-items/{await context.resolve_work_item(work_item_id)}/diff")


async def artifact(work_item_id: str | None = None) -> dict:
    """The document the item's pending gate is a decision about.

    404s when there is no pending gate or the hook produced no document —
    reading a gate's artifact is only meaningful while the gate is open.
    """
    return await transport._get(
        f"/work-items/{await context.resolve_work_item(work_item_id)}/artifact"
    )


async def documents(work_item_id: str | None = None) -> list[dict]:
    """The specs, plans and summaries the indexer linked to this item. No
    content: that is one `document()` call per id."""
    payload = await transport._get(
        f"/work-items/{await context.resolve_work_item(work_item_id)}/documents"
    )
    return payload.get("documents", [])


async def document(doc_id: str) -> dict:
    return await transport._get(f"/documents/{doc_id}")


async def open_document(doc_id: str, editor: str | None = None) -> dict:
    """Hand the document to an editor on the server's machine.

    Reuses the server's editor table and its 501-when-headless answer rather
    than growing a second launcher here.
    """
    return await transport._act(f"/documents/{doc_id}/open", {"editor": editor} if editor else {})


async def repos() -> list[dict]:
    """Every connected repo, as the API shapes it.

    This and `resolve_repo` are the only readers of the repo list on the client
    side, and neither parses `repos.yaml`: a local YAML read would work with the
    server down, but would drift from the API's shaping (spec D §4).
    """
    return (await transport._get("/repos")).get("repos", [])


async def workspaces() -> dict[str, dict]:
    """Every declared workspace by id, as `GET /repos` shapes it."""
    return (await transport._get("/repos")).get("workspaces", {})


async def health() -> dict:
    """The server's own view of itself: invalid config, index state, reattach.

    Raises when the server's run_dir does not match this client's -- the CLI
    resolved a live server on host:port, but it is not the instance
    KRAFT_HOME/KRAFT_RUN_DIR says it should be (Kraft-kquf: "status ok,
    documents 2" from somebody's e2e fixture on the same port). Reporting
    somebody else's index as this machine's is exactly the failure this
    guards against -- loud and naming both paths, not a quiet wrong answer.
    """
    payload = await transport._get("/health")
    expected = str(Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir()).resolve())
    actual = payload.get("run_dir")
    if actual != expected:
        cause = (
            "probably an older kraft daemon still running on this port - restart it"
            if actual is None
            else "probably a different KRAFT_HOME/KRAFT_RUN_DIR, or something "
            "else already on this port"
        )
        raise ValueError(
            f"kraft: the server at {transport.base_url()} is a different instance "
            f"(run_dir {actual!r}, expected {expected!r}) - {cause}"
        )
    return payload


async def reindex(repo: str | None = None) -> dict:
    """Rescan one repo's documents, or every repo's. Returns the change counts.

    Not through `_act`: `/index/rescan` takes `repo` as a query parameter, and
    `_post` only sends JSON bodies.
    """
    response = await transport._send(
        "POST", "/index/rescan", params={"repo": repo} if repo else None
    )
    if response.status_code >= 400:
        raise ValueError(f"kraft {response.status_code}: {transport._detail(response)}")
    return response.json()


async def search(q: str, limit: int = 20) -> dict:
    """Cross-repo search over specs, plans, and session summaries."""
    return await transport._get("/search", q=q, limit=limit)


async def lint_templates() -> dict:
    """The installed template library, linted as it is on disk: the chains that
    resolve and every issue with the rest. Reads only; nothing is reloaded."""
    return await transport._get("/templates/lint")


async def template(template_id: str) -> dict:
    """One chain template's file, as its author wrote it."""
    return await transport._get(f"/templates/chains/{template_id}")


async def resolved_template(template_id: str) -> dict:
    """One saved chain with its library components expanded, before any work
    item materializes it."""
    return await transport._get(f"/templates/chains/{template_id}/resolved")


async def library() -> dict:
    """The template library's components: each one's definition as written,
    the chains that use it, and the lint issues that name it."""
    return await transport._get("/templates/library")


async def library_component(component_id: str) -> dict:
    """One library component, by `tasks.implementer` or a bare unique name."""
    return await transport._get(f"/templates/library/{component_id}")
