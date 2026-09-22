from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from kraft import auth as auth_mod
from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft import policy as policy_mod
from kraft import store
from kraft.adapters import beads
from kraft.api import api_router, deps, perimeter
from kraft.templates import catalogue
from kraft.templates.library import (
    CHAINS_DIR,
    LIBRARY_FILE,
    TemplateIssue,
    TemplateLibrary,
    TemplateLibraryError,
)
from kraft.templates.models import ResolvedChain, retired_keys
from kraft.worker import steering as steering_mod

# ══ settings (design 5a–5e) ═════════════════════════════════════════════════
#
# Every write lands in the same versioned YAML an operator edits by hand
# (`02` §4.7 revised), and every write re-reads and re-validates the whole set
# so a bad save is refused rather than discovered at the next restart.


def _chain_summary(library: TemplateLibrary, id: str) -> dict:
    """One saved chain as the chain picker and the intake preview show it:
    the resolved nodes in the SPA's `ChainNode` shape, which carry the
    `covered_by` an attachment strikes by. A chain that does not resolve is
    listed with its error rather than dropped, so the Chains screen can still
    open it to fix it. `uses` names, per node id, the library components that
    node is built from, which is what the screen links each node to."""
    uses = {node: sorted(refs) for node, refs in library.references(id).items() if refs}
    try:
        nodes = [store.node_view(n) for n in library.resolve_chain(id).nodes]
    except TemplateLibraryError as exc:
        return {"id": id, "nodes": [], "gates": 0, "error": str(exc), "uses": uses}
    gates = sum(1 for n in nodes if n["kind"] == "gate")
    return {"id": id, "nodes": nodes, "gates": gates, "error": None, "uses": uses}


@api_router.get("/templates/chains")
async def list_templates(request: Request):
    """Every saved V1 chain -- the chains intake materializes, so the intake
    preview and intake agree (Kraft-pplyo)."""
    library = deps.library_or_503(request.app.state)
    return [_chain_summary(library, id) for id in sorted(library.chain_ids)]


# ── the library itself (Kraft-6xkkm). Chains live under `/templates/chains/`
# and the library under `/templates/library` (Ruling 204), so no chain id can
# shadow a library route or the other way round, whatever order they declare.


def _library_view(st, library: TemplateLibrary) -> dict:
    """`library.yaml` as the Library screen edits it -- the file's text -- and
    every component in it, linted by the same pass `admin templates lint` and
    `/health` use (`TemplateLibrary.lint`)."""
    path = st.templates_dir / LIBRARY_FILE
    issues = library.lint(getattr(st, "instance_policy", None))
    listed = catalogue.components(library, issues)
    for component in listed:
        component["issues"] = [_issue_view(i) for i in component["issues"]]
    try:
        text = path.read_text()
    except OSError as exc:
        raise HTTPException(500, f"{path}: cannot read: {exc}") from exc
    return {"file": str(path), "text": text, "components": listed}


@api_router.get("/templates/library")
async def get_library(request: Request):
    st = request.app.state
    return _library_view(st, deps.library_or_503(st))


@api_router.get("/templates/library/{ref}")
async def get_library_component(ref: str, request: Request):
    """One component, by its id (`tasks.implementer`) or a bare name no
    other section shares."""
    st = request.app.state
    listed = _library_view(st, deps.library_or_503(st))["components"]
    found = catalogue.find(listed, ref)
    if found is None:
        raise HTTPException(404, f"no library component {ref!r}")
    return found


class LibraryText(BaseModel):
    text: str


@api_router.put("/templates/library")
async def put_library(body: LibraryText, request: Request):
    """Save `library.yaml`, only if every chain that resolves now still
    resolves against it -- the chain save's own checks, over every chain --
    and written verbatim. A chain that did not resolve before the edit is not
    the edit's to fix, so it does not refuse it."""
    st = request.app.state
    library = deps.library_or_503(st)
    path = st.templates_dir / LIBRARY_FILE
    data = _authored_mapping(body.text, "library")
    try:
        candidate = library.with_library(data, path)
    except TemplateLibraryError as exc:
        raise HTTPException(422, str(exc)) from exc
    policy = getattr(st, "instance_policy", None)
    broken_before = {i.chain for i in library.lint(policy)}
    if issues := [i for i in candidate.lint(policy) if i.chain not in broken_before]:
        raise HTTPException(422, str(issues[0]))
    config_mod.write_text(path, body.text)
    deps._reload_templates(st)
    return _library_view(st, deps.library_or_503(st))


