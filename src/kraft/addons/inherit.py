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
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

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


def tree_order(addons: Sequence[InstalledAddon]) -> tuple[InstalledAddon, ...]:
    """`addons`, parents before children: a stable sort on how many ancestors
    each reaches by `parent` id among `addons` that are not repo libraries,
    counted up to `MAX_DEPTH` or a repeat. Roots, and add-ons whose parent is
    not among them, keep the order given."""
    by_id = {a.id: a for a in addons if a.repo_id is None}

    def depth(addon: InstalledAddon) -> int:
        seen = {addon.id}
        current = by_id.get(addon.parent) if addon.parent is not None else None
        while current is not None and current.id not in seen and len(seen) < MAX_DEPTH:
            seen.add(current.id)
            current = by_id.get(current.parent) if current.parent is not None else None
        return len(seen) - 1

    return tuple(sorted(addons, key=depth))


class Side(NamedTuple):
    """One add-on as the set rule sees it, locked or moved: its version, and
    the parent and range it builds on."""

    version: str
    parent: str | None
    range: str | None


def _breaks(addon_id: str, sides: Mapping[str, Side]) -> list[tuple[str, str]]:
    """Each range `addon_id` breaks against `sides` (every add-on present, at
    its kept or locked side), as `(other id, why)`: its own range or missing
    parent first, then each child's range, in child-id order. A range that
    does not parse breaks, worded with its error."""
    from kraft.addons import manifest

    def outside(range_: str, version: str) -> str | None:
        try:
            return manifest.version_in_range(range_, version)
        except ValueError as exc:
            return str(exc)

    side = sides[addon_id]
    out: list[tuple[str, str]] = []
    if side.parent is not None:
        parent = sides.get(side.parent)
        if parent is None:
            out.append((side.parent, f"its parent {side.parent} is not installed"))
        elif side.range is not None and (why := outside(side.range, parent.version)):
            out.append(
                (
                    side.parent,
                    f"{addon_id}'s range {side.range} does not accept "
                    f"{side.parent} {parent.version} ({why})",
                )
            )
    for child_id in sorted(sides):
        child = sides[child_id]
        if child_id != addon_id and child.parent == addon_id and child.range is not None:
            if why := outside(child.range, side.version):
                out.append(
                    (
                        child_id,
                        f"{addon_id} {side.version} is outside {child_id}'s range "
                        f"{child.range} ({why})",
                    )
                )
    return out


def path(addon_id: str, parents: Mapping[str, str | None]) -> tuple[str, ...] | None:
    """`(addon_id, parent, ..., root)` through `parents`, at most `MAX_DEPTH`
    steps; None when `addon_id` meets a cycle on the way."""
    line = [addon_id]
    while len(line) <= MAX_DEPTH and (parent := parents.get(line[-1])) is not None:
        if parent in line:
            return None
        line.append(parent)
    return tuple(line)


def settle(locked: Mapping[str, Side], moves: Mapping[str, Side]) -> dict[str, str]:
    """Which of `moves` (id to its new side) the set rule drops, each mapped
    to why; the rest are kept. `locked` holds every installed add-on, so a move
    not in it is an install. Every move that breaks a range, judged with the
    other kept moves applied, is dropped at once, until none breaks. Then each
    dropped move is offered again, parents first and then by id, and kept when
    the kept set with it breaks nothing, until a pass keeps nothing new. A move
    still dropped is judged once more, alone with the final kept set, for its
    reason: never one about a move that was later dropped or kept."""

    def sides(kept: Collection[str]) -> dict[str, Side]:
        return {**locked, **{i: moves[i] for i in kept}}

    kept = set(moves)
    while True:
        applied = sides(kept)
        failing = {i for i in kept if _breaks(i, applied)}
        if not failing:
            break
        kept -= failing
    parents = {i: s.parent for i, s in locked.items()}
    parents |= {i: s.parent for i, s in moves.items() if i not in locked}
    order = sorted(set(moves) - kept, key=lambda i: (len(path(i, parents) or (i,)), i))
    offered = True
    while offered:
        offered = False
        for addon_id in order:
            if addon_id in kept:
                continue
            if not _breaks(addon_id, sides(kept | {addon_id})):
                kept.add(addon_id)
                offered = True
    dropped: dict[str, str] = {}

    def reason(addon_id: str, asking: tuple[str, ...] = ()) -> str:
        # A parent install that is dropped too is worded first, whatever its
        # depth, since a locked add-on's new parent can be one.
        if addon_id not in dropped:
            other, why = _breaks(addon_id, sides(kept | {addon_id}))[0]
            if other in moves and other not in kept and other not in locked and other not in asking:
                parent_why = reason(other, (*asking, addon_id))
                why = f"its parent {other} cannot be installed: {parent_why}"
            dropped[addon_id] = why
        return dropped[addon_id]

    for addon_id in order:
        if addon_id not in kept:
            reason(addon_id)
    return dropped


