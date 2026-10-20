"""Loading a V1 template directory and resolving its chains.

The layout is docs/templates-v1-design.md's "Configuration layout": one
`library.yaml` of reusable steering profiles, tasks, steps and nodes, and one
selectable chain per file under `chains/`
(`template-library-has-shared-components-and-chain-files`).

This module owns the two things a typed model cannot do for itself: boundary
I/O, and `extends` expansion -- which has to happen on raw mappings, because a
derived component inherits its parent's `kind` and so is not a valid typed task
or node until the merge is done. Everything after the merge belongs to
`kraft.templates.models`.

Every failure is a `TemplateLibraryError` naming the file and the component an
author has to correct (`template-resolution-preserves-source-context`).
"""

from __future__ import annotations

import functools
from collections import ChainMap
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import ValidationError

from kraft import skill as _skill
from kraft.addons import inherit
from kraft.addons.load import InstalledAddon, alias_extends, builds_on, qualify
from kraft.config import bounded_yaml
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError
from kraft.templates import extension, positions
from kraft.templates.models import (
    AUTO_REVIEW_SEGMENT,
    JUDGE_SEGMENT,
    MAIN_STEP,
    PATH_SEPARATOR,
    Chain,
    NodeKind,
    ResolvedChain,
    SteeringProfile,
    TaskKind,
    displaced_route,
    first_error,
)
from kraft.templates.positions import Position

LIBRARY_FILE = "library.yaml"
CHAINS_DIR = "chains"
#: Every pre-V1 home was seeded with one, and V1 has no reader for it.
LEGACY_REGISTRY = "registry.yaml"

#: The section a chain's author is filed under (`TemplateLibrary.authored_by`).
_CHAINS = "chains"

#: How many YAML nodes one add-on file may expand to (`config.bounded_yaml`):
#: the same budget a repository's own config gets.
ADDON_YAML_NODES = 20_000


def is_pre_v1(path: str | Path) -> bool:
    """Whether the template directory at `path` holds the legacy, pre-V1
    configuration: a hook registry and no V1 `library.yaml`. Such a home is not
    converted (`template-v1-is-not-backward-compatible`); `kraft admin update`
    backs it up and replaces it once the operator accepts
    (`major-update-requires-explicit-acceptance`)."""
    root = Path(path)
    return (root / LEGACY_REGISTRY).is_file() and not (root / LIBRARY_FILE).is_file()


#: Discriminator tags pydantic inserts into a tagged-union error location. They
#: are not authored structure, so they are dropped before a location is matched
#: against the components this resolver expanded.
_UNION_TAGS = frozenset({*TaskKind, *NodeKind})


class TemplateLibraryError(Exception):
    """A library or chain that cannot be used, and -- when the raise site knows
    it -- where: `loc` is a key path into the file the message names, `related`
    the `(file, key path)` of a definition inherited from `library.yaml`.
    `errors` is every schema failure, not only the one the message names:
    `(key path into the chain file, message)` each (the drafts' problem list)."""

    def __init__(
        self,
        message: str,
        *,
        loc: tuple[object, ...] | None = None,
        related: tuple[Path, tuple[object, ...]] | None = None,
        errors: tuple[tuple[tuple[object, ...], str], ...] = (),
        addon_ref: bool = False,
    ) -> None:
        super().__init__(message)
        self.loc = loc
        self.related = related
        self.errors = errors
        #: The failure is a reference into an add-on namespace that is not loaded.
        self.addon_ref = addon_ref


@dataclass(frozen=True)
class TemplateIssue:
    """One thing wrong with an authored library, named where an author can find
    it (`template-resolution-preserves-source-context`)."""

    file: Path
    #: `None` for an issue in `library.yaml` itself, which no one chain owns.
    chain: str | None
    message: str
    #: Key path into `file`; `None` when the raise site could not say.
    loc: tuple[object, ...] | None = None
    #: `(library.yaml, key path)` of an inherited definition the issue is about.
    related: tuple[Path, tuple[object, ...]] | None = None
    #: The YAML parser's own position, for a file that does not parse.
    mark: Position | None = None
    #: `TemplateLibraryError.errors`: every schema failure, when there were several.
    errors: tuple[tuple[tuple[object, ...], str], ...] = ()
    #: `TemplateLibraryError.addon_ref`.
    addon_ref: bool = False

    def __str__(self) -> str:
        return f"{self.chain or self.file}: {self.message}"

    @classmethod
    def from_error(cls, file: Path, chain: str | None, exc: BaseException) -> TemplateIssue:
        return cls(
            file=file,
            chain=chain,
            message=str(exc),
            loc=getattr(exc, "loc", None),
            related=getattr(exc, "related", None),
            mark=positions.yaml_mark(exc),
            errors=getattr(exc, "errors", ()),
            addon_ref=getattr(exc, "addon_ref", False),
        )