# ── Template Schema V1 inspection (docs/templates-v1-design.md "Validation
# surface"). Every one of these reads and none writes: not a file, and not the
# library the daemon is running (`st.library`).


def _resolved_view(chain: ResolvedChain) -> dict:
    """A resolved chain as the API shows it: the chain with `extends` expanded
    -- what the author wrote plus what it inherited, no defaults filled in --
    its canonical task paths, the steering text it selects, and the nodes in
    the SPA's `ChainNode` shape. Nothing per work item: no target, no policy,
    no attachment trim (`resolved-template-is-deterministic`)."""
    return {
        "id": chain.id,
        "chain": chain.chain.model_dump(mode="json", exclude_unset=True),
        "task_paths": list(chain.task_paths),
        "steering": chain.steering or {},
        "nodes": [store.node_view(n) for n in chain.nodes],
    }


def _issue_view(issue: TemplateIssue) -> dict:
    return {"file": str(issue.file), "chain": issue.chain, "message": issue.message}


@api_router.get("/templates/lint")
async def lint_templates(request: Request):
    """The installed library as it is on disk now, which is what an operator
    who just edited it is asking about -- read into a scratch library, never
    into `st.library` (`template-lint-reports-library-validity`)."""
    st = request.app.state
    report = await asyncio.to_thread(
        TemplateLibrary.lint_dir,
        st.templates_dir,
        skills_dir=st.skills_dir,
        instance_policy=getattr(st, "instance_policy", None),
    )
    return {
        "valid": report.valid,
        "chains": list(report.chains),
        "issues": [_issue_view(i) for i in report.issues],
    }


@api_router.get("/templates/chains/{tid}/resolved")
async def get_resolved_template(tid: str, request: Request):
    """A saved chain of the library this daemon runs, resolved and not
    materialized (`resolved-template-api-shows-saved-chain`)."""
    library = deps.library_or_503(request.app.state)
    if tid not in library.chain_ids:
        raise HTTPException(404, f"unknown chain template {tid!r}")
    try:
        return _resolved_view(library.resolve_chain(tid))
    except TemplateLibraryError as exc:
        raise HTTPException(422, str(exc)) from exc


class ResolveBody(BaseModel):
    """`POST /templates/resolve`: one unsaved chain against the installed
    library, or a complete unsaved library on its own -- never both."""

    model_config = ConfigDict(extra="forbid")

    chain: dict | None = None
    #: `library.yaml`'s content, and the chains beside it.
    library: dict | None = None
    chains: list[dict] = []

    @model_validator(mode="after")
    def _one_input(self):
        if (self.chain is None) == (self.library is None):
            raise ValueError("send either 'chain' or 'library', not both and not neither")
        if self.chains and self.library is None:
            raise ValueError("'chains' come with a 'library'")
        return self


#: The nominal root of a request's unsaved input, so an error names the part of
#: the request it is about the same way it would name a file. Nothing is there.
_UNSAVED = Path("<unsaved>")


def _unsaved_chain_path(body: dict, index: int) -> Path:
    name = body.get("id") if isinstance(body.get("id"), str) else f"chain-{index}"
    return _UNSAVED / CHAINS_DIR / f"{name}.yaml"


