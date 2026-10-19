"""The Templates editor's ops on a chain that extends another (`extends:`
plus `remove`, `add` and `overrides`): each writes one of those edits, and none
copies an inherited component into the file. A node the chain adds lives in
the file (`add[].node`), so the ordinary op edits it there; an inherited one
has no edit form for a structural change, which answers 422 naming the YAML
view and Detach (`docs/intent/templates-editor.md`)."""

from __future__ import annotations

import copy
import functools
from collections.abc import Mapping
from typing import Literal

from kraft.drafts import authored, ops
from kraft.drafts.ops import Draft, OpError, _check_id, _position
from kraft.drafts.ops import _extending as parent_of
from kraft.templates import catalogue
from kraft.templates.extension import IDENTITY
from kraft.templates.library import ComponentSource, Namespace, TemplateLibraryError, _Resolution
from kraft.templates.models import PATH_SEPARATOR

Owner = Literal["chain", "added", "inherited", "removed"]
#: What an extending chain sets on itself; its parent and edits are the YAML view's.
CHAIN_FIELDS = ("description", "policy")


def _chain_field(d: Draft, field: str | None) -> None:
    """Refuse a write to the chain itself other than `CHAIN_FIELDS`: `extends`,
    `remove`, `add` and `overrides` are what the other ops write, and changing
    one by hand could leave the edits without a parent."""
    if field is None or field.split(PATH_SEPARATOR)[0] not in CHAIN_FIELDS:
        raise OpError(
            f"{d.key} extends {parent_of(d)}: on the chain itself only "
            f"{' and '.join(CHAIN_FIELDS)} change here; edit the rest in the YAML view"
        )


def _inherited_message(d: Draft, path: str) -> str:
    return (
        f"{path} is inherited from {parent_of(d)}; change it in the YAML view "
        "(an override that replaces its list), or detach the chain"
    )


def _order(d: Draft) -> list[dict]:
    """The resolved chain's nodes, edits applied (item 1's `_extend`, through
    `Draft.expanded`)."""
    nodes = d.expanded().get("nodes")
    if not isinstance(nodes, list):
        raise OpError(f"{d.key} does not resolve; fix it in the YAML view first")
    return [n for n in nodes if isinstance(n, dict)]


def _ids(d: Draft) -> list[str]:
    return [n.get("id") for n in _order(d)]


def _list(d: Draft, key: str) -> list:
    """The chain's `remove` or `add` list, made when absent."""
    if not isinstance(d.chain.get(key), list):
        authored.put(d.chain, "", key, [])
    return d.chain[key]


def _prune(d: Draft, key: str) -> None:
    """An edit key left empty goes."""
    if key in d.chain and not d.chain[key]:
        del d.chain[key]


def _added(d: Draft) -> list[str]:
    return [n.get("id") for n in authored.added_nodes(d._chain)]


def _removed(d: Draft) -> list:
    removed = d._chain.get("remove")
    return removed if isinstance(removed, list) else []


def _entry(d: Draft, id: str) -> int:
    """The index of the `add` entry adding node `id`."""
    return next(i for i, e in enumerate(_list(d, "add")) if (e.get("node") or {}).get("id") == id)


def owner(d: Draft, path: str) -> Owner:
    """Who holds the component at `path`, decided by its node: the file (the
    chain itself, or a node it adds), the parent, or nobody since this chain
    removes it."""
    if not path:
        return "chain"
    root = path.split(PATH_SEPARATOR)[0]
    if root in _added(d):
        return "added"
    if root in _removed(d):
        return "removed"
    if root in _ids(d):
        return "inherited"
    raise OpError(f"nothing at {path!r}")


def _place(d: Draft, entry: dict, order: list[str], at: int) -> None:
    """Write `entry` (`{after, node}`) into `add` so its node resolves at index
    `at` of `order`, the resolved ids without it. Adds after one node resolve
    in written order (item 1), and anchor only on a parent node: after an
    inherited `prev`, first among its adds; after an added `prev`, right behind
    `prev`'s entry, on its anchor."""
    if at == 0:
        first = order[0] if order else "the first node"
        raise OpError(
            "an extending chain adds a node after one of its parent's nodes; "
            f"there is no place before {first}"
        )
    adds = _list(d, "add")
    index = {(e.get("node") or {}).get("id"): i for i, e in enumerate(adds)}
    prev = order[at - 1]
    if prev in index:
        entry["after"] = adds[index[prev]]["after"]
        adds.insert(index[prev] + 1, entry)
    else:
        entry["after"] = prev
        first = next((i for i, e in enumerate(adds) if e.get("after") == prev), len(adds))
        adds.insert(first, entry)


