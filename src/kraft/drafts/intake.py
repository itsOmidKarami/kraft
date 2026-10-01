"""The `intake` draft area (W13 G): `intake.yaml`, and the schedules of `policy.yaml`
(its `triggers:`). `max_concurrent` is `policy.yaml`'s, where the loader reads it."""

from __future__ import annotations

from pydantic import ValidationError

from kraft import apply
from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft.api import config_check, deps
from kraft.drafts import config, store
from kraft.drafts.ops import OpError
from kraft.policy import PolicyError

NAME = "intake"
INTAKE, POLICY = FILES = ("intake.yaml", "policy.yaml")
INTAKE_FIELDS = ("enabled", "interval_s", "max_concurrent", "priority_ceiling", "repos")
SCHEDULE_FIELDS = ("cron", "repo", "chain", "title", "description")


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


# ── ops ──


def set_intake(d, patch: dict) -> None:
    """Any intake setting; an unwritten one keeps its default."""
    if bad := sorted(set(patch) - set(INTAKE_FIELDS)):
        raise OpError(f"{', '.join(bad)} is not an intake setting")
    for k, v in patch.items():
        if k == "max_concurrent":
            if v is None:
                d.file(POLICY).pop(k, None)
            else:
                d.file(POLICY)[k] = v
        else:
            data = d.file(INTAKE)
            for name, default in config_mod.INTAKE_DEFAULT.items():
                data.setdefault(name, default)
            data[k] = v


def _triggers(d, write: bool = True) -> list:
    data = d.file(POLICY) if write else d.read(POLICY)
    triggers = data.setdefault("triggers", []) if write else data.get("triggers", [])
    if not isinstance(triggers, list):
        raise OpError(f"{POLICY}: triggers is not a list")
    return triggers


def _schedule(d, index: int) -> dict:
    triggers = _triggers(d, write=False)
    if not 0 <= index < len(triggers) or not isinstance(triggers[index], dict):
        raise OpError(f"there is no schedule {index}")
    return triggers[index]


def add_schedule(d, cron: str, repo: str, chain: str, title: str, description: str = "") -> None:
    _triggers(d).append(
        {"cron": cron, "repo": repo, "chain": chain, "title": title, "description": description}
    )


def set_schedule(d, index: int, patch: dict) -> None:
    if bad := sorted(set(patch) - set(SCHEDULE_FIELDS)):
        raise OpError(f"{', '.join(bad)} is not a schedule field")
    entry = _schedule(d, index)
    d.file(POLICY)
    entry.update(patch)


def remove_schedule(d, index: int) -> None:
    entry = _schedule(d, index)
    _triggers(d).remove(entry)


OPS = {
    "set_intake": set_intake,
    "add_schedule": add_schedule,
    "set_schedule": set_schedule,
    "remove_schedule": remove_schedule,
}


# ── resolve ──


def _problem(index: int, field: str, message: str) -> dict:
    return {
        "path": f"triggers[{index}].{field}",
        "field": field,
        "message": message,
        "file": POLICY,
        "line": 1,
        "col": 1,
        "schedule": index,
    }


def _schedule_problems(st, index: int, entry: dict, chains: set) -> list[dict]:
    out = []
    repo = entry.get("repo")
    if isinstance(repo, str):
        try:
            repos = config_mod.load_repos(deps.repos_path(st))
        except config_mod.ConfigError:
            repos = None  # `config_check` of repos.yaml says why
        if repos is not None and deps._connected(repos, repo) is None:
            out.append(_problem(index, "repo", f"{repo} is not a connected repo"))
    chain = entry.get("chain")
    if isinstance(chain, str) and chains and chain not in chains:
        out.append(_problem(index, "chain", f"chain {chain!r} is not in the library"))
    return out


def resolve(st, key, raw, files, published) -> dict:
    out = config.resolve_files(st, files, published, FILES, keyed=True)
    data = raw.get(INTAKE) if isinstance(raw.get(INTAKE), dict) else {}
    try:
        intake = config_mod.Intake.model_validate(data)
    except ValidationError:
        return {**out, "resolved": None}  # the problem above says why
    policy_data = raw.get(POLICY) if isinstance(raw.get(POLICY), dict) else {}
    try:
        _, policy = config_check.validate_policy(policy_data)
    except PolicyError:
        return {**out, "resolved": None}

    chains = set(getattr(getattr(st, "library", None), "chain_ids", ()) or ())
    triggers = policy_data.get("triggers")
    schedules = [
        {"index": i, **{f: t.get(f) for f in SCHEDULE_FIELDS}}
        for i, t in enumerate(triggers if isinstance(triggers, list) else ())
        if isinstance(t, dict)
    ]
    for s in schedules:
        out["problems"] += _schedule_problems(st, s["index"], s, chains)
    out["resolved"] = {
        "enabled": intake.enabled,
        "interval_s": intake.interval_s,
        "max_concurrent": policy.max_concurrent,
        "priority_ceiling": intake.priority_ceiling,
        "repos": intake.repos,
        "schedules": schedules,
    }
    return out


async def after_publish(app, written) -> None:
    """As `PUT /intake` does: the poller is replaced, not restarted."""
    if "policy.yaml" in written:
        config.reload_policy(app.state)
    if "intake.yaml" in written:
        st = app.state
        st.intake = config_mod.Intake.load(st.templates_dir / "intake.yaml").model_dump()
        apply.record(st, "intake.yaml")
        await intake_mod.restart(app)


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
