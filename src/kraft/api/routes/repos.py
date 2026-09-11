from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import config as config_mod
from kraft.api import api_router, deps

logger = logging.getLogger(__name__)


class RepoBody(BaseModel):
    path: str
    name: str | None = None
    default_chain_template: str | None = None
    test_command: str | None = None
    forge: str | None = None
    project: str | None = None
    enabled: bool = True
    default_model: str | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None


class ProbeBody(BaseModel):
    path: str


def _validate_repos(st, repos: list[dict]) -> None:
    """Write `repos` to a scratch file and run the real loader over it.

    Mirrors `put_registry`: the write side must reject exactly what the read
    side would later choke on, or a bad `POST`/`PATCH` persists and every
    subsequent `GET /repos` 500s until an operator hand-edits the file.
    """
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "repos.yaml"
        candidate.write_text(yaml.safe_dump({"repos": repos}))
        try:
            config_mod.load_repos(candidate, steering_dir=st.templates_dir / "steering")
        except config_mod.ConfigError as exc:
            raise HTTPException(422, str(exc)) from exc


@api_router.get("/repos")
async def list_repos(request: Request):
    st = request.app.state
    # No steering validation on the read path (config.load_repos): a steering
    # file deleted out from under an entry must not 422 the screen that would
    # let an operator clear it. See `config.load_repos`'s docstring.
    return {"repos": config_mod.load_repos(deps.repos_path(st), validate_steering=False)}


@api_router.post("/repos/probe")
async def probe_repo(body: ProbeBody, request: Request):
    """Read-only inspection of a candidate repo — Kraft never edits repo files."""
    try:
        return config_mod.probe_repo(body.path)
    except config_mod.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc


@api_router.post("/repos", status_code=201)
async def add_repo(body: RepoBody, request: Request):
    st = request.app.state
    try:
        probed = config_mod.probe_repo(body.path)
    except config_mod.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
    if any(r["path"] == probed["path"] for r in repos):
        raise HTTPException(409, f"{probed['path']} is already connected")
    entry = {
        "path": probed["path"],
        "name": body.name or probed["name"],
        "default_chain_template": body.default_chain_template or "default",
        "test_command": body.test_command or probed["test_command"],
        # Only when the connecting caller left `test_command` unset: an
        # explicit override there means one command for every diff, and
        # `test_scopes` wrapping it (config.load_repos) already gives that
        # the same effect without a stale probed scope list beside it.
        "test_scopes": None if body.test_command else (probed.get("test_scopes") or None),
        "forge": body.forge or probed["forge"],
        "project": body.project or probed["project"],
        "enabled": body.enabled,
        "default_model": body.default_model,
        "deny_tools": body.deny_tools or [],
        "steering": body.steering or [],
    }
    repos.append(entry)
    _validate_repos(st, repos)
    config_mod.save_repos(deps.repos_path(st), repos)
    # Index it now: a repo connected mid-session would otherwise stay invisible
    # to search (and to the intake picker) until the next restart. The scan is
    # `git ls-files .engineering/` — cheap enough to await. A scan failure must
    # not fail a connect that is already saved.
    try:
        await st.indexer.rescan_repo(entry["path"])
    except Exception:  # noqa: BLE001 -- scan_repo touches git and the filesystem
        logger.exception("index scan failed for newly connected repo %s", entry["path"])
    return entry


class RepoPatch(BaseModel):
    name: str | None = None
    default_chain_template: str | None = None
    test_command: str | None = None
    forge: str | None = None
    project: str | None = None
    enabled: bool | None = None
    default_model: str | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None


@api_router.patch("/repos")
async def update_repo(body: RepoPatch, request: Request, path: str):
    st = request.app.state
    repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
    entry = deps._connected(repos, path)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    entry.update({k: v for k, v in body.model_dump().items() if v is not None})
    _validate_repos(st, repos)
    config_mod.save_repos(deps.repos_path(st), repos)
    return entry


@api_router.delete("/repos", status_code=204)
async def remove_repo(request: Request, path: str):
    st = request.app.state
    repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
    entry = deps._connected(repos, path)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    kept = [r for r in repos if r["path"] != entry["path"]]
    config_mod.save_repos(deps.repos_path(st), kept)
    # Mirror of the connect-time scan. A repo with work items stays in
    # `Indexer.repos()` and is simply re-ingested by the next rescan.
    await st.indexer.purge_repo(entry["path"])