@api_router.post("/templates/resolve")
async def resolve_templates(body: ResolveBody, request: Request):
    """Resolve input that is not saved, and leave it unsaved
    (`resolve-api-supports-candidate-and-library-input`). Every chain that
    resolves is in `chains`; every one that does not is an issue."""
    st = request.app.state
    issues: list[TemplateIssue] = []
    try:
        if body.chain is not None:
            path = _unsaved_chain_path(body.chain, 0)
            library, id = deps.library_or_503(st).with_chain(path, body.chain)
            ids = [id]
        else:
            library = TemplateLibrary.from_mappings(
                body.library,
                [(_unsaved_chain_path(c, i), c) for i, c in enumerate(body.chains)],
                library_path=_UNSAVED / LIBRARY_FILE,
                skills_dir=st.skills_dir,
            )
            ids = list(library.chain_ids)
    except TemplateLibraryError as exc:
        return {"chains": [], "issues": [_issue_view(TemplateIssue(_UNSAVED, None, str(exc)))]}
    chains = []
    for id in ids:
        try:
            chains.append(_resolved_view(library.resolve_chain(id)))
        except TemplateLibraryError as exc:
            issues.append(TemplateIssue(_UNSAVED, id, str(exc)))
    return {"chains": chains, "issues": [_issue_view(i) for i in issues]}


#: An authored chain id, the same rule as every other V1 identifier: it names
#: the file `PUT /templates/chains/{id}` writes, so it can never walk out of `chains/`.
_CHAIN_ID = re.compile(r"[a-z][a-z0-9_-]*")


def _authored_mapping(text: str, what: str) -> dict:
    """A chain or library file's text as the mapping a save checks, or the 422
    that says why it cannot be one."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"not YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, f"a {what} file is a mapping")
    if retired := retired_keys(data):
        raise HTTPException(
            422,
            f"{retired[0]} is retired (Ruling 196): a wait's timeout is its task's own "
            "policy.total_time_cap_minutes",
        )
    return data


@api_router.get("/templates/chains/{tid}")
async def get_template(tid: str, request: Request):
    """One saved chain as its author wrote it: the file's text, which is what
    the Chains screen edits, and the mapping it parses to."""
    library = deps.library_or_503(request.app.state)
    if tid not in library.chain_ids:
        raise HTTPException(404, f"unknown chain template {tid!r}")
    path = library.chain_file(tid)
    try:
        text = path.read_text()
    except OSError as exc:
        raise HTTPException(500, f"{path}: cannot read: {exc}") from exc
    return {"id": tid, "file": str(path), "text": text, "chain": yaml.safe_load(text) or {}}


class ChainText(BaseModel):
    text: str


@api_router.put("/templates/chains/{tid}")
async def put_template(tid: str, body: ChainText, request: Request):
    """Save one chain file, only if the library still resolves it: the text is
    checked as a candidate against the installed library -- the same check
    `POST /templates/resolve` runs -- and written verbatim, so the author's
    comments and layout survive. A new id becomes `chains/<id>.yaml`."""
    st = request.app.state
    if not _CHAIN_ID.fullmatch(tid):
        raise HTTPException(400, f"invalid chain template id {tid!r}")
    library = deps.library_or_503(st)
    path = (
        library.chain_file(tid)
        if tid in library.chain_ids
        else st.templates_dir / CHAINS_DIR / f"{tid}.yaml"
    )
    chain = _authored_mapping(body.text, "chain")
    if chain.get("id", tid) != tid:
        raise HTTPException(422, f"the file declares id {chain['id']!r}, not {tid!r}")
    try:
        candidate, _ = library.with_chain(path, {**chain, "id": tid})
    except TemplateLibraryError as exc:
        raise HTTPException(422, str(exc)) from exc
    issues = [i for i in candidate.lint(getattr(st, "instance_policy", None)) if i.chain == tid]
    if issues:
        raise HTTPException(422, issues[0].message)
    path.parent.mkdir(parents=True, exist_ok=True)
    config_mod.write_text(path, body.text)
    deps._reload_templates(st)
    return {"id": tid, "file": str(path), "text": body.text}


class ParseBody(BaseModel):
    text: str


@api_router.post("/templates/parse")
async def parse_template_yaml(body: ParseBody):
    """Operator-typed YAML into the mapping `POST /templates/resolve` checks.
    No YAML library ships in the frontend; this is the server doing the one
    direction that is genuinely hard to hand-roll. Parsing only."""
    try:
        data = yaml.safe_load(body.text)
    except yaml.YAMLError as exc:
        # str(exc) is safe here: pyyaml's message is a parse-position
        # description of the operator's own submitted text, not a traceback.
        return {"chain": None, "error": str(exc)}
    if not isinstance(data, dict):
        return {"chain": None, "error": "a chain file is a mapping"}
    return {"chain": data, "error": None}


@api_router.post("/templates/reload")
async def reload_templates_endpoint(request: Request):
    """Reread the V1 library from disk into the running server. A library that
    does not load is reported, not raised: the daemon keeps running degraded,
    exactly as it would have started. `policy.yaml` is reread first, since a
    chain past a `maxima:` ceiling is a lint issue; a policy that does not
    validate is refused and the running one kept (Kraft-m86uq)."""
    st = request.app.state
    refused_policy = deps.reload_policy(st)
    deps._reload_templates(st)
    ids = st.library.chain_ids if st.library is not None else ()
    valid = sorted(id for id in ids if id not in st.invalid_chains)
    return {
        "valid": valid,
        "invalid_templates": deps.invalid_templates(st),
        "refused_policy": refused_policy,
    }


class PolicyBody(BaseModel):
    """What the Policy screen edits. `extra="allow"`: a key this model doesn't
    name (V1 `defaults:`/`maxima:`, `forge_cli_timeout_s`, ...) still reaches
    the merge and the loader's own validation instead of being dropped here."""

    model_config = ConfigDict(extra="allow")

    loops: dict
    default: dict
    findings: dict | None = None
    budget: dict | None = None
    max_concurrent: int = Field(default=3, ge=1)
    rate_limit_retries: int | None = None
    triggers: list[dict] | None = None
    archive: dict | None = None
    auto_escalate_stuck: bool | None = None
    auto_escalate_stuck_cap: int | None = None
    auto_escalate_delay_s: int | None = None


