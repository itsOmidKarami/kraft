from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from kraft import auth as auth_mod
from kraft import escalate, events, store
from kraft import logs as logs_mod
from kraft.adapters import agent as _agent
from kraft.api import api_router, deps, perimeter
from kraft.executor.dispatch import ESCALATION_HOOK, scope_policy
from kraft.grants import matching
from kraft.templates.models import AgentTask


def _session_row(st, sid: str):
    row = st.db.read(
        lambda c: c.execute(
            "SELECT id, log_path, status FROM worker_sessions WHERE id = ?", (sid,)
        ).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown session")
    return row


async def _tail(st, sid: str, path: Path, *, poll_s: float = 0.4):
    """SSE tail: every structured line, then new ones until the session ends.

    Stops one poll *after* the session stops being pending or running, so the
    lines written between the last poll and the exit are not dropped on the
    floor. That last read is `final`, so a line the agent never finished is
    sent too.

    One `logs.Tail` per stream, read in a worker thread: each poll reads only
    what was written since the last, and never on the loop that serves every
    other request (Kraft-21jy). The status check stays on the loop -- a primary
    key lookup on the reader connection, which belongs to this thread.
    """
    tail = logs_mod.Tail(path)
    running = True
    while True:
        for line in await asyncio.to_thread(tail.read, final=not running):
            yield f"data: {json.dumps(line)}\n\n"
        if not running:
            break
        # not *finished* -- a session still in 'pending' has an agent about to
        # write to this log, and a stream opened on it used to get one poll and
        # an end event
        running = _session_row(st, sid)["status"] in ("pending", "running")
        await asyncio.sleep(poll_s)
    yield "event: end\ndata: {}\n\n"


@api_router.get("/worker-sessions/{sid}/log")
async def get_log(sid: str, request: Request, format: str | None = None, follow: bool = False):
    """Plain text by default (the modal's copy button); `format=jsonl` for the
    filterable, followable view (design 6c).

    The jsonl views are bounded (`logs.MAX_LOG_BYTES`, with a marker row when
    they had to be); plain text is the whole file, streamed from disk, which is
    what that marker points a reader at."""
    st = request.app.state
    row = _session_row(st, sid)
    path = Path(row["log_path"])
    if format == "jsonl":
        if follow:
            return StreamingResponse(
                _tail(st, sid, path),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            )
        tail = logs_mod.Tail(path)
        lines = await asyncio.to_thread(tail.read, final=True)
        # `next_line`: where a follow resumes when no line was printed to
        # resume after (`kraft view logs -n 0 -f`, Kraft-tbnse).
        return {
            "session_id": sid,
            "status": row["status"],
            "lines": lines,
            "next_line": tail.next_line,
        }
    if not path.exists():
        raise HTTPException(404, "log not found")
    return FileResponse(path, media_type="text/plain")


class PermissionAsk(BaseModel):
    """What `--permission-prompt-tool` hands over: the tool the agent wants to
    use, the arguments it wants to use it with, and the CLI's own id for the
    call. A before-every-call hook (Kraft-4in7z) asks in `enforce` mode."""

    tool_name: str
    input: dict = {}
    tool_use_id: str | None = None
    mode: Literal["prompt", "enforce"] = "prompt"
    #: Which harness's hook asked, and the tool's own name before
    #: `tool_names:` mapped it -- recorded on the event, never decided on.
    harness: str | None = None
    cli_tool: str | None = None
    #: Enforce only: the hook was installed `--fail-closed` (its launch has an
    #: allowlist), so a policy the route cannot resolve is a logged deny, not
    #: `unresolved`.
    fail_closed: bool = False


def _resolved_tools(st, row) -> tuple[tuple[str, ...] | None, tuple[str, ...], tuple[str, ...]]:
    """`(allowed_tools, deny_tools, grants)` as this session's own launch resolved
    them, or raise naming why they cannot be known.

    From the V1 task at the session's canonical path, under the policy it
    resolves at its scope (`MaterializedChain.policy_for`, Kraft-v4nrd), through
    the same `resolve_agent_task` the launch passed to `--allowedTools`/
    `--disallowed-tools` -- never the legacy registry keyed by hook name, which
    no V1 path matches (Kraft-hwrks). An allowlist of `None` is unbounded: no
    layer set one. An escalation turn is node-scoped (Kraft-l8ype): it is
    answered from the policy of the node its session sits at, as
    `escalate.dispatch` launched it.
    """
    item = st.db.read(
        lambda c: c.execute(
            "SELECT * FROM work_items WHERE id = ?", (row["work_item_id"],)
        ).fetchone()
    )
    snapshot = store.materialized_chain_of(item) if item is not None else None
    if snapshot is None:
        raise LookupError("its work item has no materialized chain")
    if row["hook_point"] == ESCALATION_HOOK:
        scope = next((n for n in snapshot.chain.nodes if n.id == row["node_id"]), None)
        if scope is None:
            raise LookupError(f"no node {row['node_id']!r} in its work item's chain")
        agent_task = escalate.ESCALATION_TASK
        policy = escalate.turn_policy(scope_policy(item, scope))
    else:
        scope = next(
            (t for n in snapshot.chain.nodes for t in n.tasks() if t.path == row["hook_point"]),
            None,
        )
        if scope is None or not isinstance(scope.task, AgentTask):
            raise LookupError(f"{row['hook_point']} is no agent task in its work item's chain")
        agent_task = scope.task
        policy = scope_policy(item, scope)
    launch = deps.launch(st, item["repo"])
    inv = _agent.resolve_agent_task(
        agent_task,
        launch.repo_entry,
        launch.library_steering,
        skills_dir=launch.skills_dir,
        steering=snapshot.chain.steering,
        repository_steering=snapshot.repository_steering,
        # The item's own repo as recorded at intake (Kraft-jzdyp): the tool
        # lists reported here are the item's own launch's, never a fanned-out
        # member's.
        item_repo=item["repo"],
        policy=policy,
    )
    return inv.allowed_tools, inv.deny_tools, inv.grants


@api_router.post("/worker-sessions/{sid}/permission")
async def permission_request(sid: str, body: PermissionAsk, request: Request):
    """Answer a worker's permission prompt from its task's grant (Kraft-oor).

    The grant is the task's resolved policy, as its launch resolved it
    (`_resolved_tools`): a tool in `deny_tools` is denied; otherwise, when no
    layer set `allowed_tools`, every tool is allowed -- an unset
    `maxima.allowed_tools` bounds nothing -- and when one did, only what it
    lists, so an empty allowlist allows nothing. A call that is an instance of
    one of the task's named grants (`kraft.grants`) is allowed unless denied,
    whatever the allowlist. A grant that cannot be resolved at all -- the task
    or its profile gone -- is denied: not knowing is not a grant.

    `enforce` mode (a before-every-call hook, Kraft-4in7z) differs twice:
    unbounded is `no_opinion`, so the CLI's own classifier decides, and an
    unresolvable policy is `unresolved` -- unless the hook says it was
    installed `fail_closed`, when it is a deny like prompt mode's. Neither
    `no_opinion` nor `unresolved` is a decision, so neither is logged.

    Every decision appends an event. That is the whole point -- it is the only
    way the orchestrator ever learns what a worker decided it was allowed to do.
    """
    st = request.app.state
    row = st.db.read(
        lambda c: c.execute(
            "SELECT work_item_id, node_id, hook_point FROM worker_sessions WHERE id = ?", (sid,)
        ).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown session")
    task = row["hook_point"]
    grant = None
    try:
        allowed, denied, grants = _resolved_tools(st, row)
    except Exception as exc:  # noqa: BLE001 -- fail closed on any resolution failure
        if body.mode == "enforce" and not body.fail_closed:
            # A fail-open hook renders this as no opinion: not a decision,
            # so not logged.
            return {"behavior": "unresolved", "message": f"cannot resolve {task}'s policy: {exc}"}
        decision, reason = "deny", f"cannot resolve {task}'s allowed_tools: {exc}"
    else:
        grant = matching(grants, body.tool_name, body.input)
        if body.tool_name in denied:
            decision, reason = "deny", f"{body.tool_name} is in {task}'s deny_tools"
        elif grant is not None:
            decision, reason = "allow", f"{task} holds the {grant} grant"
        elif allowed is None:
            if body.mode == "enforce":
                return {"behavior": "no_opinion"}
            decision, reason = "allow", f"no layer of {task}'s policy sets allowed_tools"
        elif body.tool_name in allowed:
            decision, reason = "allow", f"{body.tool_name} is in {task}'s allowed_tools"
        else:
            decision, reason = (
                "deny",
                f"{body.tool_name} is not in {task}'s allowed_tools ({', '.join(allowed)})",
            )
    await st.db.write(
        lambda c: events.append(
            c,
            row["work_item_id"],
            "permission_decision",
            {
                "session_id": sid,
                "node_id": row["node_id"],
                "tool": body.tool_name,
                "decision": decision,
                "reason": reason,
                "harness": body.harness,
                "cli_tool": body.cli_tool,
                "grant": grant,
            },
        )
    )
    if decision == "allow":
        return {"behavior": "allow", "updatedInput": body.input}
    return {"behavior": "deny", "message": reason}


@api_router.websocket("/ws/events")
async def ws_events(websocket: WebSocket, after_seq: int = 0):
    if not perimeter._origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    # Rule 1 of `_perimeter`, restated: HTTP middleware does not run for
    # websockets, so without this the live event stream is the one route that
    # still answers a remote peer on a server that has no password to demand.
    if not perimeter._client_is_local(websocket) and not (
        (getattr(websocket.app.state, "access", None) or {}).get("password_hash")
    ):
        await websocket.close(code=1008)
        return
    # HTTP middleware does not run for websockets, so the session check has to be
    # here too — otherwise a LAN bind would leave the live event stream open.
    #
    # Both credentials, for the same reason the HTTP middleware takes both: a
    # browser has a session cookie, and a non-browser client (`kraft view watch`, an
    # agent) has the bearer token from run/. Accepting only the cookie made the
    # live stream the one endpoint a CLI could not reach.
    if perimeter._requires_auth(websocket.app, websocket):
        bearer = websocket.headers.get("authorization", "")
        expected = getattr(websocket.app.state, "mcp_token", None)
        authorised = bool(
            expected and bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], expected)
        )
        if not authorised:
            token = websocket.cookies.get(auth_mod.COOKIE)
            authorised = bool(
                token
                and await websocket.app.state.db.write(
                    lambda c, token=token: auth_mod.touch_session(c, token)
                )
            )
        if not authorised:
            await websocket.close(code=1008)
            return
    st = websocket.app.state
    bc = st.broadcaster
    client = bc.register()
    live_start = bc.cursor
    await websocket.accept()
    # Uvicorn's graceful shutdown asks every open connection to close and then
    # waits for its handler to return; Starlette delivers that ask as a
    # `websocket.disconnect` message to `receive()`. A handler that only ever
    # sends never sees it, so one browser with the board open kept the server
    # alive indefinitely and `kraft admin stop` reported a false failure
    # (Kraft-9oab). Any completion of this future means the connection is going
    # away -- which is also how a tab closed without a close frame stops
    # leaking a Client into the broadcaster's set until a send happens to fail.
    closing = asyncio.ensure_future(websocket.receive())
    try:
        for ev in st.db.read(lambda c: events.read_after(c, after_seq)):
            if ev["seq"] <= live_start:
                await websocket.send_json(ev)
        while True:
            if closing.done() or client.dropped:
                if client.dropped:
                    await websocket.close(code=1011)
                return
            try:
                ev = await asyncio.wait_for(client.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if ev["seq"] <= live_start:
                continue
            await websocket.send_json(ev)
    except WebSocketDisconnect:
        pass
    finally:
        closing.cancel()
        bc.unregister(client)
