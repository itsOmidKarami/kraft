"""An add-on that builds on another: its ancestry, and the left-out cascade.

An add-on names at most one parent in its `addon.json` (`requires.addons`),
and that parent may name its own, so installed add-ons form a tree. A child
loads only when every ancestor loads, accepts its parent's version, and the
tree has no cycle, no ancestor sharing a descendant's name, and no more than
`MAX_DEPTH` levels.

Kept a leaf like `kraft.addons.load`: nothing from `kraft.templates` or
`kraft.config` is imported at module level, since the template library reads
this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from kraft.addons.load import InstalledAddon

if TYPE_CHECKING:
    from kraft.addons.manifest import AddonManifest, Requires

#: The deepest tree an instance loads, the root included.
MAX_DEPTH = 8


class InheritanceError(Exception):
    """Why an add-on's ancestry does not resolve, worded as its `left_out`.
    `cycle` holds the ids of the add-ons in a cycle it found, else is empty."""

    def __init__(self, message: str, cycle: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.cycle = tuple(cycle)


def ancestry(addon: InstalledAddon, addons: Sequence[InstalledAddon]) -> tuple[InstalledAddon, ...]:
    """`(addon, parent, ..., root)`, each parent looked up by id among `addons`
    that are not repo libraries. Raises `InheritanceError` naming the first
    link that does not hold: a parent not installed or not loaded, a parent
    outside its child's range, a cycle, a tree deeper than `MAX_DEPTH`, or an
    ancestor whose name a descendant has."""
    from kraft.addons import manifest

    by_id = {a.id: a for a in addons if a.repo_id is None}
    chain = [addon]
    current = addon
    while current.parent is not None:
        parent_id = current.parent
        ids = [a.id for a in chain]
        if parent_id in ids:
            members = ids[ids.index(parent_id) :]
            loop = " -> ".join([*members, parent_id])
            raise InheritanceError(f"its ancestry {loop} is a cycle", cycle=members)
        parent = by_id.get(parent_id)
        if parent is None:
            raise InheritanceError(f"its parent {parent_id} is not installed")
        if parent.left_out is not None:
            raise InheritanceError(f"its parent {parent_id} is not loaded: {parent.left_out}")
        if current.parent_range is not None:
            try:
                why = manifest.version_in_range(current.parent_range, parent.version)
            except ValueError as exc:
                why = str(exc)
            if why:
                raise InheritanceError(
                    f"its parent {parent_id} is {parent.version}, outside its range "
                    f"{current.parent_range} ({why})"
                )
        if len(chain) >= MAX_DEPTH:
            raise InheritanceError(f"its ancestry is deeper than {MAX_DEPTH} add-ons")
        if any(a.name == parent.name for a in chain):
            raise InheritanceError(f"its ancestor {parent_id} has its name {parent.name}")
        chain.append(parent)
        current = parent
    return tuple(chain)


def cascade(addons: Sequence[InstalledAddon]) -> tuple[InstalledAddon, ...]:
    """`addons`, in the same order, with every loading add-on whose ancestry
    does not resolve left out with why, repeated until nothing changes. A
    child waits for its failing parent to be left out first, so its reason
    names its own parent: `its parent <id> is not loaded: <parent's reason>`."""
    current = list(addons)
    while True:
        failing: dict[int, InheritanceError] = {}
        for i, addon in enumerate(current):
            if addon.left_out is None:
                try:
                    ancestry(addon, current)
                except InheritanceError as exc:
                    failing[i] = exc
        if not failing:
            return tuple(current)
        failing_ids = {current[i].id for i in failing}
        # Parents first. When every failing add-on waits on another, there is a
        # cycle: its members go, and what hangs below them waits a round more.
        first = [i for i in failing if current[i].parent not in failing_ids]
        if not first:
            looped = {id for exc in failing.values() for id in exc.cycle}
            first = [i for i in failing if current[i].id in looped] or list(failing)
        for i in first:
            current[i] = replace(current[i], left_out=str(failing[i]))


def read_manifest(addon: InstalledAddon) -> AddonManifest:
    """`addon`'s `addon.json`, read through its store's digest: never a FIFO's
    endless read, and never an edit. Raises what the store or the manifest
    raises."""
    from kraft.addons import fetch, manifest

    text = (fetch.open_store(addon.root).read(manifest.ADDON_JSON) or b"").decode()
    return manifest.addon(manifest.parse(text, manifest.ADDON_JSON), manifest.ADDON_JSON)


def requirements(
    chain: Sequence[InstalledAddon], manifests: Mapping[str, AddonManifest] | None = None
) -> Requires:
    """What `chain` (`ancestry`'s answer) needs from the instance together: the
    union of every member's harnesses and agent profiles, in chain order, and
    `chain[0]`'s `requires.kraft`. Inherited tasks name the harnesses and bare
    profiles their own authors required. `manifests` (id to manifest) are
    already-read manifests to use in place of reading the stores again."""
    from kraft.addons.manifest import Requires

    known = manifests or {}
    declared = [(known.get(a.id) or read_manifest(a)).requires for a in chain]
    harnesses: list[str] = []
    profiles: list[str] = []
    for requires in declared:
        harnesses += [h for h in requires.harnesses if h not in harnesses]
        profiles += [p for p in requires.profiles if p not in profiles]
    return Requires(kraft=declared[0].kraft, harnesses=harnesses, profiles=profiles)


#: What separates a child's namespace from an ancestor level in a layer's
#: namespace (`team^1`). Never shown: `humanize` and `public` take it out.
LAYER_MARK = "^"

#: A layered name: `<top>^<k>:<name>`, wherever it sits in a text. A name
#: runs to whitespace, a quote or a separator, and never ends in a period:
#: `... team^1:scan.` ends a sentence.
_NAME_CHAR = r"[^\s'\"`:,;()\[\]{}<>]"
_LAYERED = re.compile(rf"(?<![\w-])([\w-]+\^\d+):({_NAME_CHAR}+)(?<!\.)")
#: A reference written in the layered shape (`team^1:scan`).
_LAYER_REFERENCE = re.compile(r"[\w-]+\^\d+:")


def layer_namespace(top: str, depth: int) -> str:
    """Where level `depth` of `top`'s ancestry is registered: `top` itself for
    the child's merged library (0), else `top^<depth>`, a hidden namespace
    holding that ancestor's merged library as `top` inherits it."""
    return top if depth == 0 else f"{top}{LAYER_MARK}{depth}"


def is_layer(name: str) -> bool:
    """Whether `name` (`team^1:scan`, or a bare qualifier) is in a hidden layer."""
    return LAYER_MARK in name.partition(":")[0]


def requalify(
    data: object,
    *,
    top: str,
    depth: int,
    author: InstalledAddon,
    parent_name: str | None,
    skill_owner: Callable[[str, int], str],
    profile_owner: Callable[[str, int], str],
    steering_owner: Callable[[str, int], str],
) -> object:
    """`data`, a file `author` wrote, with its references re-pointed for its
    place at `depth` in `top`'s ancestry. Walks it like `load.qualify`.

    A bare `extends` or `steering` name, or one qualified by the author's own
    name, means `top`'s merged library, so an inherited entry reads the
    child's overrides; the parent's name means the layer below the author's.
    A skill or qualified agent profile is a file in one store: it becomes the
    namespace of the nearest ancestor defining it, looked for from the top
    child down (own or bare) or from the author's parent down (the parent's
    name). A steering profile holds no reference of its own, so the parent's
    name in `steering` is re-pointed the same way, to the ancestor's own
    namespace: a hidden layer's name never reaches a resolved chain. A bare
    `profile:` is the instance's and stays. Any other qualifier is left as
    written."""
    below = depth + 1

    def library_ref(value: str) -> str:
        qualifier, colon, name = value.partition(":")
        if not colon:
            return f"{top}:{value}"
        if qualifier == author.name:
            return f"{top}:{name}"
        if qualifier == parent_name:
            return f"{layer_namespace(top, below)}:{name}"
        return value

    def file_ref(value: str, owner: Callable[[str, int], str], *, bare_is_own: bool) -> str:
        qualifier, colon, name = value.partition(":")
        if not colon:
            return owner(value, 0) if bare_is_own else value
        if qualifier == author.name:
            return owner(name, 0)
        if qualifier == parent_name:
            return owner(name, below)
        return value

    def steering_ref(value: str) -> str:
        qualifier, colon, name = value.partition(":")
        if colon and qualifier == parent_name:
            return steering_owner(name, below)
        return library_ref(value)

    def walk(node: object) -> object:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, Mapping):
            return node
        out: dict[object, object] = {}
        for key, value in node.items():
            if key == "extends" and isinstance(value, str):
                out[key] = library_ref(value)
            elif key == "steering" and isinstance(value, list):
                out[key] = [steering_ref(v) if isinstance(v, str) else v for v in value]
            elif key == "skill" and isinstance(value, str):
                out[key] = file_ref(value, skill_owner, bare_is_own=True)
            elif key == "profile" and isinstance(value, str):
                out[key] = file_ref(value, profile_owner, bare_is_own=False)
            else:
                out[key] = walk(value)
        return out

    return walk(data)


