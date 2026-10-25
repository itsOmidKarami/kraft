"""`/api/workers`: the machines that may run work item steps, and the
WebSocket one dials in on.

The server owns `workers.yaml`: the CLI and the MCP tools only call these.
The dial-in route does not use the login cookie or the bearer token. A
machine is authenticated by its own token, judged by `Hub.accept`."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request, WebSocket
from pydantic import BaseModel, ConfigDict, ValidationError

from kraft import apply as apply_mod
from kraft import config as config_mod
from kraft import store
from kraft.api import api_router, deps, perimeter
from kraft.remote import local_link, protocol, registry
from kraft.remote.transport import WebSocketTransport

#: Bind values that listen everywhere: no single address to tell a machine.
_WILDCARD = {"0.0.0.0", "::", ""}
_PLACEHOLDER = "<this-machine-address>"


class AddWorker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ssh: str | None = None
    capacity: int | None = None
    repos: list[str] | None = None
    give_up_after: str | None = None


def _forbid_sessions(request: Request) -> None:
    """A Kraft session never manages machines. This is consistency, not the
    security boundary: the sandbox's is the allowlist in `worker/callback.py`,
    which does not name these routes."""
    if deps.caller_ref(request) is not None:
        raise HTTPException(403, "a Kraft session cannot manage workers")


async def _edit(request: Request, change) -> registry.WorkersFile:
    """Rewrite `workers.yaml` with `change(current)` and run the result."""
    st = request.app.state
    path = st.templates_dir / registry.FILE
    async with st.workers_lock:
        try:
            current = await asyncio.to_thread(registry.WorkersFile.load, path)
        except config_mod.ConfigError as exc:
            raise HTTPException(409, f"{exc}; fix the file first") from exc
        updated = change(current)
        await asyncio.to_thread(config_mod.write_preserving, path, updated.to_yaml())
        apply_mod.record(st, registry.FILE)
        st.workers_hub.apply(updated)
    st.invalid_workers = None
    apply_mod.notify(request.app)
    return updated


def _row(request: Request, name: str) -> dict:
    return next(r for r in request.app.state.workers_hub.snapshot() if r["name"] == name)


def _serve_command(st, token: str) -> tuple[str, str]:
    """`(command, note)`: what to run on the machine, and what else to do."""
    host, port = st.bound_host, st.bound_port
    if host in config_mod.LOOPBACK:
        address = _PLACEHOLDER
        note = (
            "This server listens on loopback only. Bind it beyond loopback with a password "
            "(Settings > Access) and replace "
            f"{_PLACEHOLDER} with an address the machine can reach."
        )
    elif host in _WILDCARD:
        address = _PLACEHOLDER
        note = f"Replace {_PLACEHOLDER} with an address the machine can reach."
    else:
        address, note = host, ""
    url = f"http://{address}:{port}"
    return f"kraft admin worker serve --server {url} --token {token}", note


@api_router.get("/workers")
async def list_workers(request: Request):
    _forbid_sessions(request)
    st = request.app.state
    rows = st.workers_hub.snapshot()
    link = local_link.current()
    if link is not None:
        running = st.db.read(store.running_local_sessions)
        rows = [link.snapshot_row(running), *rows]
    return {"workers": rows}


@api_router.post("/workers", status_code=201)
async def add_worker(body: AddWorker, request: Request):
    _forbid_sessions(request)
    st = request.app.state
    try:
        registry.check_name(body.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    token = None if body.ssh else registry.new_token()
    fields = {"ssh": body.ssh, "capacity": body.capacity, "repos": body.repos}
    if body.give_up_after is not None:
        fields["give_up_after"] = body.give_up_after
    if token is not None:
        fields["token_sha256"] = registry.hash_token(token)
    try:
        worker = registry.Worker(**fields)
    except ValidationError as exc:
        raise HTTPException(422, config_mod.first_error(exc, "workers")) from exc
    if worker.repos:
        try:
            connected = await asyncio.to_thread(
                config_mod.load_repos, st.templates_dir / "repos.yaml"
            )
        except config_mod.ConfigError:
            connected = []
        unknown = [
            r for r in worker.repos if not any(registry.repo_matches(e, r) for e in connected)
        ]
        if unknown:
            raise HTTPException(422, f"no connected repo is named {', '.join(map(repr, unknown))}")

    def add(current: registry.WorkersFile) -> registry.WorkersFile:
        if body.name in current.workers:
            raise HTTPException(409, f"a worker named {body.name!r} already exists")
        return current.with_workers({**current.workers, body.name: worker})

    await _edit(request, add)
    if token is None:
        return {
            "worker": _row(request, body.name),
            "probe": await st.workers_hub.probe(body.name, 15),
        }
    command, note = _serve_command(st, token)
    return {
        "worker": _row(request, body.name),
        "token": token,
        "serve_command": command,
        "note": note,
    }


@api_router.delete("/workers/{name}")
async def remove_worker(name: str, request: Request):
    _forbid_sessions(request)

    def remove(current: registry.WorkersFile) -> registry.WorkersFile:
        if name not in current.workers:
            raise HTTPException(404, f"no worker named {name!r}")
        return current.with_workers({n: w for n, w in current.workers.items() if n != name})

    await _edit(request, remove)
    # Nothing will come back from a machine that is no longer listed.
    await request.app.state.workers_hub.runs.machine_removed(name)
    return {"removed": name}


@api_router.websocket("/workers/connect")
async def ws_connect(websocket: WebSocket):
    # HTTP middleware does not run for websockets, so this route is already
    # outside the login-cookie check; the machine proves itself with its
    # token, in `Hub.accept`. Nothing here reads a cookie, a session or the
    # bearer token. The perimeter's own rules still apply: a server with no
    # password refuses a peer that is not local, and a rebound name is refused.
    if perimeter._refusal(websocket, check_origin=True) is not None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    t = WebSocketTransport(websocket.send_text, websocket.receive_text, websocket.close)
    try:
        await websocket.app.state.workers_hub.accept(t)
    except protocol.Refused:
        pass  # the machine was told, and the hub recorded why
