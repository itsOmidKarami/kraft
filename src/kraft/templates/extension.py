"""Chains that extend a chain: a parent's resolved nodes with the child's
`remove`, `overrides` and `add` applied
(`an-extending-chain-is-its-parent-with-edits`).

This works on expanded mappings -- the parent's nodes after its own `extends`
expansion, before typing -- because only there does a canonical path such as
`verification.review.code_review` exist while an edit can still change it.
`kraft.templates.library._Resolution` calls `apply` and keeps the locations,
sources and references around it. The vocabulary and the placement rule are
`revision.ChangeSet`'s; the code is not shared, because a revision acts on a
validated, materialised chain under limits written for an agent author.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from kraft.cap_levels import SCOPE_CAP_FIELDS
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError, TemplatePolicyOverride
from kraft.templates.environment import Identifier
from kraft.templates.models import PATH_SEPARATOR, TaskKind, displaced_route, first_error

#: The keys an extending chain edits its parent with.
EDIT_KEYS = ("remove", "add", "overrides")
#: What an override may not set: changing one replaces the component, which is
#: `remove` plus `add` (`an-extending-chains-override-keeps-identity`).
IDENTITY = ("id", "kind", "extends", "chain_finalized")
_TASK_KINDS = frozenset(kind.value for kind in TaskKind)
#: A container holds one shape: `tasks` given drops an inherited `steps`, and
#: the other way round (`ExecutionShape`; the drafts' `_one_shape`).
_SHAPES = (("tasks", "steps"), ("steps", "tasks"))


class ExtensionError(ValueError):
    """An edit that cannot apply. `loc` is its key path in the extending
    chain's own file."""

    def __init__(self, message: str, loc: tuple[object, ...]) -> None:
        super().__init__(message)
        self.loc = loc


