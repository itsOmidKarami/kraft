from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from itertools import count
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import HTTPException, Request
from pydantic import AfterValidator, AliasChoices, BaseModel, Field

from kraft import config as config_mod
from kraft import detect
from kraft.api import api_router, deps
from kraft.store import open_counts_by_repo

logger = logging.getLogger(__name__)


def _absolute(path: str) -> str:
    """Refuse a relative path: this process would resolve it against its own
    cwd, not the caller's, and connect the wrong directory (Kraft-9efnk.32)."""
    if not Path(path).expanduser().is_absolute():
        raise ValueError(f"path must be absolute, not {path!r}: resolve it where you stand")
    return path


AbsolutePath = Annotated[str, AfterValidator(_absolute)]


class RepoBody(BaseModel):
    path: AbsolutePath
    name: str | None = None
    #: `default_chain_template` before 2.0; both names are read.
    default_chain: str | None = Field(
        default=None, validation_alias=AliasChoices("default_chain", "default_chain_template")
    )
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
    #: Model per harness profile id (Ruling 165); the loader checks the keys.
    models: dict[str, str] | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None


class ProbeBody(BaseModel):
    path: AbsolutePath
    #: False: only what needs no detector table (the path, name, forge),
    #: which is what resolving a path or checking "already connected" needs.
    detect: bool = True
    #: The root's test command, as `POST /repos` takes it: the proposal is
    #: what connecting with it would save.
    test_command: str | None = None


#: How long probing all of a connect's submodules may take, together.
_CHILDREN_BUDGET_S = 120.0


def _probe_children(
    parent: str, submodule_paths: list[str], templates_dir: Path
) -> dict[str, dict]:
    """Each submodule's probe, by its path relative to `parent`. Done before
    repos.yaml is read, in the probe's thread: the read-to-save section of a
    connect must not await, or a PATCH or DELETE in between is overwritten.
    An uninitialized submodule is an empty directory, not a repo: it is left
    out, and reappears as a candidate the next time the parent is connected,
    so skipping is the whole recovery.

    `.gitmodules` is the repository's to write: a path that resolves to the
    parent itself (`path = .`), outside it (`..`), or to one already probed
    is skipped, and all of them together get `_CHILDREN_BUDGET_S`."""
    out: dict[str, dict] = {}
    root = Path(parent).resolve()
    seen: set[Path] = set()
    deadline = time.monotonic() + _CHILDREN_BUDGET_S
    for rel in submodule_paths:
        where = (root / rel).resolve()
        if where == root or not where.is_relative_to(root) or where in seen:
            continue
        seen.add(where)
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            out[rel] = config_mod.probe_repo(where, templates_dir=templates_dir, timeout=left)
        except config_mod.ConfigError:
            continue
    return out


def _nested_scopes(probed: dict) -> list[dict] | None:
    """The probe's `test_scopes` worth persisting: only when one is nested.
    A single-stack repo probes to one root `["**"]` scope that just repeats
    test_command, and persisting it would shadow every later test_command
    edit (config.TestScope, Kraft-9wzy). Counting the scopes would be wrong:
    a repo whose only project is nested probes to exactly one, and it is
    real."""
    scopes = probed.get("test_scopes") or []
    return scopes if any(s.get("paths") != ["**"] for s in scopes) else None


