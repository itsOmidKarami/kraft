"""Kraft's V1 chain templates, found and resolved the way Kraft does.

Stdlib only, like `kl.py`. Where a chain comes from follows Kraft's precedence:
the repo library in `<root>/.kraft/` (its own namespace, `repo:<id>` outside
it), else the user's library (`$KRAFT_CONFIG_DIR`, `$KRAFT_TEMPLATES_DIR`,
`$KRAFT_HOME/config`, `~/.kraft/config`, whichever holds `library.yaml`), else
the packaged library bundled beside this file. `resolve` expands `extends` as
`kraft.templates.library._Resolution` and `kraft.templates.extension` do and
returns the chain in Kraft Lite's frozen, JSON-ready shape, every task at the
canonical path Kraft gives it (`implementation.main.implement`).
"""

from __future__ import annotations

import copy
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import kl_shape
import kl_yaml

BUNDLED = Path(__file__).parent / "config"

SECTIONS = ("tasks", "steps", "nodes")
TASK_KINDS = ("agent", "builtin", "forge", "subprocess")
#: What a `kind: builtin` task's `ref` may name (`kraft.templates.models.BuiltinAction`).
BUILTIN_REFS = ("kraft.verify_changed_test_scopes", "kraft.mr_rebase")
#: What a `kind: forge` task may target (`kraft.templates.models.ForgeAction`).
FORGE_TARGETS = (
    "mr.open_draft",
    "mr.sync",
    "mr.ci",
    "mr.automated_review",
    "mr.mark_ready",
    "mr.external_approval",
    "mr.merge",
    "mr.post_merge_ci",
)
#: The task keys the frozen shape carries; absent ones are None.
TASK_KEYS = (
    "prompt",
    "skill",
    "produces",
    "profile",
    "model",
    "effort",
    "ref",
    "target",
    "command",
)
#: The keys an extending chain edits its parent with, and what an override may not set.
EDIT_KEYS = ("remove", "add", "overrides")
IDENTITY = ("id", "kind", "extends", "chain_finalized")
#: An agent task's two routes to a model: the nearer layer's wins whole.
ROUTES = (("profile",), ("model", "effort"))
#: In the repo library, `library:<name>` is its own entry, as a bare name is.
OWN = "library"
REPO = "repo:"
MAIN = "main"
#: Recovery Kraft runs that Lite's frozen shape has no place for: refused at
#: resolve rather than walked past without it.
UNSUPPORTED = "is not supported by Kraft Lite"
#: Where expansion keeps a task's `extends` names, nearest first. Not a YAML key
#: an author can write: it holds a NUL.
_COMPONENTS = "\0components"


