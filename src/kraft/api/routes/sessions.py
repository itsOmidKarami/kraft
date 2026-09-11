from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path

from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from kraft import auth as auth_mod
from kraft import events
from kraft import logs as logs_mod
from kraft.api import api_router, perimeter


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
    floor.
    """
    sent = 0
    running = True
    while True:
        for line in logs_mod.jsonl(path, start_line=sent):
            sent = line["n"] + 1
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
    filterable, followable view (design 6c)."""
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
        return {"session_id": sid, "status": row["status"], "lines": list(logs_mod.jsonl(path))}
    if not path.exists():
        raise HTTPException(404, "log not found")
    return FileResponse(path, media_type="text/plain")


class PermissionAsk(BaseModel):
    """What `--permission-prompt-tool` hands over: the tool the agent wants to
    use, the arguments it wants to use it with, and the CLI's own id for the
    call."""

    tool_name: str
    input: dict = {}
    tool_use_id: str | None = None


@api_router.post("/worker-sessions/{sid}/permission")
async def permission_request(sid: str, body: PermissionAsk, request: Request):
    """Answer a worker's permission prompt from its node's grant (Kraft-oor).

    The policy is the hook binding's `allowed_tools` (Kraft-3tw), resolved
    session -> hook_point -> binding. A binding that declares none allows
    everything: `--permission-mode auto` already resolves these asks silently
    today, and a default that denied would turn an observability change into a
    behaviour change on every node at once. A binding that *does* declare an
    allowlist is taken at its word.

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
    allowed = st.registry.hooks.get(row["hook_point"], {}).get("allowed_tools") or []
    if not allowed:
        decision, reason = "allow", f"{row['node_id']} declares no allowed_tools"
    elif body.tool_name in allowed:
        decision, reason = "allow", f"{body.tool_name} is in {row['node_id']}'s allowed_tools"
    else:
        decision, reason = (
            "deny",
            f"{body.tool_name} is not in {row['node_id']}'s allowed_tools ({', '.join(allowed)})",
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