def needed(
    named: Collection[str], locked: Mapping[str, Side], kept: Mapping[str, Side]
) -> frozenset[str]:
    """The ids of `named` among `kept` (moves the set rule kept), plus each
    other kept move they cannot apply without, to a fixed point: one whose
    add-on, back at its locked side or not there for an install, breaks a
    range with a move already needed. The moves returned settle on their own."""
    need = {i for i in named if i in kept}
    while True:
        sides = {**locked, **{i: kept[i] for i in need}}
        more = {other for i in need for other, _ in _breaks(i, sides) if other in kept}
        if more <= need:
            return frozenset(need)
        need |= more


def trees(
    ids: Iterable[str], parents: Mapping[str, str | None], *more: Mapping[str, str | None]
) -> list[tuple[str, ...]]:
    """`ids` grouped by the root each reaches through `parents`, which may
    hold add-ons not among `ids`, followed at most `MAX_DEPTH` steps. With
    `more` maps (the locked parents that move, say), the roots an id reaches
    through any of them are one group: a child that changes parent joins its
    old parent's tree. Groups come in the order they first appear in `ids`;
    within one, by depth through `parents`, then id. An id that meets a
    cycle forms a group of its own, unless another map joins it to one, and
    then sorts at depth 0 within it."""
    joined: dict[object, object] = {}

    def find(root: object) -> object:
        while (up := joined.setdefault(root, root)) != root:
            root = up
        return root

    roots: dict[str, tuple[object, int]] = {}
    for addon_id in dict.fromkeys(ids):
        lines = [path(addon_id, m) for m in (parents, *more)]
        reached = [line[-1] if line is not None else (addon_id,) for line in lines]
        for root in reached[1:]:
            if (a := find(root)) != (b := find(reached[0])):
                joined[a] = b
        roots[addon_id] = (reached[0], len(lines[0] or ()))
    groups: dict[object, list[tuple[int, str]]] = {}
    for addon_id, (root, depth) in roots.items():
        groups.setdefault(find(root), []).append((depth, addon_id))
    return [tuple(i for _, i in sorted(members)) for members in groups.values()]


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


#: The sections a child's `remove:` names entries of: what it may drop from
#: the library it inherits.
REMOVABLE = ("chains", "tasks", "steps", "nodes", "steering", "skills", "profiles")

#: The removable sections that are files in an add-on's store, not entries of
#: its library: what a level inherits of them is what `Visible` says it sees.
FILE_SECTIONS = ("skills", "profiles")

#: The message a `remove:` gets anywhere but in a child add-on's `library.yaml`.
REMOVE_OUTSIDE_A_CHILD = "`remove:` is for an add-on that builds on another"


