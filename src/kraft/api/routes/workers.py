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
from kraft import ids, store
from kraft.api import api_router, deps, perimeter
from kraft.remote import local_link, placement, protocol, registry
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
    groups: list[str] = []
    enabled: bool = True


class EditWorker(BaseModel):
    """What `PATCH /workers/{name}` may change. A field left out is unchanged. Only `capacity`
    and `repos` may be null; the rest are nullable here so that a null is answered in words
    (`_edit_fields`), not with a validation list."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    capacity: int | None = None
    give_up_after: str | None = None
    groups: list[str] | None = None
    repos: list[str] | None = None
    ssh: str | None = None


_NOT_NULL = ("enabled", "give_up_after", "groups", "ssh")
_LOCAL_FIELDS = frozenset({"enabled", "capacity", "groups"})
_LOCAL_CANNOT_BE_REMOVED = (
    "the local worker is part of this Kraft and cannot be removed; disable it instead"
)
_LISTED_TITLES = 3


def _forbid_sessions(request: Request, what: str = "workers") -> None:
    """A Kraft session never manages machines. This is consistency, not the
    security boundary: the sandbox's is the allowlist in `worker/callback.py`,
    which does not name these routes."""
    if deps.caller_ref(request) is not None:
        raise HTTPException(403, f"a Kraft session cannot manage {what}")


async def _edit(request: Request, change) -> registry.WorkersFile:
    """Rewrite `workers.yaml` with `change(current)` and run the result. `change` may refuse
    (an `HTTPException`). From `change` to `Hub.apply` nothing awaits, so a step cannot take a
    machine between the check `change` makes of it and the file that reflects the check."""
    st = request.app.state
    path = st.templates_dir / registry.FILE
    async with st.workers_lock:
        try:
            current = await asyncio.to_thread(registry.WorkersFile.load, path)
        except config_mod.ConfigError as exc:
            raise HTTPException(409, f"{exc}; fix the file first") from exc
        updated = change(current)
        config_mod.write_preserving(path, updated.to_yaml())
        apply_mod.record(st, registry.FILE)
        st.workers_hub.apply(updated)
    st.invalid_workers = None
    apply_mod.notify(request.app)
    return updated


def holders(request: Request, name: str) -> list[dict]:
    """The work items with a step on machine `name`, `[{"id", "title", "steps"}]`: a step holds
    it from its claim, before its session says `running`, or by a `pending`/`running` row (a
    server restart leaves the row and not the claim). One step is counted once."""
    st = request.app.state
    held: set[str] = set()
    for key in st.workers_hub.claims_on(name):
        if not key.startswith("pre:"):  # a slot picked for a group step, not yet a step
            held.add(key)
    for wid, sid in st.db.read(lambda c: store.live_sessions_on(c, name)):
        held.add(ids.session_ref(wid, sid))
    per_item: dict[str, int] = {}
    for key in held:
        try:
            wid = ids.parse_session_ref(key)[0] or key
        except ValueError:
            wid = key
        per_item[wid] = per_item.get(wid, 0) + 1
    if not per_item:
        return []
    titles = st.db.read(lambda c: store.titles_of(c, sorted(per_item)))
    return [
        {"id": wid, "title": titles.get(wid, wid), "steps": per_item[wid]}
        for wid in sorted(per_item)
    ]


def _holding(name: str, held: list[dict]) -> str:
    """`gpu is running 2 steps of A, B and 3 more.`"""
    steps = sum(h["steps"] for h in held)
    titles = [
        h["title"].strip().splitlines()[0][:80] if h["title"].strip() else h["id"] for h in held
    ]
    named = ", ".join(titles[:_LISTED_TITLES])
    if len(titles) > _LISTED_TITLES:
        named += f" and {len(titles) - _LISTED_TITLES} more"
    return f"{name} is running {steps} step{'' if steps == 1 else 's'} of {named}."


def _remove_text(name: str, held: list[dict]) -> str | None:
    if not held:
        return None
    return (
        f"{_holding(name, held)} Disable it so nothing new starts (Takes new steps off in "
        f"Settings › Workers, or kraft admin worker disable {name}), then remove it once "
        "they end. A step held by a machine that is offline is freed by pausing or cancelling "
        "its item."
    )


def remove_blocker(request: Request, name: str) -> str | None:
    """Why `name` cannot be removed now: the words every surface shows. None when it can."""
    if name == placement.LOCAL:
        return _LOCAL_CANNOT_BE_REMOVED
    return _remove_text(name, holders(request, name))


def _remote_row(request: Request, row: dict) -> dict:
    held = holders(request, row["name"])
    return {**row, "holders": held, "remove_blocked": _remove_text(row["name"], held)}


def _local_row(request: Request) -> dict | None:
    link = local_link.current()
    if link is None:
        return None
    running = request.app.state.db.read(store.running_local_sessions)
    return {
        **link.snapshot_row(running),
        "holders": [],
        "remove_blocked": _LOCAL_CANNOT_BE_REMOVED,
    }


def _row(request: Request, name: str) -> dict:
    """The row of machine `name`, or of the local worker; a 404 for neither."""
    if name == placement.LOCAL:
        if (found := _local_row(request)) is not None:
            return found
    else:
        for row in request.app.state.workers_hub.snapshot():
            if row["name"] == name:
                return _remote_row(request, row)
    raise HTTPException(404, f"no worker named {name!r}")


async def _check_repos(st, repos: list[str] | None) -> None:
    """422 naming every repo in `repos` that no connected repo goes by."""
    if not repos:
        return
    try:
        connected = await asyncio.to_thread(config_mod.load_repos, st.templates_dir / "repos.yaml")
    except config_mod.ConfigError:
        connected = []
    unknown = [r for r in repos if not any(registry.repo_matches(e, r) for e in connected)]
    if unknown:
        raise HTTPException(422, f"no connected repo is named {', '.join(map(repr, unknown))}")


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
    rows = [_remote_row(request, r) for r in request.app.state.workers_hub.snapshot()]
    if (local := _local_row(request)) is not None:
        rows = [local, *rows]
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
    fields = {
        "ssh": body.ssh,
        "capacity": body.capacity,
        "repos": body.repos,
        "groups": body.groups,
        "enabled": body.enabled,
    }
    if body.give_up_after is not None:
        fields["give_up_after"] = body.give_up_after
    if token is not None:
        fields["token_sha256"] = registry.hash_token(token)
    try:
        worker = registry.Worker(**fields)
    except ValidationError as exc:
        raise HTTPException(422, config_mod.first_error(exc, "workers")) from exc
    await _check_repos(st, worker.repos)

    def add(current: registry.WorkersFile) -> registry.WorkersFile:
        if body.name in current.workers:
            raise HTTPException(409, f"a worker named {body.name!r} already exists")
        try:
            return current.with_workers({**current.workers, body.name: worker})
        except ValidationError as exc:
            raise HTTPException(422, config_mod.first_error(exc, "workers")) from exc

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


def _edit_fields(name: str, body: EditWorker) -> dict:
    """What `body` sets, refused in words when it names a field that cannot be null (or, for the
    local worker, one it does not have)."""
    sent = body.model_fields_set
    for field in _NOT_NULL:
        if field in sent and getattr(body, field) is None:
            raise HTTPException(422, f"{field} cannot be null; leave it out to keep it")
    if "repos" in sent and body.repos == []:
        raise HTTPException(422, "give at least one repo, or every repo (repos: null)")
    if name == placement.LOCAL and (extra := sorted(sent - _LOCAL_FIELDS)):
        raise HTTPException(
            422, f"the local worker takes only enabled, capacity and groups, not {extra[0]}"
        )
    return {f: getattr(body, f) for f in sent}


@api_router.patch("/workers/{name}")
async def edit_worker(name: str, body: EditWorker, request: Request):
    _forbid_sessions(request)
    st = request.app.state
    changes = _edit_fields(name, body)
    row = _row(request, name)
    if not changes:
        return {"worker": row}
    await _check_repos(st, changes.get("repos"))

    def edit_local(current: registry.WorkersFile) -> registry.WorkersFile:
        base = (current.local or registry.LocalWorker()).model_dump()
        try:
            return current.with_local(registry.LocalWorker.model_validate({**base, **changes}))
        except ValidationError as exc:
            raise HTTPException(422, config_mod.first_error(exc, "workers")) from exc

    def edit(current: registry.WorkersFile) -> registry.WorkersFile:
        old = current.workers.get(name)
        if old is None:
            raise HTTPException(404, f"no worker named {name!r}")
        if "ssh" in changes:
            if old.ssh is None:
                raise HTTPException(
                    422,
                    f"{name} dials in; it has no ssh host. Remove it and add it again over "
                    "ssh to change that.",
                )
            if changes["ssh"] != old.ssh and (held := holders(request, name)):
                # a new host drops the connection (`Hub.apply`), and with it the steps
                raise HTTPException(409, f"{_holding(name, held)} Change its host once they end.")
        try:
            new = registry.Worker.model_validate({**old.model_dump(), **changes})
            return current.with_workers({**current.workers, name: new})
        except ValidationError as exc:
            raise HTTPException(422, config_mod.first_error(exc, "workers")) from exc

    await _edit(request, edit_local if name == placement.LOCAL else edit)
    return {"worker": _row(request, name)}


@api_router.post("/workers/{name}/rotate-token")
async def rotate_worker_token(name: str, request: Request):
    """A new token for a dial-in machine, shown once. The old one stops working and the machine's
    connection drops (`Hub.apply`): its steps go on, up to `give_up_after`, until `serve` is
    started with the new token."""
    _forbid_sessions(request)
    st = request.app.state
    if name == placement.LOCAL:
        raise HTTPException(409, "the local worker is part of this Kraft and has no token")
    token = registry.new_token()

    def rotate(current: registry.WorkersFile) -> registry.WorkersFile:
        old = current.workers.get(name)
        if old is None:
            raise HTTPException(404, f"no worker named {name!r}")
        if old.ssh is not None:
            raise HTTPException(409, f"{name} connects over ssh; it has no token to rotate")
        new = registry.Worker.model_validate(
            {**old.model_dump(), "token_sha256": registry.hash_token(token)}
        )
        return current.with_workers({**current.workers, name: new})

    await _edit(request, rotate)
    command, note = _serve_command(st, token)
    return {
        "worker": _row(request, name),
        "token": token,
        "serve_command": command,
        "note": note,
    }


@api_router.delete("/workers/{name}")
async def remove_worker(name: str, request: Request):
    _forbid_sessions(request)

    def remove(current: registry.WorkersFile) -> registry.WorkersFile:
        if name == placement.LOCAL:
            raise HTTPException(409, _LOCAL_CANNOT_BE_REMOVED)
        if name not in current.workers:
            raise HTTPException(404, f"no worker named {name!r}")
        if (why := remove_blocker(request, name)) is not None:
            raise HTTPException(409, why)
        return current.with_workers({n: w for n, w in current.workers.items() if n != name})

    await _edit(request, remove)
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