def _auto_connect_children(
    repos: list[dict], parent: dict, probes: dict[str, dict]
) -> dict[str, dict]:
    """One disabled entry per `.gitmodules` path, appended to `repos` in place.
    Returns every child now connected -- new or already there -- by its path
    relative to `parent`: the members of the workspace `add_repo` declares.

    `managed: False` -- detected, nobody has looked. Each child is probed on
    its own, so a Rust submodule under a Python workspace gets `cargo test`
    rather than inheriting its parent's command.

    Never touches an entry that already exists: re-connecting a workspace, or
    connecting one whose child an operator already added by hand -- at the
    submodule's path, or as its own clone elsewhere -- must not reset that
    child's latch.
    """
    known = {r["path"]: r for r in repos}
    children: dict[str, dict] = {}
    # Only an entry a person connected, and never the root itself: a repo
    # mounting its own URL (a docs branch), or another root's stub of a
    # shared library, is not the member connected on its own.
    connected = [
        (r, config_mod.repository_identity(r["path"]))
        for r in repos
        if r is not parent and r.get("managed", True)
    ]
    for rel, probed in probes.items():
        if probed["path"] in known:
            children[rel] = known[probed["path"]]
            continue
        # The member connected on its own before its root: the submodule
        # checkout is another clone of it, and the member is the entry the
        # workspace must name, not a second, empty one (Kraft-d7aj3).
        identity = config_mod.repository_identity(probed["path"])
        same = [r for r, other in connected if config_mod.same_repository(identity, other)]
        if len(same) == 1:
            children[rel] = same[0]
            continue
        child = {
            "path": probed["path"],
            "name": probed["name"],
            "default_chain": "default",
            "test_command": probed["test_command"],
            "test_scopes": _nested_scopes(probed),
            "setup_command": probed["setup_command"],
            "forge": probed["forge"],
            "project": probed["project"],
            "enabled": False,
            "managed": False,
            "models": {},
            "deny_tools": [],
            "steering": [],
        }
        known[probed["path"]] = children[rel] = child
        repos.append(child)
    return children


def _repository_id(entry: dict, repos: list[dict]) -> str:
    """`entry`'s repository id, minted from its name when it has none: the
    `[a-z][a-z0-9_-]*` rule a workspace reference must match, and unique
    among `repos`. Adding an id changes nothing else about an entry."""
    if entry.get("id"):
        return entry["id"]
    base = re.sub(r"[^a-z0-9_-]+", "-", str(entry.get("name") or "repo").lower()).strip("-_")
    base = base if base[:1].isalpha() else f"repo-{base}".rstrip("-")
    taken = {r.get("id") for r in repos}
    entry["id"] = next(c for n in count(1) if (c := base if n == 1 else f"{base}-{n}") not in taken)
    return entry["id"]


def _declare_workspace(
    workspaces: dict, repos: list[dict], root: dict, children: dict[str, dict]
) -> None:
    """Declare `root` a workspace mounting `children` (`workspace-declares-
    root-and-members`), unless one is already rooted there. Typed membership
    replaces reading `.gitmodules` at intake: the intake picker offers these
    members, and the item's checkout assembles exactly the ones chosen."""
    if not children:
        return
    root_id = _repository_id(root, repos)
    if any(w.get("root") == root_id for w in workspaces.values()):
        return
    members = {}
    for rel, child in children.items():
        child_id = _repository_id(child, repos)
        members[child_id] = {"repository": child_id, "path": rel}
    ws_id = root_id if root_id not in workspaces else f"{root_id}-workspace"
    workspaces[ws_id] = {"root": root_id, "members": members}


def _editable_repos(st, path: str | None = None) -> tuple[list[dict], dict | None]:
    """The connected entries as the plain mappings `save_repos` writes -- the
    writer's boundary, where a route edits and re-saves what it read -- and
    the one connected at `path` (`deps._connected`), if any. Loaded through
    `RepoEntry` first, so a legacy shape is migrated on the way.

    Each holds the fields its entry sets and nothing it leaves to a default:
    saving one entry rewrote every entry with every field spelled out (23
    keys after a re-connect, `id: null`, `env: {}` and the rest), in a file
    people diff and edit by hand (R10a-07). A default left out reads back the
    same."""
    models = config_mod.load_repos(deps.repos_path(st))
    found = deps._connected(models, path) if path is not None else None
    repos = [r.model_dump_repo(exclude_unset=True) for r in models]
    return repos, next((d for m, d in zip(models, repos, strict=True) if m is found), None)


def _workspaces(st) -> dict:
    """The raw `workspaces:` section, as written."""
    return dict(config_mod.read_yaml(deps.repos_path(st), {}).get("workspaces") or {})


