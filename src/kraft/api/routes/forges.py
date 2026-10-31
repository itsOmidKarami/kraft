"""`/api/forges`: the accounts in `forges.yaml`.

The server owns the file; `kraft admin forge` only calls these. `test` asks a CLI
account's forge CLI whether it is signed in for the host, and probes an API account's
token."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from kraft import apply as apply_mod
from kraft import config as config_mod
from kraft import forges, redact
from kraft.adapters.forge import credentials, git, probe
from kraft.adapters.forge.models import ForgeError
from kraft.api import api_router
from kraft.api.routes.workers import _forbid_sessions
from kraft.paths import kraft_home

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
_COMMAND_403 = (
    "a command source runs code on the server's machine, so it is set there: "
    "kraft admin forge add … --token-from 'command:…', or by hand in forges.yaml"
)
_SHARED_403 = (
    "that token source is already read by another account, or is another account's pasted "
    "file: a token is sent only to the host it was set for. Use a source of its own"
)
_BODY_MAX = 64 * 1024
_TOKEN_BODY = 'the body must be {"value": …, "kind": "pat" | "oauth" | "unknown"}'


class AddForge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    forge: str
    web_url: str
    api_url: str | None = None
    default_for_host: bool = False
    client: str = "cli"
    ca_file: str | None = None
    # Untyped: FastAPI's own 422 quotes the input it refused, and this may hold a token.
    token: Any = None


class EditForge(BaseModel):
    """What an edit may change; only the keys sent are applied."""

    model_config = ConfigDict(extra="forbid")

    api_url: str | None = None
    ca_file: str | None = None
    client: str | None = None
    default_for_host: bool | None = None
    token: Any = None


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


def _save(st, updated: forges.ForgesFile, probe_names: tuple[str, ...] = ()) -> None:
    """Written, recorded as loaded and running. The callers hold `forges_lock` and await
    nothing between their last check and here, so the file reflects the check. A new
    `env` or `file` source is protected now, not at the next reload. `probe_names` are
    probed again even when the account is unchanged: a second paste leaves it equal."""
    path = _file_path(st)
    updated.save(path)
    apply_mod.record(st, forges.ForgesFile.FILE)
    credentials.strip_env_sources(path)
    credentials.set_secret_files(path, running=updated)
    credentials.forget_tokens()
    st.forges = updated
    for gone in set(st.forge_probes) - set(probe.api_accounts(st)):
        del st.forge_probes[gone]  # a removed account must not keep /health degraded
    st.invalid_forges = "; ".join(forges.repo_problems(updated, st.templates_dir)) or None
    probe.kick(st, [n for n in probe_names if n in probe.api_accounts(st)])


def _own_file(name: str, account: forges.ForgeAccount | None) -> Path | None:
    """The file Kraft wrote for `name`'s pasted token, when the account reads it: its own
    path, returned only when the account's path leads to the same real file."""
    token = account.token if account else None
    if token is None or token.file is None or not _NAME.fullmatch(name):
        return None
    mine = kraft_home().resolve() / "secrets" / name
    return mine if os.path.realpath(mine) == _real(token.file) else None


def _real(spec: str) -> str:
    return os.path.realpath(os.path.expandvars(os.path.expanduser(spec)))


def _same(a, b) -> bool:
    """The same file, even where the disk ignores case (realpath keeps the case typed);
    paths that do not exist yet compare as text."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return str(a) == str(b)


def _drop_own_file(name: str, before: forges.ForgeAccount | None, after: forges.ForgesFile) -> None:
    """Delete the pasted token file once no account reads it: the account is gone, or reads
    another source now. Only the file Kraft wrote for this name, never one it was pointed at."""
    gone = _own_file(name, before)
    if gone is not None and not any(_own_file(name, a) for a in after.accounts.values()):
        gone.unlink(missing_ok=True)


def _refuse_added(st, current: forges.ForgesFile, updated: forges.ForgesFile) -> None:
    """A write that adds a repo/account problem is refused, as `_validate_repos` does."""
    before = forges.repo_problems(current, st.templates_dir)
    if added := [p for p in forges.repo_problems(updated, st.templates_dir) if p not in before]:
        raise HTTPException(422, "; ".join(added))


def _validated(accounts: dict) -> forges.ForgesFile:
    try:
        return forges.ForgesFile.model_validate({"accounts": accounts})
    except ValidationError as exc:
        raise HTTPException(422, forges.validation_text(exc)) from exc


def _no_command(sent, updated: forges.ForgeAccount) -> None:
    if sent is not None and updated.token is not None and updated.token.from_ == "command":
        raise HTTPException(403, _COMMAND_403)


def _keeps_command(old: forges.ForgeAccount | None, replaces_token: bool) -> None:
    """The file can gain a command source while a browser dialog is open, so the guard reads
    the account as it is now: no write over HTTP replaces or erases a command."""
    if replaces_token and old is not None and old.token and old.token.from_ == "command":
        raise HTTPException(403, _COMMAND_403)


def _no_shared(updated: forges.ForgesFile, name: str, sent) -> None:
    """A write over HTTP never points an account at a secret another account already reads,
    nor at another name's pasted file: the probe would send it to this account's host.
    Fixed words, never the name or path. Hand edits on disk are not checked here."""
    token = updated.accounts[name].token
    if sent is None or token is None:
        return
    others = [a.token for n, a in updated.accounts.items() if n != name and a.token]
    if token.env and any(o.env == token.env for o in others):
        raise HTTPException(403, _SHARED_403)
    if token.file:
        real = _real(token.file)
        secrets = kraft_home().resolve() / "secrets"
        own = secrets / name
        under = any(_same(str(p), secrets) for p in (Path(real), *Path(real).parents))
        if any(o.file and _same(_real(o.file), real) for o in others) or (
            under and not _same(real, own)
        ):
            raise HTTPException(403, _SHARED_403)


async def _json(request: Request):
    """The body parsed, or None when it is not JSON. Capped: nothing here is larger than a
    token, and a deep or huge body must not cost the server more than a refusal."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _BODY_MAX:
        raise HTTPException(422, "the body is larger than 64 KiB")
    raw = await request.body()
    if len(raw) > _BODY_MAX:
        raise HTTPException(422, "the body is larger than 64 KiB")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


