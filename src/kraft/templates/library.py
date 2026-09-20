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

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import ValidationError

from kraft.templates.models import (
    JUDGE_SEGMENT,
    MAIN_STEP,
    PATH_SEPARATOR,
    Chain,
    NodeKind,
    ResolvedChain,
    SteeringProfile,
    TaskKind,
)

LIBRARY_FILE = "library.yaml"
CHAINS_DIR = "chains"

#: Discriminator tags pydantic inserts into a tagged-union error location. They
#: are not authored structure, so they are dropped before a location is matched
#: against the components this resolver expanded.
_UNION_TAGS = frozenset({*TaskKind, *NodeKind})


class TemplateLibraryError(Exception):
    pass


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

    def __str__(self) -> str:
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


def _read(path: Path) -> Mapping[str, object]:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise TemplateLibraryError(f"{path}: cannot read/parse: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise TemplateLibraryError(f"{path}: expected a mapping")
    return data


def _section(path: Path, data: Mapping[str, object], namespace: Namespace) -> Mapping[str, object]:
    section = data.get(namespace.value) or {}
    if not isinstance(section, Mapping):
        raise TemplateLibraryError(
            f"{path}: {namespace.value!r} must be a mapping of name to definition"
        )
    for name, body in section.items():
        if not isinstance(body, Mapping):
            raise TemplateLibraryError(f"{path}: {namespace.value}.{name} must be a mapping")
    return section


class TemplateLibrary:
    """One template directory's authored configuration: reusable components,
    named steering profiles, and the selectable chains."""

    def __init__(
        self,
        components: Mapping[Namespace, Mapping[str, RawComponent]],
        chains: Mapping[str, RawComponent],
        steering: Mapping[str, SteeringProfile],
    ) -> None:
        self._components = components
        self._chains = chains
        self.steering = steering

    @classmethod
    def from_yaml_dir(cls, path: str | Path) -> TemplateLibrary:
        """Read `library.yaml` and every `chains/*.yaml` under `path`. The only
        boundary I/O here; a read or parse failure becomes a
        `TemplateLibraryError` naming the file."""
        root = Path(path)
        library_path = root / LIBRARY_FILE
        if not library_path.is_file():
            raise TemplateLibraryError(f"{library_path}: no {LIBRARY_FILE} to read")
        library = _read(library_path)

        components = {
            namespace: {
                name: RawComponent(body, ComponentSource(library_path, namespace, name))
                for name, body in _section(library_path, library, namespace).items()
            }
            for namespace in _EXTENDABLE
        }

        steering: dict[str, SteeringProfile] = {}
        for name, body in _section(library_path, library, Namespace.STEERING).items():
            source = ComponentSource(library_path, Namespace.STEERING, name)
            try:
                steering[name] = SteeringProfile.model_validate(body)
            except ValidationError as exc:
                raise TemplateLibraryError(f"{source}: {_first(exc)}") from exc

        chains: dict[str, RawComponent] = {}
        for chain_path in sorted((root / CHAINS_DIR).glob("*.yaml")):
            body = _read(chain_path)
            id = body.get("id") or chain_path.stem
            if not isinstance(id, str):
                raise TemplateLibraryError(f"{chain_path}: 'id' must be a string")
            if id in chains:
                # One selectable chain per file: two files claiming one id would
                # otherwise make the loser's chain vanish from `chain_ids`.
                raise TemplateLibraryError(
                    f"{chain_path}: chain id {id!r} is already declared by {chains[id].source.file}"
                )
            chains[id] = RawComponent(
                {**body, "id": id}, ComponentSource(chain_path, Namespace.NODES, id)
            )
        return cls(components, chains, steering)

    @property
    def chain_ids(self) -> tuple[str, ...]:
        return tuple(self._chains)

    def component_names(self, namespace: str | Namespace) -> tuple[str, ...]:
        return tuple(self._components[Namespace(namespace)])

    def component(self, namespace: Namespace, name: str) -> RawComponent | None:
        return self._components[namespace].get(name)

    def namespace_of(self, name: str) -> Namespace | None:
        """Which namespace declares `name`, for the cross-namespace `extends`
        error that says what the author actually referenced."""
        return next((ns for ns in _EXTENDABLE if name in self._components[ns]), None)

    def resolve_chain(self, id: str) -> ResolvedChain:
        """Expand one chain's `extends` references, validate the result into a
        typed `Chain`, and assign canonical execution paths
        (`resolved-chain-is-validated-before-use`)."""
        raw = self._chains.get(id)
        if raw is None:
            raise TemplateLibraryError(
                f"no chain {id!r} in this library; known chains: {list(self._chains)}"
            )
        resolution = _Resolution(self, raw.source)
        expanded = resolution.expand_chain(raw.data)
        try:
            chain = Chain.model_validate(expanded)
        except ValidationError as exc:
            raise TemplateLibraryError(resolution.explain(exc)) from exc
        resolved = ResolvedChain.from_chain(chain)
        for node in resolved.nodes:
            for task in node.tasks():
                for name in task.task.steering:
                    if name not in self.steering:
                        raise TemplateLibraryError(
                            f"{resolution.at(task.path)}: selects no steering profile {name!r}"
                        )
        return resolved


def _first(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = PATH_SEPARATOR.join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else error["msg"]


@dataclass(frozen=True)
class _Located:
    """One expanded component: where it sits in the resolved chain, and where
    its definition came from."""

    path: str
    source: ComponentSource


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

    # ── expansion ──

    def expand_chain(self, data: Mapping[str, object]) -> Mapping[str, object]:
        if "extends" in data:
            # `component-extends-has-one-parent` allows a chain to extend one
            # chain, but V1 has no consumer for it and no chain namespace to
            # resolve it against. Say so, rather than letting the author read
            # pydantic's "Extra inputs are not permitted".
            raise TemplateLibraryError(
                f"{self._chain.file}: chain-level 'extends' is not supported in V1; "
                "share structure through a reusable node instead"
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
        node = self._expand(raw, Namespace.NODES, self._provisional(raw, "", index), loc)
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
        return expanded

    def _container(
        self, raw: Mapping[str, object], path: str, loc: tuple[object, ...]
    ) -> Mapping[str, object]:
        """Expand one execution shape: a `tasks` group (whose tasks already sit
        under the `main` step they resolve to), ordered `steps`, and a fix
        loop's dedicated judge."""
        container = dict(raw)
        tasks = container.get("tasks")
        if isinstance(tasks, list):
            prefix = f"{path}{PATH_SEPARATOR}{MAIN_STEP}"
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
        return self._container(step, path, loc)

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
        if segment is not None:
            # A dedicated task takes its container's segment as its path
            # (`component-identifiers-are-qualified-by-node-instance`), so its
            # authored id never becomes a path segment of its own.
            self._locate(loc, f"{prefix}{PATH_SEPARATOR}{segment}")
            return task
        self._record(task, loc, prefix=prefix, fallback="task")
        return task

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

    def _locate(self, loc: tuple[object, ...], path: str) -> None:
        source = self._inherited.pop(loc, self._chain)
        self._located[loc] = _Located(path, source)
        self._sources.setdefault(path, source)

    # ── extends ──

    def _expand(
        self, raw: object, namespace: Namespace, where: str, loc: tuple[object, ...]
    ) -> Mapping[str, object]:
        if not isinstance(raw, Mapping):
            raise TemplateLibraryError(f"{self._chain.file}: {where}: must be a mapping")
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
                f"of the same kind, not {name!r}"
            )
        if name in seen:
            raise TemplateLibraryError(
                f"{self._chain.file}: {where}: 'extends' cycle: {' -> '.join((*seen, name))}"
            )
        parent = self._parent(namespace, name, where)
        # The *resolved* parent, so a kind inherited further up the chain still
        # counts as the parent's kind.
        resolved = self._merge_chain(parent.data, namespace, where, loc, seen=(*seen, name))
        self._reject_kind_change(resolved, child, where, name)
        merged = _merge(resolved, child)
        assert isinstance(merged, Mapping)
        self._inherited.setdefault(loc, parent.source)
        return merged

    def _parent(self, namespace: Namespace, name: str, where: str) -> RawComponent:
        component = self._library.component(namespace, name)
        if component is not None:
            return component
        other = self._library.namespace_of(name)
        if other is not None:
            raise TemplateLibraryError(
                f"{self._chain.file}: {where}: extends {name!r}, which is a "
                f"{other.singular}, not a {namespace.singular}"
            )
        raise TemplateLibraryError(
            f"{self._chain.file}: {where}: extends no {namespace.singular} named {name!r}"
        )

    def _reject_kind_change(
        self, parent: Mapping[str, object], child: Mapping[str, object], where: str, name: str
    ) -> None:
        """`extends-cannot-change-kind`."""
        inherited, declared = parent.get("kind"), child.get("kind")
        if inherited is not None and declared is not None and inherited != declared:
            raise TemplateLibraryError(
                f"{self._chain.file}: {where}: extends {name!r} and cannot change kind "
                f"{str(inherited)!r} to {str(declared)!r}"
            )

    # ── diagnostics ──

    def at(self, path: str) -> str:
        """`path`, prefixed by the chain file and suffixed by the file the
        component's definition came from when it was inherited."""
        source = self._sources.get(path)
        inherited = f" <- {source}" if source is not None and source != self._chain else ""
        return f"{self._chain.file}: {path}{inherited}"

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
