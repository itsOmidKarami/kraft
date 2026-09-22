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
from kraft.executor import fallback
from kraft.templates.environment import (
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
    selections, table: HarnessProfileTable | None, path, providers
) -> dict[tuple[str, str], str]:
    """Why each selecting task, keyed (chain, task path), could not launch on
    `table` (`None`: the file does not load, so none can): the launch's own
    checks, including its agent profile's pairing (Kraft-ps1ao), plus a task's
    own `model`/`effort` against its profile's provider."""
    problems = {}
    for chain, task_path, task in selections:
        where = f"chain {chain!r} task {task_path!r}: harness {task.harness!r}"
        if table is None:
            problems[chain, task_path] = f"{where}: {path} does not load"
            continue
        try:
            profile = select_profile(table.profiles, task.harness, path)
        except HarnessUnavailable as exc:
            problems[chain, task_path] = f"{where}: {exc}"
            continue
        if task.profile is not None:
            if why := table.pairing_problem(task.profile, profile, providers):
                problems[chain, task_path] = f"chain {chain!r} task {task_path!r}: {why}"
            continue
        for option in ("model", "effort"):
            value = getattr(task, option)
            if value is not None and not providers[profile.provider].value_ok(option, value):
                problems[chain, task_path] = (
                    f"{where}: provider {profile.provider!r} takes no {option} {value!r}"
                )
        # Each `fallback:` entry must pair with its harness too. A disabled one
        # is not a pairing problem: the launch skips it (`executor.fallback`).
        for n, cand in enumerate(fallback.candidates(task)[1:]):
            at = f"chain {chain!r} task {task_path!r}: fallback entry {n} (the task's list)"
            entry = profiles.get(cand.harness)
            if entry is None:
                problems[chain, f"{task_path} fallback[{n}]"] = (
                    f"{at}: harness {cand.harness!r} is not in {path}"
                )
                continue
            for option in ("model", "effort"):
                value = getattr(cand, option)
                if value is not None and not providers[entry.provider].value_ok(option, value):
                    problems[chain, f"{task_path} fallback[{n}]"] = (
                        f"{at}: provider {entry.provider!r} (harness {cand.harness!r}) "
                        f"takes no {option} {value!r}"
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
    problems = _problems(selections, table, path, providers)
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
    return {
        "file": str(path),
        "error": error,
        "profiles": listed,
        "agent_profiles": _agent_profiles_view(
            table, library, _selections(library), path, providers
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
        current = HarnessProfileTable.from_mapping(data, path, harnesses=providers)
    except yaml.YAMLError as exc:
        raise HTTPException(409, f"{path} does not parse; fix it by hand: {exc}") from exc
    except TemplateEnvironmentError:
        current = None
    if not isinstance(data, dict) or not isinstance(data.get("harnesses") or {}, dict):
        raise HTTPException(409, f"{path} is not a mapping of profiles; fix it by hand")
    candidate = {**data, "harnesses": {**(data.get("harnesses") or {}), pid: body}}
    try:
        table = HarnessProfileTable.from_mapping(candidate, path, harnesses=providers)
    except TemplateEnvironmentError as exc:
        raise HTTPException(422, str(exc)) from exc
    selections = _selections(library)
    before = _problems(selections, current, path, providers)
    after = _problems(selections, table, path, providers)
    if broken := [message for key, message in sorted(after.items()) if key not in before]:
        raise HTTPException(422, broken[0])
    config_mod.write_yaml(path, candidate)
    return next(p for p in _view(st)["profiles"] if p["id"] == pid)