@api_router.get("/policy")
async def get_policy(request: Request):
    st = request.app.state
    data = config_mod.read_yaml(st.templates_dir / "policy.yaml", {"loops": {}, "default": {}})
    data.setdefault("max_concurrent", st.policy.max_concurrent if st.policy else 3)
    return data


@api_router.put("/policy")
async def put_policy(body: PolicyBody, request: Request):
    """Caps apply to loops that start after the save — a counter already running
    keeps the cap it snapshotted at first fire (`02` §2.C).

    The body is merged over the file on disk, top-level key by key (Kraft-xh2x8):
    a key the request doesn't send is kept, and a key it sends as `null` is
    removed. A save never drops what it didn't edit. The V1 instance policy
    (`defaults:`/`maxima:`) is refreshed with the legacy `Policy`, so the
    daemon holds the ceiling the file now says from the next intake on."""
    st = request.app.state
    try:
        data = config_mod.read_yaml(st.templates_dir / "policy.yaml", {})
    except config_mod.ConfigError:
        # An unparseable file has nothing to keep; the save replaces it, as
        # it always has, so the screen stays a way out of a broken file.
        data = {}
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    data.setdefault("max_concurrent", body.max_concurrent)
    # A file written before Ruling 196 is saved under the name that replaced
    # its retired key, so a save never writes it back.
    maxima = data.get("maxima")
    if isinstance(maxima, dict) and policy_mod.RETIRED_WAIT_TIMEOUT in maxima:
        value = maxima.pop(policy_mod.RETIRED_WAIT_TIMEOUT)
        if value is not None:
            maxima.setdefault("total_time_cap_minutes", value)
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "policy.yaml"
        candidate.write_text(yaml.safe_dump(data))
        try:
            parsed = policy_mod.PolicyInput.from_yaml(candidate)
            policy_obj = policy_mod.Policy.from_input(parsed, source=candidate)
        except policy_mod.PolicyError as exc:
            raise HTTPException(422, str(exc)) from exc
    config_mod.write_yaml(st.templates_dir / "policy.yaml", data)
    deps.apply_policy(st, policy_obj, parsed.instance_policy())
    deps.lint_loaded(st)
    return data


@api_router.get("/theme")
async def get_theme(request: Request):
    st = request.app.state
    return config_mod.Theme.load(st.templates_dir / "theme.yaml").model_dump()