@dataclass(frozen=True)
class Visible:
    """Per depth of an ancestry, which ancestor defines each skill, agent
    profile and steering profile name: what a child sees of the files its ancestors ship, after
    each level's removals."""

    #: `levels[d][kind][name]` is the `namespace:name` level `d` sees.
    levels: tuple[Mapping[str, Mapping[str, str]], ...]

    @classmethod
    def build(
        cls,
        chain: Sequence[InstalledAddon],
        defined: Sequence[Mapping[str, Collection[str]]],
        removed: Sequence[Mapping[str, Collection[str]]] = (),
    ) -> Visible:
        """From the root down: at each level, first drop that level's removed
        names (`removed[d]`, kind to names), then add its own (`defined[d]`)."""
        levels: list[Mapping[str, Mapping[str, str]]] = [{}] * len(chain)
        seen: dict[str, dict[str, str]] = {}
        for d in reversed(range(len(chain))):
            gone = removed[d] if d < len(removed) else {}
            seen = {
                kind: {n: owner for n, owner in names.items() if n not in gone.get(kind, ())}
                for kind, names in seen.items()
            }
            for kind, names in defined[d].items():
                seen[kind] = {
                    **seen.get(kind, {}),
                    **{n: f"{chain[d].namespace}:{n}" for n in names},
                }
            levels[d] = seen
        return cls(tuple(levels))

    def owner(self, kind: str, name: str, from_depth: int) -> str | None:
        """The `namespace:name` of the ancestor whose `kind` (`skills`,
        `profiles`, `steering`) `name` level `from_depth` sees; None when none does."""
        if from_depth >= len(self.levels):
            return None
        return self.levels[from_depth].get(kind, {}).get(name)