class ChainError(SystemExit):
    """A chain that cannot be found or resolved. `detail` is the message
    without the `kraft-lite: ` prefix the CLI prints."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"kraft-lite: {detail}")
        self.detail = detail


@dataclass(frozen=True)
class Library:
    name: str  # "repo" | "user" | "bundled"
    path: Path
    components: dict[str, dict[str, dict]]  # section -> name -> raw
    steering: dict[str, str]
    chains: dict[str, dict]
    #: Chain id -> the file it was read from.
    files: dict[str, Path] = field(default_factory=dict)


# -- finding libraries -------------------------------------------------------


def _mapping(path: Path, data: object, what: str) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ChainError(f"{path}: {what} must be a mapping")
    return data


def _load(name: str, path: Path) -> Library:
    file = path / "library.yaml"
    data = _mapping(file, kl_yaml.read(file), "the file") if file.is_file() else {}
    components: dict[str, dict[str, dict]] = {}
    for section in SECTIONS:
        entries = _mapping(file, data.get(section), f"'{section}'")
        for entry, body in entries.items():
            if not isinstance(body, dict):
                raise ChainError(f"{file}: {section}.{entry} must be a mapping")
            if ":" in str(entry):
                raise ChainError(f"{file}: {section} key {entry!r}: a name is declared bare")
        components[section] = entries
    steering = {}
    for entry, body in _mapping(file, data.get("steering"), "'steering'").items():
        text = body.get("instructions") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text:
            raise ChainError(f"{file}: steering.{entry} needs its 'instructions' text")
        steering[str(entry)] = text
    chains: dict[str, dict] = {}
    files: dict[str, Path] = {}
    for chain_file in sorted((path / "chains").glob("*.yaml")):
        body = _mapping(chain_file, kl_yaml.read(chain_file), "a chain file")
        id = body.get("id") or chain_file.stem
        if not isinstance(id, str) or ":" in id:
            raise ChainError(f"{chain_file}: 'id' must be a bare name, not {id!r}")
        if id in chains:
            raise ChainError(f"{chain_file}: chain id {id!r} is already declared by {files[id]}")
        chains[id], files[id] = body, chain_file
    return Library(name, path, components, steering, chains, files)


def config_dir(env: Mapping[str, str]) -> Path:
    """The user library's directory, as Kraft's `paths.config_dir` picks it: a
    set `KRAFT_CONFIG_DIR` (else the 1.x `KRAFT_TEMPLATES_DIR`), else
    `$KRAFT_HOME/config`, else `~/.kraft/config`. The first one set is the
    one, holding a `library.yaml` or not: Kraft never falls through to the next."""
    named = env.get("KRAFT_CONFIG_DIR") or env.get("KRAFT_TEMPLATES_DIR")
    if named:
        return Path(named).expanduser()
    home = env.get("KRAFT_HOME")
    return (Path(home).expanduser() if home else Path.home() / ".kraft") / "config"


def libraries(root: Path, env: Mapping[str, str]) -> tuple[Library | None, Library]:
    """The repo library in `root/.kraft` (None without one) and the instance's
    library: the user library in `config_dir(env)` when it holds a
    `library.yaml`, else the bundled copy of Kraft's packaged one, which is what
    Kraft seeds an empty one from. A user library replaces the packaged one
    wholesale, as in Kraft."""
    repo_dir = Path(root) / ".kraft"
    has_repo = (repo_dir / "library.yaml").is_file() or (repo_dir / "chains").is_dir()
    repo = _load("repo", repo_dir) if has_repo else None
    user = config_dir(env)
    if (user / "library.yaml").is_file():
        return repo, _load("user", user)
    return repo, bundled_library()


def available(root: Path, env: Mapping[str, str]) -> list[dict]:
    """Every chain id a run can start on, and where it came from."""
    repo, instance = libraries(root, env)
    found = []
    for library, prefix in ((repo, REPO), (instance, "")):
        if library is not None:
            found += [
                {"id": prefix + id, "source": library.name, "path": str(library.files[id])}
                for id in library.chains
            ]
    return found


def default_chain_ref(repo: Library | None) -> str:
    """The chain a run starts on without `--chain`: the repo library's
    `default` when it has one, else the instance's."""
    return f"{REPO}default" if repo is not None and "default" in repo.chains else "default"


def resolve(
    root: Path, env: Mapping[str, str], ref: str | None = None, path: Path | None = None
) -> dict:
    """The chain `ref` (default: `default_chain_ref`), or the chain file at
    `path` resolved against the instance library, in Lite's frozen shape."""
    repo, instance = libraries(root, env)
    if path is not None:
        path = Path(path)
        data = _mapping(path, kl_yaml.read(path), "a chain file")
        id = data.get("id") or path.stem
        # Not one of the library's chains, so `extends: <its own id>` is the library's.
        expanded = _Resolver(instance, path, None).chain(data)
        return _freeze(instance, str(id), str(path), expanded)
    ref = ref or default_chain_ref(repo)
    if ref.startswith(REPO):
        library, id = repo, ref[len(REPO) :]
        if library is None:
            raise ChainError(f"no repo library in {Path(root) / '.kraft'} for chain {ref!r}")
    elif ":" in ref:
        raise ChainError(f"add-on references are not supported by Kraft Lite: {ref}")
    else:
        library, id = instance, ref
    if id not in library.chains:
        raise ChainError(f"no chain {id!r} in {library.path}; known chains: {list(library.chains)}")
    expanded = _Resolver(library, library.files[id], id).chain(library.chains[id])
    return _freeze(library, ref, library.name, expanded)


def bundled_library() -> Library:
    """Kraft's packaged library, whatever user or repo library there is."""
    return _load("bundled", BUNDLED)


def resolve_bundled(ref: str = "default", library: Library | None = None) -> dict:
    """The chain `ref` from the bundled library alone (`library`, when the
    caller loaded it already), in Lite's frozen shape: what `migrate` moves a
    run onto."""
    library = library or bundled_library()
    expanded = _Resolver(library, library.files[ref], ref).chain(library.chains[ref])
    return _freeze(library, ref, library.name, expanded)


