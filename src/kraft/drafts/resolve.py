"""The resolve result every draft read and write answers with: the authored
model, the resolved chain, problems with paths and lines, the change list
against the published files, what a publish touches, and what it drops.

`files` and `published` are `{file: text | None}` maps over the same names,
relative to `templates_dir`; `files` is the published set with the draft laid
over it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import yaml

from kraft import config as config_mod
from kraft.api import config_check, deps
from kraft.api.routes import settings
from kraft.drafts import authored
from kraft.store import ENDED
from kraft.templates import positions
from kraft.templates.library import (
    LIBRARY_FILE,
    ComponentSource,
    Namespace,
    TemplateIssue,
    TemplateLibrary,
    TemplateLibraryError,
    _Resolution,
)
from kraft.templates.models import AUTO_REVIEW_SEGMENT, JUDGE_SEGMENT, MAIN_STEP, PATH_SEPARATOR

# ponytail: a `#` inside a quoted string warns too.
_COMMENT = re.compile(r"^\s*#|\s#", re.MULTILINE)


def published(templates_dir: Path, names) -> dict[str, str | None]:
    return {n: (p.read_text() if (p := templates_dir / n).is_file() else None) for n in names}


def resolve(
    st,
    area: str,
    key: str,
    files: dict[str, str | None],
    published: dict[str, str | None],
    *,
    history: Sequence[dict] = (),
    serialized: Sequence[str] = (),
) -> dict:
    # `raw` is what the checks validate; `model`, the same text with the
    # shorthand normalised, is what the canvas and the ops address.
    raw: dict[str, object] = {}
    model: dict[str, object] = {}
    yaml_error = None
    for name, text in files.items():
        try:
            raw[name] = authored.parse(text)
        except yaml.YAMLError as exc:
            line, col = positions.yaml_mark(exc) or (1, 1)
            yaml_error = yaml_error or {
                "file": name,
                "line": line,
                "col": col,
                "message": getattr(exc, "problem", None) or str(exc),
            }
            text = _last_valid(name, history, published)
            raw[name] = authored.parse(text)
        model[name] = authored.load(text)
    result = {
        "model": model,
        "resolved": None,
        "problems": [],
        "sources": {},
        "changes": [],
        "impact": None,
        "warnings": [
            {"file": f, "message": "comments in this file will be dropped"}
            for f in serialized
            if _COMMENT.search(published.get(f) or "")
        ],
        **_AREAS[area](st, key, raw, files, published),
    }
    if yaml_error is not None:
        result["yaml_error"] = yaml_error
    return result


def _last_valid(name: str, history, published) -> str | None:
    """The newest undo entry's text for `name` that parses, else the published
    file's (Decisions §9: keep the last valid model). An entry without `name`
    held its published text."""
    texts = [e["files"].get(name, published.get(name)) for e in reversed(history)]
    for text in (*texts, published.get(name)):
        try:
            authored.parse(text)
        except yaml.YAMLError:
            continue
        return text
    return None


def _problems(issue: TemplateIssue, split, st, buffers: Mapping[Path, str]) -> list[dict]:
    """One problem per schema error the issue carries, else one for the issue."""
    try:
        file = str(issue.file.relative_to(st.templates_dir))
    except ValueError:
        file = str(issue.file)
    out = []
    for loc, message in issue.errors or ((issue.loc, issue.message),):
        view = positions.issue_view(replace(issue, loc=loc, related=None), buffers)
        path, field = split(file, loc or ())
        out.append(
            {
                "path": path,
                "field": PATH_SEPARATOR.join(map(str, field)) or None,
                "message": message,
                "file": file,
                "line": view["line"],
                "col": view["column"],
            }
        )
    return out


def _not_a_mapping(file: str) -> dict:
    message = config_check.NOT_A_MAPPING
    return {"path": None, "field": None, "message": message, "file": file, "line": 1, "col": 1}


# ── chains ──


def _expand(library: TemplateLibrary | None, path: Path, key: str, data: object):
    """`data` with `extends` expanded, before validation, and the expansion
    that knows each component's canonical path. Unexpanded where expansion
    fails part-way: such a chain diffs as written."""
    if library is None or not isinstance(data, Mapping):
        return (data if isinstance(data, Mapping) else {}), None
    resolution = _Resolution(library, ComponentSource(path, Namespace.NODES, key))
    try:
        return resolution.expand_chain({**data, "id": key}), resolution
    except TemplateLibraryError:
        return data, resolution


def _chains(st, key: str, raw: dict, files: dict, published: dict) -> dict:
    name = f"chains/{key}.yaml"
    path = st.templates_dir / name
    library = getattr(st, "library", None)
    data = raw.get(name)
    try:
        before = authored.parse(published.get(name))
    except yaml.YAMLError:
        before = None
    before, _ = _expand(library, path, key, before)
    after, resolution = _expand(library, path, key, data)

    def split(file, loc):
        return resolution.split(loc) if resolution is not None and file == name else (None, loc)

    resolved = None
    if not isinstance(data, dict):
        problems = [_not_a_mapping(name)]
    else:
        issues = config_check.chain_issues(
            library, path, key, data, getattr(st, "instance_policy", None)
        )
        buffers = {path: files.get(name) or ""}
        problems = [p for i in issues for p in _problems(i, split, st, buffers)]
        if library is not None:
            try:
                candidate, _ = library.with_chain(path, {**data, "id": key})
                resolved = settings._resolved_view(candidate.resolve_chain(key))
            except TemplateLibraryError:
                pass
    return {
        "resolved": resolved,
        "problems": problems,
        "changes": changes(_flat(before), _flat(after)),
        "impact": _impact(st, key),
    }


def _impact(st, key: str) -> dict:
    # An item or repo naming no chain runs `default` (Kraft-cd47).
    running = st.db.read(
        lambda c: c.execute(
            "SELECT COUNT(*) FROM work_items WHERE (chain_template = ? OR "
            "(chain_template IS NULL AND ? = 'default')) AND status NOT IN (?, ?)",
            (key, key, *ENDED),
        ).fetchone()[0]
    )
    try:
        repos = config_mod.load_repos(deps.repos_path(st))
    except config_mod.ConfigError:
        repos = []
    return {
        "running": running,
        "repos": [r.path for r in repos if (r.default_chain_template or "default") == key],
    }


#: Keys that hold nested components, each flattened under its own path.
_NESTED = frozenset(
    {"tasks", "steps", JUDGE_SEGMENT, "on_failure", "fix_loop", "escalation", AUTO_REVIEW_SEGMENT}
)


def _flat(chain: Mapping) -> dict[str, dict]:
    """Every component of an expanded chain by canonical path, in chain order,
    each with its own fields only: the prototype's `flat()`."""
    out: dict[str, dict] = {}

    def join(*parts: str) -> str:
        return PATH_SEPARATOR.join(p for p in parts if p)

    def put(obj: Mapping, path: str) -> None:
        own = {k: v for k, v in obj.items() if k not in _NESTED}
        if isinstance(own.get("on_base_changed"), Mapping):
            own["on_base_changed"] = {
                k: v for k, v in own["on_base_changed"].items() if k != "on_conflict"
            }
        out[path] = own

    def named(obj: Mapping, prefix: str, index: int) -> str:
        id = obj.get("id")
        return join(prefix, id if isinstance(id, str) else f"[{index}]")

    def items(value: object) -> list[tuple[int, Mapping]]:
        listed = value if isinstance(value, list) else []
        return [(i, x) for i, x in enumerate(listed) if isinstance(x, Mapping)]

    def child(obj: Mapping, key: str, path: str, walk) -> None:
        if isinstance(obj.get(key), Mapping):
            walk(obj[key], join(path, key))

    def task(t: Mapping, path: str) -> None:
        put(t, path)
        child(t, "on_failure", path, shape)

    def shape(c: Mapping, path: str) -> None:
        put(c, path)
        for i, t in items(c.get("tasks")):
            task(t, named(t, join(path, MAIN_STEP), i))
        for i, s in items(c.get("steps")):
            step = named(s, path, i)
            shape(s, step)
            child(s, "on_failure", step, shape)
        child(c, JUDGE_SEGMENT, path, task)

    for i, node in items(chain.get("nodes")):
        path = named(node, "", i)
        shape(node, path)
        for key in ("on_failure", "fix_loop"):
            child(node, key, path, shape)
        for key in ("escalation", AUTO_REVIEW_SEGMENT):
            child(node, key, path, task)
        base_change = node.get("on_base_changed")
        if isinstance(base_change, Mapping):
            child(base_change, "on_conflict", join(path, "on_base_changed"), shape)
    return out


