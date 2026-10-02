"""The `repos` draft area (W13 E): `repos.yaml`.

`DELETE /repos` refuses a repo with open (not ended) items with a 409; the draft
reports the same as a problem."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from kraft import config as config_mod
from kraft import detect
from kraft.api import deps
from kraft.config import RepoEntry
from kraft.drafts import authored, config, store
from kraft.drafts.ops import OpError
from kraft.policy import PolicyError, TemplatePolicyOverride
from kraft.store import open_counts_by_repo

NAME = "repos"
FILE = "repos.yaml"
FILES = (FILE,)


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


# ── ops ──


def _repos(d, write: bool = True) -> list:
    data = d.file(FILE) if write else d.read(FILE)
    repos = data.setdefault("repos", []) if write else data.get("repos", [])
    if not isinstance(repos, list):
        raise OpError(f"{FILE}: repos is not a list")
    return repos


def _entry(d, path: str) -> dict:
    for e in _repos(d, write=False):
        if isinstance(e, dict) and e.get("path") == path:
            return e
    raise OpError(f"{path} is not in the draft")


def add_repo(d, path: str, fields: dict | None = None) -> None:
    if any(isinstance(e, dict) and e.get("path") == path for e in _repos(d, write=False)):
        raise OpError(f"{path} is already in the draft")
    # Refused as `POST /repos` refuses it: its items' branches would be empty orphans.
    root = config_mod.normalized_repo_root(Path(path)) if Path(path).is_dir() else None
    if root is not None and detect.source_ref(root) is None:
        raise OpError(detect.no_commit(path))
    # Not probed: a path that is no git repository is a problem the draft shows.
    _repos(d).append(
        {
            "path": path,
            "name": Path(path).name,
            "default_chain_template": "default",
            "enabled": False,
            "managed": True,
            **(fields or {}),
        }
    )


def set_repo(d, path: str, patch: dict) -> None:
    """Any entry field; an explicit null clears it. Like `PATCH /repos`, a save is a touch."""
    entry = _entry(d, path)
    d.file(FILE)
    for k, v in patch.items():
        if v is None:
            entry.pop(k, None)
        else:
            entry[k] = v
    entry["managed"] = True


def remove_repo(d, path: str) -> None:
    entry = _entry(d, path)
    _repos(d).remove(entry)


def connect_detected(d, path: str) -> None:
    entry = _entry(d, path)
    if entry.get("managed", True):
        raise OpError(f"{path} is not a detected repo")
    d.file(FILE)
    entry["managed"] = True


OPS = {
    "add_repo": add_repo,
    "set_repo": set_repo,
    "remove_repo": remove_repo,
    "connect_detected": connect_detected,
}


# ── resolve ──


def _problem(path: str, field: str | None, message: str) -> dict:
    return {
        "path": field,
        "field": field,
        "message": message,
        "file": FILE,
        "line": 1,
        "col": 1,
        "repo": path,
    }


def _entries(raw: object) -> list[RepoEntry]:
    items = raw.get("repos") if isinstance(raw, dict) else None
    out = []
    for item in items if isinstance(items, list) else ():
        try:
            out.append(RepoEntry.model_validate(item))
        except ValidationError:
            continue  # `config_check` of the file says why
    return out


def _library_steering(st, chain: str) -> list[str]:
    library = getattr(st, "library", None)
    try:
        return list(library.resolve_chain(chain).steering or ()) if library else []
    except Exception:  # noqa: BLE001 -- a chain that does not resolve is the library's problem
        return []


def _jsonable(value):
    if isinstance(value, tuple):
        return list(value)
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _view(st, entry: RepoEntry, instance) -> tuple[dict, list[dict]]:
    problems = []
    override = entry.repository_override()
    try:
        layered = deps._layered(instance, entry)
    except PolicyError as exc:
        layered = instance
        problems.append(_problem(entry.path, exc.field, f"{entry.path}: {exc}"))
    set_keys = set(override.model_dump(exclude_none=True)) if override else set()
    fields = TemplatePolicyOverride.model_fields
    chain = entry.default_chain_template or "default"
    view = {
        "path": entry.path,
        "name": entry.name,
        "managed": entry.managed,
        "entry": entry.model_dump_repo(mode="json"),
        "resolved": {
            "steering": entry.steering or _library_steering(st, chain),
            "deny_tools": list(layered.deny_tools),
            "models": entry.models,
            "policy": {k: _jsonable(getattr(layered, k, None)) for k in fields},
        },
        # No `default:` repo entry exists, so `steering` is the repo's own or the library's.
        "sources": {
            "steering": "repo" if entry.steering else "library",
            "deny_tools": "repo" if "deny_tools" in set_keys else "default",
            "models": "repo" if entry.models else "default",
            "policy": {k: "repo" if k in set_keys else "default" for k in fields},
        },
    }
    return view, problems


def _path_problem(entry: RepoEntry) -> dict | None:
    p = Path(entry.path).expanduser()
    if not p.is_dir():
        return _problem(entry.path, "path", f"{entry.path} is not a directory")
    if config_mod.normalized_repo_root(p) is None:
        return _problem(entry.path, "path", f"{entry.path} is not a git repository")
    return None


def _changes(before: object, after: object) -> list[dict]:
    def by_path(data):
        items = data.get("repos") if isinstance(data, dict) else None
        return {e["path"]: e for e in items or () if isinstance(e, dict) and "path" in e}

    was, now = by_path(before), by_path(after)
    out = []
    for path in sorted({*was, *now}):
        if was.get(path) == now.get(path):
            continue
        kind = "add" if path not in was else "remove" if path not in now else "change"
        fields = sorted(
            k
            for k in {*was.get(path, {}), *now.get(path, {})}
            if was.get(path, {}).get(k) != now.get(path, {}).get(k)
        )
        out.append({"path": path, "kind": kind, "summary": ", ".join(fields), "fields": fields})
    return out


def resolve(st, key, raw, files, published) -> dict:
    out = config.resolve_files(st, files, published, FILES)
    entries = _entries(raw.get(FILE))
    before = {e.path for e in _entries(authored.parse(published.get(FILE)))}
    running = st.db.read(open_counts_by_repo)
    instance = deps.instance_policy(st)
    chains = set(getattr(getattr(st, "library", None), "chain_ids", ()) or ())

    views = []
    for entry in entries:
        view, problems = _view(st, entry, instance)
        out["problems"] += problems
        if bad := _path_problem(entry):
            out["problems"].append(bad)
        if chains and entry.default_chain_template not in {None, *chains}:
            out["problems"].append(
                _problem(
                    entry.path,
                    "default_chain_template",
                    f"{entry.path}: default_chain_template {entry.default_chain_template!r} "
                    "names no chain in the library",
                )
            )
        views.append(view)

    gone = sorted(before - {e.path for e in entries})
    for path in gone:
        if running.get(path):
            msg = f"{path} has {running[path]} open item(s); finish or cancel them first"
            out["problems"].append(_problem(path, None, msg))
    out["resolved"] = {
        "repos": [v for v in views if v["managed"]],
        "detected": [v for v in views if not v["managed"]],
    }
    out["changes"] += _changes(authored.parse(published.get(FILE)), raw.get(FILE))
    counted = [v["path"] for v in views] + gone
    out["impact"] = {"running": {p: running.get(p, 0) for p in counted}}
    return out


async def after_publish(app, written) -> None:
    """`repos.yaml` is read from disk per use: nothing to reload."""


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
