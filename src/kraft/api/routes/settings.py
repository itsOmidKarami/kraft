from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from kraft import auth as auth_mod
from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft import policy as policy_mod
from kraft import store
from kraft.adapters import beads
from kraft.api import api_router, deps, perimeter
from kraft.templates import (
    CONFIG_FILES,
    RegistryError,
    load_registry,
    load_templates,
)
from kraft.worker import steering as steering_mod

# ══ settings (design 5a–5e) ═════════════════════════════════════════════════
#
# Every write lands in the same versioned YAML an operator edits by hand
# (`02` §4.7 revised), and every write re-reads and re-validates the whole set
# so a bad save is refused rather than discovered at the next restart.


class TemplateBody(BaseModel):
    nodes: list[dict]


def _template_path(st, tid: str) -> Path:
    if not tid.isidentifier() and not tid.replace("-", "_").isidentifier():
        raise HTTPException(400, f"invalid template id {tid!r}")
    return st.templates_dir / f"{tid}.yaml"


@api_router.get("/templates")
async def list_templates(request: Request):
    st = request.app.state
    return [
        {
            "id": tid,
            "nodes": [dict(n.items()) for n in t.nodes],
            "gates": sum(1 for n in t.nodes if n.get("gate_after")),
        }
        for tid, t in sorted(st.templates.valid.items())
    ]


@api_router.get("/templates/{tid}")
async def get_template(tid: str, request: Request):
    st = request.app.state
    template = st.templates.valid.get(tid)
    if template is None:
        raise HTTPException(404, f"unknown template {tid!r}")
    return {"id": tid, "nodes": [n.model_dump(exclude_unset=True) for n in template.authored]}


def _validate_template(st, tid: str, nodes: list[dict]) -> dict:
    """Write the candidate to a scratch dir and run the real loader over it.

    Re-using `load_templates` rather than re-deriving the rules is the point:
    there is one definition of a valid chain, and this is it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        (scratch / "registry.yaml").write_text((st.templates_dir / "registry.yaml").read_text())
        (scratch / f"{tid}.yaml").write_text(yaml.safe_dump({"id": tid, "nodes": nodes}))
        result = load_templates(scratch, st.registry)
    return {
        "id": tid,
        "valid": tid in result.valid,
        "error": result.invalid.get(tid),
        # Per node and per task, not per repo. `by_repo.resolvable` was
        # `all(h in st.registry.hooks for h in hooks)` -- one bit of information
        # repeated once per connected repo, computed without consulting the repo
        # at all, despite the comment that used to sit above it. "unresolvable"
        # named a repo; the human editing a chain needs the node. Replaced, not
        # repaired: two answers to one question, one of them wrong, is worse
        # than one.
        "unresolved": [
            {"node": n.get("id"), "task": task}
            for n in nodes
            for task in (n.get("tasks") or [])
            if task not in st.registry.hooks
        ],
    }


@api_router.post("/templates/{tid}/validate")
async def validate_template(tid: str, body: TemplateBody, request: Request):
    return _validate_template(request.app.state, tid, body.nodes)


@api_router.put("/templates/{tid}")
async def put_template(tid: str, body: TemplateBody, request: Request):
    st = request.app.state
    path = _template_path(st, tid)
    report = _validate_template(st, tid, body.nodes)
    if not report["valid"]:
        raise HTTPException(422, report["error"] or f"template {tid!r} is not valid")
    config_mod.write_yaml(path, {"id": tid, "nodes": body.nodes})
    deps._reload_templates(st)
    return {"id": tid, "nodes": body.nodes}


class ParseBody(BaseModel):
    text: str


@api_router.post("/templates/parse")
async def parse_template_yaml(body: ParseBody):
    """The YAML pane's other direction (UI v2 · 09): typed text back into the
    node list the graph/form render from. Parsing only — `/templates/{tid}/validate`
    is the separate, existing check against the registry. No YAML library ships
    in the frontend; this is the server doing the one direction that's genuinely
    hard to hand-roll (arbitrary operator-typed YAML), reusing pyyaml already
    imported here.
    """
    try:
        data = yaml.safe_load(body.text)
    except yaml.YAMLError as exc:
        # str(exc) is safe here: pyyaml's message is a parse-position
        # description of the operator's own submitted text, not a traceback.
        return {"nodes": None, "error": str(exc)}
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return {"nodes": None, "error": "expected a mapping with a 'nodes' list"}
    if not all(isinstance(n, dict) for n in data["nodes"]):
        return {"nodes": None, "error": "every node must be a mapping"}
    return {"nodes": data["nodes"], "error": None}


class RegistryBody(BaseModel):
    hooks: dict


@api_router.get("/registry")
async def get_registry(request: Request):
    # `raw`, not `hooks`: `hooks` has load-time defaults (`harness: claude`)
    # normalised into every binding for dispatch/doctor to read without
    # re-deriving them, which would show up here as keys nobody wrote and
    # break the GET/PUT round trip.
    return {"hooks": request.app.state.registry.raw}


@api_router.put("/registry")
async def put_registry(body: RegistryBody, request: Request):
    """Save bindings, then re-run the chain validator over every template.

    A binding change affects intake only — a live work item keeps the
    `chain_definition` it materialized — but a template that stops resolving
    has to surface immediately, not at the next intake.
    """
    st = request.app.state
    current = yaml.safe_load((st.templates_dir / "registry.yaml").read_text()) or {}
    doc = {"hooks": body.hooks}
    if "defaults" in current:
        doc["defaults"] = current["defaults"]
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "registry.yaml"
        candidate.write_text(yaml.safe_dump(doc))
        try:
            registry = load_registry(
                candidate, steering_dir=st.templates_dir / "steering", skills_dir=st.skills_dir
            )
        except RegistryError as exc:
            raise HTTPException(422, str(exc)) from exc
        for src in st.templates_dir.glob("*.yaml"):
            if src.name not in CONFIG_FILES:
                (Path(tmp) / src.name).write_text(src.read_text())
        checked = load_templates(Path(tmp), registry)
    config_mod.write_yaml(st.templates_dir / "registry.yaml", doc)
    deps._reload_templates(st)
    return {"hooks": body.hooks, "invalid_templates": checked.invalid}


@api_router.get("/registry/{hook}/runs")
async def hook_runs(hook: str, request: Request):
    st = request.app.state
    return {"runs": st.db.read(lambda c: store.recent_sessions_for_hook(c, hook))}


@api_router.post("/templates/reload")
async def reload_templates_endpoint(request: Request):
    st = request.app.state
    try:
        deps._reload_templates(st)
    except RegistryError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"valid": sorted(st.templates.valid), "invalid_templates": st.templates.invalid}


class PolicyBody(BaseModel):
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
    keeps the cap it snapshotted at first fire (`02` §2.C)."""
    st = request.app.state
    data = {"loops": body.loops, "default": body.default, "max_concurrent": body.max_concurrent}
    if body.findings is not None:
        data["findings"] = body.findings
    if body.budget is not None:
        data["budget"] = body.budget
    if body.rate_limit_retries is not None:
        data["rate_limit_retries"] = body.rate_limit_retries
    if body.triggers is not None:
        data["triggers"] = body.triggers
    if body.archive is not None:
        data["archive"] = body.archive
    if body.auto_escalate_stuck is not None:
        data["auto_escalate_stuck"] = body.auto_escalate_stuck
    if body.auto_escalate_stuck_cap is not None:
        data["auto_escalate_stuck_cap"] = body.auto_escalate_stuck_cap
    if body.auto_escalate_delay_s is not None:
        data["auto_escalate_delay_s"] = body.auto_escalate_delay_s
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "policy.yaml"
        candidate.write_text(yaml.safe_dump(data))
        try:
            policy_obj = policy_mod.load_policy(candidate)
        except policy_mod.PolicyError as exc:
            raise HTTPException(422, str(exc)) from exc
    config_mod.write_yaml(st.templates_dir / "policy.yaml", data)
    st.policy = policy_obj
    st.invalid_policy = []
    return data