_MISSING = object()


def _lis(values: list[int]) -> set[int]:
    """Indexes of one longest increasing subsequence of `values`."""
    length, prev, best = [1] * len(values), [-1] * len(values), -1
    for i in range(len(values)):
        for j in range(i):
            if values[j] < values[i] and length[j] + 1 > length[i]:
                length[i], prev[i] = length[j] + 1, j
        if best < 0 or length[i] > length[best]:
            best = i
    keep = set()
    while best >= 0:
        keep.add(best)
        best = prev[best]
    return keep


def changes(before: dict[str, dict], after: dict[str, dict]) -> list[dict]:
    """The prototype's `changes()`: what `after` adds, changes, removes and
    moves against `before`, both `_flat` maps. A path under an added or removed
    one folds into it."""
    out: dict[str, dict] = {}
    for path, own in after.items():
        if path not in before:
            out[path] = {"path": path, "kind": "add", "summary": "added"}
        elif before[path] != own:
            old = before[path]
            fields = [
                k
                for k in dict.fromkeys([*old, *own])
                if old.get(k, _MISSING) != own.get(k, _MISSING)
            ]
            summary = ", ".join(f.replace("_", " ") for f in fields)
            out[path] = {"path": path, "kind": "change", "summary": summary, "fields": fields}
    for path in before:
        if path not in after:
            out[path] = {"path": path, "kind": "remove", "summary": "removed"}
    order = list(before)
    groups: dict[str, list[str]] = {}
    for path in after:
        if path in before:
            groups.setdefault(path.rpartition(PATH_SEPARATOR)[0], []).append(path)
    for paths in groups.values():
        keep = _lis([order.index(p) for p in paths])
        for i, path in enumerate(paths):
            if i in keep:
                continue
            change = out.setdefault(path, {"path": path, "kind": "change", "summary": ""})
            change["summary"] = ", ".join(s for s in (change["summary"], "moved") if s)
    folded = [p for p, c in out.items() if c["kind"] != "change"]
    return [
        c
        for p, c in out.items()
        if not any(p.startswith(f + PATH_SEPARATOR) for f in folded if f != p)
    ]


