"""The draft ops: every model-changing action of the Templates editor as one
server op (`reference/kraft-templates-actions.js`), applied to the authored
mapping `kraft.drafts.authored` loads and written back with its `dump`.

`apply` runs one request's ops in order on a copy of the draft's files, all or
nothing: an op that cannot apply raises `OpError` and the request saves
nothing. A reference an op breaks is left dangling, a problem the resolve
result reports, never re-targeted (Decisions §9).
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Mapping

import yaml
from pydantic import TypeAdapter, ValidationError
from yaml import YAMLError

from kraft.drafts import authored, resolve, store
from kraft.plugins import load as plugins_load
from kraft.templates import catalogue, positions
from kraft.templates.environment import Identifier
from kraft.templates.library import (
    LIBRARY_FILE,
    Namespace,
    TemplateLibrary,
    TemplateLibraryError,
)
from kraft.templates.models import (
    AUTO_REVIEW_SEGMENT,
    JUDGE_SEGMENT,
    MAIN_STEP,
    PATH_SEPARATOR,
    RESERVED_SEGMENTS,
    TaskKind,
)

_IDENTIFIER = TypeAdapter(Identifier)
#: A named slot's task id, by slot (the prototype's `addTask`).
_SLOT_IDS = {JUDGE_SEGMENT: "judge", "escalation": "escalate", AUTO_REVIEW_SEGMENT: "reviewer"}
#: What `add_handler` and `remove_handler` take, as the key it sets.
_HANDLERS = ("on_failure", "fix_loop", "on_conflict")
#: What `remove` drops as a key of its container rather than a list entry.
_SLOTS = ("fix_loop", JUDGE_SEGMENT, "escalation", AUTO_REVIEW_SEGMENT)
#: What `reset_field` with no field keeps (the prototype's `resetAll`).
_KEPT = ("id", "extends", "icon", "on_failure")


class OpError(Exception):
    """An op that cannot apply: the request answers 422 and saves nothing."""

    #: The failing op's position in its request, set by `apply`.
    index: int | None = None

    def __init__(self, message: str, **extra: object) -> None:
        super().__init__(message)
        #: More of the 422's body: a fragment's `line` and `col`.
        self.extra = extra


class PluginReadOnly(OpError):
    """An op on a plugin's own chain or component: the request answers 409."""


def _check_id(id: object, taken) -> str:
    try:
        _IDENTIFIER.validate_python(id)
    except ValidationError:
        raise OpError(
            f"{id!r} is not an id: lowercase letters, digits, _ and -, starting with a letter"
        ) from None
    if id in RESERVED_SEGMENTS:
        raise OpError(f"{id!r} is a reserved word")
    if id in taken:
        raise OpError(f"{id!r} is already taken here")
    return id


def _unique(base: str, taken) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def plugin_components(st) -> dict[str, dict]:
    """The loaded plugins' components by section, under their qualified names
    (`release:base`): what a draft reads through and never writes. Kept with
    the library it was read from, which a reload replaces."""
    library = getattr(st, "library", None)
    if library is None or not library.plugins:
        return {}
    cached = getattr(st, "_plugin_components", None)
    if cached is not None and cached[0] is library:
        return cached[1]
    out: dict[str, dict] = {}
    for component in catalogue.components(library, []):
        if component["plugin"] is not None:
            out.setdefault(component["kind"], {})[component["name"]] = plain(
                component["definition"]
            )
    st._plugin_components = (library, out)
    return out


def with_plugins(st, own: object) -> dict:
    """The library mapping `own` (normalised, as `authored.load` gives it)
    with the plugins' components beside its own: what an `extends` is read
    through. A copy of theirs, so owning what a node inherits changes nothing."""
    return _beside(own, authored.normalise(copy.deepcopy(plugin_components(st))))


def _beside(own: object, shipped: Mapping[str, dict]) -> dict:
    """A library mapping with the plugins' components in each section."""
    merged = dict(own) if isinstance(own, dict) else {}
    for section, components in shipped.items():
        entries = merged.get(section)
        merged[section] = {**(entries if isinstance(entries, dict) else {}), **components}
    return merged


