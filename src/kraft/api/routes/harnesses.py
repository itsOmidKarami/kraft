"""Harness profiles as their own resource (Kraft-archr, Ruling 206):
`harnesses.yaml`'s profiles with the library tasks and chains that select each,
the provider packages' read-only capability surfaces, and a save of one profile.

Read from disk on every request, the way a launch reads it
(`agent.harness_profile`): the daemon holds no copy of this file to go stale.
A save goes through the loader's own parse (`HarnessProfileTable.from_mapping`)
and the launch's own selection rule (`agent.select_profile`), so the screen
cannot accept a profile a launch would refuse."""

from __future__ import annotations

import yaml
from fastapi import HTTPException, Request

from kraft import config as config_mod
from kraft import harness as harness_mod
from kraft.adapters.agent import HarnessUnavailable, select_profile
from kraft.api import api_router, deps
from kraft.templates.environment import (
    HarnessProfile,
    HarnessProfileTable,
    TemplateEnvironmentError,
)
from kraft.templates.library import Namespace, TemplateLibrary, TemplateLibraryError
from kraft.templates.models import AgentTask

FILE = "harnesses.yaml"


def _selections(library: TemplateLibrary | None) -> list[tuple[str, str, AgentTask]]:
    """(chain, task path, task) for every agent task of every chain that resolves."""
    out = []
    for chain in library.chain_ids if library is not None else ():
        try:
            resolved = library.resolve_chain(chain)
        except TemplateLibraryError:
            continue  # not resolving already; `lint` says why
        for node in resolved.nodes:
            out += [(chain, t.path, t.task) for t in node.tasks() if isinstance(t.task, AgentTask)]
    return out


def _problems(
    selections, profiles: dict[str, HarnessProfile] | None, path, providers
) -> dict[tuple[str, str], str]:
    """Why each selecting task, keyed (chain, task path), could not launch on
    `profiles` (`None`: the file does not load, so none can): the launch's own
    checks, plus a task's own `model`/`effort` against its profile's provider."""
    problems = {}
    for chain, task_path, task in selections:
        where = f"chain {chain!r} task {task_path!r}: harness {task.harness!r}"
        if profiles is None:
            problems[chain, task_path] = f"{where}: {path} does not load"
            continue
        try:
            profile = select_profile(profiles, task.harness, path)
        except HarnessUnavailable as exc:
            problems[chain, task_path] = f"{where}: {exc}"
            continue
        for option in ("model", "effort"):
            value = getattr(task, option)
            if value is not None and not providers[profile.provider].value_ok(option, value):
                problems[chain, task_path] = (
                    f"{where}: provider {profile.provider!r} takes no {option} {value!r}"
                )
    return problems


def _task_harness(library: TemplateLibrary, name: str) -> str | None:
    """The profile library task `name` selects, its own or the one it extends."""
    seen = set()
    while name not in seen and (component := library.component(Namespace.TASKS, name)):
        seen.add(name)
        if "harness" in component.data:
            return component.data["harness"]
        name = component.data.get("extends")
    return None


def _view(st) -> dict:
    path = st.templates_dir / FILE
    providers = harness_mod.load(None).valid
    library = getattr(st, "library", None)
    try:
        profiles = HarnessProfileTable.from_yaml(path, harnesses=providers).profiles
        error = None
    except TemplateEnvironmentError as exc:
        profiles, error = {}, str(exc)
    tasks = library.component_names(Namespace.TASKS) if library is not None else ()
    used_by = {name: _task_harness(library, name) for name in tasks}
    chains: dict[str, set[str]] = {}
    for chain, _, task in _selections(library):
        chains.setdefault(task.harness, set()).add(chain)
    listed = [
        {
            "id": p.id,
            "provider": p.provider,
            "enabled": p.enabled,
            "executable": p.executable,
            "defaults": p.defaults,
            "used_by": [f"tasks.{t}" for t, h in used_by.items() if h == p.id],
            "chains": sorted(chains.get(p.id, ())),
        }
        for p in profiles.values()
    ]
    return {"file": str(path), "error": error, "profiles": listed}


@api_router.get("/harnesses/profiles")
async def list_harnesses(request: Request):
    return _view(request.app.state)


def _provider_view(h: harness_mod.Harness) -> dict:
    return {
        "id": h.id,
        "kind": h.kind,
        "command": list(h.command),
        "path": str(h.path),
        "capabilities": {
            name: {"values": list(c.values), "always": c.always, "channel": c.channel}
            for name, c in h.capabilities.items()
        },
    }


# Profiles and providers each have their own prefix (the Ruling 204 shape), so
# no profile or provider id can shadow a route, and none is reserved.


@api_router.get("/harnesses/providers")
async def list_providers():
    """Each provider package (`src/kraft/harnesses/*.yaml`, or its
    `$KRAFT_HOME` overlay) as the capabilities a profile may select from.
    Read-only: a provider is a fact about a CLI, not a setting."""
    loaded = harness_mod.load(None)
    return {
        "valid": {h.id: _provider_view(h) for h in loaded.valid.values()},
        "invalid": loaded.invalid,
    }


@api_router.get("/harnesses/providers/{hid}")
async def get_provider(hid: str):
    """One provider. One whose file did not load is a 404 that says why."""
    loaded = harness_mod.load(None)
    if hid not in loaded.valid:
        raise HTTPException(404, loaded.invalid.get(hid, f"no harness provider {hid!r}"))
    return _provider_view(loaded.valid[hid])


@api_router.get("/harnesses/profiles/{pid}")
async def get_harness(pid: str, request: Request):
    found = next((p for p in _view(request.app.state)["profiles"] if p["id"] == pid), None)
    if found is None:
        raise HTTPException(404, f"no harness profile {pid!r}")
    return found


@api_router.put("/harnesses/profiles/{pid}")
async def put_harness(pid: str, body: dict, request: Request):
    """Save one profile, added or replaced, into `harnesses.yaml`. Refused,
    writing nothing, when the whole file would not load, or when an agent task
    of a chain that resolves would stop launching because of it. A task that
    could not launch before the edit is not the edit's to fix."""
    st = request.app.state
    library = deps.library_or_503(st)
    path = st.templates_dir / FILE
    providers = harness_mod.load(None).valid
    try:
        data = (yaml.safe_load(path.read_text()) if path.is_file() else None) or {}
        current = HarnessProfileTable.from_mapping(data, path, harnesses=providers).profiles
    except yaml.YAMLError as exc:
        raise HTTPException(409, f"{path} does not parse; fix it by hand: {exc}") from exc
    except TemplateEnvironmentError:
        current = None
    if not isinstance(data, dict) or not isinstance(data.get("harnesses") or {}, dict):
        raise HTTPException(409, f"{path} is not a mapping of profiles; fix it by hand")
    candidate = {**data, "harnesses": {**(data.get("harnesses") or {}), pid: body}}
    try:
        profiles = HarnessProfileTable.from_mapping(candidate, path, harnesses=providers).profiles
    except TemplateEnvironmentError as exc:
        raise HTTPException(422, str(exc)) from exc
    selections = _selections(library)
    before = _problems(selections, current, path, providers)
    after = _problems(selections, profiles, path, providers)
    if broken := [message for key, message in sorted(after.items()) if key not in before]:
        raise HTTPException(422, broken[0])
    config_mod.write_yaml(path, candidate)
    return next(p for p in _view(st)["profiles"] if p["id"] == pid)