@dataclass(frozen=True)
class LintReport:
    """`TemplateLibrary.lint_dir`'s answer: the chains that resolve, and every
    issue with the rest."""

    chains: tuple[str, ...]
    issues: tuple[TemplateIssue, ...]
    #: Offline only: chains left unjudged because they reference an add-on.
    unchecked: tuple[TemplateIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues


class Namespace(StrEnum):
    """The `library.yaml` sections, which are also the `extends` namespaces: a
    component extends one parent of its own kind and no other
    (`component-extends-has-one-parent`)."""

    STEERING = "steering"
    TASKS = "tasks"
    STEPS = "steps"
    NODES = "nodes"

    @property
    def singular(self) -> str:
        return {
            Namespace.STEERING: "steering profile",
            Namespace.TASKS: "task",
            Namespace.STEPS: "step",
            Namespace.NODES: "node",
        }[self]


#: Namespaces `extends` can expand. A steering profile is named text, not a
#: composable component.
_EXTENDABLE = (Namespace.TASKS, Namespace.STEPS, Namespace.NODES)


@dataclass(frozen=True)
class ComponentSource:
    """Where one authored component came from, so an error names the input to
    correct rather than only the failure."""

    file: Path
    namespace: Namespace
    name: str
    #: The component is a whole chain file, named `chain <id>`, not a
    #: `library.yaml` section entry.
    chain: bool = False

    def __str__(self) -> str:
        if self.chain:
            return f"{self.file}: chain {self.name}"
        return f"{self.file}: {self.namespace.value}.{self.name}"


@dataclass(frozen=True)
class RawComponent:
    """One authored mapping, before `extends` expansion and before typing."""

    data: Mapping[str, object]
    source: ComponentSource


def _merge(parent: object, child: object) -> object:
    """`extends-merges-objects-and-replaces-arrays`: mappings merge
    recursively, arrays replace their inherited array as a whole, and any
    scalar -- including an explicit `null` -- replaces."""
    if isinstance(parent, Mapping) and isinstance(child, Mapping):
        merged = dict(parent)
        for key, value in child.items():
            merged[key] = _merge(parent[key], value) if key in parent else value
        return merged
    return child


def _read(path: Path, *, bounded: bool = False) -> Mapping[str, object]:
    """One YAML file as a mapping. `bounded` for a file an add-on's author
    wrote: alias expansion is budgeted, where the operator's own file is not."""
    try:
        text = path.read_text()
        data = bounded_yaml(text, [ADDON_YAML_NODES]) if bounded else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise TemplateLibraryError(f"{path}: cannot read/parse: {exc}") from exc
    return _as_mapping(path, data)


def _read_stored(store, rel: str, path: Path, *, optional: bool = False) -> Mapping[str, object]:
    """One YAML file of an add-on's store (`fetch.StoreFiles`), read through
    its digest, as a mapping; `path` is the file an error names. With
    `optional`, a file the store neither lists nor holds is empty."""
    from kraft.addons import fetch  # fetch imports kraft.config, which imports templates

    try:
        raw = store.read(rel, optional=optional)
        data = None if raw is None else bounded_yaml(raw.decode(), [ADDON_YAML_NODES])
    except fetch.StoreMismatch as exc:
        raise TemplateLibraryError(f"{path}: {exc}") from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise TemplateLibraryError(f"{path}: cannot read/parse: {exc}") from exc
    return _as_mapping(path, data)


def _store_skills(store) -> set[str]:
    """The skills an add-on's store (`fetch.StoreFiles`) ships, by name."""
    return {
        parts[1]
        for rel in store.paths("skills/")
        if len(parts := rel.split("/")) == 3 and parts[2] == "SKILL.md"
    }


def _store_profiles(store, addon: InstalledAddon) -> set[str]:
    """The agent profiles an add-on's store's `profiles.yaml` declares, by
    name. A file that does not read declares none here: the profile table
    says why."""
    path = addon.root / "profiles.yaml"
    try:
        profiles = _read_stored(store, "profiles.yaml", path, optional=True).get("profiles")
    except TemplateLibraryError:
        return set()
    return {str(name) for name in profiles} if isinstance(profiles, Mapping) else set()


def _as_mapping(path: Path, data: object) -> Mapping[str, object]:
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise TemplateLibraryError(f"{path}: expected a mapping")
    return data


def _refuse_qualified(path: Path, what: str, name: object) -> None:
    """A declaration is bare, in local files and in an add-on's own: `:` only
    ever appears in a reference to an add-on's declaration."""
    if ":" in str(name):
        raise TemplateLibraryError(
            f"{path}: {what} {name!r}: a name is declared bare; '<addon>:<name>' is how "
            "an add-on's declaration is referenced"
        )


def _refuse_layer_reference(path: Path, data: Mapping[str, object]) -> None:
    """No file reaches a child add-on's hidden ancestor levels by name: a
    child reads its parent's entries by the parent's name."""
    if (found := inherit.layer_reference(data)) is not None:
        raise TemplateLibraryError(
            f"{path}: {found!r} is no name a library declares; an add-on reads its "
            "parent's entries by the parent's name"
        )


def _refuse_remove(path: Path, data: Mapping[str, object]) -> None:
    """A `remove:` drops entries from an inherited layer, which only a child
    add-on has: anywhere else it would silently do nothing."""
    if "remove" in data:
        raise TemplateLibraryError(f"{path}: {inherit.REMOVE_OUTSIDE_A_CHILD}")


def _section(path: Path, data: Mapping[str, object], namespace: Namespace) -> Mapping[str, object]:
    section = data.get(namespace.value) or {}
    if not isinstance(section, Mapping):
        raise TemplateLibraryError(
            f"{path}: {namespace.value!r} must be a mapping of name to definition"
        )
    for name, body in section.items():
        _refuse_qualified(path, f"{namespace.value} key", name)
        if not isinstance(body, Mapping):
            raise TemplateLibraryError(f"{path}: {namespace.value}.{name} must be a mapping")
    return section


def _chain_id(chain_path: Path, body: Mapping[str, object]) -> str:
    id = body.get("id") or chain_path.stem
    if not isinstance(id, str):
        raise TemplateLibraryError(f"{chain_path}: 'id' must be a string")
    return id


def _steering_profile(body: object, source: ComponentSource) -> SteeringProfile:
    try:
        return SteeringProfile.model_validate(body)
    except ValidationError as exc:
        raise TemplateLibraryError(
            f"{source}: {first_error(exc)}",
            loc=(Namespace.STEERING.value, source.name, *exc.errors()[0]["loc"]),
        ) from exc


class TemplateLibrary:
    """One template directory's authored configuration: reusable components,
    named steering profiles, and the selectable chains."""

    def __init__(
        self,
        components: dict[Namespace, dict[str, RawComponent]],
        chains: Mapping[str, RawComponent],
        steering: dict[str, SteeringProfile],
        skills_dir: Path | None = None,
        addons: Sequence[InstalledAddon] = (),
        library_path: Path | None = None,
    ) -> None:
        self._components = components
        self._chains = dict(chains)
        self.steering = steering
        #: A child add-on's hidden ancestor levels (`inherit.layer_namespace`),
        #: under `<top>^<depth>:<name>`: what its parent's name reads. Looked
        #: up after the public maps, never listed.
        self._layer_components: dict[Namespace, dict[str, RawComponent]] = {
            ns: {} for ns in _EXTENDABLE
        }
        self._layer_chains: dict[str, RawComponent] = {}
        #: Each layer namespace (`team^1`) to the namespace of the ancestor
        #: it holds, for `inherit.humanize` and `inherit.public`.
        self._layers: dict[str, str] = {}
        #: `(section, name)` to the add-on whose file a child's entry was read
        #: from (`authored_by`); a root add-on's entries are not listed.
        self._authors: dict[tuple[str, str], InstalledAddon] = {}
        #: Each child add-on's `remove:` names its parent does not have, as
        #: `<top>: remove.<section>: <name> is not inherited from <parent>`:
        #: recorded at load, which they do nothing to, and never raised there.
        self.stale_removals: tuple[str, ...] = ()
        #: The `library.yaml` this was read from, whose directory also holds
        #: `harnesses.yaml`. `None` for a library built with no file behind it.
        self.library_path = library_path
        #: Where an operator may overlay a method file (`kraft.skill`); `None`
        #: means the bundled methods only.
        self.skills_dir = skills_dir
        #: The installed add-ons whose declarations sit in the maps above under
        #: `<namespace>:<name>`.
        self.addons = tuple(addons)

    @functools.cached_property
    def addon_dirs(self) -> dict[str, Path | None]:
        """Namespace to store directory, for `skill.validate`, and each skill a
        child inherits as `<child>:<skill>` to its defining ancestor's store
        (`inherit.skill_dirs`). None for an add-on that is known but not
        loaded: a reference into it is refused, never handed to the agent as
        another tool's skill. Computed on first use: it reads each child's
        tree, after `_add_addon` has taken every add-on."""
        return inherit.skill_dirs(self.addons)

    @classmethod
    def from_yaml_dir(
        cls,
        path: str | Path,
        *,
        skills_dir: Path | None = None,
        issues: list[TemplateIssue] | None = None,
        addons: Sequence[InstalledAddon] = (),
    ) -> TemplateLibrary:
        """Read `library.yaml` and every `chains/*.yaml` under `path`. The only
        boundary I/O here; a read or parse failure becomes a
        `TemplateLibraryError` naming the file. `skills_dir` is the method
        overlay a task's `skill:` resolves against, beside the bundled ones.

        Given `issues`, a chain file that cannot be read or claims a taken id is
        recorded there and left out instead, so lint can report every bad file
        rather than the first (`template-lint-reports-library-validity`). A bad
        `library.yaml` still raises: there is nothing to resolve a chain against.
        """
        root = Path(path)
        library_path = root / LIBRARY_FILE
        if is_pre_v1(root):
            raise TemplateLibraryError(
                f"{root} holds a Kraft 0.x template configuration (a registry.yaml and no "
                "library.yaml), which this Kraft does not run; `kraft admin update` backs it "
                "up and installs the current one"
            )
        if not library_path.is_file():
            raise TemplateLibraryError(f"{library_path}: no {LIBRARY_FILE} to read")
        library = cls.from_mappings(
            _read(library_path),
            (),
            library_path=library_path,
            skills_dir=skills_dir,
            addons=addons,
        )
        for chain_path in sorted((root / CHAINS_DIR).glob("*.yaml")):
            try:
                library._add_chain(chain_path, _read(chain_path))
            except TemplateLibraryError as exc:
                if issues is None:
                    raise
                issues.append(TemplateIssue.from_error(chain_path, chain_path.stem, exc))
        return library

    @classmethod
    def from_mappings(
        cls,
        library: Mapping[str, object],
        chains: Iterable[tuple[Path, Mapping[str, object]]],
        *,
        library_path: Path,
        skills_dir: Path | None = None,
        addons: Sequence[InstalledAddon] = (),
    ) -> TemplateLibrary:
        """A library from already-parsed mappings: `library` is `library.yaml`'s
        content and each chain is paired with the file it is named after --
        real for `from_yaml_dir`, nominal for an unsaved library, which is
        resolved without ever being written (`POST /templates/resolve`)."""
        _refuse_layer_reference(library_path, library)
        _refuse_remove(library_path, library)
        components = {
            namespace: {
                name: RawComponent(body, ComponentSource(library_path, namespace, name))
                for name, body in _section(library_path, library, namespace).items()
            }
            for namespace in _EXTENDABLE
        }

        steering = {
            name: _steering_profile(body, ComponentSource(library_path, Namespace.STEERING, name))
            for name, body in _section(library_path, library, Namespace.STEERING).items()
        }

        built = cls(components, {}, steering, skills_dir, addons, library_path)
        for addon in addons:
            if addon.left_out is None:
                built._add_addon(addon)
        for chain_path, body in chains:
            built._add_chain(chain_path, body)
        return built

    def _add_addon(self, addon: InstalledAddon) -> None:
        """One add-on's `library.yaml` and `chains/`, under its namespace. Its
        own references are qualified first (`addons.load.qualify`), so the
        resolver only ever sees qualified names. Every `ComponentSource` is the
        file in the add-on's store: that is the file an error names. Every
        file is read through the store's digest, and the chains are the ones
        its manifest lists, so an edited store is refused, never read."""
        from kraft.addons import fetch  # fetch imports kraft.config, which imports templates

        if addon.parent is not None:
            try:
                ancestry = inherit.ancestry(addon, self.addons)
            except inherit.InheritanceError as exc:
                raise TemplateLibraryError(f"{addon.root}: {exc}") from exc
            self._add_inherited(addon, ancestry)
            return
        try:
            store = fetch.open_store(addon.root)
        except fetch.StoreMismatch as exc:
            raise TemplateLibraryError(f"{addon.root / fetch.DIGEST_FILE}: {exc}") from exc
        library_path = addon.root / LIBRARY_FILE
        written = _read_stored(store, LIBRARY_FILE, library_path, optional=True)
        _refuse_layer_reference(library_path, written)
        _refuse_remove(library_path, written)
        library = qualify(written, addon)
        assert isinstance(library, Mapping)
        aliases = self._listed_namespaces(addon, library)
        for namespace in _EXTENDABLE:
            for name, body in _section(library_path, library, namespace).items():
                self._components[namespace][f"{addon.namespace}:{name}"] = RawComponent(
                    body, ComponentSource(library_path, namespace, name)
                )
        for name, body in _section(library_path, library, Namespace.STEERING).items():
            self.steering[f"{addon.namespace}:{name}"] = _steering_profile(
                body, ComponentSource(library_path, Namespace.STEERING, name)
            )
        for rel in store.paths(f"{CHAINS_DIR}/"):
            if not rel.endswith(".yaml") or "/" in rel.removeprefix(f"{CHAINS_DIR}/"):
                continue
            chain_path = addon.root / rel
            written = _read_stored(store, rel, chain_path)
            body = alias_extends(written, qualify(written, addon), aliases)
            assert isinstance(body, Mapping)
            self._add_chain(chain_path, body, addon=addon)

    def _add_inherited(self, addon: InstalledAddon, chain: tuple[InstalledAddon, ...]) -> None:
        """A child add-on (`chain`, `inherit.ancestry`'s answer): each
        ancestor's files re-pointed for `addon` (`inherit.requalify`), then
        laid one over another from the root down. Level `d` is registered
        under `inherit.layer_namespace(addon.namespace, d)`: the child's own
        namespace is the merged result, and each hidden level below is what
        that ancestor's name reads in the files one level up. Every entry's
        `ComponentSource` is the file its author wrote."""
        from kraft.addons import fetch  # fetch imports kraft.config, which imports templates

        top = addon.namespace
        stores = []
        for author in chain:
            try:
                stores.append(fetch.open_store(author.root))
            except fetch.StoreMismatch as exc:
                raise TemplateLibraryError(f"{author.root / fetch.DIGEST_FILE}: {exc}") from exc
        written = [
            _read_stored(store, LIBRARY_FILE, author.root / LIBRARY_FILE, optional=True)
            for author, store in zip(chain, stores, strict=True)
        ]
        removed: list[dict[str, tuple[str, ...]]] = []
        for author, library in zip(chain, written, strict=True):
            _refuse_layer_reference(author.root / LIBRARY_FILE, library)
            # A stray `remove:` at the root is refused by the root's own
            # `_add_addon`: every load path loads the root by itself too.
            removed.append(inherit.removals(author.root / LIBRARY_FILE, library))
        try:
            visible = inherit.visible(chain)
        except fetch.StoreMismatch as exc:
            raise TemplateLibraryError(str(exc)) from exc

        def owner(kind: str) -> Callable[[str, int], str]:
            # Defined nowhere visible: the level asked from names it, and its
            # own lookup says what is missing.
            return lambda name, depth: (
                visible.owner(kind, name, depth)
                or (f"{chain[min(depth, len(chain) - 1)].namespace}:{name}")
            )

        authored = []
        for depth, (author, store, library) in enumerate(zip(chain, stores, written, strict=True)):

            def requalify(data: object, depth: int = depth, author: InstalledAddon = author):
                return inherit.requalify(
                    data,
                    top=top,
                    depth=depth,
                    author=author,
                    parent_name=chain[depth + 1].name if depth + 1 < len(chain) else None,
                    skill_owner=owner("skills"),
                    profile_owner=owner("profiles"),
                    steering_owner=owner("steering"),
                )

            library_path = author.root / LIBRARY_FILE
            library = requalify(library)
            assert isinstance(library, Mapping)
            entries: dict[str, dict[str, tuple[object, InstalledAddon]]] = {
                namespace.value: {
                    name: (
                        RawComponent(body, ComponentSource(library_path, namespace, name)),
                        author,
                    )
                    for name, body in _section(library_path, library, namespace).items()
                }
                for namespace in _EXTENDABLE
            }
            entries[Namespace.STEERING.value] = {
                name: (
                    _steering_profile(
                        body, ComponentSource(library_path, Namespace.STEERING, name)
                    ),
                    author,
                )
                for name, body in _section(library_path, library, Namespace.STEERING).items()
            }
            chains: dict[str, tuple[object, InstalledAddon]] = {}
            for rel in store.paths(f"{CHAINS_DIR}/"):
                if not rel.endswith(".yaml") or "/" in rel.removeprefix(f"{CHAINS_DIR}/"):
                    continue
                chain_path = author.root / rel
                chain_written = _read_stored(store, rel, chain_path)
                _refuse_layer_reference(chain_path, chain_written)
                body = requalify(chain_written)
                assert isinstance(body, Mapping)
                id = _chain_id(chain_path, body)
                _refuse_qualified(chain_path, "chain id", id)
                if id in chains:
                    claimed = chains[id][0][0]
                    raise TemplateLibraryError(
                        f"{chain_path}: chain id {id!r} is already declared by {claimed}"
                    )
                chains[id] = ((chain_path, body), author)
            entries[_CHAINS] = chains
            authored.append(entries)

        merged: dict[str, dict[str, tuple[object, InstalledAddon]]] = {}
        stale: list[str] = []
        for depth in reversed(range(len(chain))):
            for section, names in removed[depth].items():
                # Only the top child's own: a middle level records its stale
                # names when it loads as a top add-on itself.
                if depth == 0:
                    if section in inherit.FILE_SECTIONS:
                        inherited = visible.levels[1].get(section, {})
                    else:
                        inherited = merged.get(section, {})
                    stale += [
                        f"{top}: remove.{section}: {name} is not inherited from "
                        f"{chain[1].namespace}"
                        for name in names
                        if name not in inherited
                    ]
                if section in merged:
                    merged[section] = {
                        n: entry for n, entry in merged[section].items() if n not in names
                    }
            for section, items in authored[depth].items():
                merged[section] = {**merged.get(section, {}), **items}
            namespace = inherit.layer_namespace(top, depth)
            if depth:
                self._layers[namespace] = chain[depth].namespace
            self._register_layer(namespace, merged, public=depth == 0)
        self.stale_removals = (*self.stale_removals, *stale)

    def _register_layer(
        self,
        namespace: str,
        merged: Mapping[str, Mapping[str, tuple[object, InstalledAddon]]],
        *,
        public: bool,
    ) -> None:
        """One level of a child's layering under `namespace`: into the public
        maps for the child's own (`public`), else into the hidden ones. A
        hidden level holds no steering: a reference to an ancestor's steering
        profile names that ancestor's own (`inherit.requalify`). Namespaces
        are unique and a local id holds no `:`, so no key here is taken."""
        components = self._components if public else self._layer_components
        chains = self._chains if public else self._layer_chains
        for section, items in merged.items():
            for name, (entry, author) in items.items():
                key = f"{namespace}:{name}"
                if section == _CHAINS:
                    chain_path, body = entry  # type: ignore[misc]
                    chains[key] = RawComponent(
                        {**body, "id": key},
                        ComponentSource(chain_path, Namespace.NODES, key, chain=True),
                    )
                elif section == Namespace.STEERING.value:
                    if public:
                        self.steering[key] = entry  # type: ignore[assignment]
                else:
                    components[Namespace(section)][key] = entry  # type: ignore[assignment]
                if public:
                    self._authors[(section, key)] = author

    def _listed_namespaces(
        self, addon: InstalledAddon, library: Mapping[str, object]
    ) -> dict[str, str]:
        """For a repo library: each add-on its `addons:` lists, by name, to
        what its chains' `extends` read it as here
        (`a-listed-addons-name-means-that-addon-alone`). One this instance
        knows maps to its namespace: when it is left out, nothing loads
        there, and the chain fails with its `left_out` reason. One it does not
        know maps to its id, which names no namespace. Neither ever falls
        through to another add-on or repo library that holds the name."""
        if addon.repo_id is None:
            return {}
        known = {p.id: p for p in self.addons if p.repo_id is None}
        return {
            addon_id.split("@", 1)[0]: known[addon_id].namespace if addon_id in known else addon_id
            for addon_id in builds_on(library)
        }

    def with_library(self, library: Mapping[str, object], library_path: Path) -> TemplateLibrary:
        """This library's chains against a replacement `library.yaml` content:
        how an edit of the library is checked before it is saved. `self` is not
        changed."""
        return TemplateLibrary.from_mappings(
            library,
            # An add-on's chains come back with its layer, not as local chains.
            [(raw.source.file, raw.data) for id, raw in self._chains.items() if ":" not in id],
            library_path=library_path,
            skills_dir=self.skills_dir,
            addons=self.addons,
        )

    def addon_of(self, name: str) -> InstalledAddon | None:
        """The loaded add-on a qualified chain or component name comes from
        (`release:ship`, or the `release:base` of `tasks.release:base`); None
        for a local name. An add-on's declarations are read-only."""
        qualifier, colon, _ = name.rpartition(".")[2].partition(":")
        if not colon:
            return None
        return next(
            (p for p in self.addons if p.left_out is None and p.namespace == qualifier), None
        )

    def authored_by(self, name: str, section: str | None = None) -> InstalledAddon | None:
        """The add-on whose file a qualified chain or component was read from:
        for a child add-on's entry, the ancestor that wrote it, which
        `addon_of` (the add-on it is served under) is not. `section` (`chains`,
        `tasks`, `steps`, `nodes`, `steering`) says which entry when a chain
        and components share the name; given as a `tasks.` prefix it is read
        from `name`. None for a local name."""
        if section is None:
            head, dot, rest = name.partition(".")
            if dot and head in (_CHAINS, *(ns.value for ns in Namespace)):
                section, name = head, rest
        sections = [section] if section else [_CHAINS, *(ns.value for ns in Namespace)]
        for each in sections:
            if (author := self._authors.get((each, name))) is not None:
                return author
        return self.addon_of(name)

    def public_name(self, name: str) -> str:
        """`name` as a listing shows it: a hidden layer's
        (`team^1:default`) as its ancestor's own (`platform-dept:default`)."""
        found = self.public(name)
        assert isinstance(found, str)
        return found

    def public(self, data: object) -> object:
        """`data`, an entry as authored, with every name in a hidden layer
        written as `public_name` writes it: what a listing shows."""
        return inherit.public(data, self._layers) if self._layers else data

    def _humanize(self, text: str) -> str:
        return inherit.humanize(text, self._layers) if self._layers else text

    def _share_layers(self, other: TemplateLibrary) -> None:
        """`other`'s children's hidden levels, authors and stale removals,
        shared, not copied: they are never changed once built."""
        self._layer_components = other._layer_components
        self._layer_chains = other._layer_chains
        self._layers = other._layers
        self._authors = other._authors
        self.stale_removals = other.stale_removals

    @property
    def _all_chains(self) -> Mapping[str, RawComponent]:
        """The chains, with a child's hidden ancestor levels after them."""
        return ChainMap(self._chains, self._layer_chains)

    def with_addons(self, addons: Sequence[InstalledAddon]) -> TemplateLibrary:
        """This library's own components and chains with `addons` in place of
        the add-ons it was built with: what a work item that pinned other
        versions resolves against (`addons.load.for_item`). `self` is not
        changed. An add-on whose store no longer reads (edited since it was
        loaded) is left out with why, not the whole library."""
        built = TemplateLibrary(
            {
                ns: {name: raw for name, raw in entries.items() if ":" not in name}
                for ns, entries in self._components.items()
            },
            {id: raw for id, raw in self._chains.items() if ":" not in id},
            {name: profile for name, profile in self.steering.items() if ":" not in name},
            self.skills_dir,
            addons,
            self.library_path,
        )
        for i, addon in enumerate(addons):
            if addon.left_out is None:
                try:
                    built._add_addon(addon)
                except TemplateLibraryError as exc:
                    # Half added: build again with it left out (one more each time).
                    # Its descendants go with it: they read it.
                    left = replace(addon, left_out=str(exc))
                    return self.with_addons(inherit.cascade([*addons[:i], left, *addons[i + 1 :]]))
        return built

    def with_chain(
        self, chain_path: Path, body: Mapping[str, object]
    ) -> tuple[TemplateLibrary, str]:
        """This library plus one unsaved chain, and the chain's id. A chain of
        the same id is shadowed, which is how a saved chain's edit is checked
        before it is saved. `self` is not changed."""
        candidate = TemplateLibrary(
            self._components,
            self._chains,
            self.steering,
            self.skills_dir,
            self.addons,
            self.library_path,
        )
        candidate._share_layers(self)
        if "addon_dirs" in self.__dict__:
            # The same add-ons, so the same answer: never read every child's
            # tree again for each chain a check or a draft tries.
            candidate.__dict__["addon_dirs"] = self.addon_dirs
        candidate._chains.pop(_chain_id(chain_path, body), None)
        return candidate, candidate._add_chain(chain_path, body)

    def _add_chain(
        self,
        chain_path: Path,
        body: Mapping[str, object],
        *,
        addon: InstalledAddon | None = None,
    ) -> str:
        id = _chain_id(chain_path, body)
        _refuse_qualified(chain_path, "chain id", id)
        _refuse_layer_reference(chain_path, body)
        if addon is not None:
            id = f"{addon.namespace}:{id}"
        if id in self._chains:
            # One selectable chain per file: two files claiming one id would
            # otherwise make the loser's chain vanish from `chain_ids`.
            claimed = self._chains[id].source.file
            raise TemplateLibraryError(
                f"{chain_path}: chain id {id!r} is already declared by {claimed}"
            )
        self._chains[id] = RawComponent(
            {**body, "id": id}, ComponentSource(chain_path, Namespace.NODES, id, chain=True)
        )
        return id

    @property
    def chain_ids(self) -> tuple[str, ...]:
        return tuple(self._chains)

    def chain_data(self, id: str) -> Mapping[str, object]:
        """Chain `id` as authored, before `extends` expansion or validation.
        A child add-on's chain may name a hidden ancestor level, which reads
        too, so a walk up `extends` reaches its ancestors: a screen or a copy
        shows it through `public`."""
        return self._all_chains[id].data

    def has_chain(self, id: str) -> bool:
        """Whether `chain_data(id)` reads: a listed chain, or a child add-on's
        hidden ancestor level an `extends` names."""
        return id in self._all_chains

    def chain_file(self, id: str) -> Path:
        """The file chain `id` was read from -- what an edit of it rewrites."""
        return self._chains[id].source.file

    def component_names(self, namespace: str | Namespace) -> tuple[str, ...]:
        return tuple(self._components[Namespace(namespace)])

    def references(self, id: str) -> dict[str, set[str]]:
        """The library components chain `id` uses, per node id, each named
        `<section>.<name>` (`tasks.implementer`): every `extends` parent its
        expansion follows, transitively, and every steering profile a task
        selects. A chain that stops expanding part-way answers with what it
        reached; why it stopped is `lint`'s to say."""
        references = self._expansion(id).references
        if not self._layers:
            return references
        return {node: {self.public_name(ref) for ref in refs} for node, refs in references.items()}

    def usages(self, id: str) -> dict[str, list[dict[str, object]]]:
        """Per library component chain `id` uses (as `references` names it),
        each component of the chain that extends or selects it:
        `{path, overrides, via?}` (`_Resolution.usages`)."""
        usages = self._expansion(id).usages
        if not self._layers:
            return usages
        out: dict[str, list[dict[str, object]]] = {}
        for ref, uses in usages.items():
            shown = self.public(uses)
            assert isinstance(shown, list)
            out.setdefault(self.public_name(ref), []).extend(shown)
        return out

    def provenance(self, id: str) -> dict[str, object] | None:
        """For an extending chain: its parent, each resolved node's origin
        (`inherited`, `added`, `overridden`), the override paths per node, and
        the parent nodes it removed (`a-resolved-node-says-where-it-came-from`).
        None for a chain that extends none. Computed, never stored."""
        provenance = self._expansion(id).provenance
        if provenance is None or not self._layers:
            return provenance
        return {**provenance, "parent": self.public_name(str(provenance["parent"]))}

    def extended_by(self, id: str) -> tuple[str, ...]:
        """Every chain whose `extends` reaches `id`, directly or through a chain
        between, by id. Read from the files as written, so a child that does not
        resolve is still listed."""
        out = []
        for other, raw in self._chains.items():
            seen: set[str] = set()
            name = raw.data.get("extends")
            while isinstance(name, str) and name not in seen:
                # A child's parent level is listed as the ancestor's own chain.
                if self.public_name(name) == id:
                    out.append(other)
                    break
                seen.add(name)
                parent = self._all_chains.get(name)
                name = parent.data.get("extends") if parent is not None else None
        return tuple(sorted(out))

    def _expansion(self, id: str) -> _Resolution:
        raw = self._all_chains[id]
        resolution = _Resolution(self, raw.source)
        try:
            resolution.expand_chain(raw.data)
        except TemplateLibraryError:
            pass
        return resolution

    def component(self, namespace: Namespace, name: str) -> RawComponent | None:
        return self._components[namespace].get(name) or self._layer_components[namespace].get(name)

    def namespace_of(self, name: str) -> Namespace | None:
        """Which namespace declares `name`, for the cross-namespace `extends`
        error that says what the author actually referenced."""
        return next((ns for ns in _EXTENDABLE if self.component(ns, name) is not None), None)

    @classmethod
    def lint_dir(
        cls,
        path: str | Path,
        *,
        skills_dir: Path | None = None,
        instance_policy: InstancePolicy | None = None,
        addons: Sequence[InstalledAddon] | None = (),
    ) -> LintReport:
        """Everything wrong with the template directory at `path` -- every
        chain file that does not parse and every chain that does not resolve,
        or the one `library.yaml` failure that leaves nothing to resolve
        against (`template-lint-reports-library-validity`). Reads; never writes.
        `addons=None` is the offline lint: chains that reference an add-on
        namespace are returned as `unchecked`."""
        root = Path(path)
        issues: list[TemplateIssue] = []
        try:
            library = cls.from_yaml_dir(
                root, skills_dir=skills_dir, issues=issues, addons=addons or ()
            )
        except TemplateLibraryError as exc:
            return LintReport(
                chains=(), issues=(TemplateIssue.from_error(root / LIBRARY_FILE, None, exc),)
            )
        issues += library.lint(instance_policy)
        # `None` is the offline lint (`--dir`): no add-ons are known, so a
        # reference into an add-on namespace is unjudged, not wrong.
        unchecked = [i for i in issues if i.addon_ref] if addons is None else []
        issues = [i for i in issues if i not in unchecked]
        failed = {issue.chain for issue in (*issues, *unchecked)}
        return LintReport(
            chains=tuple(id for id in library.chain_ids if id not in failed),
            issues=tuple(issues),
            unchecked=tuple(
                replace(i, message=f"{i.message}: not checked (add-on)") for i in unchecked
            ),
        )

    def lint(self, instance_policy: InstancePolicy | None = None) -> list[TemplateIssue]:
        """Every chain in this library that does not resolve, rather than the
        first (`template-lint-reports-library-validity`).

        A chain any of whose `policy:` scopes a broader one refuses is an issue
        too (`ResolvedChain.chain_policy`) -- a task widening its node's, or,
        given the `instance_policy`, a chain past a `maxima:` ceiling --
        rather than a refusal the first intake on it finds (Kraft-ib2af).

        Over the library already in memory: no file is read and none is written,
        so an edit landing mid-lint cannot be reported against configuration the
        caller never loaded. Parse errors are `from_yaml_dir`'s -- a library that
        cannot be parsed has no instance to lint, and the caller turns that one
        raise into its own issue.
        """
        issues = []
        for id, raw in self._chains.items():
            try:
                self.resolve_chain(id).chain_policy(
                    instance_policy
                    if instance_policy is not None
                    else InstancePolicy.from_input(InstancePolicyInput())
                )
            except (TemplateLibraryError, PolicyError) as exc:
                issue = TemplateIssue.from_error(raw.source.file, id, exc)
                issues.append(replace(issue, message=self._humanize(issue.message)))
        return issues

    def resolve_chain(
        self, id: str, *, strict_pins: bool = False, pinned: Iterable[str] = ()
    ) -> ResolvedChain:
        """Expand one chain's `extends` references, validate the result into a
        typed `Chain`, and assign canonical execution paths
        (`resolved-chain-is-validated-before-use`). A child add-on's hidden
        levels are named in its errors as the ancestors they hold.

        With `strict_pins`, a chain that reads an add-on left out by a fault
        (not quietly, as a disabled one is) is refused with why, instead of
        not pinning it: what a revision adds to a work item runs on it. An
        add-on whose namespace is in `pinned`, which the work item already
        pinned, is never refused: it stays pinned, and the launch restores
        its store or says why it cannot."""
        # A hidden level resolves too: a child's chain extending it resolves it.
        raw = self._all_chains.get(id)
        if raw is None:
            raise TemplateLibraryError(
                f"no chain {id!r} in this library; known chains: {list(self._chains)}"
            )
        try:
            return self._resolve(id, raw, strict=frozenset(pinned) if strict_pins else None)
        except TemplateLibraryError as exc:
            if self._layers:
                exc.args = (self._humanize(str(exc)), *exc.args[1:])
            raise

    def _resolve(
        self, id: str, raw: RawComponent, *, strict: frozenset[str] | None = None
    ) -> ResolvedChain:
        resolution = _Resolution(self, raw.source)
        expanded = resolution.expand_chain(raw.data)
        if inherit.is_layer(id):
            # A hidden level is resolved only for a child that extends it,
            # which takes its nodes, never its id: an id holds no `^`.
            expanded = {**expanded, "id": self.public_name(id)}
        try:
            chain = Chain.model_validate(expanded)
        except ValidationError as exc:
            raise TemplateLibraryError(
                resolution.explain(exc),
                **resolution.error_location(exc),
                errors=tuple(
                    (tuple(p for p in e["loc"] if p not in _UNION_TAGS), e["msg"])
                    for e in exc.errors()
                ),
            ) from exc
        resolved = ResolvedChain.from_chain(chain)
        for node in resolved.nodes:
            # A node whose own tasks do not agree on what the node produces
            # cannot be trimmed unambiguously when an attachment arrives. Two
            # shapes, one rule: some tasks declaring `produces` and some not, and
            # two tasks declaring *different* kinds. `> 1` covers both; the
            # earlier `> 1 and None in produces` missed `{spec, plan}`, where an
            # attached spec leaves the node -- and so the plan half of it -- in
            # place, which is the same ambiguity from the other side:
            # the node-level rule
            # (`MaterializedChain`'s `trim_for_attachments`) would keep it and
            # re-author the attached document, and dropping the one producing
            # task instead is not available -- `Step.tasks` has `min_length=1`,
            # so an emptied step is invalid. Refused here, at load, which is
            # what makes the trim a clean node-level decision (Ruling 35).
            produces = node.produces()
            if len(produces) > 1:
                raise TemplateLibraryError(
                    f"{resolution.at(node.id)}: node {node.id!r} does not agree on what it "
                    f"produces ({sorted(k or '(none)' for k in produces)}); a node either "
                    f"wholly produces one kind or declares none",
                    **resolution.path_location(node.id),
                )
            for task in node.tasks():
                for name in task.task.steering:
                    if name not in self.steering:
                        raise TemplateLibraryError(
                            f"{resolution.at(task.path)}: selects no steering profile {name!r}",
                            **resolution.path_location(task.path),
                            addon_ref=":" in name,
                        )
                # A skill that names no method is refused here, where lint and
                # intake see it, never discovered by an agent at launch
                # (Kraft-vhcop). A loaded Kraft add-on's skill is looked up in
                # its store; another tool's cannot be, and `skill.UNAVAILABLE`
                # has the agent stop on it.
                selected = getattr(task.task, "skill", None)
                if selected is not None:
                    try:
                        _skill.validate(
                            self.skills_dir,
                            selected,
                            where=task.path,
                            addon_dirs=self.addon_dirs,
                        )
                    except _skill.SkillError as exc:
                        raise TemplateLibraryError(
                            f"{resolution.at(task.path)}: selects skill {selected!r}, "
                            f"which resolves to no method: {exc}",
                            **resolution.path_location(task.path),
                        ) from exc
        # The text, not the names: `materialize` freezes it into the item's
        # snapshot, so a later edit to `library.yaml` cannot reach a running item.
        return replace(
            resolved,
            steering={
                name: self.steering[name].instructions
                for node in resolved.nodes
                for task in node.tasks()
                for name in task.task.steering
            },
            addons=self._pins(id, resolved, resolution.ancestors, strict=strict),
        )

    def _fallback_profiles(self, chain: object) -> list[str]:
        """The agent profiles named only by the `fallback:` list of a local
        profile `chain` selects, and of those in turn. A `profile:` is read
        live from `harnesses.yaml` at launch, so an add-on profile reached only
        through such a list is read from the add-on and must be pinned too. An
        unreadable or missing file adds nothing: the launch reports it."""
        if self.library_path is None:
            return []
        try:
            profiles = _read(self.library_path.parent / "harnesses.yaml").get("profiles")
        except TemplateLibraryError:
            return []
        if not isinstance(profiles, Mapping):
            return []
        pending = list(seen := _profile_names(chain))
        reached: list[str] = []
        while pending:
            body = profiles.get(pending.pop())
            for entry in (body.get("fallback") if isinstance(body, Mapping) else None) or ():
                name = entry.get("profile") if isinstance(entry, Mapping) else None
                if isinstance(name, str) and name not in seen:
                    seen.add(name)
                    reached.append(name)
                    pending.append(name)
        return reached

    def _pins(
        self,
        id: str,
        resolved: ResolvedChain,
        ancestors: Sequence[str] = (),
        *,
        strict: frozenset[str] | None = None,
    ) -> dict[str, dict[str, str | None]]:
        """The loaded add-ons chain `id` reads anything from: the chain itself,
        every chain it extends (`an-extending-chain-pins-its-ancestors`), a
        component it extends or selects, or a skill or agent profile one of
        its tasks names; and every ancestor of each, root first
        (`an-item-pins-every-ancestor`). One it reads that is left out is not
        pinned; with `strict`, the namespaces a work item already pinned, one
        left out by a fault that is not among them raises instead."""
        data = resolved.chain.model_dump(mode="json")
        named = {
            id,
            *ancestors,
            *(
                name.partition(".")[2]
                for names in self._expansion(id).references.values()
                for name in names
            ),
            *_qualified_names(data),
            *self._fallback_profiles(data),
        }
        qualifiers = {name.partition(":")[0] for name in named if ":" in name}
        if strict is not None:
            loaded = {a.namespace for a in self.addons if a.left_out is None}
            for addon in self.addons:
                if (
                    addon.namespace in qualifiers - loaded - strict
                    and not addon.quiet
                    and addon.left_out
                ):
                    raise TemplateLibraryError(
                        f"it reads add-on {addon.id}, which is not loaded for this work "
                        f"item: {addon.left_out}"
                    )
        # A child is read with its ancestors: an item that kept only the child
        # would run it on whatever parent the lock holds later.
        for addon in self.addons:
            if addon.left_out is None and addon.namespace in qualifiers:
                try:
                    qualifiers.update(a.namespace for a in inherit.ancestry(addon, self.addons))
                except inherit.InheritanceError:
                    pass
        return {
            addon.namespace: addon.pin
            for addon in inherit.tree_order(self.addons)
            if addon.left_out is None and addon.namespace in qualifiers
        }


def _qualified_names(data: object) -> set[str]:
    """Every name `data`, a chain dumped to JSON, reads something by: each
    `skill:` and `profile:` value (a task's and a `fallback:` entry's) and
    each `steering:` item. Prose (a prompt, a model) names nothing."""
    if isinstance(data, dict):
        found = {v for k, v in data.items() if k in ("skill", "profile") and isinstance(v, str)}
        steering = data.get("steering")
        if isinstance(steering, list):
            found.update(v for v in steering if isinstance(v, str))
        return found.union(*(_qualified_names(v) for v in data.values()))
    if isinstance(data, list):
        return set().union(*(_qualified_names(v) for v in data))
    return set()


def _profile_names(data: object) -> set[str]:
    """Every value of a `profile:` key in `data`, a chain dumped to JSON: a
    task's own agent profile and the profile of a `fallback:` entry."""
    if isinstance(data, dict):
        found = {v for k, v in data.items() if k == "profile" and isinstance(v, str)}
        return found.union(*(_profile_names(v) for v in data.values()))
    if isinstance(data, list):
        return set().union(*(_profile_names(v) for v in data))
    return set()


@dataclass(frozen=True)
class _Located:
    """One expanded component: where it sits in the resolved chain, and where
    its definition came from."""

    path: str
    source: ComponentSource
    #: The component's key path in `source.file` when that file is another
    #: chain's; None for a library component, which `_at` names by section and
    #: name.
    key: tuple[object, ...] | None = None


class _Resolution:
    """One chain's expansion pass.

    The path-to-source map it keeps is for diagnostics only. Identifier
    uniqueness is validated per container by the typed models and never against
    this map -- a canonical path is its container's path plus one local
    identifier, so sibling checks are already sufficient
    (docs/templates-v1-design.md "Resolution and execution").
    """

    def __init__(self, library: TemplateLibrary, chain: ComponentSource) -> None:
        self._library = library
        self._chain = chain
        #: Keyed by pydantic error location, so a schema failure can be reported
        #: at the canonical path an author recognises.
        self._located: dict[tuple[object, ...], _Located] = {}
        self._sources: dict[str, ComponentSource] = {}
        #: Where the component at one location was inherited from, filled by
        #: `_merge_chain` and consumed by `_locate` once its path is known.
        self._inherited: dict[tuple[object, ...], ComponentSource] = {}
        #: Per node id, the library components its expansion used
        #: (`TemplateLibrary.references`); filed under the node being expanded.
        self.references: dict[str, set[str]] = {}
        self._refs: set[str] = set()
        #: Per library component, each chain component that extends or
        #: selects it: its canonical `path`; `overrides`, whether it sets a key
        #: besides `id`, `extends` and `kind`; and `via`, the library node it
        #: sits in when the node's own expansion put it there. Kept pending
        #: under its location until `_locate` knows the path.
        self.usages: dict[str, list[dict[str, object]]] = {}
        self._pending: dict[tuple[object, ...], list[dict[str, object]]] = {}
        #: The chain node being expanded, as authored, and the library node it extends.
        self._node_raw: object = None
        self._via: object = None
        #: For an extending chain, where each final node came from
        #: (`extension.Applied.origins`); None for a chain that extends none.
        self._origins: list[tuple[str, int]] | None = None
        #: Per node id, the override paths at or under it.
        self._overridden: dict[str, list[str]] = {}
        #: The parent's locations by path, for a parent node an override
        #: re-expands: its components keep the source that defined them.
        self._seed: dict[str, _Located] = {}
        #: `TemplateLibrary.provenance`'s answer; None for a chain that extends none.
        self.provenance: dict[str, object] | None = None
        #: Every chain this one extends, nearest first.
        self.ancestors: tuple[str, ...] = ()
        #: The chain as written and its expanded node ids, for `split` to place
        #: a location in an extending chain's edits.
        self._written: Mapping[str, object] = {}
        self._order: list[object] = []

    # ── expansion ──

    def expand_chain(self, data: Mapping[str, object]) -> Mapping[str, object]:
        self._written = data
        if "extends" in data:
            out = self._extend(data)
            self._order = [n.get("id") for n in out["nodes"] if isinstance(n, Mapping)]
            return out
        if written := [key for key in extension.EDIT_KEYS if key in data]:
            # Said here, rather than letting the author read pydantic's "Extra
            # inputs are not permitted" (`edits-need-a-parent`).
            raise TemplateLibraryError(
                f"{self._chain.file}: {written[0]!r} edits a parent chain; add "
                "'extends: <chain id>' or drop it",
                loc=(written[0],),
            )
        nodes = data.get("nodes")
        if not isinstance(nodes, list):
            raise TemplateLibraryError(f"{self._chain.file}: 'nodes' must be a list")
        return {
            **data,
            "nodes": [self._node(node, index) for index, node in enumerate(nodes)],
        }

    def _node(self, raw: object, index: int) -> Mapping[str, object]:
        loc: tuple[object, ...] = ("nodes", index)
        provisional = self._provisional(raw, "", index)
        # ponytail: keyed by the node's authored id (its position without one);
        # a node whose id is only inherited is filed under `[index]`.
        self._refs = self.references.setdefault(provisional, set())
        self._node_raw = raw
        self._via = raw.get("extends") if isinstance(raw, Mapping) else None
        node = self._expand(raw, Namespace.NODES, provisional, loc)
        path = self._record(node, loc, prefix="", fallback=f"nodes[{index}]")

        expanded = dict(self._container(node, path, loc))
        for key in ("on_failure", "fix_loop"):
            body = expanded.get(key)
            if isinstance(body, Mapping):
                expanded[key] = self._container(body, f"{path}{PATH_SEPARATOR}{key}", (*loc, key))
        if isinstance(expanded.get("escalation"), Mapping):
            expanded["escalation"] = self._task(
                expanded["escalation"], f"{path}{PATH_SEPARATOR}escalation", (*loc, "escalation")
            )
        if isinstance(expanded.get("auto_review"), Mapping):
            expanded["auto_review"] = self._task(
                expanded["auto_review"],
                path,
                (*loc, AUTO_REVIEW_SEGMENT),
                segment=AUTO_REVIEW_SEGMENT,
            )
        base_change = expanded.get("on_base_changed")
        if isinstance(base_change, Mapping) and isinstance(base_change.get("on_conflict"), Mapping):
            where = f"{path}{PATH_SEPARATOR}on_base_changed{PATH_SEPARATOR}on_conflict"
            expanded["on_base_changed"] = {
                **base_change,
                "on_conflict": self._container(
                    base_change["on_conflict"], where, (*loc, "on_base_changed", "on_conflict")
                ),
            }
        return expanded

    def _with_handler(
        self, component: Mapping[str, object], path: str, loc: tuple[object, ...]
    ) -> Mapping[str, object]:
        """`component` with its own `on_failure` recovery plan expanded, if it
        declares one (a step's or a task's, `nearest-recovery-handler-wins`)."""
        handler = component.get("on_failure")
        if not isinstance(handler, Mapping):
            return component
        return {
            **component,
            "on_failure": self._container(
                handler, f"{path}{PATH_SEPARATOR}on_failure", (*loc, "on_failure")
            ),
        }

    # ── a chain that extends a chain ──

    def _extend(self, data: Mapping[str, object]) -> Mapping[str, object]:
        """`an-extending-chain-is-its-parent-with-edits`: the parent chain,
        resolved, with this chain's edits applied, as the expanded mapping
        `Chain` validates."""
        file = self._chain.file
        name = data["extends"]
        if not isinstance(name, str):
            raise TemplateLibraryError(
                f"{file}: 'extends' names exactly one parent chain, not {name!r}",
                loc=("extends",),
            )
        if "nodes" in data:
            raise TemplateLibraryError(
                f"{file}: an extending chain holds no 'nodes'; change {name}'s with remove, "
                "add and overrides, or copy it to own its structure",
                loc=("nodes",),
            )
        self.ancestors = self._ancestry(name)
        try:
            # Any failure of the parent, a schema one included, is the parent's
            # to report: the child says once that it does not resolve.
            self._library.resolve_chain(name)
        except TemplateLibraryError as exc:
            # A hidden level is not linted on its own: its cause is said here.
            cause = f": {exc}" if inherit.is_layer(name) else ""
            raise TemplateLibraryError(
                f"{file}: extends {name!r}, which does not resolve{cause}",
                loc=("extends",),
                addon_ref=exc.addon_ref,
            ) from exc
        try:
            edits = extension.Edits.read(data)
        except extension.ExtensionError as exc:
            # `first_error` already starts the message with its location.
            raise TemplateLibraryError(f"{file}: {exc}", loc=exc.loc) from exc
        raw = self._library._all_chains[name]
        parent = _Resolution(self._library, raw.source)
        expanded = parent.expand_chain(raw.data)
        paths = {located.path: loc for loc, located in parent._located.items()}
        try:
            applied = extension.apply(name, expanded["nodes"], paths, edits)
            policy = extension.tighten(expanded.get("policy"), data.get("policy"))
        except extension.ExtensionError as exc:
            raise TemplateLibraryError(f"{file}: {exc}", loc=exc.loc) from exc
        self._origins, self._overridden = applied.origins, applied.overridden
        nodes = []
        for index, (node, (origin, at)) in enumerate(
            zip(applied.nodes, applied.origins, strict=True)
        ):
            if origin == "parent" and node["id"] not in applied.overridden:
                nodes.append(self._inherit(parent, at, index, node))
                continue
            if origin == "parent":
                self._seed.update({v.path: v for v in parent.exported(("nodes", at)).values()})
            nodes.append(self._node(node, index))
            if origin == "parent":
                self._adopt(parent, node["id"])
        self._refuse_a_second_final_gate(nodes, applied.origins)
        self.provenance = {
            "parent": name,
            "nodes": {
                node["id"]: "added"
                if origin == "add"
                else "overridden"
                if node["id"] in applied.overridden
                else "inherited"
                for node, (origin, _) in zip(applied.nodes, applied.origins, strict=True)
            },
            "overrides": {id: list(paths) for id, paths in applied.overridden.items()},
            "removed": list(applied.removed),
        }
        out = {k: v for k, v in expanded.items() if k not in ("id", "nodes", "policy")}
        out.update(
            {
                k: v
                for k, v in data.items()
                if k not in ("extends", "nodes", "policy", *extension.EDIT_KEYS)
            }
        )
        out["nodes"] = nodes
        if policy is not None:
            out["policy"] = policy
        return out

    def _ancestry(self, parent: str) -> tuple[str, ...]:
        """The chains this one extends, nearest first. Refuses a parent that is
        not a chain and a cycle (`component-extends-has-one-parent`). A chain
        missing further up is the parent's own failure, reported as its not
        resolving."""
        chains = self._library._all_chains
        if parent not in chains:
            raise self._no_chain(parent)
        seen = [self._chain.name]
        name: object = parent
        while isinstance(name, str):
            if name in seen:
                # Only a chain that names itself: `default.yaml` saying
                # `extends: default` meant the instance's `default`.
                hint = self._instance_chain_hint(name) if parent == self._chain.name else ""
                raise TemplateLibraryError(
                    f"{self._chain.file}: 'extends' cycle: {' -> '.join((*seen, name))}{hint}",
                    loc=("extends",),
                )
            if name not in chains:
                break
            seen.append(name)
            name = chains[name].data.get("extends")
        return tuple(seen[1:])

    def _no_chain(self, name: str) -> TemplateLibraryError:
        file = self._chain.file
        other = self._library.namespace_of(name)
        if other is not None:
            return TemplateLibraryError(
                f"{file}: extends {name!r}, which is a library {other.singular}, not a chain",
                loc=("extends",),
            )
        return TemplateLibraryError(
            f"{file}: extends no chain named {name!r}{self._left_out(name)}"
            f"{self._instance_chain_hint(name)}",
            loc=("extends",),
            addon_ref=":" in name,
        )

    def _instance_chain_hint(self, name: str) -> str:
        """For a repo library's chain whose parent `name` (`app:default`) came
        from a bare name: when the instance has a local chain of that bare
        name, what the author most likely meant, and why it is refused
        (`a-bare-repo-library-parent-is-its-own`). Empty otherwise. Local ids
        have no `:`, so `bare in chain_ids` holds only for a local chain."""
        owner = self._library.addon_of(name)
        bare = name.partition(":")[2]
        if owner is None or owner.repo_id is None or bare not in self._library.chain_ids:
            return ""
        return (
            "; a bare name in a repo library is its own chain, and an instance chain "
            "cannot be extended"
        )

    def _left_out(self, name: str) -> str:
        """Why the add-on `name` is qualified by is not loaded, as a suffix;
        empty for a local name or a loaded add-on."""
        qualifier = name.partition(":")[0] if ":" in name else None
        return next(
            (
                f"; {addon.left_out}"
                for addon in self._library.addons
                if addon.namespace == qualifier and addon.left_out is not None
            ),
            "",
        )

    def _inherit(
        self, parent: _Resolution, at: int, index: int, node: Mapping[str, object]
    ) -> Mapping[str, object]:
        """Parent node `at`, unchanged, as this chain's node `index`: its
        locations, sources, references and usages carried over, not expanded
        again (its `extends` are already gone)."""
        for loc, located in parent.exported(("nodes", at)).items():
            self._located[("nodes", index, *loc[2:])] = located
            self._sources.setdefault(located.path, located.source)
        self._adopt(parent, str(node["id"]))
        return node

    def _adopt(self, parent: _Resolution, node: str) -> None:
        """The parent's references and usages under `node`, for the components
        this chain still has there (`_sources`, filled by now): one an
        override's replacement list dropped is not carried over, so it neither
        pins its add-on nor shows as a use of the node."""
        kept: set[str] = set()
        for ref, entries in parent.usages.items():
            mine = self.usages.setdefault(ref, [])
            for usage in entries:
                path = str(usage["path"])
                under = path == node or path.startswith(f"{node}{PATH_SEPARATOR}")
                if under and path in self._sources:
                    kept.add(ref)
                    if not any(u["path"] == path for u in mine):
                        mine.append(dict(usage))
            if not mine:
                del self.usages[ref]
        refs = self.references.setdefault(node, set())
        refs.update(r for r in parent.references.get(node, ()) if r in kept)

    def _refuse_a_second_final_gate(
        self, nodes: list[Mapping[str, object]], origins: list[tuple[str, int]]
    ) -> None:
        """`an-extending-chain-holds-one-final-gate`, on the expanded result: an
        added node can take `chain_finalized` from a library node it extends,
        which `extension.apply` cannot see on the node as written."""
        finals = [
            (node, origin)
            for node, origin in zip(nodes, origins, strict=True)
            if node.get("kind") == "gate" and node.get("chain_finalized") is True
        ]
        if len(finals) > 1:
            node, (_, at) = next((f for f in finals if f[1][0] == "add"), finals[-1])
            raise TemplateLibraryError(
                f"{self._chain.file}: add {node.get('id')}: the chain already has a final "
                "gate (chain_finalized); it holds one",
                loc=("add", at, "node"),
            )

    def exported(self, prefix: tuple[object, ...]) -> dict[tuple[object, ...], _Located]:
        """Every location under `prefix`, as a chain extending this one sees
        it: a component this chain's own file holds names that file and its
        key path there."""
        return {
            loc: _Located(located.path, located.source, self._authored(loc))
            if located.source == self._chain
            else located
            for loc, located in self._located.items()
            if loc[: len(prefix)] == prefix
        }

    def _authored(self, key: tuple[object, ...]) -> tuple[object, ...]:
        """`key`, a location in the expanded chain, as a key path into this
        chain's own file. The same tuple for a chain that extends none."""
        if self._origins is None or len(key) < 2 or key[0] != "nodes":
            return key
        origin, at = self._origins[int(key[1])]  # type: ignore[call-overload]
        if origin == "add":
            return ("add", at, "node", *key[2:])
        located = self._located.get(key)
        if located is not None:
            node = located.path.split(PATH_SEPARATOR, 1)[0]
            for path in sorted(self._overridden.get(node, ()), key=len, reverse=True):
                if located.path == path or located.path.startswith(f"{path}{PATH_SEPARATOR}"):
                    return ("overrides", path)
        return ("extends",)

    def _container(
        self, raw: Mapping[str, object], path: str, loc: tuple[object, ...], *, step: bool = False
    ) -> Mapping[str, object]:
        """Expand one execution shape: a `tasks` group (whose tasks already sit
        under the `main` step they resolve to, or under the `step` itself),
        ordered `steps`, and a fix loop's dedicated judge."""
        container = dict(raw)
        tasks = container.get("tasks")
        if isinstance(tasks, list):
            prefix = path if step else f"{path}{PATH_SEPARATOR}{MAIN_STEP}"
            container["tasks"] = [
                self._task(task, prefix, (*loc, "tasks", index)) for index, task in enumerate(tasks)
            ]
        steps = container.get("steps")
        if isinstance(steps, list):
            container["steps"] = [
                self._step(step, path, index, (*loc, "steps", index))
                for index, step in enumerate(steps)
            ]
        judge = container.get(JUDGE_SEGMENT)
        if isinstance(judge, Mapping):
            container[JUDGE_SEGMENT] = self._task(
                judge, path, (*loc, JUDGE_SEGMENT), segment=JUDGE_SEGMENT
            )
        return container

    def _step(
        self, raw: object, prefix: str, index: int, loc: tuple[object, ...]
    ) -> Mapping[str, object]:
        step = self._expand(raw, Namespace.STEPS, self._provisional(raw, prefix, index), loc)
        path = self._record(step, loc, prefix=prefix, fallback=f"steps[{index}]")
        return self._with_handler(self._container(step, path, loc, step=True), path, loc)

    def _task(
        self,
        raw: object,
        prefix: str,
        loc: tuple[object, ...],
        *,
        segment: str | None = None,
    ) -> Mapping[str, object]:
        provisional = (
            f"{prefix}{PATH_SEPARATOR}{segment}" if segment else self._provisional(raw, prefix, 0)
        )
        task = self._expand(raw, Namespace.TASKS, provisional, loc)
        steering = task.get("steering")
        if isinstance(steering, list):
            selected = [f"{Namespace.STEERING.value}.{n}" for n in steering if isinstance(n, str)]
            self._refs.update(selected)
            for ref in selected:
                self._use(ref, raw, loc)
        if segment is not None:
            # A dedicated task takes its container's segment as its path
            # (`component-identifiers-are-qualified-by-node-instance`), so its
            # authored id never becomes a path segment of its own.
            self._locate(loc, f"{prefix}{PATH_SEPARATOR}{segment}")
            return task
        path = self._record(task, loc, prefix=prefix, fallback="task")
        return self._with_handler(task, path, loc)

    def _provisional(self, raw: object, prefix: str, index: int) -> str:
        """The best path available *before* expansion, for an error raised by
        the expansion itself. An id inherited from a parent is not known yet."""
        id = raw.get("id") if isinstance(raw, Mapping) else None
        tail = id if isinstance(id, str) else f"[{index}]"
        return f"{prefix}{PATH_SEPARATOR}{tail}" if prefix else tail

    def _record(
        self,
        component: Mapping[str, object],
        loc: tuple[object, ...],
        *,
        prefix: str,
        fallback: str,
    ) -> str:
        id = component.get("id")
        tail = id if isinstance(id, str) else fallback
        path = f"{prefix}{PATH_SEPARATOR}{tail}" if prefix else tail
        self._locate(loc, path)
        return path

    def _use(self, ref: str, raw: object, loc: tuple[object, ...]) -> None:
        """File one usage of component `ref` by the authored `raw` at `loc`;
        `None` for the one filed there first, which reaches `ref` through its
        parent."""
        if raw is None:
            first = self._pending[loc][0]
            self._pending[loc].append({**first, "component": ref})
            return
        own = set(raw) - {"id", "extends", "kind"} if isinstance(raw, Mapping) else set()
        usage: dict[str, object] = {"component": ref, "overrides": bool(own)}
        # The chain's own node holds nothing at `loc`: the library node put it there.
        found = self._node_raw
        for part in loc[2:]:
            if isinstance(found, Mapping):
                found = found.get(part)
            elif isinstance(found, list) and isinstance(part, int) and part < len(found):
                found = found[part]
            else:
                found = None
        if not isinstance(found, Mapping) and isinstance(self._via, str):
            usage["via"] = self._via
        self._pending.setdefault(loc, []).append(usage)

    def _locate(self, loc: tuple[object, ...], path: str) -> None:
        for usage in self._pending.pop(loc, ()):
            ref = usage.pop("component")
            self.usages.setdefault(ref, []).append({"path": path, **usage})
        inherited = self._inherited.pop(loc, None)
        # A parent node an override re-expands keeps the source that defined
        # each of its components, which its own expansion recorded.
        seeded = self._seed.get(path) if inherited is None else None
        located = seeded or _Located(path, inherited or self._chain)
        self._located[loc] = located
        self._sources.setdefault(path, located.source)

    # ── extends ──

    def _expand(
        self, raw: object, namespace: Namespace, where: str, loc: tuple[object, ...]
    ) -> Mapping[str, object]:
        if not isinstance(raw, Mapping):
            raise TemplateLibraryError(f"{self._chain.file}: {where}: must be a mapping", loc=loc)
        return self._merge_chain(raw, namespace, where, loc, seen=())

    def _merge_chain(
        self,
        raw: Mapping[str, object],
        namespace: Namespace,
        where: str,
        loc: tuple[object, ...],
        *,
        seen: tuple[str, ...],
    ) -> Mapping[str, object]:
        """Merge `raw` onto its `extends` parent, recursively. Rejects multiple
        parents, a parent of another kind, an unknown parent, a cycle, and a
        derived component that changes its parent's `kind`."""
        name = raw.get("extends")
        child = {k: v for k, v in raw.items() if k != "extends"}
        if name is None:
            return child
        if not isinstance(name, str):
            raise TemplateLibraryError(
                f"{self._chain.file}: {where}: 'extends' names exactly one parent "
                f"of the same kind, not {name!r}",
                loc=(*loc, "extends"),
            )
        if name in seen:
            raise TemplateLibraryError(
                f"{self._chain.file}: {where}: 'extends' cycle: {' -> '.join((*seen, name))}",
                loc=(*loc, "extends"),
            )
        parent = self._parent(namespace, name, where, loc)
        # A parent's own parent is used by the same component, through it.
        self._use(f"{namespace.value}.{name}", None if seen else raw, loc)
        # The *resolved* parent, so a kind inherited further up the chain still
        # counts as the parent's kind.
        resolved = self._merge_chain(parent.data, namespace, where, loc, seen=(*seen, name))
        self._reject_kind_change(resolved, child, where, name, loc)
        if namespace is Namespace.TASKS:
            displaced = displaced_route(child)
            resolved = {k: v for k, v in resolved.items() if k not in displaced}
        merged = _merge(resolved, child)
        assert isinstance(merged, Mapping)
        self._inherited.setdefault(loc, parent.source)
        return merged

    def _parent(
        self, namespace: Namespace, name: str, where: str, loc: tuple[object, ...]
    ) -> RawComponent:
        component = self._library.component(namespace, name)
        if component is not None:
            self._refs.add(f"{namespace.value}.{name}")
            return component
        other = self._library.namespace_of(name)
        if other is not None:
            raise TemplateLibraryError(
                f"{self._chain.file}: {where}: extends {name!r}, which is a "
                f"{other.singular}, not a {namespace.singular}",
                loc=(*loc, "extends"),
            )
        raise TemplateLibraryError(
            f"{self._chain.file}: {where}: extends no {namespace.singular} named "
            f"{name!r}{self._left_out(name)}",
            loc=(*loc, "extends"),
            addon_ref=":" in name,
        )

    def _reject_kind_change(
        self,
        parent: Mapping[str, object],
        child: Mapping[str, object],
        where: str,
        name: str,
        loc: tuple[object, ...],
    ) -> None:
        """`extends-cannot-change-kind`."""
        inherited, declared = parent.get("kind"), child.get("kind")
        if inherited is not None and declared is not None and inherited != declared:
            raise TemplateLibraryError(
                f"{self._chain.file}: {where}: extends {name!r} and cannot change kind "
                f"{str(inherited)!r} to {str(declared)!r}",
                loc=(*loc, "kind"),
            )

    # ── diagnostics ──

    def at(self, path: str) -> str:
        """`path`, prefixed by the chain file and suffixed by the file the
        component's definition came from when it was inherited."""
        source = self._sources.get(path)
        inherited = f" <- {source}" if source is not None and source != self._chain else ""
        return f"{self._chain.file}: {path}{inherited}"

    def error_location(self, exc: ValidationError) -> dict[str, object]:
        """`loc`/`related` for a schema failure: the same component `explain`
        names. Positional union tags are dropped, as in `explain`, which turns
        pydantic's `loc` into a key path of the authored file."""
        loc = tuple(part for part in exc.errors()[0]["loc"] if part not in _UNION_TAGS)
        key = next((k for k in _prefixes(loc) if k in self._located), None)
        if key is None:
            return {"loc": loc}
        return self._at(key, loc[len(key) :])

    def split(self, loc: tuple[object, ...]) -> tuple[str | None, tuple[object, ...]]:
        """The canonical path of the deepest expanded component `loc` walks to,
        and the rest of `loc` below it; `None` and all of `loc` for none. A
        location in an extending chain's edits lands on the component the edit
        addresses (`a-problem-in-an-edit-lands-on-its-component`)."""
        found = extension.edit_location(self._written, loc) if "extends" in self._written else None
        if found is not None:
            if loc[2:3] != ("node",) or found[0] not in self._order:
                return found
            # Inside an added node: its expanded location knows the deeper path.
            loc = ("nodes", self._order.index(found[0]), *found[1])
        key = next((k for k in _prefixes(loc) if k in self._located), None)
        return (None, loc) if key is None else (self._located[key].path, loc[len(key) :])

    def path_location(self, path: str) -> dict[str, object]:
        """`loc`/`related` for the component at canonical `path`."""
        key = next((k for k, v in self._located.items() if v.path == path), None)
        return {} if key is None else self._at(key, ())

    def _at(self, key: tuple[object, ...], rest: tuple[object, ...]) -> dict[str, object]:
        located = self._located[key]
        # For a chain that extends none, `_authored(key) == key`.
        source, where = located.source, self._authored(key)
        if source == self._chain:
            return {"loc": (*where, *rest)}
        if located.key is not None:
            return {"loc": where, "related": (source.file, (*located.key, *rest))}
        return {
            "loc": where,
            "related": (source.file, (source.namespace.value, source.name, *rest)),
        }

    def explain(self, exc: ValidationError) -> str:
        """A schema failure in the author's own terms: the chain file, the
        canonical path of the offending component, and the file its definition
        came from when it was inherited."""
        error = exc.errors()[0]
        # Positional assumption: pydantic puts a discriminated-union tag in its
        # own `loc` part, so dropping parts equal to a tag drops the tag, not a
        # field or an id that happens to share the name.
        loc = tuple(part for part in error["loc"] if part not in _UNION_TAGS)
        key = next((k for k in _prefixes(loc) if k in self._located), None)
        if key is None:
            return f"{self._chain.file}: {error['msg']}"
        located = self._located[key]
        field = PATH_SEPARATOR.join(str(part) for part in loc[len(key) :])
        where = f"{located.path}{PATH_SEPARATOR}{field}" if field else located.path
        inherited = f" <- {located.source}" if located.source != self._chain else ""
        return f"{self._chain.file}: {where}{inherited}: {error['msg']}"


def _prefixes(loc: tuple[object, ...]) -> list[tuple[object, ...]]:
    """`loc`'s prefixes, longest first, so the most specific known component
    wins."""
    return [loc[:length] for length in range(len(loc), 0, -1)]