# -- merging -----------------------------------------------------------------


def _merge(parent: object, child: object) -> object:
    """`extends`: mappings merge recursively; lists and scalars, an explicit
    null included, replace."""
    if isinstance(parent, dict) and isinstance(child, dict):
        merged = dict(parent)
        for key, value in child.items():
            merged[key] = _merge(parent[key], value) if key in parent else value
        return merged
    return child


def _edit_merge(parent: object, child: object) -> object:
    """An override's merge: `_merge`, except that a container given `tasks`
    loses an inherited `steps`, and the other way round."""
    if isinstance(parent, dict) and isinstance(child, dict):
        merged = dict(parent)
        for key, other in (("tasks", "steps"), ("steps", "tasks")):
            if key in child and other not in child:
                merged.pop(other, None)
        for key, value in child.items():
            merged[key] = _edit_merge(merged[key], value) if key in merged else value
        return merged
    return child


def _displaced(nearer: Mapping) -> tuple[str, ...]:
    """The route keys an inherited task loses under `nearer`."""
    for route, other in (ROUTES, ROUTES[::-1]):
        if any(key in nearer for key in route):
            return other
    return ()


def _identity(value: Mapping, target: Mapping, prefix: str = "") -> list[str]:
    found = [f"{prefix}{key}" for key in IDENTITY if key in value]
    for key, item in value.items():
        if isinstance(item, dict) and isinstance(target.get(key), dict):
            found += _identity(item, target[key], f"{prefix}{key}.")
    return found


def _replaces_a_list(value: object) -> bool:
    return isinstance(value, dict) and any(
        (key in ("tasks", "steps") and isinstance(item, list)) or _replaces_a_list(item)
        for key, item in value.items()
    )


# -- resolving ---------------------------------------------------------------


