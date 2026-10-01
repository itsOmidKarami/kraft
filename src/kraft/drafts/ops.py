"""The draft ops: every model-changing action of the Templates editor as one
server op (`reference/kraft-templates-actions.js`), applied to the authored
mapping `kraft.drafts.authored` loads and written back with its `dump`.

`apply` runs one request's ops in order on a copy of the draft's files, all or
nothing: an op that cannot apply raises `OpError` and the request saves
nothing. A reference an op breaks is left dangling, a problem the resolve
result reports, never re-targeted (Decisions §9).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping

import yaml
from pydantic import TypeAdapter, ValidationError

from kraft.drafts import authored, resolve, store
from kraft.templates.environment import Identifier
from kraft.templates.library import LIBRARY_FILE, Namespace
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


class OpError(Exception):
    """An op that cannot apply: the request answers 422 and saves nothing."""

    #: The failing op's position in its request, set by `apply`.
    index: int | None = None


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
    """One request's working copy of a `chains` draft's files."""

    def __init__(self, st, key: str, files: Mapping[str, str | None], *, exists: bool) -> None:
        self.st = st
        self.key = key
        self.name = f"chains/{key}.yaml"
        self.files = dict(files)
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
        self._chain: object = authored.load(self.files.get(self.name))

    # ── addressing ──

    @property
    def chain(self) -> dict:
        # Every op that reaches the chain writes it.
        if not isinstance(self._chain, dict):
            raise OpError(f"there is no chain {self.key!r}; create it with new_chain")
        self.dirty.add(self.name)
        return self._chain

    def nodes(self) -> list:
        if not isinstance(self.chain.get("nodes"), list):
            authored.put(self.chain, "", "nodes", [])
        return self.chain["nodes"]

    def at(self, path: str) -> dict:
        """The component at `path`, owning every container it inherits on the way."""
        found = authored.at(self.chain, path, library=self.library, write=True)
        if found is None:
            raise OpError(f"nothing at {path!r}")
        return found

    def own(self, path: str, key: str) -> object:
        try:
            return authored.own(self.chain, path, key, library=self.library)
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
        authored.put(self.chain, path, field, value, library=self.library)

    def base_node(self, name: object) -> Mapping:
        return authored.inherited(self.library, Namespace.NODES, name)

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
        if (authored.at(self.expanded(), path) or {}).get(key) is not None:
            self.put(path, key, None)

    def expanded(self) -> dict:
        """The chain as it stands, `extends` expanded and the shorthand
        normalised; as written where it does not expand."""
        raw = authored.parse(authored.dump(self.chain))
        data, _ = resolve._expand(
            getattr(self.st, "library", None), self.st.templates_dir / self.name, self.key, raw
        )
        return authored.normalise(dict(data))

    # ── writing ──

    def finish(self) -> dict[str, str | None]:
        """The files with every one an op touched written back by `dump`."""
        if self.name in self.dirty:
            if isinstance(self._chain, dict):
                self._one_shape()
                self.files[self.name] = authored.dump(self._chain)
            else:
                self.files[self.name] = None
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
            parent = authored.inherited(self.library_raw, Namespace.NODES, node["extends"])
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
                    authored.put(self._chain, path, "tasks", None, library=self.library)


def _dicts(value: object) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


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
    id = id_base if id_base is not None else _unique(kind or extends, taken)
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
        if slot == AUTO_REVIEW_SEGMENT and extends is not None:
            # The library does not expand a gate's reviewer (Kraft-67yp8).
            raise OpError("a gate's reviewer cannot extend a library task yet; give its kind")
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
        inherited = (authored.at(d.expanded(), path) or {}).get("on_base_changed")
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
    if set(source) - {"from"}:
        raise OpError(f"unexpected {', '.join(sorted(set(source) - {'from'}))}")
    if d.exists:
        raise OpError(f"chain {d.key!r} already exists")
    origin = source.get("from")
    if origin is None:
        d._chain = {"id": d.key, "description": "", "nodes": []}
    else:
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
    d.chain  # noqa: B018 -- refuses a chain that is not there
    d._chain = None


#: Every op by name. E and F add theirs here.
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
}


def apply(d: Draft, ops: list) -> list[dict]:
    """Run `ops` on `d` in order; each one's own answer. Raises `OpError`
    with `.index` set to the op that failed."""
    out = []
    for index, op in enumerate(ops):
        try:
            if not isinstance(op, dict) or op.get("op") not in OPS:
                name = op.get("op") if isinstance(op, dict) else op
                raise OpError(f"no op {name!r}")
            fn = OPS[op["op"]]
            fields = {k: v for k, v in op.items() if k != "op"}
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
