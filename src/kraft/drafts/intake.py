"""The `intake` draft area (W13 G): `intake.yaml`, its settings and its
`schedules:`. `max_concurrent` is `policy.yaml`'s (Settings › Policy), not this area's."""

from __future__ import annotations

import re

from pydantic import ValidationError

from kraft import apply, policy
from kraft import config as config_mod
from kraft import intake as intake_mod
from kraft.api import deps
from kraft.drafts import config, store
from kraft.drafts.ops import OpError

NAME = "intake"
INTAKE = "intake.yaml"
FILES = (INTAKE,)
INTAKE_FIELDS = ("enabled", "interval_s", "priority_ceiling", "repos")
SCHEDULE_FIELDS = ("cron", "repo", "chain", "title", "description")


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


# ── ops ──


def set_intake(d, patch: dict) -> None:
    """Any intake setting; an unwritten one keeps its default."""
    if bad := sorted(set(patch) - set(INTAKE_FIELDS)):
        raise OpError(f"{', '.join(bad)} is not an intake setting")
    data = d.file(INTAKE)
    for name, default in config_mod.INTAKE_DEFAULT.items():
        data.setdefault(name, default)
    data.update(patch)


def _triggers(d, write: bool = True) -> list:
    data = d.file(INTAKE) if write else d.read(INTAKE)
    schedules = data.setdefault("schedules", []) if write else data.get("schedules", [])
    if not isinstance(schedules, list):
        raise OpError(f"{INTAKE}: schedules is not a list")
    return schedules


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
    d.file(INTAKE)
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
        "path": f"schedules[{index}].{field}",
        "field": field,
        "message": message,
        "file": INTAKE,
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
    cron = entry.get("cron")
    if isinstance(cron, str):
        try:
            policy._cron_fields("cron", cron)
        except policy.PolicyError as exc:
            out.append(_problem(index, "cron", str(exc).removeprefix("cron: ")))
    return out


#: A file-level problem `_schedule_problems` already names on its schedule.
_SCHEDULE_CRON = re.compile(r"schedules\.\d+\.cron")


def _only_crons(exc: ValidationError) -> bool:
    """Whether every error is a schedule's cron, which `_schedule_problems`
    names on that schedule, so the screen still renders the file."""
    return all(e["loc"][:1] == ("schedules",) and e["loc"][2:] == ("cron",) for e in exc.errors())


def resolve(st, key, raw, files, published) -> dict:
    out = config.resolve_files(st, files, published, FILES, keyed=True)
    out["problems"] = [
        p for p in out["problems"] if not _SCHEDULE_CRON.fullmatch(str(p.get("field")))
    ]
    data = raw.get(INTAKE) if isinstance(raw.get(INTAKE), dict) else {}
    try:
        intake = config_mod.Intake.model_validate(data)
    except ValidationError as exc:
        if not _only_crons(exc):
            return {**out, "resolved": None}  # the problem above says why
        intake = config_mod.Intake.model_validate({**data, "schedules": []})

    chains = set(getattr(getattr(st, "library", None), "chain_ids", ()) or ())
    raw_schedules = data.get("schedules")
    schedules = [
        {"index": i, **{f: t.get(f) for f in SCHEDULE_FIELDS}}
        for i, t in enumerate(raw_schedules if isinstance(raw_schedules, list) else ())
        if isinstance(t, dict)
    ]
    for s in schedules:
        out["problems"] += _schedule_problems(st, s["index"], s, chains)
    out["resolved"] = {
        "enabled": intake.enabled,
        "interval_s": intake.interval_s,
        "priority_ceiling": intake.priority_ceiling,
        "repos": intake.repos,
        "schedules": schedules,
    }
    return out


async def after_publish(app, written) -> None:
    """As `PUT /intake` does: the poller is replaced, not restarted."""
    if INTAKE in written:
        st = app.state
        st.intake = config_mod.Intake.load(st.templates_dir / INTAKE).model_dump()
        st.invalid_intake = None
        apply.record(st, INTAKE)
        await intake_mod.restart(app)


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
