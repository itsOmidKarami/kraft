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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import ValidationError

from kraft import skill as _skill
from kraft.policy import InstancePolicy, InstancePolicyInput, PolicyError
from kraft.templates.models import (
    JUDGE_SEGMENT,
    MAIN_STEP,
    PATH_SEPARATOR,
    Chain,
    NodeKind,
    ResolvedChain,
    SteeringProfile,
    TaskKind,
    first_error,
)

LIBRARY_FILE = "library.yaml"
CHAINS_DIR = "chains"
#: Every pre-V1 home was seeded with one, and V1 has no reader for it.
LEGACY_REGISTRY = "registry.yaml"


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
    pass


@dataclass(frozen=True)
class TemplateIssue:
    """One thing wrong with an authored library, named where an author can find
    it (`template-resolution-preserves-source-context`)."""

    file: Path
    #: `None` for an issue in `library.yaml` itself, which no one chain owns.
    chain: str | None
    message: str

    def __str__(self) -> str:
        return f"{self.chain or self.file}: {self.message}"


@dataclass(frozen=True)
class LintReport:
    """`TemplateLibrary.lint_dir`'s answer: the chains that resolve, and every
    issue with the rest."""

    chains: tuple[str, ...]
    issues: tuple[TemplateIssue, ...]

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


def _chain_id(chain_path: Path, body: Mapping[str, object]) -> str:
    id = body.get("id") or chain_path.stem
    if not isinstance(id, str):
        raise TemplateLibraryError(f"{chain_path}: 'id' must be a string")
    return id


