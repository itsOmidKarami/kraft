"""The `harnesses` draft area (W13 D): `harnesses.yaml` and the access lists of `policy.yaml`.

The page's per-harness access state is one of `available`, `override` or `never`;
`access` and `set_access` map it onto `policy.yaml`'s `allowed_harnesses` lists
(`defaults` and `maxima`) and back.
"""

from __future__ import annotations

import copy
import shutil
from collections.abc import Mapping

import yaml
from pydantic import ValidationError

from kraft import harness as harness_mod
from kraft import storage
from kraft.api import config_check
from kraft.drafts import authored, config, store
from kraft.drafts import resolve as resolve_mod
from kraft.drafts.ops import OpError, _check_id
from kraft.executor import fallback as fallback_mod
from kraft.policy import (
    DEFAULT_ESCALATION_HARNESS,
    FOLLOW_ITEM,
    InstancePolicy,
    InstancePolicyInput,
)
from kraft.templates.environment import HarnessProfileTable, TemplateEnvironmentError
from kraft.templates.library import TemplateIssue
from kraft.templates.models import PATH_SEPARATOR

NAME = "harnesses"
FILES = ("harnesses.yaml", "policy.yaml")
STATES = ("available", "override", "never")
_PROFILE_KEYS = ("providers", "model", "effort", "fallback")
_HARNESS_KEYS = ("enabled", "executable", "defaults")


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


# ── the access mapping ──


def _section(data: object, name: str) -> dict:
    found = data.get(name) if isinstance(data, Mapping) else None
    return found if isinstance(found, dict) else {}


def _list(data: object, section: str) -> list | None:
    found = _section(data, section).get("allowed_harnesses")
    return found if isinstance(found, list) else None


def access(policy: object, universe: list[str]) -> dict[str, str]:
    """Each harness's state in `policy`'s two lists. An absent list is no bound
    (every harness); an absent `defaults` list means the maxima's."""
    defaults, maxima = _list(policy, "defaults"), _list(policy, "maxima")
    out = {}
    for h in universe:
        in_maxima = maxima is None or h in maxima
        in_defaults = h in defaults if defaults is not None else in_maxima
        out[h] = "available" if in_defaults else "override" if in_maxima else "never"
    return out


def _universe(harnesses_yaml: object) -> list[str]:
    return list(_section(harnesses_yaml, "harnesses"))


def _sub(policy: dict, key: str) -> dict:
    body = policy.get(key)
    if not isinstance(body, dict):
        body = policy[key] = {}
    return body


def _tidy(policy: dict, key: str) -> None:
    if not policy.get(key):
        policy.pop(key, None)


def _rewrite(existing: list, universe: list[str], keep: set[str]) -> list[str]:
    """`existing` with the harnesses outside `keep` taken out, those of `keep`
    it lacked appended in `universe` order; an entry that names no harness stays."""
    out = [h for h in existing if h not in universe or h in keep]
    return out + [h for h in universe if h in keep and h not in out]


def set_access(d, harness: str, state: str) -> None:
    universe = _universe(d.read("harnesses.yaml"))
    if harness not in universe:
        raise OpError(f"no harness {harness!r} in harnesses.yaml; known: {universe}")
    if state not in STATES:
        raise OpError(f"state {state!r} is not one of {list(STATES)}")
    policy = d.file("policy.yaml")
    states = {**access(policy, universe), harness: state}
    published = authored.parse(
        resolve_mod.published(d.st.templates_dir, ["policy.yaml"])["policy.yaml"]
    )
    keep = {
        "defaults": {h for h in universe if states[h] == "available"},
        "maxima": {h for h in universe if states[h] != "never"},
    }
    for section, kept in keep.items():
        body = _sub(policy, section)
        existing = body.get("allowed_harnesses")
        written = _rewrite(existing if isinstance(existing, list) else [], universe, kept)
        # Nothing bound, and no list there before: the key stays out.
        unbound = kept == set(universe) and set(written) <= set(universe)
        if unbound and _list(published, section) is None:
            body.pop("allowed_harnesses", None)
        else:
            body["allowed_harnesses"] = written
        _tidy(policy, section)