@api_router.put("/theme")
async def put_theme(body: config_mod.Theme, request: Request):
    st = request.app.state
    body.save(st.templates_dir / "theme.yaml")
    return body.model_dump()


class SteeringBody(BaseModel):
    body: str


def _steering_dir(st) -> Path:
    return st.templates_dir / "steering"


def _check_steering_change(st, name: str, body: str | None) -> None:
    """Would the configs still load with this change applied? Raise if not.

    Names resolve at config-load time, not at write time, so the edited file is
    only half the question: `repos.yaml` names it, and a body
    that pushes an assembled block past the injection budget -- or a delete that
    orphans a name -- breaks a launch nowhere near this screen.

    Checked against a scratch *copy* of the steering directory with the change
    applied (`body=None` means the delete), never by writing the real file and
    undoing it after. Agent dispatch reads these files straight off disk, so the
    real directory must never be briefly wrong -- and an undo only undoes the
    failures it anticipated. Note the inversion versus `_validate_repos`: there
    the config is the candidate and the steering dir is real; here it is the
    other way round, which is why both loaders take a `steering_dir`.

    Synchronous and directory-sized, so both callers run it through
    `asyncio.to_thread` (Kraft-e9a) -- an `HTTPException` raised here propagates
    out of the thread unchanged. The real write (`config_mod.write_text`) and
    `path.unlink()` stay on the loop: one file, one syscall, and moving those
    would be cargo.
    """
    real = _steering_dir(st)
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        if real.is_dir():
            for src in real.glob("*.md"):
                try:
                    shutil.copy2(src, scratch / src.name)
                except OSError:
                    # This directory is hand-editable, so it can hold a broken
                    # symlink, an unreadable file, or a directory named *.md.
                    # Skip it rather than failing the save: if a config
                    # references it, the loaders below reject the save with a
                    # message naming it; if nothing does, it is none of this
                    # save's business, and the write path it replaced never
                    # touched unreferenced entries either.
                    continue
        target = scratch / f"{name}.md"
        if body is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(body)
        try:
            config_mod.load_repos(deps.repos_path(st), steering_dir=scratch)
        except config_mod.ConfigError as exc:
            raise HTTPException(422, str(exc)) from exc


@api_router.get("/steering")
async def list_steering(request: Request):
    """Names and sizes, not bodies: the list is a picker, and the assembled
    budget is the number an operator is actually rationing."""
    steering_dir = _steering_dir(request.app.state)
    if not steering_dir.is_dir():
        return {"files": [], "max_bytes": steering_mod.Steering.MAX_BYTES}
    files = []
    for path in sorted(steering_dir.glob("*.md")):
        try:
            files.append({"name": path.stem, "bytes": len(path.read_text().encode())})
        except OSError, ValueError:
            # Unreadable or not UTF-8: it exists and it is broken, which is
            # more useful on the screen than a file that silently is not there.
            files.append({"name": path.stem, "bytes": None})
    return {"files": files, "max_bytes": steering_mod.Steering.MAX_BYTES}


@api_router.get("/steering/{name}")
async def get_steering(name: str, request: Request):
    st = request.app.state
    try:
        path = steering_mod.Steering(dir=_steering_dir(st)).path(name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return {"name": name, "body": path.read_text()}
    except FileNotFoundError:
        raise HTTPException(404, f"unknown steering file {name!r}") from None
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"{name}: cannot read: {exc}") from exc


@api_router.put("/steering/{name}")
async def put_steering(name: str, body: SteeringBody, request: Request):
    st = request.app.state
    steering_dir = _steering_dir(st)
    try:
        path = steering_mod.Steering(dir=steering_dir).path(name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    await asyncio.to_thread(_check_steering_change, st, name, body.body)
    config_mod.write_text(path, body.body)
    # Nothing to reload: no `app.state` holds steering bodies. They are read
    # from disk at dispatch, and `resolve_invocation` re-checks the assembled
    # budget at every launch, so the next agent picks this up on its own.
    return {"name": name, "body": body.body}


@api_router.delete("/steering/{name}")
async def delete_steering(name: str, request: Request):
    st = request.app.state
    try:
        path = steering_mod.Steering(dir=_steering_dir(st)).path(name, where="steering")
    except steering_mod.SteeringError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, f"unknown steering file {name!r}")
    await asyncio.to_thread(_check_steering_change, st, name, None)
    path.unlink()
    return {"deleted": name}


