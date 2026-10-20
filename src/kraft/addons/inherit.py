"""An add-on that builds on another: its ancestry, and the left-out cascade.

An add-on names at most one parent in its `addon.json` (`requires.addons`),
and that parent may name its own, so installed add-ons form a tree. A child
loads only when every ancestor loads, accepts its parent's version, and the
tree has no cycle, no ancestor sharing a descendant's name, and no more than
`MAX_DEPTH` levels.

Imports nothing from `kraft.templates` or `kraft.config` at module level, like
`kraft.addons.load`: the template library reads this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from kraft.addons.load import InstalledAddon

if TYPE_CHECKING:
    from kraft.addons.manifest import AddonManifest, Requires

#: The deepest tree an instance loads, the root included.
MAX_DEPTH = 8


class InheritanceError(Exception):
    """Why an add-on's ancestry does not resolve, worded as its `left_out`."""


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
            loop = [*ids[ids.index(parent_id) :], parent_id]
            raise InheritanceError(f"its ancestry {' -> '.join(loop)} is a cycle")
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
        # Parents first; when every failing add-on waits on another (a cycle),
        # all of them go at once.
        first = [i for i in failing if current[i].parent not in failing_ids] or list(failing)
        for i in first:
            current[i] = replace(current[i], left_out=str(failing[i]))


def read_manifest(addon: InstalledAddon) -> AddonManifest:
    """`addon`'s `addon.json`, read through its store's digest: never a FIFO's
    endless read, and never an edit. Raises what the store or the manifest
    raises."""
    from kraft.addons import fetch, manifest

    text = (fetch.open_store(addon.root).read(manifest.ADDON_JSON) or b"").decode()
    return manifest.addon(manifest.parse(text, manifest.ADDON_JSON), manifest.ADDON_JSON)


def requirements(chain: Sequence[InstalledAddon]) -> Requires:
    """What `chain` (`ancestry`'s answer) needs from the instance together: the
    union of every member's harnesses and agent profiles, and `chain[0]`'s
    `requires.kraft`. Inherited tasks name the harnesses and bare profiles
    their own authors required."""
    from kraft.addons.manifest import Requires

    declared = [read_manifest(a).requires for a in chain]
    harnesses: list[str] = []
    profiles: list[str] = []
    for requires in declared:
        harnesses += [h for h in requires.harnesses if h not in harnesses]
        profiles += [p for p in requires.profiles if p not in profiles]
    return Requires(kraft=declared[0].kraft, harnesses=harnesses, profiles=profiles)
