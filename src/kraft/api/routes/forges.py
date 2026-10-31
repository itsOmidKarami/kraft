"""`/api/forges`: the accounts in `forges.yaml`.

The server owns the file; `kraft admin forge` only calls these. `test` asks a CLI
account's forge CLI whether it is signed in for the host, and probes an API account's
token."""

from __future__ import annotations

import asyncio
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from kraft import apply as apply_mod
from kraft import config as config_mod
from kraft import forges
from kraft.adapters.forge import git, probe
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


def _row(st, name: str, account: forges.ForgeAccount) -> dict:
    found = st.forge_probes.get(name)
    return {
        **account.model_dump(by_alias=True),
        "api_url": account.api(),
        "token": account.token.model_dump(by_alias=True, exclude_none=True)
        if account.token
        else None,
        "probe": found.to_json() if found else None,
    }


def _file_path(st):
    return st.templates_dir / forges.ForgesFile.FILE


async def _load(st) -> forges.ForgesFile:
    """The file a write starts from. An edit on disk that is not applied (one a reload
    refused, or not reloaded yet) is refused: saving over it would apply it unchecked."""
    if st.forges is not None and apply_mod.pending_file(st, forges.ForgesFile.FILE):
        raise HTTPException(
            409,
            "forges.yaml has an edit that is not applied; `kraft admin reload` applies it "
            "or says why not. Apply or undo it first",
        )
    try:
        return await asyncio.to_thread(forges.ForgesFile.load, _file_path(st))
    except config_mod.ConfigError as exc:
        raise HTTPException(409, f"{exc}; fix the file first") from exc


def _naming(st, name: str) -> list[str]:
    """The connected repos whose `forge_account` is `name`. Read in the event loop, not a
    thread: a repos.yaml save cannot land between this and the caller's `_save`."""
    try:
        entries = config_mod.load_repos(st.templates_dir / "repos.yaml")
    except config_mod.ConfigError as exc:
        # An unreadable repos.yaml may name the account: never remove past it.
        raise HTTPException(409, f"{exc}; fix repos.yaml first") from exc
    return [str(e.path) for e in entries if e.forge_account == name]


def _save(st, updated: forges.ForgesFile) -> None:
    """Written, recorded as loaded and running. The callers hold `forges_lock` and await
    nothing between their last check and here, so the file reflects the check."""
    updated.save(_file_path(st))
    apply_mod.record(st, forges.ForgesFile.FILE)
    st.forges = updated
    st.invalid_forges = "; ".join(forges.repo_problems(updated, st.templates_dir)) or None


async def _read(st) -> forges.ForgesFile:
    """The file; when it does not load, the accounts running (a refused reload),
    which a read may still show. None running means it never loaded: 409."""
    try:
        return await asyncio.to_thread(forges.ForgesFile.load, _file_path(st))
    except config_mod.ConfigError as exc:
        if st.forges is None:
            raise HTTPException(409, f"{exc}; fix the file first") from exc
        return st.forges


@api_router.get("/forges")
async def list_forges(request: Request):
    _forbid_sessions(request, "forge accounts")
    current = await _read(request.app.state)
    return {
        "accounts": {name: _row(request.app.state, name, a) for name, a in current.accounts.items()}
    }


@api_router.post("/forges", status_code=201)
async def add_forge(body: AddForge, request: Request):
    _forbid_sessions(request, "forge accounts")
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
                422,
                "; ".join(
                    (f"{e['loc'][-1]}: " if e["loc"] else "")
                    + e["msg"].removeprefix("Value error, ")
                    for e in exc.errors()
                ),
            ) from exc
        _save(st, updated)
    apply_mod.notify(request.app)
    return _row(st, body.name, updated.accounts[body.name])


@api_router.delete("/forges/{name}", status_code=204)
async def remove_forge(name: str, request: Request):
    _forbid_sessions(request, "forge accounts")
    st = request.app.state
    async with st.forges_lock:
        current = await _load(st)
        if name not in current.accounts:
            raise HTTPException(404, f"no forge account named {name!r}")
        if repos := _naming(st, name):
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
    _forbid_sessions(request, "forge accounts")
    st = request.app.state
    account = (await _read(st)).accounts.get(name)
    if account is None:
        raise HTTPException(404, f"no forge account named {name!r}")
    if account.client == "api":  # never the CLI: it would prove another credential
        await probe.refresh(st, {name: account})
        found = st.forge_probes[name]
        return {"ok": found.state == "ok", "output": found.text(), "probe": found.to_json()}
    host = forges.web_host(account.web_url)
    argv = (
        ["gh", "auth", "status", "-h", host]
        if account.forge == "github"
        else ["glab", "auth", "status", "--hostname", host]
    )
    try:
        done = await git.run_cli(st.templates_dir, argv, errors="replace")
    # Fixed text, not the exception's: nothing from inside a raised error
    # reaches an API answer.
    except git.CliUnavailable:
        return {"ok": False, "output": f"{argv[0]} is not installed or cannot start on the server"}
    except ForgeError:  # run_cli's only other failure: the CLI did not finish in time
        return {"ok": False, "output": f"{' '.join(argv)} timed out"}
    return {"ok": done.returncode == 0, "output": done.stdout + done.stderr}