def removals(path: Path, library: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    """`library`'s (`path`'s content, a `library.yaml`) `remove:` key, section to the
    names it drops from the inherited layer; empty when it has none. Raises
    `TemplateLibraryError` for a section outside `REMOVABLE` or a value that
    is not a list of names."""
    from kraft.templates.library import TemplateLibraryError

    written = library.get("remove")
    if written is None:
        return {}
    if not isinstance(written, Mapping):
        raise TemplateLibraryError(
            f"{path}: 'remove' must be a mapping of section to names, one of {', '.join(REMOVABLE)}"
        )
    out: dict[str, tuple[str, ...]] = {}
    for section, names in written.items():
        if section not in REMOVABLE:
            raise TemplateLibraryError(
                f"{path}: remove.{section}: no such section; one of {', '.join(REMOVABLE)}"
            )
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            raise TemplateLibraryError(f"{path}: remove.{section} must be a list of names")
        out[section] = tuple(names)
    return out


#: What separates a child's namespace from an ancestor level in a layer's
#: namespace (`team^1`). Never shown: `humanize` and `public` take it out.
LAYER_MARK = "^"

#: A layered name: `<top>^<k>:<name>`, wherever it sits in a text. A name
#: runs to whitespace, a quote or a separator, and never ends in a period:
#: `... team^1:scan.` ends a sentence.
_NAME_CHAR = r"[^\s'\"`:,;()\[\]{}<>]"
_LAYERED = re.compile(rf"(?<![\w-])([\w-]+\^\d+):({_NAME_CHAR}+)(?<!\.)")


def _is_layer_reference(value: str) -> bool:
    """Whether `value` starts in the layered shape `<top>^<k>:` (`team^1:scan`).
    Scanned by hand, not a regex: authored text of many `-` would make a
    backtracking pattern slow. A name char is alphanumeric, `_` or `-`
    (`str.isalnum`, close to the old `\\w`)."""
    head, colon, _ = value.partition(":")
    name, caret, depth = head.rpartition("^")
    return bool(
        colon
        and caret
        and depth.isascii()
        and depth.isdigit()
        and name
        and all(ch.isalnum() or ch in "_-" for ch in name)
    )


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

    def names(self, kind: str, from_depth: int = 0) -> tuple[str, ...]:
        """Every `kind` name level `from_depth` sees, sorted."""
        if from_depth >= len(self.levels):
            return ()
        return tuple(sorted(self.levels[from_depth].get(kind, {})))

    def owner(self, kind: str, name: str, from_depth: int) -> str | None:
        """The `namespace:name` of the ancestor whose `kind` (`skills`,
        `profiles`, `steering`) `name` level `from_depth` sees; None when none does."""
        if from_depth >= len(self.levels):
            return None
        return self.levels[from_depth].get(kind, {}).get(name)


def visible(chain: Sequence[InstalledAddon]) -> Visible:
    """`Visible` for `chain` (`ancestry`'s answer): the skills, agent profiles
    and steering profiles each member's store ships, less what each level
    removes. Reads each store through `fetch.open_store`, so a
    `fetch.StoreMismatch` propagates; raises `TemplateLibraryError` for a file
    that does not read."""
    from kraft.addons import fetch
    from kraft.templates import library as lib

    stores = [fetch.open_store(author.root) for author in chain]
    written = [
        lib._read_stored(store, lib.LIBRARY_FILE, author.root / lib.LIBRARY_FILE, optional=True)
        for author, store in zip(chain, stores, strict=True)
    ]
    return Visible.build(
        chain,
        [
            {
                "skills": lib._store_skills(store),
                "profiles": lib._store_profiles(store, author),
                "steering": lib._section(
                    author.root / lib.LIBRARY_FILE, library, lib.Namespace.STEERING
                ).keys(),
            }
            for author, store, library in zip(chain, stores, written, strict=True)
        ],
        [
            removals(author.root / lib.LIBRARY_FILE, library)
            for author, library in zip(chain, written, strict=True)
        ],
    )


def inherited(kind: str, addon: InstalledAddon, addons: Sequence[InstalledAddon]) -> dict[str, str]:
    """For each `kind` (`skills` or `profiles`) name `addon` sees at depth 0
    that an ancestor defines, the defining ancestor's namespace. Empty for a
    root add-on, one that is left out, and one whose ancestry or stores do not
    read. A name `addon` defines itself, or removed, is not in it."""
    if addon.left_out is not None or addon.parent is None:
        return {}
    try:
        return _inherited(kind, addon, addons)
    except _unreadable_tree():
        return {}


def _unreadable_tree() -> tuple[type[Exception], ...]:
    """What reading a child's tree raises when it does not read: its ancestry
    does not resolve, a store was edited, or a file does not parse."""
    from kraft.addons import fetch

    # library imports this module at module level
    from kraft.templates.library import TemplateLibraryError

    return (InheritanceError, fetch.StoreMismatch, TemplateLibraryError, OSError)


def _inherited(
    kind: str, addon: InstalledAddon, addons: Sequence[InstalledAddon]
) -> dict[str, str]:
    """`inherited` for a loaded child, raising what `_unreadable_tree` names."""
    seen = visible(ancestry(addon, addons))
    out: dict[str, str] = {}
    for name in seen.names(kind):
        owner = (seen.owner(kind, name, 0) or "").partition(":")[0]
        if owner and owner != addon.namespace:
            out[name] = owner
    return out


def skill_dirs(addons: Sequence[InstalledAddon]) -> dict[str, Path | None]:
    """Where `kraft.skill` reads an add-on's skill: each namespace to its
    store, or to None when its add-on is left out (a reference into it is
    refused, never handed to the agent as another tool's skill). A loaded
    namespace wins over a left-out one of the same name.

    Each skill a loaded child inherits is also a key, `<child>:<skill>`, to
    the store of the ancestor that defines it, so a reference from outside
    the tree names it by the child's name. A skill the child ships itself, or
    removed, has no key: it is looked for in the child's own store. A child
    whose tree does not read maps to None, with no keys, so its skills are
    refused as not loaded rather than looked for in its own store."""
    dirs: dict[str, Path | None] = {}
    for addon in addons:
        if addon.left_out is None:
            dirs[addon.namespace] = addon.root
        else:
            dirs.setdefault(addon.namespace, None)
    for addon in addons:
        if addon.left_out is not None or addon.parent is None:
            continue
        try:
            owners = _inherited("skills", addon, addons)
        except _unreadable_tree():
            dirs[addon.namespace] = None
            continue
        for name, owner in owners.items():
            # An ancestor is loaded (`ancestry` says so), so this is its store.
            dirs[f"{addon.namespace}:{name}"] = dirs.get(owner)
    return dirs


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
        layered = [v for v in values if isinstance(v, str) and _is_layer_reference(v)]
        if key in _REFERENCE_KEYS and layered:
            return layered[0]
        if (found := layer_reference(value)) is not None:
            return found
    return None