@api_router.get("/theme")
async def get_theme(request: Request):
    st = request.app.state
    return config_mod.load_theme(st.templates_dir / "theme.yaml").model_dump()


@api_router.put("/theme")
async def put_theme(body: config_mod.Theme, request: Request):
    st = request.app.state
    data = body.model_dump()
    config_mod.write_yaml(st.templates_dir / "theme.yaml", data)
    return data


class SteeringBody(BaseModel):
    body: str


def _steering_dir(st) -> Path:
    return st.templates_dir / "steering"


def _check_steering_change(st, name: str, body: str | None) -> None:
    """Would the configs still load with this change applied? Raise if not.

    Names resolve at config-load time, not at write time, so the edited file is
    only half the question: `registry.yaml` and `repos.yaml` name it, and a body
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
            load_registry(
                st.templates_dir / "registry.yaml", steering_dir=scratch, skills_dir=st.skills_dir
            )
            config_mod.load_repos(deps.repos_path(st), steering_dir=scratch)
        except (RegistryError, config_mod.ConfigError) as exc:
            raise HTTPException(422, str(exc)) from exc


@api_router.get("/steering")
async def list_steering(request: Request):
    """Names and sizes, not bodies: the list is a picker, and the assembled
    budget is the number an operator is actually rationing."""
    steering_dir = _steering_dir(request.app.state)
    if not steering_dir.is_dir():
        return {"files": [], "max_bytes": steering_mod.MAX_BYTES}
    files = []
    for path in sorted(steering_dir.glob("*.md")):
        try:
            files.append({"name": path.stem, "bytes": len(path.read_text().encode())})
        except OSError, ValueError:
            # Unreadable or not UTF-8: it exists and it is broken, which is
            # more useful on the screen than a file that silently is not there.
            files.append({"name": path.stem, "bytes": None})
    return {"files": files, "max_bytes": steering_mod.MAX_BYTES}


@api_router.get("/steering/{name}")
async def get_steering(name: str, request: Request):
    st = request.app.state
    try:
        path = steering_mod.path_for(_steering_dir(st), name, where="steering")
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
        path = steering_mod.path_for(steering_dir, name, where="steering")
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
        path = steering_mod.path_for(_steering_dir(st), name, where="steering")
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
        data = config_mod.load_intake(st.templates_dir / "intake.yaml").model_dump()
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
            ready = await beads.ready(cwd=r["path"])
            count = len(ready)
        except Exception:  # noqa: BLE001 -- a settings-page read must not 500 on a bad repo
            count = None
        repo_pickups[r["path"]] = {"items": count, "last_picked_up": last_picked_up.get(r["path"])}
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
    config_mod.write_yaml(st.templates_dir / "intake.yaml", data)
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
    config_mod.save_access(st.templates_dir / "access.yaml", access)
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
        config_mod.load_notify(st.templates_dir / "notify.yaml").model_dump(), st.notifier.last_test
    )


@api_router.put("/notify")
async def put_notify(body: NotifyBody, request: Request):
    st = request.app.state
    path = st.templates_dir / "notify.yaml"
    cfg = config_mod.load_notify(path).model_dump()
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
    config_mod.save_notify(path, cfg)
    st.notifier.reload()
    return _notify_view(cfg, st.notifier.last_test)


@api_router.post("/notify/test")
async def notify_test(request: Request):
    st = request.app.state
    try:
        return await st.notifier.send_test()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
