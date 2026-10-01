"""The resolve result every draft read and write answers with: the authored
model, the resolved chain, problems with paths and lines, the change list
against the published files, what a publish touches, and what it drops.

`files` and `published` are `{file: text | None}` maps over the same names,
relative to `templates_dir`; `files` is the published set with the draft laid
over it.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import yaml
from pydantic_core import to_jsonable_python

from kraft import config as config_mod
from kraft.api import config_check, deps
from kraft.api.routes import settings
from kraft.drafts import authored
from kraft.policy import Policy, PolicyError, TaskPolicyOverride, TemplatePolicyOverride
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
from kraft.templates.models import (
    AUTO_REVIEW_SEGMENT,
    JUDGE_SEGMENT,
    MAIN_STEP,
    PATH_SEPARATOR,
    ExecNode,
    GateNode,
    ResolvedChain,
    _scoped,
)

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
    policy = getattr(st, "policy", None) or Policy
    result = {
        "model": model,
        "resolved": None,
        "problems": [],
        "sources": {},
        "changes": [],
        "impact": None,
        "policy_values": {
            k: getattr(policy, k) for k in ("auto_escalate_delay_s", "auto_review_attempts")
        },
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


def chain_file(key: str, files: Mapping[str, str | None]) -> str:
    """The file a `chains` draft of `key` keeps its chain in: `chains/<key>.yaml`,
    or the chain file a `rename` moved it to, the old one being null."""
    name = f"chains/{key}.yaml"
    if files.get(name) is not None:
        return name
    return next((n for n, t in files.items() if n.startswith("chains/") and t is not None), name)


def _chains(st, key: str, raw: dict, files: dict, published: dict) -> dict:
    old = f"chains/{key}.yaml"
    name = chain_file(key, files)
    id = name.removeprefix("chains/").removesuffix(".yaml")
    path = st.templates_dir / name
    library = getattr(st, "library", None)
    data = raw.get(name)
    try:
        before = authored.parse(published.get(old))
    except yaml.YAMLError:
        before = None
    before, _ = _expand(library, st.templates_dir / old, key, before)
    after, resolution = _expand(library, path, id, data)

    def split(file, loc):
        return resolution.split(loc) if resolution is not None and file == name else (None, loc)

    resolved = None
    sources: dict = {}
    impact = _impact(st, key)
    # The draft deletes or renames the chain: only a repo still defaulting to it stops that.
    problems = [
        {**_not_a_mapping(old), "message": f"repo {repo} defaults to it"}
        for repo in (impact["repos"] if files.get(old) is None else ())
    ]
    if data is not None and not isinstance(data, dict):
        problems = [_not_a_mapping(name)]
    elif data is not None:
        issues = config_check.chain_issues(
            library, path, id, data, getattr(st, "instance_policy", None)
        )
        buffers = {path: files.get(name) or ""}
        problems += [p for i in issues for p in _problems(i, split, st, buffers)]
        if library is not None:
            try:
                candidate, _ = library.with_chain(path, {**data, "id": id})
                chain = candidate.resolve_chain(id)
            except TemplateLibraryError:
                pass
            else:
                resolved = settings._resolved_view(chain)
                sources = _sources(
                    authored.normalise(copy.deepcopy(data)),
                    _library_model(st, files),
                    chain,
                    deps.instance_policy(st),
                )
    return {
        "resolved": resolved,
        "problems": problems,
        "sources": sources,
        "changes": changes(_flat(before), _flat(after)),
        "impact": impact,
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


# ── sources ──


def _library_model(st, files: Mapping[str, str | None]) -> Mapping:
    """The authored library a draft resolves against: the draft's own
    `library.yaml` when it holds one, else the published one."""
    if LIBRARY_FILE in files:
        text = files[LIBRARY_FILE]
    else:
        text = published(st.templates_dir, [LIBRARY_FILE])[LIBRARY_FILE]
    try:
        library = authored.load(text)
    except yaml.YAMLError:
        return {}
    return library if isinstance(library, dict) else {}


def _layers(chain: Mapping, library: Mapping, path: str) -> list[tuple[str, Mapping]]:
    """The authored mappings the component at canonical `path` takes its fields
    from, nearest first, each labelled `chain` or `library:<section>.<name>`:
    the chain file's own, then each library component it inherits them through.
    A list (`steps`, `tasks`) comes whole from the nearest layer holding it, as
    `extends` replaces arrays; a mapping merges across the layers."""

    def parents(own: list, namespace: Namespace) -> list:
        out = list(own)
        section = library.get(namespace.value)
        name = next((m["extends"] for _, m in own if m.get("extends")), None)
        seen = set()
        while isinstance(name, str) and name not in seen and isinstance(section, Mapping):
            seen.add(name)
            body = section.get(name)
            if not isinstance(body, Mapping):
                break
            out.append((f"library:{namespace.value}.{name}", body))
            name = body.get("extends")
        return out

    def merged(layers: list, key: str) -> list:
        out = []
        for label, m in layers:
            if key in m:
                if not isinstance(m[key], Mapping):
                    break  # an explicit null replaces what is further up
                out.append((label, m[key]))
        return out

    def listed(layers: list, key: str, id: str) -> list:
        for label, m in layers:
            if key in m:
                items = m[key] if isinstance(m[key], list) else []
                return [(label, x) for x in items if isinstance(x, Mapping) and x.get("id") == id]
        return []

    segs = iter(path.split(PATH_SEPARATOR))
    layers = parents(listed([("chain", chain)], "nodes", next(segs)), Namespace.NODES)
    holds = "steps"  # the list a plain segment names an entry of
    for seg in segs:
        if seg in ("on_failure", "fix_loop"):
            layers, holds = merged(layers, seg), "steps"
        elif seg == "on_base_changed":
            layers, holds = merged(merged(layers, seg), next(segs, "on_conflict")), "steps"
        elif seg in (JUDGE_SEGMENT, "escalation", AUTO_REVIEW_SEGMENT):
            if seg == "escalation":
                next(segs, None)  # the task's own id
            layers, holds = parents(merged(layers, seg), Namespace.TASKS), "tasks"
        elif holds == "steps":
            layers, holds = parents(listed(layers, "steps", seg), Namespace.STEPS), "tasks"
        else:
            layers = parents(listed(layers, "tasks", seg), Namespace.TASKS)
    return layers


def _setter(layers: list[tuple[str, Mapping]], field: str) -> str:
    """The label of the nearest layer that sets dotted `field`."""
    for label, m in layers:
        value: object = m
        for key in field.split(PATH_SEPARATOR):
            value = value.get(key, _MISSING) if isinstance(value, Mapping) else _MISSING
        if value is not _MISSING:
            return label
    return "chain"


def _sources(chain: Mapping, library: Mapping, resolved: ResolvedChain, policy) -> dict:
    """`{path: {field: {value, source}}}` for every component of `resolved`:
    its model's own fields, and its scope's `policy.*` caps. A field the
    expanded chain does not set is `default`; a cap it does not set is
    `policy`, at the value the scope runs under, when that has one."""
    try:
        policy = resolved.chain_policy(policy)
        scopes = {s.path: s for n in resolved.nodes for s in (n, *n.steps_in(), *n.tasks())}
    except PolicyError:
        scopes = {}
    out: dict[str, dict] = {}

    def put(model, path: str) -> None:
        layers = _layers(chain, library, path)
        dumped = model.model_dump(mode="json")
        fields = {}
        for name in type(model).model_fields:
            if name in _NESTED or name == "policy":
                continue
            value = dumped[name]
            if name == "on_base_changed" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k != "on_conflict"}
            source = _setter(layers, name) if name in model.model_fields_set else "default"
            fields[name] = {"value": value, "source": source}
        if "policy" in type(model).model_fields:
            own = model.policy
            scope = scopes.get(path)
            effective = _scoped(scope, policy) if scope is not None else None
            caps = TemplatePolicyOverride if isinstance(model, ExecNode) else TaskPolicyOverride
            for cap in caps.model_fields:
                key = f"policy{PATH_SEPARATOR}{cap}"
                if own is not None and cap in own.model_fields_set:
                    fields[key] = {"value": dumped["policy"][cap], "source": _setter(layers, key)}
                    continue
                value = to_jsonable_python(getattr(effective, cap, None))
                source = "default" if value in (None, []) else "policy"
                fields[key] = {"value": value, "source": source}
        if fields:
            out[path] = fields

    def join(*parts: str) -> str:
        return PATH_SEPARATOR.join(parts)

    def task(t, path: str) -> None:
        put(t, path)
        if t.on_failure is not None:
            handler(t.on_failure, join(path, "on_failure"))

    def shape(c, path: str) -> None:
        for t in c.tasks or ():
            task(t, join(path, MAIN_STEP, t.id))
        for s in c.steps or ():
            put(s, join(path, s.id))
            for t in s.tasks:
                task(t, join(path, s.id, t.id))
            if s.on_failure is not None:
                handler(s.on_failure, join(path, s.id, "on_failure"))
        if getattr(c, JUDGE_SEGMENT, None) is not None:
            task(c.judge, join(path, JUDGE_SEGMENT))

    def handler(c, path: str) -> None:
        put(c, path)
        shape(c, path)

    for n in resolved.chain.nodes:
        put(n, n.id)
        if isinstance(n, GateNode):
            if n.auto_review is not None:
                task(n.auto_review, join(n.id, AUTO_REVIEW_SEGMENT))
            continue
        shape(n, n.id)
        for key in ("on_failure", "fix_loop"):
            if getattr(n, key) is not None:
                handler(getattr(n, key), join(n.id, key))
        if n.escalation is not None:
            task(n.escalation, join(n.id, "escalation", n.escalation.id))
        if n.on_base_changed is not None and n.on_base_changed.on_conflict is not None:
            handler(n.on_base_changed.on_conflict, join(n.id, "on_base_changed", "on_conflict"))
    return out


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