class _Resolver:
    """One chain file's expansion. `paths` maps each canonical path the
    expansion located to its key path in `{"nodes": [...]}`, which is what a
    chain extending this one addresses with `overrides`."""

    def __init__(self, library: Library, file: Path, name: str | None) -> None:
        self.library, self.file, self.name = library, file, name
        self.paths: dict[str, tuple] = {}

    def fail(self, where: str, message: str) -> ChainError:
        return ChainError(
            f"{self.file}: {where}: {message}" if where else f"{self.file}: {message}"
        )

    def own(self, name: object, where: str) -> str:
        """A reference as this library's own name."""
        if not isinstance(name, str):
            raise self.fail(where, f"a reference names one entry, not {name!r}")
        qualifier, colon, bare = name.partition(":")
        if not colon:
            return name
        if self.library.name == "repo" and qualifier == OWN:
            return bare
        raise self.fail(where, f"add-on references are not supported by Kraft Lite: {name}")

    # A chain, and a chain that extends a chain.

    def chain(self, data: Mapping) -> dict:
        if "extends" in data:
            return self._extend(data)
        if written := [key for key in EDIT_KEYS if key in data]:
            raise self.fail("", f"{written[0]!r} edits a parent chain; add 'extends: <chain id>'")
        nodes = data.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise self.fail("", "'nodes' must be a non-empty list")
        expanded = {"nodes": [self.node(node, i) for i, node in enumerate(nodes)]}
        _Validator(self).chain(expanded["nodes"])
        return expanded

    def _extend(self, data: Mapping) -> dict:
        if "nodes" in data:
            raise self.fail(
                "",
                f"an extending chain holds no 'nodes'; change {data['extends']}'s with remove, "
                "add and overrides, or copy it to own its structure",
            )
        name = self.own(data["extends"], "extends")
        chains = self.library.chains
        if name not in chains:
            raise self.fail("", f"extends no chain named {name!r}; known chains: {list(chains)}")
        seen, at = [self.name or str(self.file)], name
        while isinstance(at, str) and at in chains:
            if at in seen:
                raise self.fail("", f"'extends' cycle: {' -> '.join((*seen, at))}")
            seen.append(at)
            parent_extends = chains[at].get("extends")
            at = self.own(parent_extends, "extends") if parent_extends is not None else None
        parent = _Resolver(self.library, self.library.files[name], name)
        try:
            nodes = parent.chain(chains[name])["nodes"]
        except ChainError as exc:
            raise self.fail("", f"extends {name!r}, which does not resolve: {exc.detail}") from exc
        out, origins, overridden = self._apply(name, nodes, parent.paths, data)
        result = []
        for index, (node, (origin, at)) in enumerate(zip(out, origins, strict=True)):
            if origin == "parent" and node["id"] not in overridden:
                self.paths.update(
                    {
                        p: ("nodes", index, *loc[2:])
                        for p, loc in parent.paths.items()
                        if loc[1] == at
                    }
                )
                result.append(node)
            else:
                result.append(self.node(node, index))
        finals = [n["id"] for n in result if n.get("kind") == "gate" and n.get("chain_finalized")]
        if len(finals) > 1:
            raise self.fail("add", f"{finals[-1]}: the chain already has a final gate")
        _Validator(self).chain(result)
        return {"nodes": result}

    def _apply(self, parent: str, nodes: list, paths: dict, data: Mapping):
        """`remove`, then `overrides` (an ancestor path before its descendants),
        then `add`, as `kraft.templates.extension.apply`."""
        remove, add, overrides = (
            data.get("remove", []),
            data.get("add", []),
            data.get("overrides", {}),
        )
        if not isinstance(remove, list) or not all(isinstance(r, str) for r in remove):
            raise self.fail("remove", "must be a list of node ids")
        if not isinstance(overrides, dict) or not all(
            isinstance(v, dict) for v in overrides.values()
        ):
            raise self.fail("overrides", "must map canonical paths to mappings")
        if not isinstance(add, list) or not all(
            isinstance(a, dict) and set(a) == {"after", "node"} and isinstance(a["node"], dict)
            for a in add
        ):
            raise self.fail("add", "each entry is {after: <node id>, node: <a chain node>}")
        root = {"nodes": copy.deepcopy(nodes)}
        ids = [node.get("id") for node in root["nodes"]]
        final = next(
            (n["id"] for n in nodes if n.get("kind") == "gate" and n.get("chain_finalized")), None
        )
        removed: list[str] = []
        for id in remove:
            if id not in ids:
                raise self.fail(f"remove {id!r}", f"{parent} has no node {id!r}")
            if id == final:
                raise self.fail(
                    f"remove {id!r}",
                    f"it is {parent}'s final gate (chain_finalized), which an extending "
                    "chain keeps",
                )
            if id in removed:
                raise self.fail(f"remove {id!r}", "named twice")
            removed.append(id)
        overridden: dict[str, list[str]] = {}
        replaced: list[str] = []
        for path in sorted(overrides, key=lambda p: p.count(".")):
            value, where, node = overrides[path], f"overrides.{path}", path.split(".", 1)[0]
            if node in removed:
                raise self.fail(where, f"{node!r} is removed by this chain")
            if path not in paths:
                raise self.fail(where, f"{parent} has no {path!r}")
            if under := next((r for r in replaced if path.startswith(f"{r}.")), None):
                raise self.fail(where, f"it sits under overrides {under}, which replaces its list")
            target = root
            for part in paths[path]:
                target = target[part]
            if fixed := _identity(value, target):
                raise self.fail(
                    where,
                    f"an override cannot set {', '.join(fixed)}; remove the node and add "
                    "one instead",
                )
            if target.get("kind") in TASK_KINDS:
                target = {k: v for k, v in target.items() if k not in _displaced(value)}
            merged = _edit_merge(target, value)
            holder = root
            for part in paths[path][:-1]:
                holder = holder[part]
            holder[paths[path][-1]] = merged
            if _replaces_a_list(value):
                replaced.append(path)
            overridden.setdefault(node, []).append(path)
        after: dict[str, list[tuple[int, dict]]] = {}
        anchors: dict[str, str] = {}
        for j, entry in enumerate(add):
            id = entry["node"].get("id")
            if not isinstance(id, str):
                raise self.fail("add", "the node needs an id")
            if id in ids or id in anchors:
                raise self.fail(f"add {id}", f"{parent} already has a node {id!r}")
            if entry["after"] not in ids:
                raise self.fail(f"add {id}", f"{parent} has no node {entry['after']!r}")
            if entry["after"] in removed:
                raise self.fail(f"add {id}", f"{entry['after']!r} is removed by this chain")
            anchors[id] = entry["after"]
            after.setdefault(entry["after"], []).append((j, copy.deepcopy(entry["node"])))
        out, origins = [], []
        for i, node in enumerate(root["nodes"]):
            if node["id"] not in removed:
                out.append(node)
                origins.append(("parent", i))
            for j, new in after.get(node["id"], ()):
                out.append(new)
                origins.append(("add", j))
        for node in out:
            base = node.get("on_base_changed")
            restart = base.get("restart_from") if isinstance(base, dict) else None
            for key, target in (("reject_to", node.get("reject_to")), ("restart_from", restart)):
                if target in removed:
                    raise self.fail(
                        node["id"], f"its {key} names {target!r}, which this chain removes"
                    )
        return out, origins, overridden

    # Components inside a chain, each expanded onto its `extends` parents.

    def expand(self, raw: object, section: str, where: str, seen: tuple = ()) -> dict:
        if not isinstance(raw, dict):
            raise self.fail(where, "must be a mapping")
        child = {k: v for k, v in raw.items() if k != "extends"}
        if "extends" not in raw:
            child.setdefault(_COMPONENTS, [])
            return child
        name = self.own(raw["extends"], f"{where}.extends")
        if name in seen:
            raise self.fail(where, f"'extends' cycle: {' -> '.join((*seen, name))}")
        parent = self.library.components[section].get(name)
        if parent is None:
            other = next((s for s in SECTIONS if name in self.library.components[s]), None)
            what = section[:-1]
            if other is not None:
                raise self.fail(where, f"extends {name!r}, which is a {other[:-1]}, not a {what}")
            raise self.fail(where, f"extends no {what} named {name!r}")
        resolved = self.expand(parent, section, where, (*seen, name))
        inherited, declared = resolved.get("kind"), child.get("kind")
        if inherited is not None and declared is not None and inherited != declared:
            raise self.fail(
                where, f"extends {name!r} and cannot change kind {inherited!r} to {declared!r}"
            )
        if section == "tasks":
            resolved = {k: v for k, v in resolved.items() if k not in _displaced(child)}
        merged = _merge(resolved, child)
        merged[_COMPONENTS] = [name, *resolved[_COMPONENTS]]
        return merged

    def node(self, raw: object, index: int) -> dict:
        loc = ("nodes", index)
        node = self.expand(raw, "nodes", _provisional(raw, "", index))
        path = self._record(node, loc, "", f"nodes[{index}]")
        node = self._container(node, path, loc)
        for key in ("on_failure", "fix_loop"):
            if isinstance(node.get(key), dict):
                node[key] = self._container(node[key], f"{path}.{key}", (*loc, key))
        if isinstance(node.get("escalation"), dict):
            node["escalation"] = self._task(
                node["escalation"], f"{path}.escalation", (*loc, "escalation")
            )
        if isinstance(node.get("auto_review"), dict):
            node["auto_review"] = self._task(
                node["auto_review"], path, (*loc, "auto_review"), "auto_review"
            )
        base = node.get("on_base_changed")
        if isinstance(base, dict) and isinstance(base.get("on_conflict"), dict):
            where = f"{path}.on_base_changed.on_conflict"
            conflict = self._container(
                base["on_conflict"], where, (*loc, "on_base_changed", "on_conflict")
            )
            node["on_base_changed"] = {**base, "on_conflict": conflict}
        return node

    def _container(self, raw: dict, path: str, loc: tuple, step: bool = False) -> dict:
        container = dict(raw)
        if isinstance(container.get("tasks"), list):
            prefix = path if step else f"{path}.{MAIN}"
            container["tasks"] = [
                self._task(t, prefix, (*loc, "tasks", i)) for i, t in enumerate(container["tasks"])
            ]
        if isinstance(container.get("steps"), list):
            container["steps"] = [
                self._step(s, path, i, (*loc, "steps", i)) for i, s in enumerate(container["steps"])
            ]
        if isinstance(container.get("judge"), dict):
            container["judge"] = self._task(container["judge"], path, (*loc, "judge"), "judge")
        return container

    def _step(self, raw: object, prefix: str, index: int, loc: tuple) -> dict:
        step = self.expand(raw, "steps", _provisional(raw, prefix, index))
        path = self._record(step, loc, prefix, f"steps[{index}]")
        return self._handler(self._container(step, path, loc, step=True), path, loc)

    def _task(self, raw: object, prefix: str, loc: tuple, segment: str | None = None) -> dict:
        where = f"{prefix}.{segment}" if segment else _provisional(raw, prefix, 0)
        task = self.expand(raw, "tasks", where)
        if isinstance(task.get("steering"), list):
            task["steering"] = [self.own(n, f"{where}.steering") for n in task["steering"]]
        if segment is not None:
            self.paths[where] = loc
            return task
        path = self._record(task, loc, prefix, "task")
        return self._handler(task, path, loc)

    def _handler(self, component: dict, path: str, loc: tuple) -> dict:
        if isinstance(component.get("on_failure"), dict):
            where = f"{path}.on_failure"
            component["on_failure"] = self._container(
                component["on_failure"], where, (*loc, "on_failure")
            )
        return component

    def _record(self, component: dict, loc: tuple, prefix: str, fallback: str) -> str:
        id = component.get("id")
        tail = id if isinstance(id, str) else fallback
        path = f"{prefix}.{tail}" if prefix else tail
        self.paths[path] = loc
        return path