def humanize(text: str, layers: Mapping[str, str]) -> str:
    """`text` with each `<top>^<k>:<name>` written as `<ancestor>'s <name>, as
    <top> inherits it`. `layers` maps `top^k` to the namespace this instance
    loads that ancestor under (its alias, when it has one), which is how the
    instance's own listings name it."""

    def say(match: re.Match[str]) -> str:
        ancestor = layers.get(match[1])
        if ancestor is None:
            return match[0]
        top = match[1].partition(LAYER_MARK)[0]
        return f"{ancestor}'s {match[2]}, as {top} inherits it"

    return _LAYERED.sub(say, text)


def public(data: object, layers: Mapping[str, str]) -> object:
    """`data` (a string, or a mapping or list of them) with each
    `<top>^<k>:<name>` written as the ancestor's own `<ancestor>:<name>`: what
    a listing shows as the parent of an entry a child builds on."""
    if isinstance(data, str):
        return _LAYERED.sub(lambda m: f"{layers[m[1]]}:{m[2]}" if m[1] in layers else m[0], data)
    if isinstance(data, list):
        return [public(item, layers) for item in data]
    if isinstance(data, Mapping):
        return {key: public(value, layers) for key, value in data.items()}
    return data


#: The keys whose values name a library entry, a skill or an agent profile.
_REFERENCE_KEYS = ("extends", "steering", "skill", "profile")


def layer_reference(data: object) -> str | None:
    """The first reference in `data`, an authored file, that names a hidden
    layer (`team^1:scan`); None when there is none. Only a child's own
    layering writes one: a person's file reaching it would bypass the
    parent's name."""
    if isinstance(data, list):
        return next((found for item in data if (found := layer_reference(item))), None)
    if not isinstance(data, Mapping):
        return None
    for key, value in data.items():
        values = value if key == "steering" and isinstance(value, list) else [value]
        layered = [v for v in values if isinstance(v, str) and _LAYER_REFERENCE.match(v)]
        if key in _REFERENCE_KEYS and layered:
            return layered[0]
        if (found := layer_reference(value)) is not None:
            return found
    return None