def add_node(d: Draft, at: int, id: str, kind: str) -> None:
    ids = _ids(d)
    _check_id(id, [*ids, *_removed(d)])
    if kind not in ("exec", "gate"):
        raise OpError(f"a node is exec or gate, not {kind!r}")
    node = {"id": id, "kind": kind} if kind == "gate" else {"id": id, "kind": kind, "steps": []}
    _place(d, {"after": None, "node": node}, ids, _position(at, len(ids)))


def move(d: Draft, path: str, to: int, to_step: str | None = None) -> None:
    if not path:
        raise OpError("no node ''")
    if PATH_SEPARATOR in path or to_step is not None:
        return _inside(d, path, ops.move, path=path, to=to, to_step=to_step)
    if owner(d, path) != "added":
        raise OpError(
            f"{path} comes from {parent_of(d)}, so it keeps its place. "
            "Detach the chain to reorder it"
        )
    before = _order(d)
    rest = [n for n in before if n.get("id") != path]
    to = _position(to, len(rest))
    moved = next(n for n in before if n.get("id") == path)
    # Only a violation the new order adds refuses it, as `ops.move` checks.
    old = set(ops.order_issues(before))
    if bad := [x for x in ops.order_issues([*rest[:to], moved, *rest[to:]]) if x not in old]:
        raise OpError(f"can't move {path}: {bad[0]}")
    entry = _list(d, "add").pop(_entry(d, path))
    _place(d, entry, [n.get("id") for n in rest], to)
    return None


def remove(d: Draft, path: str) -> dict:
    if not path:
        raise OpError("no node ''")
    if PATH_SEPARATOR in path:
        return _inside(d, path, ops.remove, path=path)
    who = owner(d, path)
    if who == "removed":
        raise OpError(f"{path} is already removed; put it back with restore")
    nodes = _order(d)
    broken = [r for r in ops.references(nodes, path) if r["path"] != path]
    if who == "added":
        del _list(d, "add")[_entry(d, path)]
        _prune(d, "add")
        return {"broken": broken}
    # Refused here, not only at resolve: once the edits don't apply, the
    # chain has no nodes for the canvas to draw or to edit back.
    if next(n for n in nodes if n.get("id") == path).get("chain_finalized"):
        raise OpError(f"{path} is the final gate, which an extending chain keeps")
    adds = [e for e in d._chain.get("add") or () if isinstance(e, dict)]
    if follows := [(e.get("node") or {}).get("id") for e in adds if e.get("after") == path]:
        raise OpError(f"can't remove {path}: {follows[0]} goes after it; move {follows[0]} first")
    # Its overrides would name a removed node; dropping them silently loses work.
    overrides = d._chain.get("overrides")
    if any(k == path or str(k).startswith(f"{path}{PATH_SEPARATOR}") for k in overrides or ()):
        raise OpError(f"{path} has overrides; reset them first")
    if broken:
        ref = broken[0]
        raise OpError(
            f"can't remove {path}: {ref['path']}'s {ref['field']} names it; "
            "point it elsewhere first"
        )
    _list(d, "remove").append(path)
    return {"broken": []}


def restore(d: Draft, node: str) -> None:
    if node not in _removed(d):
        raise OpError(f"{node} is not removed from {parent_of(d)}")
    # The parent's own node: item 1 refuses an override of a removed one.
    bare = d.expanded({"id": d.key, "extends": parent_of(d)}).get("nodes") or []
    mine = next((n for n in bare if n.get("id") == node), {})
    pointers = (mine.get("reject_to"), ops._restart_from(mine))
    if gone := [t for t in pointers if t != node and t in _removed(d)]:
        raise OpError(f"restore {gone[0]} first: {node} names it, and this chain removes it")
    d.chain["remove"].remove(node)
    _prune(d, "remove")


def _inside(d: Draft, address: str, fn, /, **fields):
    """`fn` on a component the file holds (the chain, or a node it adds); a
    refusal naming the way out otherwise."""
    who = owner(d, address)
    if who == "chain":
        raise OpError(f"{d.key} extends {parent_of(d)}; change the chain itself in the YAML view")
    if who == "added":
        return fn(d, **fields)
    if who == "removed":
        raise OpError(f"{address.split(PATH_SEPARATOR)[0]} is removed; restore it first")
    raise OpError(_inherited_message(d, address))