def _ids(items: object) -> list:
    return [x.get("id") for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _index(items: list, id: str) -> int:
    return next(i for i, x in enumerate(items) if isinstance(x, dict) and x.get("id") == id)


def _position(at: object, size: int) -> int:
    if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at <= size:
        raise OpError(f"position {at!r} is outside 0..{size}")
    return at


def _join(*parts: str) -> str:
    return PATH_SEPARATOR.join(p for p in parts if p)


class Draft:
    """One request's working copy of a `chains` or `library` draft's files.
    The file the ops address is `chain`: the library's own mapping on a
    `library` draft, whose paths start with the section."""

    def __init__(
        self,
        st,
        key: str,
        files: Mapping[str, str | None],
        *,
        exists: bool,
        area: str = "chains",
    ) -> None:
        self.st = st
        self.files = dict(files)
        self.file = authored.LIBRARY if area == "library" else authored.CHAIN
        if self.file == authored.LIBRARY:
            self.name, self.key = LIBRARY_FILE, key
        else:
            self.name = resolve.chain_file(key, self.files)
            self.key = self.name.removeprefix("chains/").removesuffix(".yaml")
        #: Whether the key had a file or a draft before this request.
        self.exists = exists
        self.dirty: set[str] = set()
        library = self.files.get(LIBRARY_FILE)
        if library is None:
            library = resolve.published(st.templates_dir, [LIBRARY_FILE])[LIBRARY_FILE]
        try:
            # As written, for `_one_shape`; normalised, for what an op owns.
            self.library_raw, self.library = authored.parse(library), authored.load(library)
        except yaml.YAMLError:
            self.library_raw = self.library = {}
        #: The plugins' components in the shape `self.library` has, to read through.
        self._shipped = authored.normalise(copy.deepcopy(plugin_components(st)))
        if self.file == authored.LIBRARY:
            self._chain: object = self.library
        else:
            self._chain = authored.load(self.files.get(self.name))

    # ── addressing ──

    @property
    def parents(self) -> Mapping:
        """What an `extends` may name: the operator's library and, beside it,
        each loaded plugin's components. Built per use, since an op adds to the
        operator's own; a plugin's names are qualified, so none collides."""
        return _beside(self.library, self._shipped)

    @property
    def chain(self) -> dict:
        # Every op that reaches the chain writes it.
        if not isinstance(self._chain, dict):
            raise OpError(f"there is no chain {self.key!r}; create it with new_chain")
        self.dirty.add(self.name)
        return self._chain

    def nodes(self) -> list:
        self.chains_only("a node")
        if not isinstance(self.chain.get("nodes"), list):
            authored.put(self.chain, "", "nodes", [])
        return self.chain["nodes"]

    def chains_only(self, what: str) -> None:
        if self.file == authored.LIBRARY:
            raise OpError(
                f"{what} is a chain's, not the library's; add a component with add_component"
            )

    def library_only(self, what: str) -> None:
        if self.file != authored.LIBRARY:
            raise OpError(f"{what} is the library draft's")

    def at(self, path: str) -> dict:
        """The component at `path`, owning every container it inherits on the way."""
        found = authored.at(self.chain, path, file=self.file, library=self.parents, write=True)
        if found is None:
            raise OpError(f"nothing at {path!r}")
        return found

    def own(self, path: str, key: str) -> object:
        try:
            return authored.own(self.chain, path, key, file=self.file, library=self.parents)
        except KeyError:
            raise OpError(f"nothing at {path!r}") from None

    def items(self, path: str, key: str) -> list:
        """The `steps` or `tasks` list at `path`, owned, made empty when absent."""
        found = self.own(path, key)
        if not isinstance(found, list):
            self.put(path, key, [])
            found = self.at(path)[key]
        return found

    def put(self, path: str, field: str, value: object) -> None:
        self.at(path)
        authored.put(self.chain, path, field, value, file=self.file, library=self.parents)

    def base_node(self, name: object) -> Mapping:
        return authored.inherited(self.parents, Namespace.NODES, name)

    def member(self, path: str) -> tuple[str, list]:
        """Whether `path` is a node, a step or a task, and the list it sits in."""
        parent, _, id = path.rpartition(PATH_SEPARATOR)
        if not parent:
            if id in _ids(self.nodes()):
                return "node", self.nodes()
            raise OpError(f"no node {id!r}")
        for key in ("steps", "tasks"):
            items = self.own(parent, key)
            if id in _ids(items):
                return key[:-1], items
        raise OpError(f"no step or task at {path!r}")

    def drop(self, path: str, key: str) -> None:
        """Remove `key` at `path`: written as null when the component would
        still inherit one, deleted otherwise."""
        self.at(path).pop(key, None)
        if (self.inherited(path) or {}).get(key) is not None:
            self.put(path, key, None)

    def inherited(self, path: str, chain: Mapping | None = None) -> dict | None:
        """The component at `path` of `expanded(chain)`."""
        return authored.at(self.expanded(chain), path, file=self.file)

    def expanded(self, chain: Mapping | None = None) -> dict:
        """The chain as it stands (or `chain` in its place), `extends` expanded
        and the shorthand normalised; as written where it does not expand.
        On the library, each component expanded on its own."""
        raw = authored.parse(authored.dump(self.chain if chain is None else chain))
        if self.file == authored.LIBRARY:
            return authored.normalise(_expand_library(raw, self.st.templates_dir / self.name))
        library = getattr(self.st, "library", None)
        if LIBRARY_FILE in self.files or LIBRARY_FILE in self.dirty:
            # As written: `parse(dump(…))` puts the shorthand back.
            library = resolve.draft_library(self.st, authored.parse(authored.dump(self.library)))
        data, _ = resolve._expand(library, self.st.templates_dir / self.name, self.key, raw)
        return authored.normalise(dict(data))

    # ── writing ──

    def finish(self) -> dict[str, str | None]:
        """The files with every one an op touched written back by `dump`."""
        if self.name in self.dirty:
            if not isinstance(self._chain, dict):
                self.files[self.name] = None
            else:
                if self.file == authored.CHAIN:
                    self._one_shape()
                self.files[self.name] = authored.dump(self._chain)
        if self.file == authored.CHAIN and LIBRARY_FILE in self.dirty:
            self.files[LIBRARY_FILE] = authored.dump(self.library)
        return self.files

    def _one_shape(self) -> None:
        """A node extending a library node merges onto it key by key, so a
        container written as `steps:` over the parent's `tasks:`, or as the
        shorthand over its `steps:`, would hold both and fail to load. Such a
        container writes `tasks: null` beside its steps.

        ponytail: node-level containers only; a step's or a task's own
        `on_failure` under a library parent is not checked."""
        for node in _dicts(self._chain.get("nodes")):
            id = node.get("id")
            if not (isinstance(id, str) and node.get("extends")):
                continue
            written = _beside(self.library_raw, plugin_components(self.st))
            parent = authored.inherited(written, Namespace.NODES, node["extends"])
            base_change = node.get("on_base_changed")
            containers = {
                id: node,
                _join(id, "on_failure"): node.get("on_failure"),
                _join(id, "fix_loop"): node.get("fix_loop"),
                _join(id, "on_base_changed", "on_conflict"): (
                    base_change.get("on_conflict") if isinstance(base_change, dict) else None
                ),
            }
            for path, mine in containers.items():
                if not isinstance(mine, dict) or "tasks" in mine or mine.get("steps") is None:
                    continue
                theirs: object = parent
                for seg in path.split(PATH_SEPARATOR)[1:]:
                    theirs = theirs.get(seg) if isinstance(theirs, Mapping) else None
                other = "steps" if authored.collapses(mine) else "tasks"
                if isinstance(theirs, Mapping) and theirs.get(other) is not None:
                    authored.put(self._chain, path, "tasks", None, library=self.parents)


def _dicts(value: object) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _expand_library(raw: object, path) -> object:
    """Each library component of `raw` merged onto its `extends` parents and
    its nested components onto theirs, as a chain using it would see it."""
    if not isinstance(raw, dict):
        return raw
    # Steering expands nothing; an unwritten profile must not stop the rest.
    components = {k: v for k, v in raw.items() if k != Namespace.STEERING}
    try:
        library = TemplateLibrary.from_mappings(
            components, (), library_path=path, plugins=plugins_load.installed(path.parent)
        )
    except TemplateLibraryError:
        return raw
    out = dict(raw)
    for namespace in (Namespace.NODES, Namespace.STEPS, Namespace.TASKS):
        expanded = {}
        for name in library.component_names(namespace):
            # A chain of one node that uses the component, read back out.
            use: dict = {"id": name, "extends": name}
            if namespace is Namespace.TASKS:
                use = {"id": "_", "tasks": [use]}
            if namespace is not Namespace.NODES:
                use = {"id": "_", "steps": [use]}
            data, _ = resolve._expand(library, path, "_", {"nodes": [use]})
            found = data["nodes"][0]
            if namespace is not Namespace.NODES:
                found = found["steps"][0]
            if namespace is Namespace.TASKS:
                found = found["tasks"][0]
            # Expansion drops `extends`: still there, it stopped part-way.
            expanded[name] = raw[namespace.value][name] if "extends" in found else found
        out[namespace.value] = expanded
    return out


# ── order and references ──


def _produces(node: Mapping) -> list:
    if node.get("kind") == "gate":
        return []
    return [t.get("produces") for s in _dicts(node.get("steps")) for t in _dicts(s.get("tasks"))]


def _restart_from(node: Mapping) -> object:
    base_change = node.get("on_base_changed")
    return base_change.get("restart_from") if isinstance(base_change, Mapping) else None


def order_issues(nodes: list[Mapping]) -> list[str]:
    """The prototype's `orderIssues`: what a node order breaks, over expanded nodes."""
    index = {n.get("id"): i for i, n in enumerate(nodes)}
    made = [_produces(n) for n in nodes]
    out = []
    for i, n in enumerate(nodes):
        id = n.get("id")
        if n.get("kind") == "gate":
            back, artifact = n.get("reject_to"), n.get("artifact")
            if back in index and index[back] >= i:
                out.append(f"{id} rejects to {back}, which would come after it")
            if (
                artifact
                and not any(artifact in m for m in made[:i])
                and any(artifact in m for m in made)
            ):
                out.append(f"{id} shows {artifact}, which no earlier node would produce")
        elif (restart := _restart_from(n)) in index and index[restart] > i:
            out.append(f"{id} restarts from {restart}, which would come after it")
    return out


def references(nodes: list[Mapping], id: str) -> list[dict]:
    """The prototype's `refsTo`: every `reject_to` and `restart_from` naming node `id`."""
    out = []
    for n in nodes:
        if n.get("kind") == "gate":
            if n.get("reject_to") == id:
                out.append({"path": n.get("id"), "field": "reject_to"})
        elif _restart_from(n) == id and n.get("id") != id:
            out.append({"path": n.get("id"), "field": "on_base_changed.restart_from"})
    return out


def _id(obj: dict) -> str:
    id = obj.get("id")
    return id if isinstance(id, str) else ""


def _components(data: Mapping, file: str) -> list[tuple[dict, Namespace, str]]:
    """Every node, step and task of an authored file (`authored.load`'s) with
    the namespace its `extends` names and its canonical path."""
    out: list[tuple[dict, Namespace, str]] = []

    def task(t: dict, path: str) -> None:
        out.append((t, Namespace.TASKS, path))
        shape(t.get("on_failure"), _join(path, "on_failure"))

    def step(s: dict, path: str) -> None:
        out.append((s, Namespace.STEPS, path))
        for t in _dicts(s.get("tasks")):
            task(t, _join(path, _id(t)))
        shape(s.get("on_failure"), _join(path, "on_failure"))

    def shape(c: object, path: str) -> None:
        if not isinstance(c, dict):
            return
        for s in _dicts(c.get("steps")):
            step(s, _join(path, _id(s)))
        if isinstance(c.get(JUDGE_SEGMENT), dict):
            task(c[JUDGE_SEGMENT], _join(path, JUDGE_SEGMENT))

    def node(n: dict, path: str) -> None:
        out.append((n, Namespace.NODES, path))
        shape(n, path)
        for key in ("on_failure", "fix_loop"):
            shape(n.get(key), _join(path, key))
        if isinstance(escalation := n.get("escalation"), dict):
            task(escalation, _join(path, "escalation", _id(escalation)))
        if isinstance(n.get(AUTO_REVIEW_SEGMENT), dict):
            task(n[AUTO_REVIEW_SEGMENT], _join(path, AUTO_REVIEW_SEGMENT))
        if isinstance(base_change := n.get("on_base_changed"), dict):
            shape(base_change.get("on_conflict"), _join(path, "on_base_changed", "on_conflict"))

    if file == authored.CHAIN:
        for n in _dicts(data.get("nodes")):
            node(n, _id(n))
        return out
    for namespace, walk in (
        (Namespace.NODES, node),
        (Namespace.STEPS, step),
        (Namespace.TASKS, task),
    ):
        entries = data.get(namespace.value)
        for name, body in entries.items() if isinstance(entries, dict) else ():
            if isinstance(body, dict):
                walk(body, _join(namespace.value, str(name)))
    return out


def _chain_files(d: Draft) -> dict[str, dict]:
    """Every chain file as the draft has it, authored."""
    names = {f"chains/{p.name}" for p in (d.st.templates_dir / "chains").glob("*.yaml")}
    names |= {n for n in d.files if n.startswith("chains/")}
    published = resolve.published(d.st.templates_dir, names - set(d.files))
    out = {}
    for name in sorted(names):
        try:
            data = authored.load(d.files[name] if name in d.files else published[name])
        except YAMLError:
            continue  # not this draft's to rewrite; the chain's own problem
        if isinstance(data, dict):
            out[name] = data
    return out


def _uses(files: Mapping[str, dict], section: str, name: str) -> list[tuple[str, dict, str, str]]:
    """Every `extends` and `steering` entry naming library component
    `section.name`: (file, the component holding it, its path, the field)."""
    out = []
    for file, data in files.items():
        kind = authored.LIBRARY if file == LIBRARY_FILE else authored.CHAIN
        for obj, namespace, path in _components(data, kind):
            if section == Namespace.STEERING:
                steering = obj.get("steering")
                if namespace is Namespace.TASKS and isinstance(steering, list) and name in steering:
                    out.append((file, obj, path, "steering"))
            elif namespace == section and obj.get("extends") == name:
                out.append((file, obj, path, "extends"))
    return out


def _entry(d: Draft, path: str) -> tuple[dict, str] | None:
    """On a library draft, the section holding component `path` when it is
    one (`tasks.implementer`), and its name."""
    section, _, name = path.partition(PATH_SEPARATOR)
    if d.file != authored.LIBRARY or PATH_SEPARATOR in name or section not in set(Namespace):
        return None
    entries = d.chain.get(section)
    if not isinstance(entries, dict) or name not in entries:
        raise OpError(f"nothing at {path!r}")
    return entries, name


def _rename_component(d: Draft, path: str, id: str) -> dict:
    """A library component, and every `extends` or `steering` naming it, in
    the library and in each chain file, which joins the draft."""
    entries, old = _entry(d, path)
    section = path.partition(PATH_SEPARATOR)[0]
    _check_id(id, entries)
    chains = _chain_files(d)
    uses = _uses({LIBRARY_FILE: d.chain, **chains}, section, old)
    authored._rekey(entries, old, id, entries[old])
    for _, obj, _, field in uses:
        if field == "extends":
            obj["extends"] = id
        else:
            obj["steering"] = [id if n == old else n for n in obj["steering"]]
    for name in {file for file, *_ in uses} - {LIBRARY_FILE}:
        d.files[name] = authored.dump(chains[name])
        d.dirty.add(name)
    return {"updated": [{"file": f, "path": p, "field": field} for f, _, p, field in uses]}


# ── the ops ──


def add_node(d: Draft, at: int, id: str, kind: str) -> None:
    nodes = d.nodes()
    _check_id(id, _ids(nodes))
    if kind not in ("exec", "gate"):
        raise OpError(f"a node is exec or gate, not {kind!r}")
    # An exec node with no step is a problem until it has one.
    node = {"id": id, "kind": kind} if kind == "gate" else {"id": id, "kind": kind, "steps": []}
    nodes.insert(_position(at, len(nodes)), node)


def add_step(d: Draft, container: str, at: int, id: str | None = None) -> dict:
    steps = d.items(container, "steps")
    if _ids(steps) == [MAIN_STEP]:
        steps[0]["id"] = _unique("step_1", _ids(steps))
    taken = _ids(steps)
    id = _check_id(id, taken) if id is not None else _unique(f"step_{len(steps) + 1}", taken)
    steps.insert(_position(at, len(steps)), {"id": id, "tasks": []})
    return {"path": _join(container, id)}


def _new_task(id_base: str | None, kind: str | None, extends: str | None, taken) -> dict:
    if (kind is None) == (extends is None):
        raise OpError("give exactly one of kind and extends")
    if kind is not None and kind not in set(TaskKind):
        raise OpError(f"no task kind {kind!r}")
    # A plugin's component is `release:base`; the task it becomes is `base`.
    id = (
        id_base
        if id_base is not None
        else _unique((kind or extends).rpartition(":")[2], [*taken, *RESERVED_SEGMENTS])
    )
    return {"id": id, "kind": kind} if kind else {"id": id, "extends": extends}


def add_task(
    d: Draft,
    container: str | None = None,
    step: str | None = None,
    kind: str | None = None,
    extends: str | None = None,
    id: str | None = None,
    slot: str | None = None,
    node: str | None = None,
) -> dict:
    if slot is not None:
        if slot not in _SLOT_IDS or node is None:
            raise OpError("a slot is judge, escalation or auto_review, with its node")
        task = _new_task(_SLOT_IDS[slot], kind, extends, ())
        if slot == JUDGE_SEGMENT:
            if not isinstance(d.own(node, "fix_loop"), dict):
                raise OpError(f"{node} has no fix loop to judge")
            d.put(_join(node, "fix_loop"), JUDGE_SEGMENT, task)
            return {"path": _join(node, "fix_loop", JUDGE_SEGMENT)}
        d.put(node, slot, task)
        tail = task["id"] if slot == "escalation" else ""
        return {"path": _join(node, slot, tail)}
    if container is None or step is None:
        raise OpError("give container and step, or slot and node")
    tasks = d.items(_join(container, step), "tasks")
    taken = _ids(tasks)
    task = _new_task(id if id is None else _check_id(id, taken), kind, extends, taken)
    tasks.append(task)
    return {"path": _join(container, step, task["id"])}


def _handler_at(path: str, kind: str) -> tuple[str, str]:
    if kind not in _HANDLERS:
        raise OpError(f"a handler is one of {', '.join(_HANDLERS)}, not {kind!r}")
    return (
        (_join(path, "on_base_changed"), "on_conflict") if kind == "on_conflict" else (path, kind)
    )


def add_handler(d: Draft, path: str, kind: str) -> dict:
    where, key = _handler_at(path, kind)
    shape = {"steps": [{"id": MAIN_STEP, "tasks": []}]}
    if kind == "on_conflict":
        # `on_base_changed` is not addressable on its own; `on_conflict` is
        # last in its canonical order, so `put`'s placement is the end.
        if not isinstance(d.own(path, "on_base_changed"), dict):
            d.put(path, "on_base_changed", {})
        d.own(path, "on_base_changed")[key] = shape
    else:
        d.put(where, key, shape)
    return {"path": _join(where, key)}


def remove_handler(d: Draft, path: str, kind: str) -> None:
    where, key = _handler_at(path, kind)
    if kind != "on_conflict":
        d.drop(where, key)
        return
    base_change = d.own(path, "on_base_changed")
    if isinstance(base_change, dict):
        base_change.pop(key, None)
        inherited = (d.inherited(path) or {}).get("on_base_changed")
        if isinstance(inherited, Mapping) and inherited.get(key) is not None:
            base_change[key] = None


def move(d: Draft, path: str, to: int, to_step: str | None = None) -> None:
    """A node, a step within its container, or a task within its step or to
    `to_step` beside it, to position `to`."""
    what, items = d.member(path)
    parent, _, id = path.rpartition(PATH_SEPARATOR)
    dest = items
    if what == "task" and to_step is not None:
        container = parent.rpartition(PATH_SEPARATOR)[0]
        if to_step not in _ids(d.own(container, "steps")):
            raise OpError(f"no step {to_step!r} beside {parent!r}")
        dest = d.items(_join(container, to_step), "tasks")
        if dest is not items and id in _ids(dest):
            raise OpError(f"step {to_step} already has a task called {id}")
    if what == "node":
        # Only a violation the new order adds refuses it.
        before = d.expanded()["nodes"]
        after = list(before)
        after.insert(_position(to, len(after) - 1), after.pop(_index(items, id)))
        old = set(order_issues(before))
        if bad := [x for x in order_issues(after) if x not in old]:
            raise OpError(f"can't move {id}: {bad[0]}")
    entry = items.pop(_index(items, id))
    dest.insert(_position(to, len(dest)), entry)


def remove(d: Draft, path: str) -> dict:
    if (entry := _entry(d, path)) is not None:
        # Its uses stay, dangling, as problems.
        entries, name = entry
        uses = _uses({LIBRARY_FILE: d.chain, **_chain_files(d)}, path.partition(".")[0], name)
        del entries[name]
        return {"broken": [{"file": f, "path": p, "field": field} for f, _, p, field in uses]}
    parent, _, last = path.rpartition(PATH_SEPARATOR)
    if parent.rpartition(PATH_SEPARATOR)[2] == "escalation":
        parent, last = parent.rpartition(PATH_SEPARATOR)[0], "escalation"
    if parent and last in _SLOTS:
        d.drop(parent, last)
        return {"broken": []}
    what, items = d.member(path)
    items.pop(_index(items, last))
    broken = references(d.expanded()["nodes"], last) if what == "node" else []
    return {"broken": broken}


def extend(d: Draft, node: str, base: str) -> dict:
    if not d.base_node(base):
        raise OpError(f"the library has no node {base!r}")
    n = d.at(node)
    dropped = [
        k
        for k in ("steps", "on_failure", "fix_loop")
        if n.get(k) is not None and (k != "steps" or n["steps"])
    ]
    for k in ("kind", "tasks", "steps", "on_failure", "fix_loop"):
        n.pop(k, None)
    d.put(node, "extends", base)
    return {"dropped": dropped}


def base_check(d: Draft, node: str, base: str) -> dict:
    """The prototype's `baseCheck`: which of the node's own keys the new base
    can take."""
    parent = d.base_node(base)
    if not parent:
        raise OpError(f"the library has no node {base!r}")
    kept, dropped = [], []
    for k, v in d.at(node).items():
        if k in ("id", "extends", "icon", "kind", "tasks"):
            continue
        if k == "steps":
            missing = [s for s in _ids(v) if s not in _ids(parent.get("steps"))]
            if v and not missing:
                kept.append(k)
            else:
                why = f"{base} has no step {', '.join(missing)}" if missing else "no steps"
                dropped.append({"key": k, "why": why})
        elif k in ("on_failure", "fix_loop", "escalation") and v is None:
            if parent.get(k) is not None:
                kept.append(k)
            else:
                dropped.append({"key": k, "why": f"{base} has none to remove"})
        else:
            kept.append(k)
    return {"kept": kept, "dropped": dropped}


def change_base(d: Draft, node: str, base: str) -> dict:
    check = base_check(d, node, base)
    n = d.at(node)
    for k in ("kind", "tasks", *(x["key"] for x in check["dropped"])):
        n.pop(k, None)
    d.put(node, "extends", base)
    return check


def new_chain(d: Draft, **source: str) -> None:
    d.chains_only("new_chain")
    if set(source) - {"from"}:
        raise OpError(f"unexpected {', '.join(sorted(set(source) - {'from'}))}")
    if d.exists:
        raise OpError(f"chain {d.key!r} already exists")
    origin = source.get("from")
    if origin is None:
        d._chain = {"id": d.key, "description": "", "nodes": []}
    else:
        library = getattr(d.st, "library", None)
        if isinstance(origin, str) and library is not None and library.plugin_of(origin):
            # A loaded plugin's chain, as the library holds it: its own bare
            # references already made `<namespace>:<name>`, so the copy resolves.
            if origin not in library.chain_ids:
                raise OpError(f"there is no chain {origin!r} to copy")
            copied = yaml.safe_dump(plain(library.chain_data(origin)), sort_keys=False)
            d._chain = authored.load(copied)
            authored.put(d._chain, "", "id", d.key)
            d.exists = True
            d.dirty.add(d.name)
            return
        # A chain id, never a path: `../../x` read any YAML file Kraft could.
        if not isinstance(origin, str) or not store.AREAS["chains"].valid(origin):
            raise OpError(f"there is no chain {origin!r} to copy")
        name = f"chains/{origin}.yaml"
        draft = d.st.db.read(lambda c: store.get(c, "chains", origin))
        if draft is not None and name in draft["files"]:
            text = draft["files"][name]
        else:
            text = resolve.published(d.st.templates_dir, [name])[name]
        if text is None:
            raise OpError(f"there is no chain {origin!r} to copy")
        d._chain = authored.load(text)
        if not isinstance(d._chain, dict):
            raise OpError(f"chain {origin!r} is not a mapping")
        authored.put(d._chain, "", "id", d.key)
    d.exists = True
    d.dirty.add(d.name)


def delete_chain(d: Draft) -> None:
    d.chains_only("delete_chain")
    d.chain  # noqa: B018 -- refuses a chain that is not there
    d._chain = None


# ── field ops ──


def _get(obj: object, field: str) -> object:
    for key in field.split(PATH_SEPARATOR):
        obj = obj.get(key) if isinstance(obj, Mapping) else None
    return obj


def _unset(obj: dict, field: str) -> None:
    """Delete dotted `field` from `obj`, and each mapping above it that it empties."""
    *outer, last = field.split(PATH_SEPARATOR)
    trail = [obj]
    for key in outer:
        if not isinstance(trail[-1].get(key), dict):
            return
        trail.append(trail[-1][key])
    trail[-1].pop(last, None)
    for parent, key in zip(reversed(trail[:-1]), reversed(outer), strict=True):
        if parent[key]:
            break
        del parent[key]


def set_field(d: Draft, path: str, field: str, value: object) -> None:
    """The prototype's `setField` and `setTaskField`: `field` dotted."""
    if field == "id":
        raise OpError("rename it with rename")
    target = d.at(path)
    if field == "on_base_changed.restart_from" and value in (None, ""):
        d.drop(path, "on_base_changed")
        return
    if value in (None, "") or (field == "artifact_required" and value is False):
        _unset(target, field)
        if field == "artifact":
            _unset(target, "artifact_required")
        return
    d.put(path, field, value)
    # The value the component would have without the key: inherited, not set.
    without = copy.deepcopy(d.chain)
    _unset(authored.at(without, path, file=d.file), field)
    if _get(d.inherited(path, without), field) == value:
        _unset(target, field)
    if field == "profile":
        target.pop("model", None)
        target.pop("effort", None)
    elif field in ("model", "effort"):
        target.pop("profile", None)


def reset_field(d: Draft, path: str, field: str | None = None) -> None:
    """Remove `field` from the file so the inherited or default value applies;
    with none, every override of a component that extends a library one."""
    target = d.at(path)
    if field is not None:
        _unset(target, field)
        return
    if not target.get("extends"):
        raise OpError(f"{path} extends nothing, so it has no overrides to reset")
    for key in [k for k in target if k not in _KEPT]:
        del target[key]


def _rename_chain(d: Draft, id: str) -> dict:
    area = store.AREAS["chains"]
    if not (area.renames and area.valid(id)):
        raise OpError(f"{id!r} is not a chain id")
    (name,) = area.files(id)
    # A file this draft holds is taken unless an earlier rename nulled it.
    taken = (
        d.files[name] is not None
        if name in d.files
        else resolve.published(d.st.templates_dir, [name])[name] is not None
        or d.st.db.read(lambda c: store.get(c, "chains", id)) is not None
    )
    if taken:
        raise OpError(f"chain {id!r} already exists")
    authored.put(d.chain, "", "id", id)
    d.files[d.name] = None
    d.dirty.add(d.name)
    d.key, d.name = id, name
    d.dirty.add(name)
    return {"updated": []}


def rename(d: Draft, path: str, id: str) -> dict:
    """A node or gate with every `reject_to` and `restart_from` naming it; a
    step, a task or a slot's task, its id only; the chain (`path: ""`), its
    file."""
    parent, _, old = path.rpartition(PATH_SEPARATOR)
    if id == (old if path else d.key):
        return {"updated": []}
    if not path:
        d.chains_only("renaming the chain")
        return _rename_chain(d, id)
    if _entry(d, path) is not None:
        return _rename_component(d, path, id)
    if old in (JUDGE_SEGMENT, AUTO_REVIEW_SEGMENT, "escalation") or (
        parent.rpartition(PATH_SEPARATOR)[2] == "escalation"
    ):
        d.put(path, "id", _check_id(id, ()))
        return {"updated": []}
    what, items = d.member(path)
    _check_id(id, _ids(items))
    refs: list[dict] = []
    if what == "node":
        nodes = d.expanded()["nodes"]
        refs = references(nodes, old)
        if any(n.get("id") == old and _restart_from(n) == old for n in nodes):
            d.put(old, "on_base_changed.restart_from", id)
        for ref in refs:
            d.put(ref["path"], ref["field"], id)
    items[_index(items, old)]["id"] = id
    return {"updated": refs}


def set_fragment(d: Draft, path: str, yaml: str) -> None:
    """The per-component YAML tab: the fragment replaces the authored
    component at `path`."""
    try:
        fragment = authored.parse(yaml)
    except YAMLError as exc:
        line, col = positions.yaml_mark(exc) or (1, 1)
        message = getattr(exc, "problem", None) or str(exc)
        raise OpError(f"line {line}, column {col}: {message}", line=line, col=col) from None
    if not isinstance(fragment, dict):
        raise OpError("the fragment is not a mapping")
    target = d.at(path)
    if fragment.get("id") != target.get("id"):
        raise OpError("the fragment changes the id; rename it with rename")
    target.clear()
    target.update(fragment)
    authored.normalise(d.chain)


# ── the library ──


def add_component(d: Draft, section: str, name: str, kind: str | None = None) -> dict:
    """A new library component, empty: a new steering profile's empty
    `instructions` are a problem until written."""
    d.library_only("add_component")
    if section not in set(Namespace):
        raise OpError(f"a library section is one of {', '.join(Namespace)}, not {section!r}")
    if not isinstance(d.chain.get(section), dict):
        d.put("", section, {})
    entries = d.chain[section]
    _check_id(name, entries)
    if section == Namespace.NODES:
        if kind not in (None, "exec", "gate"):
            raise OpError(f"a node is exec or gate, not {kind!r}")
        body: dict = {"kind": "gate"} if kind == "gate" else {"kind": "exec", "steps": []}
    elif section == Namespace.TASKS:
        if kind is not None and kind not in set(TaskKind):
            raise OpError(f"no task kind {kind!r}")
        body = {"kind": kind} if kind else {}
    elif kind is not None:
        raise OpError(f"a {Namespace(section).singular} has no kind")
    else:
        body = {"tasks": []} if section == Namespace.STEPS else {"instructions": ""}
    entries[name] = body
    return {"path": _join(section, name)}


def move_to_library(d: Draft, path: str, name: str) -> dict:
    """The exec node or task at `path` into `library.yaml` as `nodes.<name>`
    or `tasks.<name>`, the chain keeping `{id, extends: name}`. The library
    joins the draft."""
    d.chains_only("move_to_library")
    what, items = d.member(path)
    id = path.rpartition(PATH_SEPARATOR)[2]
    if what == "step" or (d.inherited(path) or {}).get("kind") == "gate":
        raise OpError("only an exec node or a task moves to the library")
    section = Namespace.NODES if what == "node" else Namespace.TASKS
    if not isinstance(d.library.get(section.value), dict):
        authored.put(d.library, "", section.value, {}, file=authored.LIBRARY)
    entries = d.library[section.value]
    _check_id(name, entries)
    index = _index(items, id)
    entries[name] = {k: v for k, v in items[index].items() if k != "id"}
    items[index] = {"id": id, "extends": name}
    d.dirty.add(LIBRARY_FILE)
    return {"path": _join(section.value, name)}


def plain(value: object) -> object:
    """A library mapping as plain dicts and lists, to serialise."""
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return value


def copy_component(d: Draft, ref: str, name: str) -> dict:
    """A loaded plugin's component (`tasks.release:base`, as the library lists
    it) into `library.yaml` as `<section>.<name>`: the way to change what a
    plugin ships. Its plugin-internal references come along qualified, so the
    copy resolves. The library joins the draft."""
    library = getattr(d.st, "library", None)
    section, _, qualified = ref.partition(".") if isinstance(ref, str) else ("", "", "")
    if library is None or section not in [ns.value for ns in Namespace]:
        raise OpError(f"there is no plugin component {ref!r} to copy")
    if library.plugin_of(qualified) is None:
        raise OpError(f"{ref!r} is not a plugin's component; edit it where it is")
    if section == Namespace.STEERING.value:
        found = library.steering.get(qualified)
        body = None if found is None else found.model_dump(mode="json", exclude_unset=True)
    else:
        raw = library.component(Namespace(section), qualified)
        body = None if raw is None else plain(raw.data)
    if body is None:
        raise OpError(f"there is no plugin component {ref!r} to copy")
    if not isinstance(d.library.get(section), dict):
        authored.put(d.library, "", section, {}, file=authored.LIBRARY)
    entries = d.library[section]
    _check_id(name, entries)
    entries[name] = body
    d.dirty.add(LIBRARY_FILE)
    return {"path": _join(section, name)}


#: Every op by name.
OPS: dict[str, Callable[..., dict | None]] = {
    "add_node": add_node,
    "add_step": add_step,
    "add_task": add_task,
    "add_handler": add_handler,
    "remove_handler": remove_handler,
    "move": move,
    "remove": remove,
    "extend": extend,
    "change_base": change_base,
    "new_chain": new_chain,
    "delete_chain": delete_chain,
    "set_field": set_field,
    "reset_field": reset_field,
    "rename": rename,
    "set_fragment": set_fragment,
    "add_component": add_component,
    "move_to_library": move_to_library,
    "copy_component": copy_component,
}


def _refuse_plugin_address(d: Draft, path: object) -> None:
    """An op that addresses a plugin's own component (`tasks.release:base`) is
    refused: a plugin's content is read-only. Extending or selecting one is
    not an address."""
    library = getattr(d.st, "library", None)
    if library is None or not isinstance(path, str):
        return
    for segment in path.split(PATH_SEPARATOR):
        if why := catalogue.read_only_message(library, segment):
            raise PluginReadOnly(why)


def apply(d: Draft, ops: list, table: Mapping[str, Callable] = OPS) -> list[dict]:
    """Run `ops` on `d` in order, each looked up in `table`; each one's own
    answer. Raises `OpError` with `.index` set to the op that failed."""
    out = []
    for index, op in enumerate(ops):
        try:
            if not isinstance(op, dict) or op.get("op") not in table:
                name = op.get("op") if isinstance(op, dict) else op
                raise OpError(f"no op {name!r}")
            fn = table[op["op"]]
            fields = {k: v for k, v in op.items() if k != "op"}
            # Every way an op names where it acts.
            for address in ("path", "container", "node"):
                _refuse_plugin_address(d, fields.get(address))
            try:
                inspect.signature(fn).bind(d, **fields)
            except TypeError as exc:
                raise OpError(f"{op['op']}: {exc}") from None
            result = fn(d, **fields)
        except OpError as exc:
            exc.index = index
            raise
        out.append({"op": op["op"], **({"result": result} if result is not None else {})})
    return out