def _validate_repos(
    st, repos: list[dict], workspaces: dict | None = None, *, steering: bool = True
) -> None:
    """Write `repos` to a scratch file and run the real loader over it.

    Mirrors `put_registry`: the write side must reject exactly what the read
    side would later choke on, or a bad `POST`/`PATCH` persists and every
    subsequent `GET /repos` 500s until an operator hand-edits the file.
    """
    workspaces = _workspaces(st) if workspaces is None else workspaces
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "repos.yaml"
        candidate.write_text(yaml.safe_dump({"repos": repos, "workspaces": workspaces}))
        try:
            if steering:
                # Against the library's profiles; with no library loaded
                # there is nothing to name, and intake refuses the item.
                config_mod.load_repos(candidate, steering=deps.library_steering(st))
            config_mod.load_workspaces(candidate)
        except config_mod.ConfigError as exc:
            raise HTTPException(422, str(exc)) from exc


@api_router.get("/repos")
async def list_repos(request: Request):
    st = request.app.state
    # No steering validation on the read path (config.load_repos): a profile
    # removed out from under an entry must not 422 the screen that would let
    # an operator clear it. See `config.load_repos`'s docstring.
    path = deps.repos_path(st)
    repos = config_mod.load_repos(path)
    return {
        "repos": [r.model_dump_repo(mode="json") for r in repos],
        "workspaces": {
            ws_id: ws.model_dump(mode="json")
            for ws_id, ws in config_mod.load_workspaces(path).items()
        },
        "suggested": await asyncio.to_thread(_suggested, [r.path for r in repos]),
    }


def _suggested(connected: list[str]) -> str | None:
    """The git checkout this server was started in, when it is not connected
    yet: the path first-run offers to connect. None outside a checkout."""
    top = config_mod.git_read(Path.cwd(), "rev-parse", "--show-toplevel", expected_failure=True)
    if top is None or any(Path(p).resolve() == Path(top).resolve() for p in connected):
        return None
    return top


@api_router.post("/repos/probe")
async def probe_repo(body: ProbeBody, request: Request):
    """Read-only inspection of a candidate repo — Kraft never edits repo files.
    Off the event loop: it reads the whole tree's listing through git."""
    st = request.app.state
    if not body.detect:  # what needs no detector table is quick, and never queued
        try:
            return await asyncio.to_thread(config_mod.probe_repo, body.path, detect=False)
        except config_mod.ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
    # Each probe is a process of up to 2 GB for up to two minutes: a few at
    # once, and a caller past that is told so rather than queued.
    if getattr(st, "probing", None) is None:
        st.probing = asyncio.Semaphore(_PROBES_AT_ONCE)
    if st.probing.locked():
        raise HTTPException(429, "Kraft is already probing other repositories; try again shortly")
    async with st.probing:
        try:
            return await asyncio.to_thread(
                config_mod.probe_repo,
                body.path,
                templates_dir=st.templates_dir,
                test_command=body.test_command,
            )
        except config_mod.ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc


_PROBES_AT_ONCE = 2


@api_router.post("/repos", status_code=201)
async def add_repo(body: RepoBody, request: Request):
    st = request.app.state
    # One connect at a time between reading repos.yaml and saving it: the
    # probe runs in a thread, and two connects interleaving at that await
    # would each save the list without the other's entry. Per app, so the
    # lock belongs to the loop that serves it.
    if getattr(st, "connecting", None) is None:
        st.connecting = asyncio.Lock()
    async with st.connecting:
        return await _add_repo(body, st)