def _provisional(raw: object, prefix: str, index: int) -> str:
    id = raw.get("id") if isinstance(raw, dict) else None
    tail = id if isinstance(id, str) else f"[{index}]"
    return f"{prefix}.{tail}" if prefix else tail


class _Validator:
    """What Kraft's typed models refuse that Kraft Lite would otherwise run
    wrongly: kinds, the one execution shape, refs, targets, steering, and the
    backward `reject_to` / `restart_from` pointers."""

    def __init__(self, resolver: _Resolver) -> None:
        self.fail, self.library = resolver.fail, resolver.library

    def chain(self, nodes: list) -> None:
        seen: dict[str, str] = {}
        for node in nodes:
            id = node.get("id")
            if not isinstance(id, str):
                raise self.fail("nodes", "every node needs an id")
            if id in seen:
                raise self.fail(id, "duplicate node id")
            kind = node.get("kind")
            for key in ("on_failure", "fix_loop", "on_base_changed"):
                if node.get(key) is not None and not isinstance(node[key], dict):
                    raise self.fail(f"{id}.{key}", "must be a mapping")
            if kind == "exec":
                self.shape(node, id)
                # Kraft refuses it at load: trim could not drop the node cleanly.
                groups = node.get("steps") or [{"tasks": node["tasks"]}]
                produces = {task.get("produces") for step in groups for task in step["tasks"]}
                if len(produces) > 1:
                    raise self.fail(
                        id,
                        f"node {id!r} does not agree on what it produces "
                        f"({sorted(k or '(none)' for k in produces)}); a node either wholly "
                        "produces one kind or declares none",
                    )
                policy = node.get("policy")
                if policy is not None:
                    if not isinstance(policy, dict):
                        raise self.fail(f"{id}.policy", "must be a mapping")
                    self.count(policy.get("max_attempts"), f"{id}.policy.max_attempts")
                for key in ("on_failure", "fix_loop"):
                    if isinstance(node.get(key), dict):
                        self.shape(node[key], f"{id}.{key}")
                self.loop(node.get("fix_loop") or {}, f"{id}.fix_loop")
                if isinstance(node.get("escalation"), dict):
                    escalation = node["escalation"]
                    self.task(escalation, f"{id}.escalation.{escalation.get('id')}")
                base = node.get("on_base_changed")
                if base is not None:
                    if base.get("on_conflict") is not None:
                        raise self.fail(
                            f"{id}.on_base_changed.on_conflict", f"on_conflict {UNSUPPORTED}"
                        )
                    target = base.get("restart_from")
                    if seen.get(target) != "exec" and target != id:
                        raise self.fail(
                            id,
                            f"on_base_changed.restart_from {target!r} must name this or an "
                            "earlier execution node",
                        )
            elif kind == "gate":
                if isinstance(node.get("auto_review"), dict):
                    self.task(node["auto_review"], f"{id}.auto_review")
                target = node.get("reject_to")
                if target is not None and seen.get(target) != "exec":
                    raise self.fail(id, f"reject_to {target!r} must name an earlier execution node")
            else:
                raise self.fail(id, f"kind must be 'exec' or 'gate', not {kind!r}")
            seen[id] = kind

    def loop(self, loop: dict, path: str) -> None:
        """A fix loop's judge is a task with an id, and its cap a count: the
        walk reads both (`kraft.templates.models.FixLoop`)."""
        judge = loop.get("judge")
        if judge is not None:
            if not isinstance(judge, dict) or not isinstance(judge.get("id"), str):
                raise self.fail(f"{path}.judge", "must be a task with an id")
            self.task(judge, f"{path}.judge")
        self.count(loop.get("max_attempts"), f"{path}.max_attempts")

    def count(self, cap: object, path: str) -> None:
        """A fix-loop cap, the loop's or the node policy's: absent, or a count."""
        # `bool` is an `int`, and `true` would be read as one attempt.
        if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or cap < 1):
            raise self.fail(path, f"must be a whole number above 0, not {cap!r}")

    def shape(self, container: dict, path: str) -> None:
        present = [key for key in ("tasks", "steps") if container.get(key) is not None]
        if len(present) != 1:
            raise self.fail(path, f"define exactly one of 'tasks' or 'steps', not {present}")
        items = container[present[0]]
        if not isinstance(items, list) or not items:
            raise self.fail(path, f"'{present[0]}' must be a non-empty list")
        steps = items if present == ["steps"] else [{"id": MAIN, "tasks": items}]
        _unique(self.fail, path, "step", steps)
        for step in steps:
            where = f"{path}.{step['id']}"
            if not isinstance(step.get("tasks"), list) or not step["tasks"]:
                raise self.fail(where, "'tasks' must be a non-empty list")
            _unique(self.fail, where, "task", step["tasks"])
            for task in step["tasks"]:
                self.task(task, f"{where}.{task['id']}")
            if step.get("on_failure") is not None:
                raise self.fail(f"{where}.on_failure", f"a step's own on_failure {UNSUPPORTED}")

    def task(self, task: dict, path: str) -> None:
        kind = task.get("kind")
        if kind not in TASK_KINDS:
            raise self.fail(path, f"kind must be one of {list(TASK_KINDS)}, not {kind!r}")
        if kind == "builtin" and task.get("ref") not in BUILTIN_REFS:
            raise self.fail(
                path, f"unknown builtin ref {task.get('ref')!r}; known: {list(BUILTIN_REFS)}"
            )
        if kind == "forge" and task.get("target") not in FORGE_TARGETS:
            raise self.fail(
                path, f"unknown forge target {task.get('target')!r}; known: {list(FORGE_TARGETS)}"
            )
        needs = {"agent": "prompt", "subprocess": "command"}.get(kind)
        if needs and not isinstance(task.get(needs), str):
            raise self.fail(path, f"a {kind} task needs its '{needs}'")
        if kind == "subprocess":
            # Split as Kraft splits it to run it; one it cannot split stops here.
            try:
                argv = shlex.split(task["command"])
            except ValueError as error:
                raise self.fail(
                    path, f"cannot split command {task['command']!r}: {error}"
                ) from None
            if not argv:
                raise self.fail(path, "a subprocess task needs its 'command'")
        for name in task.get("steering") or ():
            if name not in self.library.steering:
                raise self.fail(path, f"selects no steering profile {name!r}")
        if task.get("on_failure") is not None:
            raise self.fail(f"{path}.on_failure", f"a task's own on_failure {UNSUPPORTED}")


