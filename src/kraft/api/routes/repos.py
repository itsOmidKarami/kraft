from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import config as config_mod
from kraft.api import api_router, deps
from kraft.store.repos import RootMergePolicy

logger = logging.getLogger(__name__)


class RepoBody(BaseModel):
    path: str
    name: str | None = None
    default_chain_template: str | None = None
    test_command: str | None = None
    test_scopes: list[dict] | None = None
    setup_command: str | None = None
    forge: str | None = None
    project: str | None = None
    # None means "pick a safe default": enabled if a test command was given or
    # probed, disabled otherwise. An explicit True/False always wins, so a
    # caller that does supply one still gets the enable-without-test-command
    # refusal below.
    enabled: bool | None = None
    default_model: str | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None
    default_root_merge_policy: RootMergePolicy | None = None


class ProbeBody(BaseModel):
    path: str


def _auto_connect_children(repos: list[dict], parent: dict, submodule_paths: list[str]) -> None:
    """One disabled entry per `.gitmodules` path, appended to `repos` in place.

    `managed: False` -- detected, nobody has looked. Each child is probed on
    its own, so a Rust submodule under a Python workspace gets `cargo test`
    rather than inheriting its parent's command.

    Never touches an entry that already exists: re-connecting a workspace, or
    connecting one whose child an operator already added by hand, must not
    reset that child's latch.
    """
    known = {r["path"] for r in repos}
    for rel in submodule_paths:
        child_path = Path(parent["path"]) / rel
        try:
            probed = config_mod.probe_repo(child_path)
        except config_mod.ConfigError:
            # An uninitialized submodule is an empty directory, not a repo.
            # It reappears as a candidate the next time the parent is
            # connected, so skipping is the whole recovery.
            continue
        if probed["path"] in known:
            continue
        known.add(probed["path"])
        repos.append(
            {
                "path": probed["path"],
                "name": probed["name"],
                "default_chain_template": "default",
                "test_command": probed["test_command"],
                "test_scopes": None,
                "setup_command": probed["setup_command"],
                "forge": probed["forge"],
                "project": probed["project"],
                "enabled": False,
                "managed": False,
                "default_model": None,
                "deny_tools": [],
                "steering": [],
                "default_root_merge_policy": "bump",
            }
        )


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
        probed = config_mod.probe_repo(body.path, test_command=body.test_command)
    except config_mod.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
    if any(r["path"] == probed["path"] for r in repos):
        raise HTTPException(409, f"{probed['path']} is already connected")
    test_command = body.test_command or probed["test_command"]
    # Probed regardless of test_command now (Kraft-k4mx): probe_repo already
    # folded body.test_command into the root scope's command above, so there
    # is no longer a reason to suppress the nested scopes it finds alongside
    # it. body.test_scopes, when a caller supplies it directly, wins outright
    # -- the same "explicit beats probed" rule test_command already followed.
    #
    # A single-stack repo has no nested scopes, so probe_repo hands back one
    # root `["**"]` scope that just repeats test_command. Persisting that
    # would shadow every later test_command edit forever, the same
    # stale-override bug config.TestScope's comment describes
    # (Kraft-9wzy) -- so only a probe that actually found a nested scope (one
    # whose paths are not the root `["**"]`) is worth persisting here.
    # Counting the scopes would be wrong: a repo whose only marker is nested
    # (frontend/package.json, no root pyproject.toml) probes to exactly one
    # scope, and that one is real.
    probed_scopes = probed.get("test_scopes") or []
    has_nested = any(scope.get("paths") != ["**"] for scope in probed_scopes)
    nested_probed_scopes = probed_scopes if has_nested else None
    test_scopes = body.test_scopes if body.test_scopes is not None else nested_probed_scopes
    setup_command = (
        body.setup_command if body.setup_command is not None else probed["setup_command"]
    )
    entry = {
        "path": probed["path"],
        "name": body.name or probed["name"],
        "default_chain_template": body.default_chain_template or "default",
        "test_command": test_command,
        "test_scopes": test_scopes,
        "setup_command": setup_command,
        "forge": body.forge or probed["forge"],
        "project": body.project or probed["project"],
        "enabled": body.enabled if body.enabled is not None else bool(test_command or test_scopes),
        "default_model": body.default_model,
        "deny_tools": body.deny_tools or [],
        "steering": body.steering or [],
        "default_root_merge_policy": body.default_root_merge_policy or "bump",
        # A human typed this path. Set here rather than defaulted in the
        # loader, because `_auto_connect_children` below writes entries
        # through the same file and must NOT get this value.
        "managed": True,
    }
    repos.append(entry)
    _auto_connect_children(repos, entry, probed["submodules"])
    _refuse_enable_without_test_command(entry)
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
    test_scopes: list[dict] | None = None
    setup_command: str | None = None
    forge: str | None = None
    project: str | None = None
    enabled: bool | None = None
    default_model: str | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None
    local_files: list[str] | None = None
    default_root_merge_policy: RootMergePolicy | None = None


def _refuse_enable_without_test_command(entry: dict) -> None:
    """25's "disabled — new items can't target it" is the read side of this:
    the write side refuses to flip a repo on with nothing for verification to
    run, rather than let it enable silently and fail every verify.

    The repo's own `test_command`/`test_scopes` only. V1 verification has no
    registry fallback (`executor.dispatch._select_scopes`): a repo declaring
    neither stops every item, whatever the legacy registry's `on.test.run`
    says, so that binding cannot stand in for one here (Kraft-vd1ed).
    """
    if entry.get("enabled") and not (entry.get("test_command") or entry.get("test_scopes")):
        raise HTTPException(
            422,
            "cannot enable a repo with no test command — set its test command or test scopes first",
        )


@api_router.patch("/repos")
async def update_repo(body: RepoPatch, request: Request, path: str):
    st = request.app.state
    repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
    entry = deps._connected(repos, path)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    # exclude_unset, not `v is not None`: a field the caller left out of the
    # JSON body must not clobber the saved value, but one sent as an explicit
    # `null` (clearing forge, test_command, default_model, project — the
    # RepoDetail draft round-trips the whole Repo, nulls included) has to
    # actually take effect rather than being silently dropped.
    entry.update(body.model_dump(exclude_unset=True))
    # Any save is a touch -- editing a detected child's test command without
    # enabling it still promotes it out of the Detected section. One-way: a
    # later disable leaves this True, so the row reads as deliberately off.
    entry["managed"] = True
    _refuse_enable_without_test_command(entry)
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