class TemplateLibrary:
    """One template directory's authored configuration: reusable components,
    named steering profiles, and the selectable chains."""

    def __init__(
        self,
        components: Mapping[Namespace, Mapping[str, RawComponent]],
        chains: Mapping[str, RawComponent],
        steering: Mapping[str, SteeringProfile],
        skills_dir: Path | None = None,
    ) -> None:
        self._components = components
        self._chains = dict(chains)
        self.steering = steering
        #: Where an operator may overlay a method file (`kraft.skill`); `None`
        #: means the bundled methods only.
        self.skills_dir = skills_dir

    @classmethod
    def from_yaml_dir(
        cls,
        path: str | Path,
        *,
        skills_dir: Path | None = None,
        issues: list[TemplateIssue] | None = None,
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
                f"{root} holds a pre-V1 template configuration, which this Kraft does not "
                "run; `kraft admin update` backs it up and installs the V1 configuration"
            )
        if not library_path.is_file():
            raise TemplateLibraryError(f"{library_path}: no {LIBRARY_FILE} to read")
        library = cls.from_mappings(
            _read(library_path), (), library_path=library_path, skills_dir=skills_dir
        )
        for chain_path in sorted((root / CHAINS_DIR).glob("*.yaml")):
            try:
                library._add_chain(chain_path, _read(chain_path))
            except TemplateLibraryError as exc:
                if issues is None:
                    raise
                issues.append(TemplateIssue(chain_path, chain_path.stem, str(exc)))
        return library

    @classmethod
    def from_mappings(
        cls,
        library: Mapping[str, object],
        chains: Iterable[tuple[Path, Mapping[str, object]]],
        *,
        library_path: Path,
        skills_dir: Path | None = None,
    ) -> TemplateLibrary:
        """A library from already-parsed mappings: `library` is `library.yaml`'s
        content and each chain is paired with the file it is named after --
        real for `from_yaml_dir`, nominal for an unsaved library, which is
        resolved without ever being written (`POST /templates/resolve`)."""
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
                raise TemplateLibraryError(f"{source}: {first_error(exc)}") from exc

        built = cls(components, {}, steering, skills_dir)
        for chain_path, body in chains:
            built._add_chain(chain_path, body)
        return built

    def with_library(self, library: Mapping[str, object], library_path: Path) -> TemplateLibrary:
        """This library's chains against a replacement `library.yaml` content:
        how an edit of the library is checked before it is saved. `self` is not
        changed."""
        return TemplateLibrary.from_mappings(
            library,
            [(raw.source.file, raw.data) for raw in self._chains.values()],
            library_path=library_path,
            skills_dir=self.skills_dir,
        )

    def with_chain(
        self, chain_path: Path, body: Mapping[str, object]
    ) -> tuple[TemplateLibrary, str]:
        """This library plus one unsaved chain, and the chain's id. A chain of
        the same id is shadowed, which is how a saved chain's edit is checked
        before it is saved. `self` is not changed."""
        candidate = TemplateLibrary(self._components, self._chains, self.steering, self.skills_dir)
        candidate._chains.pop(_chain_id(chain_path, body), None)
        return candidate, candidate._add_chain(chain_path, body)

    def _add_chain(self, chain_path: Path, body: Mapping[str, object]) -> str:
        id = _chain_id(chain_path, body)
        if id in self._chains:
            # One selectable chain per file: two files claiming one id would
            # otherwise make the loser's chain vanish from `chain_ids`.
            claimed = self._chains[id].source.file
            raise TemplateLibraryError(
                f"{chain_path}: chain id {id!r} is already declared by {claimed}"
            )
        self._chains[id] = RawComponent(
            {**body, "id": id}, ComponentSource(chain_path, Namespace.NODES, id)
        )
        return id

    @property
    def chain_ids(self) -> tuple[str, ...]:
        return tuple(self._chains)

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
        resolution = _Resolution(self, self._chains[id].source)
        try:
            resolution.expand_chain(self._chains[id].data)
        except TemplateLibraryError:
            pass
        return resolution.references

    def component(self, namespace: Namespace, name: str) -> RawComponent | None:
        return self._components[namespace].get(name)

    def namespace_of(self, name: str) -> Namespace | None:
        """Which namespace declares `name`, for the cross-namespace `extends`
        error that says what the author actually referenced."""
        return next((ns for ns in _EXTENDABLE if name in self._components[ns]), None)

    @classmethod
    def lint_dir(
        cls,
        path: str | Path,
        *,
        skills_dir: Path | None = None,
        instance_policy: InstancePolicy | None = None,
    ) -> LintReport:
        """Everything wrong with the template directory at `path` -- every
        chain file that does not parse and every chain that does not resolve,
        or the one `library.yaml` failure that leaves nothing to resolve
        against (`template-lint-reports-library-validity`). Reads; never writes."""
        root = Path(path)
        issues: list[TemplateIssue] = []
        try:
            library = cls.from_yaml_dir(root, skills_dir=skills_dir, issues=issues)
        except TemplateLibraryError as exc:
            return LintReport(
                chains=(), issues=(TemplateIssue(root / LIBRARY_FILE, None, str(exc)),)
            )
        issues += library.lint(instance_policy)
        failed = {issue.chain for issue in issues}
        return LintReport(
            chains=tuple(id for id in library.chain_ids if id not in failed), issues=tuple(issues)
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
                issues.append(TemplateIssue(file=raw.source.file, chain=id, message=str(exc)))
        return issues

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
                    f"wholly produces one kind or declares none"
                )
            for task in node.tasks():
                for name in task.task.steering:
                    if name not in self.steering:
                        raise TemplateLibraryError(
                            f"{resolution.at(task.path)}: selects no steering profile {name!r}"
                        )
                # A skill that names no method is refused here, where lint and
                # intake see it, never discovered by an agent at launch
                # (Kraft-vhcop). Another plugin's skill cannot be looked up
                # from here; `skill.UNAVAILABLE` has the agent stop on it.
                selected = getattr(task.task, "skill", None)
                if selected is not None:
                    try:
                        _skill.validate(self.skills_dir, selected, where=task.path)
                    except _skill.SkillError as exc:
                        raise TemplateLibraryError(
                            f"{resolution.at(task.path)}: selects skill {selected!r}, "
                            f"which resolves to no method: {exc}"
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
        )


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
        #: Per node id, the library components its expansion used
        #: (`TemplateLibrary.references`); filed under the node being expanded.
        self.references: dict[str, set[str]] = {}
        self._refs: set[str] = set()

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
        provisional = self._provisional(raw, "", index)
        # ponytail: keyed by the node's authored id (its position without one);
        # a node whose id is only inherited is filed under `[index]`.
        self._refs = self.references.setdefault(provisional, set())
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
        return self._with_handler(self._container(step, path, loc), path, loc)

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
            self._refs.update(
                f"{Namespace.STEERING.value}.{n}" for n in steering if isinstance(n, str)
            )
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
            self._refs.add(f"{namespace.value}.{name}")
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