def _unique(fail, path: str, what: str, items: list) -> None:
    """Sibling ids, each a string and none twice: what makes a canonical path
    name one component."""
    seen = set()
    for item in items:
        id = item.get("id")
        if not isinstance(id, str):
            raise fail(path, f"every {what} needs an id")
        if id in seen:
            raise fail(path, f"duplicate {what} id {id!r}")
        seen.add(id)


# -- the frozen shape ---------------------------------------------------------


def _freeze(library: Library, chain_id: str, source: str, expanded: dict) -> dict:
    def task(raw: dict, path: str) -> dict:
        frozen = {
            "id": raw["id"],
            "path": path,
            "kind": raw["kind"],
            "components": list(raw.get(_COMPONENTS, [])),
            **{key: raw.get(key) for key in TASK_KEYS},
            "steering": [library.steering[name] for name in raw.get("steering") or ()],
        }
        if raw["kind"] == "subprocess":
            # A string in the chain, as Kraft's `StrictStr`; split like a shell
            # and run without one, as Kraft runs it (`shlex.split`).
            frozen["command"] = shlex.split(raw["command"])
        return frozen

    def steps(container: dict, prefix: str) -> list[dict]:
        groups = container.get("steps") or [{"id": MAIN, "tasks": container["tasks"]}]
        return [
            {
                "id": step["id"],
                "tasks": [task(t, f"{prefix}.{step['id']}.{t['id']}") for t in step["tasks"]],
            }
            for step in groups
        ]

    nodes = []
    for node in expanded["nodes"]:
        id = node["id"]
        if node["kind"] == "gate":
            nodes.append(
                {
                    "id": id,
                    "kind": "gate",
                    "message": node.get("message"),
                    "artifact": node.get("artifact"),
                    "reject_to": node.get("reject_to"),
                    "chain_finalized": node.get("chain_finalized") is True,
                }
            )
            continue
        failure, loop, base = (
            node.get("on_failure"),
            node.get("fix_loop"),
            node.get("on_base_changed"),
        )
        # Of the node's policy, only what caps its fix loop: Lite enforces no other.
        cap = (node.get("policy") or {}).get("max_attempts")
        nodes.append(
            {
                "id": id,
                "kind": "exec",
                "policy": {"max_attempts": cap} if cap is not None else None,
                "steps": steps(node, id),
                "on_failure": {"steps": steps(failure, f"{id}.on_failure")} if failure else None,
                "fix_loop": {
                    "steps": steps(loop, f"{id}.fix_loop"),
                    "judge": task(loop["judge"], f"{id}.fix_loop.judge")
                    if loop.get("judge")
                    else None,
                    "max_attempts": loop.get("max_attempts"),
                }
                if loop
                else None,
                "on_base_changed": {"restart_from": base["restart_from"]} if base else None,
            }
        )
    return {"format": "v1", "id": chain_id, "source": source, "nodes": nodes}