class IntakeBody(BaseModel):
    """`intake.yaml`, typed. Unlike `policy.yaml` this file is a flat fixed
    shape, so the bounds live here rather than in a loader that has to accept a
    hand-edited file it did not write."""

    enabled: bool
    # The poller floors this at 30s anyway; rejecting it is better than
    # accepting a number the running instance will not honour.
    interval_s: int = Field(ge=30)
    # Moved to `policy.yaml` (`Policy.max_concurrent`); kept optional here for
    # one release so an old client or a hand-edited file round-trips without
    # a 422. No longer read back as authoritative anywhere.
    max_concurrent: int | None = Field(default=None, ge=1)
    # P0 is the *highest* priority, so the ceiling is "P<n> and below".
    priority_ceiling: int = Field(ge=0, le=4)
    repos: list[str] = []


@api_router.get("/intake")
async def get_intake(request: Request):
    """From disk, like `GET /policy`. `intake.yaml` was hand-edited until this
    screen existed, so returning the cached state would hide an edit made since
    boot and let the next save overwrite it silently."""
    st = request.app.state
    try:
        data = config_mod.Intake.load(st.templates_dir / "intake.yaml").model_dump()
    except config_mod.ConfigError:
        # Unreadable: show what the instance is actually running on, which
        # lifespan already degraded to the defaults. Saving replaces the file.
        data = dict(st.intake)
    try:
        repos = config_mod.load_repos(deps.repos_path(st), validate_steering=False)
    except config_mod.ConfigError:
        repos = []
    last_picked_up = st.db.read(store.last_auto_pickup_at)
    repo_pickups: dict[str, dict] = {}
    for r in repos:
        try:
            ready = await beads.ready(cwd=r.path)
            count = len(ready)
        except Exception:  # noqa: BLE001 -- a settings-page read must not 500 on a bad repo
            count = None
        repo_pickups[r.path] = {"items": count, "last_picked_up": last_picked_up.get(r.path)}
    data["repo_pickups"] = repo_pickups
    data["recent_pickups"] = st.db.read(store.recent_auto_pickups)
    return data


@api_router.put("/intake")
async def put_intake(body: IntakeBody, request: Request):
    """Applies without a restart: the poller task is replaced, not just the
    config it reads. `interval_s` is read once at task start, so a live poller
    would otherwise keep the old interval until the next reboot."""
    app_ = request.app
    st = app_.state
    data = body.model_dump()
    config_mod.Intake.model_validate(data).save(st.templates_dir / "intake.yaml")
    st.intake = data
    async with st.intake_lock:
        task = st.intake_task
        # Clear it before the await: a second saver that gets in here while we
        # are waiting must not find, and cancel, a task we are already retiring.
        st.intake_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if data["enabled"]:
            st.intake_task = asyncio.ensure_future(intake_mod.poller(app_))
    return data


class AccessBody(BaseModel):
    bind: str | None = None
    port: int | None = None
    password: str | None = None
    session_expiry_days: int | None = None
    allowed_hosts: list[str] | None = None


@api_router.get("/access")
async def get_access(request: Request):
    access = request.app.state.access
    return {
        "bind": access["bind"],
        "port": access["port"],
        "session_expiry_days": access["session_expiry_days"],
        "password_set": bool(access["password_hash"]),
        "allowed_hosts": access["allowed_hosts"],
        "auth_required": perimeter._requires_auth(request.app, request),
    }