def _in_added(fn, *keys: str):
    """`fn` as an extending chain's op: run when its address (the first of
    `keys` given) is the file's, refused otherwise."""

    @functools.wraps(fn)
    def op(d: Draft, **fields):
        address = next((fields[k] for k in keys if fields.get(k) is not None), "")
        return _inside(d, address, fn, **fields)

    return op


# ── field ops: an inherited component's are overrides ──


def _override(d: Draft, path: str) -> dict:
    if not isinstance(d.chain.get("overrides"), dict):
        authored.put(d.chain, "", "overrides", {})
    overrides = d.chain["overrides"]
    if not isinstance(overrides.get(path), dict):
        overrides[path] = {}
    return overrides[path]


def _unset_override(d: Draft, path: str, field: str | None) -> None:
    """Delete `overrides[path].<field>` (all of `overrides[path]` with no
    field), and every mapping that leaves empty."""
    overrides = d.chain.get("overrides")
    if not isinstance(overrides, dict) or not isinstance(overrides.get(path), dict):
        return
    if field is None:
        del overrides[path]
    else:
        ops._unset(overrides[path], field)
        if not overrides[path]:
            del overrides[path]
    _prune(d, "overrides")


def _put(obj: dict, field: str, value: object) -> None:
    *outer, last = field.split(PATH_SEPARATOR)
    for key in outer:
        if not isinstance(obj.get(key), dict):
            obj[key] = {}
        obj = obj[key]
    obj[last] = value


def _writable(d: Draft, path: str) -> None:
    """Refuse an override at `path` that item 1 refuses at resolve: a removed
    node's, or one under an ancestor override that replaces its list."""
    if owner(d, path) == "removed":
        raise OpError(f"{path.split(PATH_SEPARATOR)[0]} is removed; restore it first")
    if d.inherited(path) is None:
        raise OpError(f"nothing at {path!r}")
    overrides = d._chain.get("overrides")
    for key, body in overrides.items() if isinstance(overrides, dict) else ():
        if not path.startswith(f"{key}{PATH_SEPARATOR}"):
            continue
        sub = body
        for seg in path[len(key) + 1 :].split(PATH_SEPARATOR):
            if not isinstance(sub, dict):
                break
            if "steps" in sub or "tasks" in sub:
                raise OpError(
                    f"{path} sits in the list overrides.{key} replaces; edit it in the YAML view"
                )
            sub = sub.get(seg)


def set_field(d: Draft, path: str, field: str, value: object) -> None:
    if field in ("reject_to", "on_base_changed.restart_from") and value in _removed(d):
        raise OpError(
            f"{path}'s {field} would name {value}, which this chain removes; restore it first"
        )
    who = owner(d, path)
    if who == "chain":
        _chain_field(d, field)
    if who in ("chain", "added"):
        return ops.set_field(d, path, field, value)
    _writable(d, path)
    # Item 1 refuses an identity key at any depth of an override.
    if fixed := next((seg for seg in field.split(PATH_SEPARATOR) if seg in IDENTITY), None):
        root = path.split(PATH_SEPARATOR)[0]
        raise OpError(f"an override can't change {fixed}; remove {root} and add your own")
    if value in (None, "") or (field == "artifact_required" and value is False):
        _unset_override(d, path, field)
        if field == "artifact":
            _unset_override(d, path, "artifact_required")
        return None
    over = _override(d, path)
    _put(over, field, value)
    # One route: item 1's merge displaces the inherited one, so the other
    # half goes here, before comparing with what the component inherits.
    if field == "profile":
        over.pop("model", None)
        over.pop("effort", None)
    elif field in ("model", "effort"):
        over.pop("profile", None)
    without = copy.deepcopy(d.chain)
    ops._unset(without["overrides"][path], field)
    if ops._get(d.inherited(path, without), field) == value:
        _unset_override(d, path, field)
    return None


def reset_field(d: Draft, path: str, field: str | None = None) -> None:
    who = owner(d, path)
    if who == "chain":
        _chain_field(d, field)
    if who in ("chain", "added"):
        return ops.reset_field(d, path, field)
    _writable(d, path)
    if field is None and path not in (d._chain.get("overrides") or {}):
        raise OpError(f"{path} has no override to reset")
    _unset_override(d, path, field)
    return None


# ── a node the chain adds: renamed and moved to the library in its entry ──