async def _body(request: Request, model: type[BaseModel]):
    """The JSON body as `model`. Not a handler parameter: FastAPI's 422 quotes the `input`
    of each error (an unknown key's value, a bare string), and a body here may be a token."""
    raw = await _json(request)
    if not isinstance(raw, dict):
        raise HTTPException(422, "the body must be a JSON object")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(422, forges.validation_text(exc)) from exc


def _token_dict(token) -> None:
    if token is not None and not isinstance(token, dict):
        raise HTTPException(422, "token: must be an object")  # never what was sent


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
async def add_forge(request: Request):
    _forbid_sessions(request, "forge accounts")
    st = request.app.state
    body = await _body(request, AddForge)
    if not _NAME.fullmatch(body.name):
        raise HTTPException(422, "name: lowercase letters, digits, . - and _, starting with one")
    _token_dict(body.token)
    async with st.forges_lock:
        current = await _load(st)
        if body.name in current.accounts:
            raise HTTPException(409, f"forge account {body.name!r} already exists")
        updated = _validated(
            {**current.accounts, body.name: body.model_dump(exclude={"name"}, exclude_none=True)}
        )
        _no_command(body.token, updated.accounts[body.name])
        _no_shared(updated, body.name, body.token)
        _refuse_added(st, current, updated)
        _save(st, updated, (body.name,))
    apply_mod.notify(request.app)
    return _row(st, body.name, updated.accounts[body.name])


@api_router.patch("/forges/{name}")
async def edit_forge(name: str, request: Request):
    _forbid_sessions(request, "forge accounts")
    st = request.app.state
    body = await _body(request, EditForge)
    _token_dict(body.token)
    async with st.forges_lock:
        current = await _load(st)
        old = current.accounts.get(name)
        if old is None:
            raise HTTPException(404, f"no forge account named {name!r}")
        sent = body.model_dump(exclude_unset=True)
        _keeps_command(old, "token" in sent or sent.get("client") == "cli")
        fields = {**old.model_dump(by_alias=True, exclude_none=True), **sent}
        if fields.get("client") == "cli":  # gh/glab keep their own sign-in
            fields.pop("token", None)
            fields.pop("ca_file", None)
        # None clears a key, as it does in the file.
        fields = {k: v for k, v in fields.items() if v is not None}
        updated = _validated({**current.accounts, name: fields})
        _no_command(sent.get("token"), updated.accounts[name])
        _no_shared(updated, name, sent.get("token"))
        _refuse_added(st, current, updated)
        _save(st, updated, (name,))
        _drop_own_file(name, old, updated)
    apply_mod.notify(request.app)
    return _row(st, name, updated.accounts[name])


@api_router.put("/forges/{name}/token")
async def set_token(name: str, request: Request):
    """A pasted token. The body is read raw, never through a model: FastAPI's 422 quotes
    its input, and this is the one body that is a token."""
    _forbid_sessions(request, "forge accounts")
    st = request.app.state
    body = await _json(request)
    value, kind = (body.get("value"), body.get("kind")) if isinstance(body, dict) else (None, None)
    if not isinstance(value, str) or not isinstance(kind, str) or kind not in credentials.KINDS:
        raise HTTPException(422, _TOKEN_BODY)
    value = value.strip()
    if any(c.isspace() or not c.isprintable() for c in value):
        raise HTTPException(422, "the token holds whitespace or a control character")
    if len(value) < redact.MIN_LEN:
        raise HTTPException(422, f"the token is shorter than {redact.MIN_LEN} characters")
    if not _NAME.fullmatch(name):
        raise HTTPException(422, "name: lowercase letters, digits, . - and _, starting with one")
    async with st.forges_lock:
        current = await _load(st)
        old = current.accounts.get(name)
        if old is None:
            raise HTTPException(404, f"no forge account named {name!r}")
        _keeps_command(old, True)
        if old.client == "cli":
            raise HTTPException(
                409, f"forge account {name!r} is client: cli, which keeps no token; edit it first"
            )
        # Before the file exists: no log line can carry it unredacted.
        redact.register(value)
        redact.register(credentials.basic_form(value, kind))
        target = kraft_home().resolve() / "secrets" / name
        source = {"from": "file", "file": str(target), "kind": kind}
        fields = {**old.model_dump(by_alias=True, exclude_none=True), "token": source}
        updated = _validated({**current.accounts, name: fields})  # before any file is touched
        previous = target.read_bytes() if target.is_file() else None
        credentials.write_pasted(name, value)
        try:
            _save(st, updated, (name,))
        except BaseException:
            # The old token stays the old token, and a first paste leaves no orphan.
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                credentials.write_pasted(name, previous.decode().strip())
            raise
        _drop_own_file(name, old, updated)
    apply_mod.notify(request.app)
    return _row(st, name, updated.accounts[name])


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
        _drop_own_file(name, current.accounts[name], forges.ForgesFile(accounts=rest))
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
        found = (await probe.refresh(st, {name: account}))[name]
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
