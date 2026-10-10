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
from kraft.templates import catalogue, positions
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
    TaskBase,
    _scoped,
)
from kraft.vocab import ENDED

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
        "choices": catalogue.choices(),
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
    data = raw.get(name)
    try:
        before = authored.parse(published.get(old))
    except yaml.YAMLError:
        before = None
    before, _ = _expand(getattr(st, "library", None), st.templates_dir / old, key, before)
    # A `move_to_library` draft holds the library its chain now extends.
    library = (
        draft_library(st, raw.get(LIBRARY_FILE))
        if LIBRARY_FILE in files
        else getattr(st, "library", None)
    )
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
    elif library is not None and library.plugin_of(key) is not None:
        # A plugin's chain is read-only and already in the library: resolved
        # as loaded, never re-added as a local chain (its id has a `:`).
        try:
            resolved = settings._resolved_view(library.resolve_chain(key))
        except TemplateLibraryError as exc:
            problems = [{**_not_a_mapping(name), "message": str(exc)}]
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
        "changes": [
            *_renamed(key, id, files, published),
            *_chain_changes(before, after),
            *changes(_flat(before), _flat(after)),
        ],
        "impact": impact,
    }


def _renamed(
    key: str, id: str, files: Mapping[str, str | None], published: Mapping[str, str | None]
) -> list[dict]:
    """One `rename` row, at the chain's own path, when the draft moved the
    chain to a new id: the ids are not among the keys `_chain_changes` and
    `_flat` compare, so a rename alone used to read "No changes" while its
    publish deleted `chains/<key>.yaml` (R10b-02). A chain never published
    has no file to move: its publish creates the new one."""
    if id == key or files.get(f"chains/{key}.yaml") is not None:
        return []
    moves = (
        f"publish moves chains/{key}.yaml to chains/{id}.yaml"
        if published.get(f"chains/{key}.yaml") is not None
        else f"publish creates chains/{id}.yaml"
    )
    return [
        {
            "path": "",
            "kind": "rename",
            "summary": f"chain id {key} → {id} · {moves}, and new items name it {id}",
            "fields": ["id"],
            "from": key,
            "to": id,
        }
    ]


def _chain_changes(before: Mapping, after: Mapping) -> list[dict]:
    """One row, at the chain's own path (`""`), for the chain-level keys that
    differ (`description`, `policy`): `_flat` walks only the nodes. Unset and
    empty read the same, so a new chain's blank description is no change."""
    fields = [
        k
        for k in dict.fromkeys([*before, *after])
        if k not in ("id", "nodes") and (before.get(k) or None) != (after.get(k) or None)
    ]
    if not fields:
        return []
    return [{"path": "", "kind": "change", "summary": ", ".join(fields), "fields": fields}]


def _impact(st, key: str) -> dict:
    # An item or repo naming no chain runs `default` (Kraft-cd47).
    running = st.db.read(
        lambda c: c.execute(
            "SELECT COUNT(*) FROM work_items WHERE (chain_template = ? OR "
            "(chain_template IS NULL AND ? = 'default')) AND status NOT IN (?, ?)",
            (key, key, *ENDED),
        ).fetchone()[0]
    )
    return {
        "running": running,
        "repos": [r.path for r in _repos(st) if (r.default_chain or "default") == key],
    }


def _repos(st) -> list:
    # A repos.yaml broken for another reason is not this draft's to report.
    try:
        return config_mod.load_repos(deps.repos_path(st))
    except config_mod.ConfigError:
        return []


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
        # What a task accepts but nothing reads is left out unless set; a cap
        # only its recovery runs under says so (`TaskBase.unread`).
        unread = model.unread() if isinstance(model, TaskBase) else frozenset()
        recovery = model.for_recovery() if isinstance(model, TaskBase) else frozenset()
        own = model.policy if "policy" in type(model).model_fields else None
        fields = {}
        for name in type(model).model_fields:
            if name in _NESTED or name == "policy":
                continue
            if name in unread and name not in model.model_fields_set:
                continue
            value = dumped[name]
            if name == "on_base_changed" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k != "on_conflict"}
            source = _setter(layers, name) if name in model.model_fields_set else "default"
            fields[name] = {"value": value, "source": source}
        if "policy" in type(model).model_fields:
            scope = scopes.get(path)
            effective = _scoped(scope, policy) if scope is not None else None
            caps = TemplatePolicyOverride if isinstance(model, ExecNode) else TaskPolicyOverride
            for cap in caps.model_fields:
                key = f"policy{PATH_SEPARATOR}{cap}"
                if own is not None and cap in own.model_fields_set:
                    fields[key] = {"value": dumped["policy"][cap], "source": _setter(layers, key)}
                    continue
                if key in unread:
                    continue
                value = to_jsonable_python(getattr(effective, cap, None))
                source = "default" if value in (None, []) else "policy"
                fields[key] = {"value": value, "source": source}
            for key in recovery & fields.keys():
                fields[key]["recovery"] = True
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