class Added(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    #: A node of the parent, still there after `remove`.
    after: Identifier
    #: A complete chain node, as any chain file writes one.
    node: dict


class Edits(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    remove: list[Identifier] = Field(default_factory=list)
    add: list[Added] = Field(default_factory=list)
    #: Keyed by canonical path (`node`, `node.step`, `node.step.task`, ...).
    overrides: dict[StrictStr, dict] = Field(default_factory=dict)

    @classmethod
    def read(cls, data: Mapping[str, object]) -> Edits:
        try:
            return cls.model_validate({k: data[k] for k in EDIT_KEYS if k in data})
        except ValidationError as exc:
            raise ExtensionError(first_error(exc), tuple(exc.errors()[0]["loc"])) from exc


@dataclass(frozen=True)
class Applied:
    """The edited nodes, still expanded mappings, and where each came from."""

    nodes: list[dict]
    #: Per node: `("parent", its index in the parent's nodes)` or
    #: `("add", its index in `add`)`.
    origins: list[tuple[str, int]]
    removed: tuple[str, ...]
    #: Per parent node id, the override paths at or under it, as applied.
    overridden: dict[str, list[str]]


def apply(
    parent: str,
    nodes: Sequence[Mapping[str, object]],
    paths: Mapping[str, tuple[object, ...]],
    edits: Edits,
) -> Applied:
    """`edits` applied to `nodes`, chain `parent`'s expanded nodes: `remove`,
    then `overrides` (an ancestor path before its descendants), then `add`.
    `paths` maps each canonical path the parent's expansion located to its key
    path in `{"nodes": nodes}`. Raises `ExtensionError` naming the edit."""
    root = {"nodes": [copy.deepcopy(dict(n)) for n in nodes]}
    ids = [n.get("id") for n in root["nodes"]]
    final = next(
        (n["id"] for n in root["nodes"] if n.get("kind") == "gate" and n.get("chain_finalized")),
        None,
    )
    removed = _removed(parent, ids, final, edits.remove)
    overridden = _override(parent, root, paths, removed, edits.overrides)
    after = _anchored(parent, ids, final, removed, edits.add)

    out: list[dict] = []
    origins: list[tuple[str, int]] = []
    for i, node in enumerate(root["nodes"]):
        if node["id"] not in removed:
            out.append(node)
            origins.append(("parent", i))
        for j, new in after.get(node["id"], ()):
            out.append(new)
            origins.append(("add", j))
    _refuse_removed_targets(out, origins, removed)
    return Applied(out, origins, tuple(removed), overridden)


def _removed(parent: str, ids: list, final: object, remove: list[str]) -> list[str]:
    removed: list[str] = []
    for i, id in enumerate(remove):
        where = ("remove", i)
        if id not in ids:
            raise ExtensionError(f"remove {id!r}: {parent} has no node {id!r}", where)
        if id == final:
            # Without it `Chain._publishes_after_the_final_gate` has nothing to
            # check, and the chain could merge with no human approval.
            raise ExtensionError(
                f"remove {id!r}: it is {parent}'s final gate (chain_finalized), which an "
                "extending chain keeps",
                where,
            )
        if id in removed:
            raise ExtensionError(f"remove {id!r}: named twice", where)
        removed.append(id)
    return removed


def _override(
    parent: str,
    root: dict,
    paths: Mapping[str, tuple[object, ...]],
    removed: list[str],
    overrides: Mapping[str, dict],
) -> dict[str, list[str]]:
    overridden: dict[str, list[str]] = {}
    replaced: list[str] = []
    # Ancestors first: a node's override lands before its task's, so the
    # task's merges onto what the node's left.
    for path in sorted(overrides, key=lambda p: p.count(PATH_SEPARATOR)):
        value = overrides[path]
        where = ("overrides", path)
        node = path.split(PATH_SEPARATOR, 1)[0]
        if node in removed:
            raise ExtensionError(f"overrides.{path}: {node!r} is removed by this chain", where)
        if path not in paths:
            raise ExtensionError(f"overrides.{path}: {parent} has no {path!r}", where)
        under = next((r for r in replaced if path.startswith(f"{r}{PATH_SEPARATOR}")), None)
        if under is not None:
            raise ExtensionError(
                f"overrides.{path}: it sits under overrides {under}, which replaces the "
                "list it was in",
                where,
            )
        if fixed := [key for key in IDENTITY if key in value]:
            raise ExtensionError(
                f"overrides.{path}: an override cannot set {', '.join(fixed)}; remove the "
                "node and add one instead",
                where,
            )
        target = _at(root, paths[path])
        merged = _merge(_displaced(target, value), value)
        target.clear()
        target.update(merged)
        if _replaces_a_list(value):
            replaced.append(path)
        overridden.setdefault(node, []).append(path)
    return overridden


def _anchored(
    parent: str, ids: list, final: object, removed: list[str], add: list[Added]
) -> dict[str, list[tuple[int, dict]]]:
    """Each added node under the parent node it goes after, in the order
    written: several after one node go directly after it, as `revise` places
    them."""
    after: dict[str, list[tuple[int, dict]]] = {}
    anchors: dict[str, str] = {}
    finals = 0 if final is None else 1
    for j, added in enumerate(add):
        where = ("add", j)
        id = added.node.get("id")
        if not isinstance(id, str):
            raise ExtensionError("add: the node needs an id", (*where, "node"))
        if id in ids:
            raise ExtensionError(
                f"add {id}: {parent} already has a node {id!r}", (*where, "node", "id")
            )
        if id in anchors:
            raise ExtensionError(f"add {id}: added twice", (*where, "node", "id"))
        if added.after in anchors:
            raise ExtensionError(
                f"add {id}: put it after {anchors[added.after]!r}, below {added.after!r}; an "
                f"added node goes after one of {parent}'s",
                (*where, "after"),
            )
        if added.after not in ids:
            raise ExtensionError(
                f"add {id}: {parent} has no node {added.after!r}", (*where, "after")
            )
        if added.after in removed:
            raise ExtensionError(
                f"add {id}: {added.after!r} is removed by this chain", (*where, "after")
            )
        if added.node.get("chain_finalized") is True:
            finals += 1
            if finals > 1:
                raise ExtensionError(
                    f"add {id}: the chain already has a final gate (chain_finalized); it holds one",
                    (*where, "node", "chain_finalized"),
                )
        anchors[id] = added.after
        after.setdefault(added.after, []).append((j, copy.deepcopy(added.node)))
    return after


#: Nothing set, so layering a policy onto it checks the policy against the one
#: below it alone.
_OPEN = InstancePolicy.from_input(InstancePolicyInput())


def tighten(parent: object, child: object) -> object:
    """The extending chain's `policy`: the parent's with the child's laid over
    it, `deny_tools` and `grants` accumulated
    (`an-extending-chains-policy-only-tightens`). The child is layered onto the
    parent by `InstancePolicy.apply_template_override`, the rule every policy
    scope follows, so it may not widen `allowed_tools` or change a sandbox;
    a scope cap (a budget, a time cap) stays at or under the parent's, as
    `ResolvedChain._check_caps` keeps a scope's under its parent's. A policy
    that does not read is returned as merged, for `Chain` to refuse."""
    if child is None:
        return parent
    if parent is None or not (isinstance(parent, Mapping) and isinstance(child, Mapping)):
        return child
    merged = {**parent, **child}
    try:
        was = TemplatePolicyOverride.model_validate(parent)
        now = TemplatePolicyOverride.model_validate(child)
    except ValidationError:
        return merged
    try:
        _OPEN.apply_template_override(was).apply_template_override(now)
    except PolicyError as exc:
        where = ("policy", exc.field) if exc.field else ("policy",)
        raise ExtensionError(f"policy: {exc}", where) from exc
    for name in SCOPE_CAP_FIELDS:
        before, after = getattr(was, name), getattr(now, name)
        if before is not None and after is not None and after > before:
            raise ExtensionError(
                f"policy.{name}: {after} cannot exceed the parent's {before}", ("policy", name)
            )
    for name in ("deny_tools", "grants"):
        if isinstance(parent.get(name), list) and isinstance(child.get(name), list):
            merged[name] = list(dict.fromkeys([*parent[name], *child[name]]))
    return merged


def _refuse_removed_targets(
    nodes: list[dict], origins: list[tuple[str, int]], removed: list[str]
) -> None:
    """`a-removed-pointer-target-is-named`: run on the result, so an override
    that re-points a `reject_to` or `restart_from` is accepted."""
    for node, (origin, index) in zip(nodes, origins, strict=True):
        base = node.get("on_base_changed")
        pointers = (
            ("reject_to", node.get("reject_to")),
            ("restart_from", base.get("restart_from") if isinstance(base, Mapping) else None),
        )
        for key, target in pointers:
            if target in removed:
                where = (
                    ("add", index, "node", key)
                    if origin == "add"
                    else ("remove", removed.index(target))
                )
                raise ExtensionError(
                    f"{node['id']}'s {key} names {target!r}, which this chain removes; override it",
                    where,
                )


def _at(root: Mapping, loc: tuple[object, ...]) -> dict:
    found: object = root
    for part in loc:
        found = found[part]  # type: ignore[index]
    assert isinstance(found, dict)
    return found


def _displaced(target: Mapping[str, object], value: Mapping[str, object]) -> Mapping[str, object]:
    """A task's route is the nearer layer's whole: `{profile: deep}` drops an
    inherited `model`/`effort` (`displaced_route`)."""
    if target.get("kind") not in _TASK_KINDS:
        return target
    gone = displaced_route(value)
    return {k: v for k, v in target.items() if k not in gone}


def _merge(parent: object, child: object) -> object:
    """The `extends` merge -- maps merge, scalars and lists replace -- except
    that a container given `tasks` loses an inherited `steps`, and the other
    way round."""
    if isinstance(parent, Mapping) and isinstance(child, Mapping):
        merged = dict(parent)
        for key, other in _SHAPES:
            if key in child and other not in child:
                merged.pop(other, None)
        for key, value in child.items():
            merged[key] = _merge(merged[key], value) if key in merged else value
        return merged
    return child


def _replaces_a_list(value: object) -> bool:
    """Whether an override replaces a `tasks` or `steps` list anywhere inside it."""
    if not isinstance(value, Mapping):
        return False
    return any(
        (key in ("tasks", "steps") and isinstance(item, list)) or _replaces_a_list(item)
        for key, item in value.items()
    )
