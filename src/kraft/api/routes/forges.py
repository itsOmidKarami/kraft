"""`/api/forges`: the accounts in `forges.yaml`.

The server owns the file; `kraft admin forge` only calls these. PR 3 has `client: cli`
accounts only, so `test` asks the forge's own CLI whether it is signed in for the host."""

from __future__ import annotations

import asyncio
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from kraft import apply as apply_mod
from kraft import config as config_mod
from kraft import forges
from kraft.adapters.forge import git
from kraft.adapters.forge.models import ForgeError
from kraft.api import api_router
from kraft.api.routes.workers import _forbid_sessions

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")


class AddForge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    forge: str
    web_url: str
    api_url: str | None = None
    default_for_host: bool = False


def _row(account: forges.ForgeAccount) -> dict:
    return {**account.model_dump(), "api_url": account.api()}


def _file_path(st):
    return st.templates_dir / forges.ForgesFile.FILE


async def _load(st) -> forges.ForgesFile:
    try:
        return await asyncio.to_thread(forges.ForgesFile.load, _file_path(st))
    except config_mod.ConfigError as exc:
        raise HTTPException(409, f"{exc}; fix the file first") from exc


async def _naming(st, name: str) -> list[str]:
    """The connected repos whose `forge_account` is `name`."""
    try:
        entries = await asyncio.to_thread(config_mod.load_repos, st.templates_dir / "repos.yaml")
    except config_mod.ConfigError as exc:
        # An unreadable repos.yaml may name the account: never remove past it.
        raise HTTPException(409, f"{exc}; fix repos.yaml first") from exc
    return [str(e.path) for e in entries if e.forge_account == name]


def _save(st, updated: forges.ForgesFile) -> None:
    """Written, recorded as loaded and running. Nothing awaits between the callers' checks
    and here, so the file reflects the check."""
    updated.save(_file_path(st))
    apply_mod.record(st, forges.ForgesFile.FILE)
    st.forges = updated
    st.invalid_forges = None


async def _read(st) -> forges.ForgesFile:
    """The file; when it does not load, the accounts running, which a read may still show."""
    try:
        return await asyncio.to_thread(forges.ForgesFile.load, _file_path(st))
    except config_mod.ConfigError:
        return st.forges


@api_router.get("/forges")
async def list_forges(request: Request):
    _forbid_sessions(request)
    current = await _read(request.app.state)
    return {"accounts": {name: _row(a) for name, a in current.accounts.items()}}


@api_router.post("/forges", status_code=201)
async def add_forge(body: AddForge, request: Request):
    _forbid_sessions(request)
    st = request.app.state
    if not _NAME.fullmatch(body.name):
        raise HTTPException(422, "name: lowercase letters, digits, . - and _, starting with one")
    async with st.forges_lock:
        current = await _load(st)
        if body.name in current.accounts:
            raise HTTPException(409, f"forge account {body.name!r} already exists")
        fields = body.model_dump(exclude={"name"})
        try:
            updated = forges.ForgesFile.model_validate(
                {"accounts": {**current.accounts, body.name: fields}}
            )
        except ValidationError as exc:
            raise HTTPException(
                422, "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
            ) from exc
        _save(st, updated)
    apply_mod.notify(request.app)
    return _row(updated.accounts[body.name])


@api_router.delete("/forges/{name}", status_code=204)
async def remove_forge(name: str, request: Request):
    _forbid_sessions(request)
    st = request.app.state
    async with st.forges_lock:
        current = await _load(st)
        if name not in current.accounts:
            raise HTTPException(404, f"no forge account named {name!r}")
        if repos := await _naming(st, name):
            return JSONResponse(
                {
                    "detail": f"forge account {name!r} is named by: {', '.join(repos)}",
                    "repos": repos,
                },
                status_code=409,
            )
        rest = {n: a for n, a in current.accounts.items() if n != name}
        _save(st, forges.ForgesFile(accounts=rest))
    apply_mod.notify(request.app)
    return Response(status_code=204)


@api_router.post("/forges/{name}/test")
async def test_forge(name: str, request: Request):
    _forbid_sessions(request)
    st = request.app.state
    account = (await _read(st)).accounts.get(name)
    if account is None:
        raise HTTPException(404, f"no forge account named {name!r}")
    host = forges.web_host(account.web_url)
    argv = (
        ["gh", "auth", "status", "-h", host]
        if account.forge == "github"
        else ["glab", "auth", "status", "--hostname", host]
    )
    try:
        done = await git.run_cli(st.templates_dir, argv, errors="replace")
    except ForgeError as exc:  # CliUnavailable is one
        return {"ok": False, "output": str(exc)}
    return {"ok": done.returncode == 0, "output": done.stdout + done.stderr}