def _covered_by(node: dict) -> str | None:
    """The attachment kind that makes `node` redundant: a gate's `artifact`
    (never a `chain_finalized` gate's), or the one kind every task in an exec
    node's own steps produces (`kraft.templates.models.ResolvedNode.covered_by`)."""
    if node["kind"] == "gate":
        return None if node["chain_finalized"] else node["artifact"]
    produces = {task["produces"] for task in kl_shape.node_tasks(node)}
    return produces.pop() if len(produces) == 1 else None


def restart_target(chain: dict, kept: list[dict], target: str) -> str:
    """Where a kept node's `on_base_changed.restart_from` points once `chain`
    is cut down to `kept`: `target` when it is kept, else the nearest kept
    exec node before it, else the first kept exec node."""
    kept_exec = [node["id"] for node in kept if node["kind"] == "exec"]
    if target in kept_exec:
        return target
    ids = [node["id"] for node in chain["nodes"]]
    before = [id for id in kept_exec if ids.index(id) < ids.index(target)]
    return before[-1] if before else kept_exec[0]


def trim(chain: dict, kinds: set[str]) -> dict:
    """`chain` without the nodes an attachment of each kind in `kinds` makes
    redundant, as Kraft's `ResolvedChain.trim_for_attachments`: a surviving
    gate whose `reject_to` named a dropped node loses the target, and falls
    back as a gate that declared none. A surviving `restart_from` naming a
    dropped node moves by `restart_target`."""
    dropped = {node["id"] for node in chain["nodes"] if _covered_by(node) in kinds}
    if not dropped:
        return chain
    if len(dropped) == len(chain["nodes"]):
        raise ChainError(
            f"dropping {sorted(dropped)} would leave chain {chain['id']!r} with no nodes"
        )
    kept = [node for node in chain["nodes"] if node["id"] not in dropped]

    def cut(node: dict) -> dict:
        if node["kind"] == "gate":
            return dict(node, reject_to=None) if node["reject_to"] in dropped else node
        base = node.get("on_base_changed")
        if base and base["restart_from"] in dropped:
            target = restart_target(chain, kept, base["restart_from"])
            return dict(node, on_base_changed={"restart_from": target})
        return node

    return dict(chain, nodes=[cut(node) for node in kept])