async def _add_repo(body: RepoBody, st) -> dict:
    templates_dir = st.templates_dir
    try:
        # "Already connected" first, from what needs no detector table: a
        # reconnect (`ensure_repo` on every handoff) must not pay for the probe,
        # or fail on an operator's broken detectors.yaml.
        # In a thread: it reads the working copy's .gitmodules and beads config.
        facts = await asyncio.to_thread(config_mod.probe_repo, body.path, detect=False)
        if any(r["path"] == facts["path"] for r in _editable_repos(st)[0]):
            raise HTTPException(409, f"{facts['path']} is already connected")
        if await asyncio.to_thread(detect.source_ref, Path(facts["path"])) is None:
            # A work item's branch starts from a commit: on a repository with
            # none, git makes it an empty orphan, without the files the
            # proposal was read from (`builtins._add_worktree` refuses too).
            raise HTTPException(422, detect.no_commit(facts["path"]))
        try:
            probed = await asyncio.to_thread(
                config_mod.probe_repo,
                body.path,
                test_command=body.test_command,
                templates_dir=templates_dir,
            )
        except config_mod.ConfigError as exc:
            if body.test_command is None or body.setup_command is None:
                raise
            # Both commands given: the proposal they replace is not needed, so
            # a repository the probe cannot read still connects with them.
            probed = {
                **facts,
                "test_command": body.test_command,
                "setup_command": body.setup_command,
                "test_scopes": None,
                **{k: [] for k in ("test_markers", "candidates", "scopes", "missing_setup")},
                "stopped": [],
                "missing_tools": [],
                "read_from": None,
                "probe_failed": str(exc),
            }
        child_probes = await asyncio.to_thread(
            _probe_children, probed["path"], probed["submodules"], templates_dir
        )
    except config_mod.ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    # From here to the save, nothing awaits: PATCH and DELETE save without the
    # connect lock, and a stale list saved over theirs would undo them.
    repos, _ = _editable_repos(st)
    # `""` is a decision, not an absence: the repo deliberately has no tests.
    test_command = body.test_command if body.test_command is not None else probed["test_command"]
    # Probed regardless of test_command now (Kraft-k4mx): probe_repo already
    # folded body.test_command into the root scope's command above, so there
    # is no longer a reason to suppress the nested scopes it finds alongside
    # it. body.test_scopes, when a caller supplies it directly, wins outright
    # -- the same "explicit beats probed" rule test_command already followed.
    #
    # Only a nested scope is worth persisting (`_nested_scopes`).
    nested_probed_scopes = _nested_scopes(probed)
    # An empty list states nothing: repos.yaml refuses `test_scopes: []`, and
    # the first-run wizard once sent exactly that for a repo with no
    # recognised stack, so the probe decides as it does for None.
    test_scopes = body.test_scopes if body.test_scopes else nested_probed_scopes
    setup_command = (
        body.setup_command if body.setup_command is not None else probed["setup_command"]
    )
    entry = {
        "path": probed["path"],
        "name": body.name or probed["name"],
        "default_chain": body.default_chain or "default",
        "test_command": test_command,
        "test_scopes": test_scopes,
        "setup_command": setup_command,
        "forge": body.forge or probed["forge"],
        "project": body.project or probed["project"],
        "enabled": body.enabled
        if body.enabled is not None
        else _has_tests({"test_command": test_command, "test_scopes": test_scopes}),
        "models": body.models or {},
        "deny_tools": body.deny_tools or [],
        "steering": body.steering or [],
        # A human typed this path. Set here rather than defaulted in the
        # loader, because `_auto_connect_children` below writes entries
        # through the same file and must NOT get this value.
        "managed": True,
    }
    repos.append(entry)
    children = _auto_connect_children(repos, entry, child_probes)
    workspaces = _workspaces(st)
    _declare_workspace(workspaces, repos, entry, children)
    _refuse_enable_without_test_command(entry)
    _validate_repos(st, repos, workspaces)
    config_mod.save_repos(deps.repos_path(st), repos, workspaces)
    # Index it now: a repo connected mid-session would otherwise stay invisible
    # to search (and to the intake picker) until the next restart. The scan is
    # `git ls-files .engineering/` — cheap enough to await. A scan failure must
    # not fail a connect that is already saved.
    try:
        await st.indexer.rescan_repo(entry["path"])
    except Exception:  # noqa: BLE001 -- scan_repo touches git and the filesystem
        logger.exception("index scan failed for newly connected repo %s", entry["path"])
    # Told, not stored: which marker files the proposed commands came from,
    # every command the evidence supported, and what is still undecided.
    told = (
        "test_markers",
        "candidates",
        "scopes",
        "missing_setup",
        "read_from",
        "stopped",
        "missing_tools",
    )
    return {**entry, **{k: probed[k] for k in told}, **_failed(probed)}


def _failed(probed: dict) -> dict:
    """Why the probe failed, when the commands were given and saved anyway."""
    return {"probe_failed": probed["probe_failed"]} if "probe_failed" in probed else {}