def rename(d: Draft, path: str, id: str) -> dict:
    if not path:
        return ops.rename(d, path, id)
    if PATH_SEPARATOR in path:
        return _inside(d, path, ops.rename, path=path, id=id)
    if owner(d, path) != "added":
        raise OpError(
            f"renaming {path} would replace {parent_of(d)}'s node; "
            "remove it and add your own, or detach the chain"
        )
    if id == path:
        return {"updated": []}
    nodes = _order(d)
    _check_id(id, [*(n.get("id") for n in nodes), *_removed(d)])
    refs = ops.references(nodes, path)
    node = _list(d, "add")[_entry(d, path)]["node"]
    node["id"] = id
    if ops._restart_from(node) == path:
        node["on_base_changed"]["restart_from"] = id
    for ref in refs:
        # An added node's own field, or an override of an inherited one.
        set_field(d, ref["path"], ref["field"], id)
    return {"updated": refs}


def move_to_library(d: Draft, path: str, name: str) -> dict:
    if not path:
        raise OpError("no node ''")
    if PATH_SEPARATOR in path:
        return _inside(d, path, ops.move_to_library, path=path, name=name)
    if owner(d, path) != "added":
        raise OpError(_inherited_message(d, path))
    entry = _list(d, "add")[_entry(d, path)]
    if (d.inherited(path) or {}).get("kind") == "gate":  # as `ops.move_to_library` checks
        raise OpError("only an exec node or a task moves to the library")
    if not isinstance(d.library.get(Namespace.NODES.value), dict):
        authored.put(d.library, "", Namespace.NODES.value, {}, file=authored.LIBRARY)
    entries = d.library[Namespace.NODES.value]
    _check_id(name, entries)
    entries[name] = {k: v for k, v in entry["node"].items() if k != "id"}
    entry["node"] = {"id": path, "extends": name}
    d.dirty.add(ops.LIBRARY_FILE)
    return {"path": f"{Namespace.NODES.value}{PATH_SEPARATOR}{name}"}


# ── detach ──


def _touched(library, data: Mapping) -> set[str]:
    """Every node id an `overrides` key names, in `data` or any chain it extends."""
    overrides = data.get("overrides")
    ids = {str(key).split(PATH_SEPARATOR)[0] for key in overrides or ()}
    parent = data.get("extends")
    if isinstance(parent, str) and parent in library.chain_ids:
        ids |= _touched(library, library.chain_data(parent))
    return ids


def _authored_node(library, data: Mapping, id: str) -> dict:
    """Node `id` as the file that defines it writes it: an `add` entry here, or
    up the parents to the chain whose `nodes:` holds it."""
    for entry in data.get("add") or ():
        if isinstance(entry, Mapping) and (entry.get("node") or {}).get("id") == id:
            return entry["node"]
    parent = data.get("extends")
    if isinstance(parent, str):
        return _authored_node(library, library.chain_data(parent), id)
    return next(n for n in data.get("nodes") or () if isinstance(n, Mapping) and n.get("id") == id)


def detach(d: Draft) -> dict:
    """The chain written as one of its own, keeping its id: its resolved
    description, policy and nodes in order, and no `extends` or edits. A node
    an override at any level names is written resolved; every other one as
    the file that defines it writes it, so it keeps its library `extends`."""
    parent = parent_of(d)
    library = d.resolving_library()
    if library is None:
        raise OpError("the template library does not load")
    raw = authored.parse(authored.dump(d.chain))
    try:
        candidate, id = library.with_chain(d.st.templates_dir / d.name, {**raw, "id": d.key})
        chain = candidate.resolve_chain(id).chain
    except TemplateLibraryError as exc:
        raise OpError(f"{d.key} does not resolve, so it can't be detached: {exc}") from None
    resolved = chain.model_dump(mode="json", exclude_unset=True)
    touched = _touched(candidate, raw)
    flat: dict = {"id": d.key}
    for key in ("description", "policy"):
        if resolved.get(key) is not None:
            flat[key] = resolved[key]
    flat["nodes"] = [
        copy.deepcopy(
            n if n["id"] in touched else ops.plain(_authored_node(candidate, raw, n["id"]))
        )
        for n in resolved["nodes"]
    ]
    d._chain = authored.normalise(flat)
    d.dirty.add(d.name)
    return {"detached_from": parent}


# ── change base: the chain an extending chain follows ──