# ── the library ──


def _library(st, key: str, raw: dict, files: dict, published: dict) -> dict:
    """`library.yaml` alone (F adds the chains it pulls in, used-by paths and
    the change list): its problems, which are the library save's own."""
    data = raw.get(LIBRARY_FILE)
    if not isinstance(data, dict):
        return {"problems": [_not_a_mapping(LIBRARY_FILE)]}
    path = st.templates_dir / LIBRARY_FILE
    try:
        repos = config_mod.load_repos(deps.repos_path(st))
    except config_mod.ConfigError:
        repos = []
    issues = config_check.library_issues(
        getattr(st, "library", None),
        data,
        path,
        getattr(st, "instance_policy", None),
        repos,
        getattr(st, "skills_dir", None),
    )

    def split(file, loc):
        # A component is `<section>.<name>`; a chain an edit broke is its own file's.
        if file != LIBRARY_FILE or len(loc) < 2:
            return None, loc
        return PATH_SEPARATOR.join(map(str, loc[:2])), loc[2:]

    buffers = {path: files.get(LIBRARY_FILE) or ""}
    return {"problems": [p for i in issues for p in _problems(i, split, st, buffers)]}


#: Per area of `drafts.store.AREAS`, the result's area-specific keys.
_AREAS = {"chains": _chains, "library": _library}