class RepoPatch(BaseModel):
    name: str | None = None
    #: `default_chain_template` before 2.0; both names are read.
    default_chain: str | None = Field(
        default=None, validation_alias=AliasChoices("default_chain", "default_chain_template")
    )
    test_command: str | None = None
    test_scopes: list[dict] | None = None
    setup_command: str | None = None
    forge: str | None = None
    project: str | None = None
    enabled: bool | None = None
    models: dict[str, str] | None = None
    deny_tools: list[str] | None = None
    steering: list[str] | None = None
    local_files: list[str] | None = None
    intent_dir: str | None = None


def _has_tests(entry: dict) -> bool:
    """Whether verification has been told what to run: a test command (`""`
    is the deliberate "no tests"), or test scopes."""
    return entry.get("test_command") is not None or bool(entry.get("test_scopes"))


def _refuse_enable_without_test_command(entry: dict) -> None:
    """25's "disabled — new items can't target it" is the read side of this:
    the write side refuses to flip a repo on with nothing for verification to
    run, rather than let it enable silently and fail every verify.

    The repo's own `test_command`/`test_scopes` only. V1 verification has no
    registry fallback (`executor.dispatch._select_scopes`): a repo declaring
    neither stops every item (Kraft-vd1ed).
    """
    # Absent means enabled (Ruling 212), as RepoEntry.enabled defaults.
    if entry.get("enabled", True) and not _has_tests(entry):
        raise HTTPException(
            422,
            "cannot enable a repo with no test command — set its test command or test scopes "
            'first (test_command: "" declares a repo with no tests)',
        )


@api_router.patch("/repos")
async def update_repo(body: RepoPatch, request: Request, path: str):
    st = request.app.state
    repos, entry = _editable_repos(st, path)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    # exclude_unset, not `v is not None`: a field the caller left out of the
    # JSON body must not clobber the saved value, but one sent as an explicit
    # `null` (clearing forge, test_command, project — the
    # RepoDetail draft round-trips the whole Repo, nulls included) has to
    # actually take effect rather than being silently dropped.
    had_tests = _has_tests(entry)
    patch = body.model_dump(exclude_unset=True)
    entry.update(patch)
    # Any save is a touch -- editing a detected child's test command without
    # enabling it still promotes it out of the Detected section. One-way: a
    # later disable leaves this True, so the row reads as deliberately off.
    entry["managed"] = True
    # Only a PATCH that *makes* the repo enabled-without-tests is refused: one
    # that turns it on, or clears its last test command. A rename on an entry
    # already in that state (absent `enabled`, Ruling 212) goes through.
    if entry.get("enabled", True) and not _has_tests(entry):
        if patch.get("enabled") is True:
            _refuse_enable_without_test_command(entry)
        elif had_tests:
            raise HTTPException(
                422,
                "cannot clear the test command of an enabled repo — "
                "disable it, or set test scopes, in the same change",
            )
    _validate_repos(st, repos)
    config_mod.save_repos(deps.repos_path(st), repos)
    # The whole entry, defaults included, as the answer always was:
    # `kraft repo connect` reads the saved entry back from it, and a client
    # of the API may read any field. Only the file keeps to what was set.
    full = config_mod.RepoEntry.model_validate(entry, context={"unrecognised_keys_reported": True})
    return full.model_dump_repo()


@api_router.delete("/repos", status_code=204)
async def remove_repo(request: Request, path: str):
    st = request.app.state
    repos, entry = _editable_repos(st, path)
    if entry is None:
        raise HTTPException(404, f"{path} is not connected")
    # Its items' worktrees and merge steps resolve the repo from repos.yaml;
    # removing it under them strands them (Kraft-d2ire).
    if live := st.db.read(open_counts_by_repo).get(entry["path"]):
        raise HTTPException(
            409,
            f"{entry['path']} has {live} open item(s), paused ones included; complete, "
            "cancel or abandon them first (kraft item complete, cancel, or abandon --yes)",
        )
    kept = [r for r in repos if r["path"] != entry["path"]]
    # A workspace still naming it would no longer load; refused, not dropped.
    # Steering is not re-checked: a profile removed from the library must not
    # block a disconnect.
    _validate_repos(st, kept, steering=False)
    config_mod.save_repos(deps.repos_path(st), kept)
    # Mirror of the connect-time scan. A repo with work items stays in
    # `Indexer.repos()` and is simply re-ingested by the next rescan.
    await st.indexer.purge_repo(entry["path"])