@api_router.put("/access")
async def put_access(body: AccessBody, request: Request):
    st = request.app.state
    access = dict(st.access)
    if body.bind is not None:
        access["bind"] = body.bind
    if body.port is not None:
        access["port"] = body.port
    if body.session_expiry_days is not None:
        access["session_expiry_days"] = body.session_expiry_days
    if body.allowed_hosts is not None:
        access["allowed_hosts"] = body.allowed_hosts
    if body.password:
        access["password_hash"] = auth_mod.hash_password(body.password)
    # Binding off-localhost without a password is the configuration that puts an
    # agent runner on the office wifi. Refuse it rather than allow it quietly.
    if access["bind"] not in config_mod.LOOPBACK and not access["password_hash"]:
        raise HTTPException(422, "set a password before binding off localhost")
    config_mod.Access.model_validate(access).save(st.templates_dir / "access.yaml")
    st.access = access
    # Only once the new hash is durable: revoking first and then failing to write
    # would sign everyone out while leaving the *old* password live.
    if body.password:
        await st.db.write(auth_mod.revoke_all)
    return await get_access(request)


class NotifyBody(BaseModel):
    enabled: bool | None = None
    #: Omitted leaves the stored secret alone — editing the event list must not
    #: require re-entering a token the UI is never allowed to show back. `""`
    #: is the explicit clear.
    url: str | None = None
    base_url: str | None = None
    events: list[str] | None = None


def _notify_view(notify_cfg: dict, last_test: dict | None = None) -> dict:
    """What `/notify` is allowed to say. The URL is not in it: a settings screen
    that renders the value back into the DOM puts the token in the browser, in
    screenshots, and in any future session recording."""
    return {
        "enabled": bool(notify_cfg["enabled"]),
        "url_set": bool(notify_cfg["url"]),
        "base_url": notify_cfg["base_url"],
        "events": notify_cfg["events"],
        "last_test": last_test,
    }


def _checked_url(value: str, field: str) -> str:
    scheme = urlsplit(value).scheme
    if scheme not in ("http", "https"):
        raise HTTPException(422, f"{field} must be an http or https URL")
    return value


@api_router.get("/notify")
async def get_notify(request: Request):
    # Reads straight off disk. `st.notifier` holds its own copy of this file in
    # memory and only refreshes it on `reload()` (called by `put_notify` below,
    # and at startup) -- so a hand edit to notify.yaml shows up here right away
    # but does not change what the running notifier actually does until the
    # next PUT or a restart. That is a knowing divergence from this module's
    # docstring claim that a hand edit and a UI edit are the same operation;
    # closing it means a file watcher, which this task does not build.
    st = request.app.state
    return _notify_view(
        config_mod.Notify.load(st.templates_dir / "notify.yaml").model_dump(), st.notifier.last_test
    )


@api_router.put("/notify")
async def put_notify(body: NotifyBody, request: Request):
    st = request.app.state
    path = st.templates_dir / "notify.yaml"
    cfg = config_mod.Notify.load(path).model_dump()
    if body.enabled is not None:
        cfg["enabled"] = body.enabled
    if body.url is not None:
        cfg["url"] = _checked_url(body.url, "url") if body.url else None
        # Clearing the URL is how an operator revokes a leaked token. It must
        # not just fail the invariant below -- it must disable, same as
        # "Clear URL"'s own hint claims. This is the only path back to a valid
        # state once the URL is gone, so force it rather than reject it.
        if not cfg["url"]:
            cfg["enabled"] = False
    if body.base_url is not None:
        cfg["base_url"] = _checked_url(body.base_url, "base_url") if body.base_url else None
    if body.events is not None:
        cfg["events"] = body.events
    # Enabled with nowhere to send is a setting that looks armed and is not.
    # Kept as a belt-and-braces check: the branch above already makes it
    # unreachable for the "clear the URL" path, but not for "enable with no
    # URL ever set" (body.url is None and cfg["url"] was already empty).
    if cfg["enabled"] and not cfg["url"]:
        raise HTTPException(422, "set a webhook URL before enabling notifications")
    config_mod.Notify.model_validate(cfg).save(path)
    st.notifier.reload()
    return _notify_view(cfg, st.notifier.last_test)


@api_router.post("/notify/test")
async def notify_test(request: Request):
    st = request.app.state
    try:
        return await st.notifier.send_test()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
