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
from kraft.api import api_router, config_check, deps
from kraft.templates.environment import (
    HarnessProfileTable,
    TemplateEnvironmentError,
)
from kraft.templates.library import Namespace, TemplateLibrary

FILE = "harnesses.yaml"


def _task_harness(library: TemplateLibrary, name: str) -> str | None:
    """The profile library task `name` selects, its own or the one it extends."""
    seen = set()
    while name not in seen and (component := library.component(Namespace.TASKS, name)):
        seen.add(name)
        if "harness" in component.data:
            return component.data["harness"]
        name = component.data.get("extends")
    return None


def _task_profile(library: TemplateLibrary, name: str) -> str | None:
    """The agent profile library task `name` selects: the nearest layer that
    picks a route decides, as `extends` resolves it (`displaced_route`)."""
    seen = set()
    while name not in seen and (component := library.component(Namespace.TASKS, name)):
        seen.add(name)
        if "profile" in component.data:
            return component.data["profile"]
        if "model" in component.data or "effort" in component.data:
            return None
        name = component.data.get("extends")
    return None


def _agent_profiles_view(table, library, selections, path, providers) -> list[dict]:
    """`profiles:`, each with the tasks and chains selecting it and why any
    pairing of it would not launch. Read-only: edited in the file."""
    tasks = library.component_names(Namespace.TASKS) if library is not None else ()
    used_by = {name: _task_profile(library, name) for name in tasks}
    problems = config_check.launch_problems(selections, table, path, providers)
    return [
        {
            "id": p.id,
            "effort": p.effort,
            "model": p.model,
            "used_by": [f"tasks.{t}" for t, pid in used_by.items() if pid == p.id],
            "chains": sorted({c for c, _, t in selections if t.profile == p.id}),
            "problems": [
                problems[c, tp]
                for c, tp, t in selections
                if t.profile == p.id and (c, tp) in problems
            ],
            # Its default fallback list, each entry with the pairing problems
            # of the tasks that take it (Kraft-0a3h8).
            "fallback": [
                {
                    **entry.model_dump(exclude_none=True),
                    "problems": [
                        problems[c, f"{tp} fallback[{n}]"]
                        for c, tp, t in selections
                        if t.profile == p.id
                        and t.fallback is None
                        and (c, f"{tp} fallback[{n}]") in problems
                    ],
                }
                for n, entry in enumerate(p.fallback)
            ],
        }
        for p in table.agent_profiles.values()
    ]


def _view(st) -> dict:
    path = st.templates_dir / FILE
    providers = harness_mod.load(None).valid
    library = getattr(st, "library", None)
    try:
        table = HarnessProfileTable.from_yaml(path, harnesses=providers)
        profiles, error = table.profiles, None
    except TemplateEnvironmentError as exc:
        table, profiles, error = HarnessProfileTable(profiles={}), {}, str(exc)
    tasks = library.component_names(Namespace.TASKS) if library is not None else ()
    used_by = {name: _task_harness(library, name) for name in tasks}
    chains: dict[str, set[str]] = {}
    for chain, _, task in config_check.selections(library):
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
    return {
        "file": str(path),
        "error": error,
        "profiles": listed,
        "agent_profiles": _agent_profiles_view(
            table, library, config_check.selections(library), path, providers
        ),
    }


@api_router.get("/harnesses/profiles")
async def list_harnesses(request: Request):
    return _view(request.app.state)


def _provider_view(h: harness_mod.Harness) -> dict:
    return {
        "id": h.id,
        "kind": h.kind,
        "command": list(h.command),
        "path": str(h.path),
        # From $KRAFT_HOME/templates/harnesses/ rather than the package.
        "override": h.path is not None and h.path.parent != harness_mod.BUNDLED,
        "capabilities": {
            name: {
                "cli": list(c.argv),
                "values": list(c.values),
                "always": c.always,
                "channel": c.channel,
                "source": c.source,
                "reader": c.reader,
                "via": c.via,
                "under_allowlist": c.under_allowlist,
            }
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
    except yaml.YAMLError as exc:
        raise HTTPException(409, f"{path} does not parse; fix it by hand: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("harnesses") or {}, dict):
        raise HTTPException(409, f"{path} is not a mapping of profiles; fix it by hand")
    candidate = {**data, "harnesses": {**(data.get("harnesses") or {}), pid: body}}
    if why := config_check.harness_breakage(library, data, candidate, path, providers):
        raise HTTPException(422, why)
    config_mod.write_yaml(path, candidate)
    return next(p for p in _view(st)["profiles"] if p["id"] == pid)