def _flat(chain: Mapping, *, library: bool = False) -> dict[str, dict]:
    """Every component of an expanded chain by canonical path, in chain order,
    each with its own fields only: the prototype's `flat()`. With `library`,
    of an authored library, by `<section>.<name>` path."""
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

    def step(s: Mapping, path: str) -> None:
        put(s, path)
        for i, t in items(s.get("tasks")):
            task(t, named(t, path, i))
        child(s, "on_failure", path, shape)

    def shape(c: Mapping, path: str) -> None:
        put(c, path)
        for i, t in items(c.get("tasks")):
            task(t, named(t, join(path, MAIN_STEP), i))
        for i, s in items(c.get("steps")):
            step(s, named(s, path, i))
        child(c, JUDGE_SEGMENT, path, task)

    def node(n: Mapping, path: str) -> None:
        shape(n, path)
        for key in ("on_failure", "fix_loop"):
            child(n, key, path, shape)
        for key in ("escalation", AUTO_REVIEW_SEGMENT):
            child(n, key, path, task)
        base_change = n.get("on_base_changed")
        if isinstance(base_change, Mapping):
            child(base_change, "on_conflict", join(path, "on_base_changed"), shape)

    if library:
        # `<section>.<name>`, each section in the file's order.
        walks = {"steering": put, "tasks": task, "steps": step, "nodes": node}
        for section, entries in chain.items():
            if section in walks and isinstance(entries, Mapping):
                for name, body in entries.items():
                    if isinstance(body, Mapping):
                        walks[section](body, join(section, str(name)))
        return out
    for i, n in items(chain.get("nodes")):
        node(n, named(n, "", i))
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


def draft_library(st, data: object) -> TemplateLibrary | None:
    """The library a draft holding `library.yaml` resolves against, `data`
    being that file's; None when it does not load."""
    if not isinstance(data, dict):
        return None
    try:
        return config_check.library_candidate(
            getattr(st, "library", None),
            data,
            st.templates_dir / LIBRARY_FILE,
            getattr(st, "skills_dir", None),
        )
    except TemplateLibraryError:
        return None


def _component(path: str) -> str:
    """`nodes.verification` for `nodes.verification.review.code_review`."""
    return PATH_SEPARATOR.join(path.split(PATH_SEPARATOR)[:2])


def _library(st, key: str, raw: dict, files: dict, published: dict) -> dict:
    """`library.yaml` and the chain files its renames rewrote: the library
    save's problems, each with the chain or repo it breaks and the component
    it comes from; the change list per component, with the chains each
    reaches; and the chains and repos a publish reaches."""
    data = raw.get(LIBRARY_FILE)
    if not isinstance(data, dict):
        return {"problems": [_not_a_mapping(LIBRARY_FILE)]}
    path = st.templates_dir / LIBRARY_FILE
    running = getattr(st, "library", None)
    skills = getattr(st, "skills_dir", None)
    chains = [
        (st.templates_dir / n, d)
        for n, d in raw.items()
        if n != LIBRARY_FILE and isinstance(d, dict)
    ]
    repos = _repos(st)
    # Repos are checked below, where each problem can name its repo.
    issues = config_check.library_issues(
        running, data, path, getattr(st, "instance_policy", None), [], skills, chains
    )
    try:
        candidate = config_check.library_candidate(running, data, path, skills, chains)
    except TemplateLibraryError:
        candidate = None
    buffers = {st.templates_dir / n: t or "" for n, t in files.items()}

    problems = []
    for issue in issues:
        resolution = None
        if candidate is not None and issue.chain in candidate.chain_ids:
            file = candidate.chain_file(issue.chain)
            _, resolution = _expand(candidate, file, issue.chain, candidate.chain_data(issue.chain))

        def split(file, loc, resolution=resolution, issue=issue):
            if issue.chain is not None:
                return resolution.split(loc) if resolution is not None else (None, loc)
            # A component of the library is `<section>.<name>`.
            if len(loc) < 2:
                return None, loc
            return PATH_SEPARATOR.join(map(str, loc[:2])), loc[2:]

        for p in _problems(issue, split, st, buffers):
            component = p["path"] if issue.chain is None else None
            source = resolution._sources.get(p["path"]) if resolution is not None else None
            if source is not None and source.file == path:
                component = f"{source.namespace.value}.{source.name}"
            problems.append({**p, "chain": issue.chain, "repo": None, "component": component})
    if candidate is not None:
        file = deps.repos_path(st)
        text = file.read_text() if file.is_file() else ""
        for index, entry in enumerate(repos):
            for _, why in config_check.steering_issues(candidate, [entry]):
                missing = next((n for n in entry.steering if n not in candidate.steering), None)
                line, col = positions.locate(text, ("repos", index, "steering"))
                problems.append(
                    {
                        "path": "steering",
                        "field": None,
                        "message": why,
                        "file": "repos.yaml",
                        "line": line,
                        "col": col,
                        "chain": None,
                        "repo": entry.path,
                        "component": f"steering.{missing}" if missing else None,
                    }
                )

    try:
        before = authored.parse(published.get(LIBRARY_FILE))
    except yaml.YAMLError:
        before = None
    listed = changes(
        _flat(before, library=True) if isinstance(before, Mapping) else {},
        _flat(data, library=True),
    )
    # Published or drafted: a removed component reached its chains before.
    reaches: dict[str, set[str]] = {}
    for library in (running, candidate):
        for chain in library.chain_ids if library is not None else ():
            for refs in library.references(chain).values():
                for ref in refs:
                    reaches.setdefault(ref, set()).add(chain)
    for change in listed:
        change["reaches"] = sorted(reaches.get(_component(change["path"]), ()))
    profiles = {
        c["path"].split(PATH_SEPARATOR)[1]
        for c in listed
        if c["path"].startswith(f"{Namespace.STEERING.value}{PATH_SEPARATOR}")
    }
    return {
        "problems": problems,
        "changes": listed,
        "impact": {
            "chains": sorted({c for change in listed for c in change["reaches"]}),
            "repos": [r.path for r in repos if set(r.steering) & profiles],
        },
    }


#: Per area of `drafts.store.AREAS`, the result's area-specific keys.
_AREAS = {"chains": _chains, "library": _library}