def _new_parent(d: Draft, base: object) -> tuple[list[dict], set[str]]:
    """Chain `base`'s expanded nodes and every canonical path in it, refusing a
    base this chain may not extend: none, itself, one that extends it (a
    cycle), or a repo library's."""
    library = d.resolving_library()
    if library is None or not isinstance(base, str) or base not in library.chain_ids:
        raise OpError(f"there is no chain {base!r} to extend")
    if base == d.key:
        raise OpError(f"{d.key} can't extend itself")
    name, seen = base, set()
    while isinstance(name, str) and name in library.chain_ids and name not in seen:
        if name == d.key:
            raise OpError(f"{base} extends {d.key}, so {d.key} extending it would be a cycle")
        seen.add(name)
        name = library.chain_data(name).get("extends")
    if why := catalogue.repo_library_copy(library, base):  # as new_chain refuses it
        raise OpError(why)
    raw = library._chains[base]
    resolution = _Resolution(library, raw.source)
    try:
        expanded = resolution.expand_chain(raw.data)
    except TemplateLibraryError as exc:
        raise OpError(f"{base} does not resolve, so nothing can extend it: {exc}") from None
    nodes = [n for n in expanded["nodes"] if isinstance(n, Mapping)]
    return nodes, {located.path for located in resolution._located.values()}


def chain_base_check(d: Draft, base: str) -> dict:
    """Which of this chain's edits `base` can take, as `ops.base_check` says
    which of a node's overrides a new library base can: each `remove`, `add`
    and `overrides` entry, kept or dropped with why."""
    nodes, paths = _new_parent(d, base)
    ids = [n.get("id") for n in nodes]
    final = {n.get("id") for n in nodes if n.get("kind") == "gate" and n.get("chain_finalized")}
    kept: list[str] = []
    dropped: list[dict] = []

    def judge(key: str, why: str | None) -> None:
        if why is None:
            kept.append(key)
        else:
            dropped.append({"key": key, "why": why})

    for id in _removed(d):
        judge(
            f"remove {id}",
            f"{base} has no node {id}"
            if id not in ids
            else f"{id} is {base}'s final gate"
            if id in final
            else None,
        )
    for entry in d._chain.get("add") or ():
        node = entry.get("node") if isinstance(entry, Mapping) else None
        id = node.get("id") if isinstance(node, Mapping) else None
        after = entry.get("after") if isinstance(entry, Mapping) else None
        judge(
            f"add {id}",
            f"{base} already has a node {id}"
            if id in ids
            else f"{base} has no node {after} to add it after"
            if after not in ids
            else None,
        )
    for path in d._chain.get("overrides") or ():
        judge(f"overrides {path}", None if path in paths else f"{base} has no {path}")
    return {"kept": kept, "dropped": dropped}


def change_chain_base(d: Draft, base: str) -> dict:
    """Follow chain `base` in place of the parent: `extends` rewritten, and the
    edits it can't take dropped, as `ops.change_base` drops the overrides a new
    node base can't take. What is left must still resolve (`ops.apply`)."""
    check = chain_base_check(d, base)
    gone = {x["key"] for x in check["dropped"]}
    if isinstance(d.chain.get("remove"), list):
        d.chain["remove"] = [id for id in d.chain["remove"] if f"remove {id}" not in gone]
        _prune(d, "remove")
    if isinstance(d.chain.get("add"), list):
        d.chain["add"] = [
            e for e in d.chain["add"] if f"add {(e.get('node') or {}).get('id')}" not in gone
        ]
        _prune(d, "add")
    if isinstance(d.chain.get("overrides"), dict):
        for path in [p for p in d.chain["overrides"] if f"overrides {p}" in gone]:
            del d.chain["overrides"][path]
        _prune(d, "overrides")
    authored.put(d.chain, "", "extends", base)
    return check


def expansion_error(d: Draft) -> str | None:
    """Why `d`'s chain does not expand (its edits don't apply to its parent),
    or None when it does."""
    library = d.resolving_library()
    if library is None or not isinstance(d._chain, dict):
        return None
    raw = authored.parse(authored.dump(d._chain))
    source = ComponentSource(d.st.templates_dir / d.name, Namespace.NODES, d.key, chain=True)
    try:
        _Resolution(library, source).expand_chain({**raw, "id": d.key})
    except TemplateLibraryError as exc:
        return str(exc)
    return None


OPS = {
    **ops.OPS,
    "add_node": add_node,
    "move": move,
    "remove": remove,
    "restore": restore,
    "rename": rename,
    "set_field": set_field,
    "reset_field": reset_field,
    "move_to_library": move_to_library,
    "add_step": _in_added(ops.add_step, "container"),
    "add_task": _in_added(ops.add_task, "container", "node"),
    "add_handler": _in_added(ops.add_handler, "path"),
    "remove_handler": _in_added(ops.remove_handler, "path"),
    "extend": _in_added(ops.extend, "node"),
    "change_base": _in_added(ops.change_base, "node"),
    "set_fragment": _in_added(ops.set_fragment, "path"),
    "detach": detach,
    "chain_base_check": chain_base_check,
    "change_chain_base": change_chain_base,
}