def _names(value: object, what: str, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise OpError(f"{what} is a list of names, or null")


def set_allowed_tools(d, tools: list | None) -> None:
    _names(tools, "tools")
    policy = d.file("policy.yaml")
    maxima = _sub(policy, "maxima")
    if tools is None:
        maxima.pop("allowed_tools", None)
    else:
        maxima["allowed_tools"] = list(tools)
    _tidy(policy, "maxima")


def set_escalation(d, harness: str | None, grants: list | None) -> None:
    if harness is not None and not isinstance(harness, str):
        raise OpError("harness is a harness id, 'item', or null")
    _names(grants, "grants")
    policy = d.file("policy.yaml")
    defaults = _sub(policy, "defaults")
    for key, value in (("escalation_harness", harness), ("escalation_grants", grants)):
        if value is None:
            defaults.pop(key, None)
        else:
            defaults[key] = value
    _tidy(policy, "defaults")


# ── agent profiles ──


def _profiles(d) -> dict:
    found = d.file("harnesses.yaml").setdefault("profiles", {})
    if not isinstance(found, dict):
        raise OpError("harnesses.yaml: profiles must be a mapping")
    return found


def _profile(d, name: str) -> dict:
    found = _profiles(d).get(name)
    if not isinstance(found, dict):
        raise OpError(f"no profile {name!r}; known: {list(_profiles(d))}")
    return found


def _users(st, name: str) -> list[dict]:
    """The agent tasks of the published library that select profile `name`,
    directly or in a `fallback:` list of their own."""
    return [
        {"chain": chain, "path": path}
        for chain, path, task in config_check.selections(getattr(st, "library", None))
        if task.profile == name or any(e.profile == name for e in task.fallback or ())
    ]


def _fallback_users(profiles: dict, name: str) -> list[str]:
    """The profiles whose own `fallback:` list names `name`."""
    return [
        p
        for p, body in profiles.items()
        if isinstance(body, dict)
        and any(
            isinstance(e, dict) and e.get("profile") == name for e in body.get("fallback") or ()
        )
    ]


def add_profile(d, name: str, copy_from: str | None = None) -> None:
    profiles = _profiles(d)
    _check_id(name, profiles)
    profiles[name] = (
        {"providers": {}} if copy_from is None else copy.deepcopy(_profile(d, copy_from))
    )


def rename_profile(d, name: str, to: str) -> dict:
    """Retarget the `fallback:` entries of `harnesses.yaml` that name it. A task
    of the library or a chain that names it is left dangling, reported as a launch
    problem and listed in `broken` (Decisions §9): this area does not own them."""
    profiles = _profiles(d)
    _profile(d, name)
    _check_id(to, profiles)
    renamed = {(to if k == name else k): v for k, v in profiles.items()}
    profiles.clear()
    profiles.update(renamed)
    for body in profiles.values():
        for entry in (body.get("fallback") or ()) if isinstance(body, dict) else ():
            if isinstance(entry, dict) and entry.get("profile") == name:
                entry["profile"] = to
    return {"broken": _users(d.st, name)}


def remove_profile(d, name: str) -> None:
    profiles = _profiles(d)
    _profile(d, name)
    used = [f"{u['chain']} {u['path']}" for u in _users(d.st, name)]
    used += [f"profile {p!r}'s fallback" for p in _fallback_users(profiles, name) if p != name]
    if used:
        raise OpError(f"profile {name!r} is used by {', '.join(used)}")
    del profiles[name]


def _to_providers(profile: dict) -> None:
    if "providers" in profile:
        return
    models = profile.pop("model", None) or {}
    effort = profile.pop("effort", None)
    profile["providers"] = {
        p: {"model": m, **({"effort": effort} if effort is not None else {})}
        for p, m in models.items()
    }


def _merge(target: dict, patch: object, what: str, entry: bool = False) -> None:
    if not isinstance(patch, dict):
        raise OpError(f"{what} is a mapping by provider, null removing one")
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif entry and not isinstance(value, dict):
            raise OpError(f"{what}.{key} is {{model, effort?}}")
        else:
            target[key] = dict(value) if entry else value


def set_profile(d, name: str, patch: dict) -> None:
    """Edit one profile in either of its two shapes. `providers` merges per provider
    (an entry replaces, null removes) and turns an older profile into that shape;
    `model` and `effort` edit an older one; `fallback` replaces its list."""
    profile = _profile(d, name)
    if not isinstance(patch, dict) or not patch:
        raise OpError("patch is a mapping of the keys to change")
    if unknown := sorted(set(patch) - set(_PROFILE_KEYS)):
        raise OpError(f"patch sets {unknown}; a profile has {list(_PROFILE_KEYS)}")
    if "providers" in patch and ("model" in patch or "effort" in patch):
        raise OpError("patch sets both 'providers' and 'model'/'effort'; use one shape")
    if "providers" in patch:
        _to_providers(profile)
        _merge(profile["providers"], patch["providers"], "providers", entry=True)
    elif "model" in patch or "effort" in patch:
        if "providers" in profile:
            raise OpError(f"profile {name!r} is written with 'providers'; patch those")
        if "model" in patch:
            _merge(profile.setdefault("model", {}), patch["model"], "model")
        if "effort" in patch:
            if patch["effort"] is None:
                profile.pop("effort", None)
            else:
                profile["effort"] = patch["effort"]
    if "fallback" in patch:
        if patch["fallback"] is None:
            profile.pop("fallback", None)
        elif isinstance(patch["fallback"], list):
            profile["fallback"] = patch["fallback"]
        else:
            raise OpError("fallback is a list of entries, or null")


def set_harness(d, id: str, patch: dict) -> None:
    """Edit one harness's own fields: `enabled`, `executable` (null clears it) and
    `defaults`, which merges per option (null removes one). Which options a
    provider accepts is checked by the resolve, like any other file edit."""
    harness = _section(d.file("harnesses.yaml"), "harnesses").get(id)
    if not isinstance(harness, dict):
        known = _universe(d.read("harnesses.yaml"))
        raise OpError(f"no harness {id!r} in harnesses.yaml; known: {known}")
    if not isinstance(patch, dict) or not patch:
        raise OpError("patch is a mapping of the keys to change")
    if unknown := sorted(set(patch) - set(_HARNESS_KEYS)):
        raise OpError(f"patch sets {unknown}; a harness has {list(_HARNESS_KEYS)}")
    if "enabled" in patch:
        if not isinstance(patch["enabled"], bool):
            raise OpError("enabled is true or false")
        harness["enabled"] = patch["enabled"]
    if "executable" in patch:
        value = patch["executable"]
        if value is None:
            harness.pop("executable", None)
        elif isinstance(value, str) and value.strip():
            harness["executable"] = value.strip()
        else:
            raise OpError("executable is a command name or path, or null")
    if "defaults" in patch:
        found = patch["defaults"]
        if not isinstance(found, dict) or not all(
            v is None or isinstance(v, str) for v in found.values()
        ):
            raise OpError("defaults is a mapping of option to text, null removing one")
        defaults = harness.get("defaults")
        defaults = dict(defaults) if isinstance(defaults, dict) else {}
        for option, value in found.items():
            if value is None or not value.strip():
                defaults.pop(option, None)
            else:
                defaults[option] = value
        if defaults:
            harness["defaults"] = defaults
        else:
            harness.pop("defaults", None)


OPS: dict = {
    "set_harness": set_harness,
    "set_access": set_access,
    "set_allowed_tools": set_allowed_tools,
    "set_escalation": set_escalation,
    "add_profile": add_profile,
    "rename_profile": rename_profile,
    "remove_profile": remove_profile,
    "set_profile": set_profile,
}


# ── resolve ──


def _table_issues(path, text, ctx) -> list[TemplateIssue]:
    """`harnesses.yaml` as the profile table loads it. `config_check`'s own check
    also filters out a task that was already broken (`harness_breakage`); a draft
    shows every problem, so launch problems come from the draft table whole."""
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        return [TemplateIssue(path, None, config_check.NOT_A_MAPPING)]
    try:
        HarnessProfileTable.from_mapping(data, path, harnesses=ctx.providers, plugins=ctx.plugins)
    except TemplateEnvironmentError as exc:
        return [TemplateIssue(path, None, str(exc))]
    return []


def _providers_of(body: object) -> dict[str, dict]:
    """A profile's routes per provider, in either file shape."""
    if not isinstance(body, dict):
        return {}
    if isinstance(body.get("providers"), dict):
        return {p: dict(e) for p, e in body["providers"].items() if isinstance(e, dict)}
    models = body.get("model") if isinstance(body.get("model"), dict) else {}
    return {
        p: {"model": m, **({"effort": body["effort"]} if body.get("effort") else {})}
        for p, m in models.items()
    }


def _problem(st, chain: str, path: str | None, field: str | None, message: str, **more) -> dict:
    try:
        file = str(st.library.chain_file(chain).relative_to(st.templates_dir))
    except (AttributeError, ValueError):
        file = f"chains/{chain}.yaml"
    return {
        "path": path,
        "field": field,
        "message": message,
        "file": file,
        "line": 1,
        "col": 1,
        "chain": chain,
        **more,
    }


def _launch_problems(st, table, providers, path, chosen) -> list[dict]:
    if table is None:
        return []  # the file's own problem is already listed; not every task's too
    return [
        _problem(
            st,
            p.chain,
            p.task.partition(" fallback[")[0],
            p.field,
            p.message,
            profile=p.profile,
            provider=p.provider,
        )
        for p in config_check.launch_problem_details(chosen, table, path, providers).values()
    ]


def _lint_problems(st, policy: object, chosen=()) -> list[dict]:
    """The library lint against the draft's own instance policy: a task that
    selects a harness the draft made `never` is where this surfaces. Its message
    opens with the task's path (`<path>: harness ...`), which is the problem's
    `path` when it names a task of that chain; otherwise `path` stays null."""
    try:
        instance = InstancePolicy.from_input(
            InstancePolicyInput.model_validate(
                {"defaults": _section(policy, "defaults"), "maxima": _section(policy, "maxima")}
            )
        )
    except ValidationError:
        return []  # the policy file's own problem, which `check` reports
    library = getattr(st, "library", None)
    issues = library.lint(instance) if library is not None else []
    paths = {(chain, path) for chain, path, _ in chosen}

    def path_of(chain: str, message: str) -> str | None:
        head = message.partition(": ")[0]
        return head if (chain, head) in paths else None

    return [
        _problem(st, i.chain, path_of(i.chain, i.message), None, i.message)
        for i in issues
        if i.chain is not None
    ]


def _fallback_problems(st, chosen, table, states, linted: list[dict]) -> list[dict]:
    """A fallback entry on a harness the draft made `never`: the entry's own path.
    The lint names a chain's first such task only; a task it already named is skipped."""
    out = []
    for chain, path, task in chosen:
        entries, _ = fallback_mod.fallback_list(task, table)
        for n, entry in enumerate(entries):
            harness = fallback_mod.apply(task, entry).harness
            if states.get(harness) != "never":
                continue
            if any(p["chain"] == chain and p["message"].startswith(f"{path}:") for p in linted):
                continue
            field = PATH_SEPARATOR.join(("fallback", str(n), "harness"))
            message = f"{path}: fallback harness {harness!r} is not in its allowed_harnesses"
            out.append(_problem(st, chain, path, field, message))
    return out


def _harness_view(id: str, body: object, state: str, providers, tasks: list[dict]) -> dict:
    body = body if isinstance(body, dict) else {}
    provider = providers.get(body.get("provider"))
    exe = body.get("executable") or (provider.command[0] if provider else None)
    return {
        "id": id,
        "state": state,
        "executable_found": bool(exe and shutil.which(exe)),
        "provider": body.get("provider"),
        "enabled": body.get("enabled", True),
        "executable": body.get("executable"),
        "defaults": body.get("defaults") if isinstance(body.get("defaults"), dict) else {},
        "tasks": tasks,
    }


def _tasks_by_harness(chosen, table) -> dict[str, list[dict]]:
    """The agent tasks each harness runs: the ones that select it, and the ones
    whose `fallback:` list lands on it (`fallback: true`). Without a loadable
    table a fallback list cannot be read, so only the selections are listed."""
    out: dict[str, list[dict]] = {}
    for chain, path, task in chosen:
        out.setdefault(task.harness, []).append(
            {"chain": chain, "path": path, "profile": task.profile, "fallback": False}
        )
        if table is None:
            continue
        entries, _ = fallback_mod.fallback_list(task, table)
        for entry in entries:
            landed = fallback_mod.apply(task, entry)
            out.setdefault(landed.harness, []).append(
                {"chain": chain, "path": path, "profile": landed.profile, "fallback": True}
            )
    return out


def _escalation_problem(states: dict[str, str], harness: object) -> list[dict]:
    """Escalation runs on a harness set to Never (Decisions §11): the policy's
    own default when the key is unset."""
    on = harness if isinstance(harness, str) else DEFAULT_ESCALATION_HARNESS
    if on == FOLLOW_ITEM or states.get(on) != "never":
        return []
    return [
        {
            "path": "defaults.escalation_harness",
            "field": "escalation_harness",
            "message": f"escalation runs on {on!r}, which is set to Never",
            "file": "policy.yaml",
            "line": None,
            "col": None,
            "fix": "Pick another harness, or set this one to Available or Override.",
        }
    ]


def _entry_changes(before: dict, after: dict, prefix: str) -> list[dict]:
    out = []
    for name in dict.fromkeys([*before, *after]):
        a, b = before.get(name), after.get(name)
        if a == b:
            continue
        kind = "add" if a is None else "remove" if b is None else "change"
        keys = {*(a or {}), *(b or {})} if kind == "change" else set()
        fields = sorted(k for k in keys if (a or {}).get(k) != (b or {}).get(k))
        out.append(
            {
                "path": f"{prefix}.{name}",
                "kind": kind,
                "summary": ", ".join(fields),
                "fields": fields,
            }
        )
    return out


def resolve(st, key, raw, files, published) -> dict:
    out = config.resolve_files(st, files, published, FILES, {"harnesses.yaml": _table_issues})
    path = st.templates_dir / "harnesses.yaml"
    providers = harness_mod.load(None).valid
    harnesses_yaml, policy = raw.get("harnesses.yaml"), raw.get("policy.yaml")
    before = {n: authored.parse(published.get(n)) for n in FILES}
    universe = _universe(harnesses_yaml)
    states = access(policy, universe)
    was = access(before["policy.yaml"], _universe(before["harnesses.yaml"]))
    try:
        table = HarnessProfileTable.from_mapping(
            harnesses_yaml,
            path,
            harnesses=providers,
            plugins=getattr(getattr(st, "library", None), "plugins", ()),
        )
    except TemplateEnvironmentError:
        table = None
    chosen = config_check.selections(getattr(st, "library", None))
    linted = _lint_problems(st, policy, chosen)
    escalation = _section(policy, "defaults").get("escalation_harness")
    out["problems"] += (
        _launch_problems(st, table, providers, path, chosen)
        + linted
        + _fallback_problems(st, chosen, table, states, linted)
        + _escalation_problem(states, escalation)
    )
    tasks = _tasks_by_harness(chosen, table)

    profiles = _section(harnesses_yaml, "profiles")
    efforts = {
        name: {e.get("effort") for e in _providers_of(body).values()}
        for name, body in profiles.items()
    }
    out["resolved"] = {
        "harnesses": [
            _harness_view(
                h, _section(harnesses_yaml, "harnesses")[h], states[h], providers, tasks.get(h, [])
            )
            for h in universe
        ],
        "allowed_tools": _section(policy, "maxima").get("allowed_tools"),
        "escalation": {
            "harness": _section(policy, "defaults").get("escalation_harness"),
            "grants": _section(policy, "defaults").get("escalation_grants"),
        },
        # `escalation` is what the file says; this is what a launch does.
        "escalation_effective": {
            "harness": escalation if isinstance(escalation, str) else DEFAULT_ESCALATION_HARNESS,
            "set": isinstance(escalation, str),
        },
        "profiles": {
            name: {
                "providers": _providers_of(body),
                "effort": next(iter(efforts[name])) if len(efforts[name]) == 1 else None,
                "tasks": _users(st, name),
                "used_by_fallback": _fallback_users(profiles, name),
            }
            for name, body in profiles.items()
        },
    }

    changed = {h for h in universe if was.get(h) != states[h]}
    out["changes"] += [
        {
            "path": f"access.{h}",
            "kind": "change",
            "summary": f"{was.get(h, 'new')} -> {states[h]}",
            "fields": ["allowed_harnesses"],
        }
        for h in universe
        if h in changed
    ]
    for section in ("harnesses", "profiles"):
        out["changes"] += _entry_changes(
            _section(before["harnesses.yaml"], section), _section(harnesses_yaml, section), section
        )
    hit = [(chain, at) for chain, at, task in chosen if task.harness in changed]
    out["impact"] = {"tasks": len(hit), "chains": sorted({c for c, _ in hit})}
    return out


async def after_publish(app, written) -> None:
    # The profile table is read from disk per use; only the policy is loaded.
    if "policy.yaml" in written:
        config.reload_policy(app.state)
        storage.kick(app)


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
