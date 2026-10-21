"""V1 template-schema environment: the workspaces built from connected
repositories, the path-scoped areas inside one, the target a work item runs
against, and the harness profiles an agent task selects from.

A repository itself is not modelled here: `config.RepoEntry`, read by
`config.load_repos`, is the one repository model (Ruling 177), and it checks
its `areas:` against `Area` below and `config.load_workspaces` its
`workspaces:` against `Workspace`. See docs/templates-v1-design.md's "Harness
profiles" and "Repositories, workspaces, and areas" sections for the YAML.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    model_validator,
)

from kraft.harness import Harness

if TYPE_CHECKING:
    from kraft.addons.load import InstalledAddon

#: One identifier rule for both sides of every reference: the harness-profile
#: id an `AgentTask.harness` names, the repository id a workspace member
#: mounts, the chain id a repository defaults to. `kraft.templates.models`
#: imports `Identifier` from here rather than defining its own, because it
#: already imports this module and the reverse would be a cycle -- so nothing
#: definable here is unreferenceable there.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")

Identifier = Annotated[StrictStr, Field(pattern=_IDENTIFIER.pattern)]

#: A reference that may name an add-on's declaration: `ship` or `release:ship`.
#: What a file declares stays `Identifier`; only references take this.
_QUALIFIED = re.compile(r"^([a-z][a-z0-9_-]*:)?[a-z][a-z0-9_-]*$")

QualifiedIdentifier = Annotated[StrictStr, Field(pattern=_QUALIFIED.pattern)]


class TemplateEnvironmentError(Exception):
    pass


#: What `git check-ref-format` never allows anywhere in a ref name: control
#: characters, space and DEL, `~ ^ : ? * [ \\`, `..`, `@{` and `//`.
_REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//")


def branch_name_problem(name: str) -> str | None:
    """Why `git check-ref-format --branch` would refuse `name`, or None.

    Pure Python, so the model can refuse a bad name at validation whatever
    door built it -- intake, a rehydrated snapshot, a retry fork, a script --
    with no git call (Kraft-j4adz). A leading `-` is refused first by name:
    it is the one that would reach `gh pr create --base` and friends as an
    option. Stricter than git only on `@`, which `--branch` reads as the
    current branch."""
    if not name:
        return "is empty"
    if name.startswith("-"):
        return "starts with '-', which a command would read as an option"
    if name in ("@", "HEAD"):
        return f"is {name!r}, which git reserves"
    if (found := _REF_FORBIDDEN.search(name)) is not None:
        return f"contains {found.group()!r}"
    if name.startswith("/") or name.endswith("/"):
        return "starts or ends with '/'"
    if name.endswith("."):
        return "ends with '.'"
    for part in name.split("/"):
        if part.startswith("."):
            return f"has a component {part!r} starting with '.'"
        if part.endswith(".lock"):
            return f"has a component {part!r} ending with '.lock'"
    return None


def _valid_branch(name: str) -> str:
    problem = branch_name_problem(name)
    if problem is not None:
        raise ValueError(f"base branch {name!r} is not a valid branch name: it {problem}")
    return name


BranchName = Annotated[StrictStr, AfterValidator(_valid_branch)]


class RootPointerPolicy(StrEnum):
    """How a workspace's root-repository submodule pointers are handled when
    its members change. `IGNORE` is the shipped default
    (`workspace-root-pointer-update-defaults-to-ignore`) -- a workspace work
    item lands only in the child repositories it touched."""

    IGNORE = "ignore"
    BUMP = "bump"


class TestScope(BaseModel):
    """One `verification.test_scopes` entry, shared verbatim by a repository
    and by one of its areas (`repository-area-can-declare-setup-and-test-
    scopes`: "the same selection and result semantics")."""

    model_config = ConfigDict(strict=True, extra="forbid")

    paths: list[StrictStr] = Field(min_length=1)
    command: StrictStr = Field(min_length=1)


class Verification(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    test_scopes: list[TestScope] = Field(default_factory=list)


class Area(BaseModel):
    """A path-scoped execution context inside one real repository. Never a
    forge target: it has no `forge` field to declare, and no `path` of its
    own -- only `paths` glob patterns inside its owning repository
    (`repositories-workspaces-and-areas-are-distinct`)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    paths: list[StrictStr] = Field(min_length=1)
    setup: StrictStr | None = None
    verification: Verification = Field(default_factory=Verification)


class WorkspaceMember(BaseModel):
    """One repository mounted into a workspace's root, and where
    (`workspace-declares-root-and-members`)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    repository: Identifier
    path: StrictStr = Field(min_length=1)


class Workspace(BaseModel):
    """A virtual monorepo: a root repository plus its mounted members."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: Identifier
    root: Identifier
    root_pointer_default: Annotated[RootPointerPolicy, Field(strict=False)] = (
        RootPointerPolicy.IGNORE
    )
    members: dict[Identifier, WorkspaceMember] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_member_at_or_inside_another(self) -> Workspace:
        """A member mounted inside another member cannot be assembled: the
        root's `git submodule update` does not reach into a submodule
        (Kraft-z0wzd, option A; nesting is Kraft-37gnq). Nor can two members
        share one mount: only one checkout fits there (Kraft-rx4n5). Here, so
        every door that reads `workspaces:` -- load, intake, a Settings write
        -- refuses it."""
        mounts = {n: PurePosixPath(m.path) for n, m in self.members.items()}
        seen: dict[PurePosixPath, str] = {}
        for name, path in mounts.items():
            if path in seen:
                raise ValueError(
                    f"member {name!r} at {str(path)!r} is mounted where member "
                    f"{seen[path]!r} is: two members cannot share one mount"
                )
            seen[path] = name
            for outer, outer_path in mounts.items():
                if outer_path in path.parents:
                    raise ValueError(
                        f"member {name!r} at {str(path)!r} is inside member {outer!r} at "
                        f"{str(outer_path)!r}: a member nested inside another cannot be "
                        "assembled; mount each member directly in the root"
                    )
        return self


class WorkItemTarget(BaseModel):
    """What one work item runs against, captured immutably at materialization
    (`work-item-target-selection-is-immutable`): one repository, or selected
    members of a workspace (optionally including its root)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    kind: Literal["repository", "workspace"]
    repository: Identifier | None = None
    workspace: Identifier | None = None
    #: A workspace target's root repository id: with each mount's repository,
    #: the repositories whose policy binds the item (Kraft-jc39p).
    root: Identifier | None = None
    #: `strict=False` here only: pydantic's strict mode never coerces a plain
    #: (decoded-JSON) list into a tuple, so a frozen target rehydrated as a
    #: dict -- the path `model_validate_json` skips but `json.loads()` then
    #: `model_validate(dict)` takes -- would otherwise fail on its own
    #: `model_dump()` output. `members` stays a tuple on the model either way.
    members: Annotated[tuple[Identifier, ...], Field(strict=False)] = ()
    #: Each selected member's repository and mount path, frozen at intake
    #: (`work-item-target-is-typed-and-immutable`): the checkout assembles
    #: from these, so a later edit to the workspace moves nothing under a
    #: running item. Keyed by member, exactly the `members` selected.
    mounts: dict[Identifier, WorkspaceMember] = Field(default_factory=dict)
    #: Meaningless outside a workspace target, so it defaults off; only
    #: `kind="workspace"` may turn it on (`_kind_owns_its_fields` below).
    include_root: bool = False
    root_pointer_policy: Annotated[RootPointerPolicy, Field(strict=False)] = (
        RootPointerPolicy.IGNORE
    )
    #: The branch the item's work starts from, rebases onto and merges into,
    #: frozen at intake (Kraft-v9gbi); None is the repository's default
    #: branch. It names the item's own repository, or a workspace's root only:
    #: each member keeps its own default branch, as it always has, since one
    #: branch name means nothing across repositories that need not share it.
    #: Read through `builtins.base_branch`, never here directly. `BranchName`
    #: holds it to git's own branch-name rules, and off a leading `-`, on
    #: every validation -- a snapshot rehydrated or a target built by any door
    #: is checked, not only intake's (Kraft-j4adz).
    base_branch: BranchName | None = None
    #: Whether the item runs in its repository's own checkout rather than a
    #: worktree of its own, frozen at intake (`in-place-is-frozen-at-intake`):
    #: the item's own choice, else its repository's `worktrees`. A snapshot
    #: from before it existed has no key, and reads as a worktree item.
    in_place: bool = False

    @model_validator(mode="after")
    def _kind_owns_its_fields(self) -> WorkItemTarget:
        """Each `kind` carries exactly its own fields. The classmethods below
        build this correctly, but they are not in the path when Phase 2
        rehydrates a frozen target from stored JSON."""
        if self.kind == "repository":
            if self.repository is None:
                raise ValueError("a repository target must name a repository")
            if self.workspace is not None or self.members or self.mounts or self.root:
                raise ValueError("a repository target has no workspace or members")
            if self.include_root:
                raise ValueError("a repository target has no root to include")
        else:
            if self.workspace is None:
                raise ValueError("a workspace target must name a workspace")
            if self.repository is not None:
                raise ValueError("a workspace target has no repository")
            if self.in_place:
                raise ValueError("a workspace target cannot run in place")
            if set(self.mounts) != set(self.members):
                raise ValueError(
                    f"a workspace target mounts exactly its selected members: selected "
                    f"{sorted(self.members)}, mounted {sorted(self.mounts)}"
                )
        return self

    def repositories(self) -> tuple[str, ...]:
        """Every repository id a workspace target selects, root first: the
        root (whose checkout the item assembles in) and each member's."""
        members = tuple(m.repository for m in self.mounts.values())
        return ((self.root,) if self.root else ()) + members

    @classmethod
    def for_repository(
        cls, repository: str, *, base_branch: str | None = None, in_place: bool = False
    ) -> WorkItemTarget:
        """A single-repository target, by the repository's id."""
        return cls(
            kind="repository", repository=repository, base_branch=base_branch, in_place=in_place
        )

    @classmethod
    def from_selection(
        cls,
        workspace: Workspace,
        *,
        members: Sequence[str],
        include_root: bool = True,
        root_pointer_policy: RootPointerPolicy | None = None,
        base_branch: str | None = None,
    ) -> WorkItemTarget:
        """Select some (or all) of `workspace`'s members. Every name must be
        one the workspace actually mounts -- an unknown member is a template
        error, not a silently empty target."""
        unknown = sorted(set(members) - set(workspace.members))
        if unknown:
            raise TemplateEnvironmentError(
                f"workspace {workspace.id!r} does not mount member(s) {unknown!r}"
            )
        return cls(
            kind="workspace",
            workspace=workspace.id,
            root=workspace.root,
            members=tuple(members),
            mounts={m: workspace.members[m] for m in members},
            include_root=include_root,
            root_pointer_policy=root_pointer_policy or workspace.root_pointer_default,
            base_branch=base_branch,
        )


class HarnessProfileInput(BaseModel):
    """One `harnesses.yaml` entry: a configured instance of a provider, never
    provider command syntax or result parsing
    (`harness-profile-has-safe-instance-configuration`)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    provider: Identifier
    enabled: StrictBool = True
    executable: StrictStr | None = None
    defaults: dict[StrictStr, StrictStr] = Field(default_factory=dict)


@dataclass(frozen=True)
class HarnessProfile:
    """A `HarnessProfileInput`, validated against the provider's own declared
    capability surface (`provider-declares-harness-capabilities`,
    `agent-task-selects-capability-compatible-runtime-options`). `harness`
    stands in for "the provider" here: `kraft.harness.Harness` already is the
    code-owned declaration of what a runtime accepts, kind, capabilities and
    all -- a profile selects only from it, never invents its own binding."""

    id: str
    provider: str
    enabled: bool
    executable: str | None
    defaults: dict[str, str]

    @classmethod
    def from_input(
        cls, id: str, parsed: HarnessProfileInput, *, harness: Harness
    ) -> HarnessProfile:
        if not _IDENTIFIER.match(id):
            raise TemplateEnvironmentError(
                f"harness profile id {id!r} must match {_IDENTIFIER.pattern} "
                f"to be nameable by a task's 'harness:'"
            )
        if parsed.provider != harness.id:
            raise TemplateEnvironmentError(
                f"harness profile {id!r}: provider {parsed.provider!r} is not the "
                f"harness it configures ({harness.id!r}) -- the provider id IS "
                f"the harness id"
            )
        for option, value in parsed.defaults.items():
            if not harness.supports(option):
                raise TemplateEnvironmentError(
                    f"harness profile {id!r}: {option!r} is not a capability "
                    f"{harness.id!r} declares"
                )
            if not harness.value_ok(option, value):
                raise TemplateEnvironmentError(
                    f"harness profile {id!r}: {value!r} is not a valid value for {option!r}"
                )
        return cls(
            id=id,
            provider=parsed.provider,
            enabled=parsed.enabled,
            executable=parsed.executable,
            defaults=dict(parsed.defaults),
        )

    def is_available(self) -> bool:
        """False when the profile is administratively disabled
        (`unavailable-selected-harness-needs-human` stops the task rather
        than substituting another profile -- that decision is Phase 6's, this
        method only reports the fact)."""
        return self.enabled


class FallbackEntry(BaseModel):
    """One entry of a `fallback:` list, on an agent task or an agent profile:
    where a launch goes next when the one before it is rate-limited or
    unavailable (`rate-limited-launch-falls-back-to-next-candidate`). An
    optional `harness:` plus one route, the same either-or a task obeys:
    `profile:`, or `model:` and/or `effort:`. Whatever it omits is kept from
    the task's own launch; the item's and the node's overrides are not
    (`executor.fallback`)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    harness: Identifier | None = None
    profile: QualifiedIdentifier | None = None
    model: StrictStr | None = None
    effort: StrictStr | None = None

    @model_validator(mode="after")
    def _one_route(self) -> FallbackEntry:
        if self.profile is not None and (self.model is not None or self.effort is not None):
            raise ValueError(
                f"a fallback entry selects profile {self.profile!r} and sets model/effort "
                "itself; an entry takes its model from one or the other"
            )
        if all(v is None for v in (self.harness, self.profile, self.model, self.effort)):
            raise ValueError(
                "a fallback entry names a harness, a profile, a model or an effort: an "
                "empty one would relaunch the same thing"
            )
        return self


class ProviderEntryInput(BaseModel):
    """One provider's route within a profile written with `providers:`."""

    model_config = ConfigDict(strict=True, extra="forbid")

    model: StrictStr
    effort: StrictStr | None = None


class AgentProfileInput(BaseModel):
    """One `harnesses.yaml` `profiles:` entry: a named model tier an agent task
    selects with `profile:` (Kraft-ps1ao). It names each provider's route in
    one of two shapes, never both: `providers: {id: {model, effort?}}`, or the
    older `model: {id: model}` with one `effort` shared by every provider. Keyed
    by *provider* id, not harness id, so two harnesses on one provider share one
    spelling."""

    model_config = ConfigDict(strict=True, extra="forbid")

    providers: dict[Identifier, ProviderEntryInput] | None = Field(default=None, min_length=1)
    model: dict[Identifier, StrictStr] | None = Field(default=None, min_length=1)
    effort: StrictStr | None = None
    #: The tier's default fallback list, for a task selecting it that sets no
    #: `fallback:` of its own (Kraft-0a3h8). An entry's profile's own list is
    #: never followed.
    fallback: list[FallbackEntry] | None = None

    @model_validator(mode="after")
    def _one_shape(self) -> AgentProfileInput:
        if self.providers is not None and (self.model is not None or self.effort is not None):
            raise ValueError(
                "sets both 'providers' and 'model'/'effort'; a profile is written in one shape"
            )
        if self.providers is None and self.model is None:
            raise ValueError("names no provider: set 'providers' (or the older 'model')")
        return self


@dataclass(frozen=True)
class ProviderEntry:
    """What a profile runs on one provider."""

    model: str
    effort: str | None = None


@dataclass(frozen=True)
class AgentProfile:
    """An agent profile: what a tier runs on, per provider. It may omit any
    provider; only a task pairing it with a harness of that provider is
    refused (`adapters.agent.resolve_profile`). Both file shapes load into
    `providers`; the older one copies its `effort` into every entry."""

    id: str
    providers: dict[str, ProviderEntry]
    fallback: tuple[FallbackEntry, ...] = ()

    @property
    def model(self) -> dict[str, str]:
        return {p: e.model for p, e in self.providers.items()}

    @property
    def effort(self) -> str | None:
        """The one effort every provider's entry shares, else None."""
        efforts = {e.effort for e in self.providers.values()}
        return efforts.pop() if len(efforts) == 1 else None

    @classmethod
    def from_input(
        cls,
        id: str,
        parsed: AgentProfileInput,
        *,
        harnesses: Mapping[str, Harness],
        qualified: bool = False,
    ) -> AgentProfile:
        # A local profile id is bare; only an add-on's arrives qualified.
        rule = _QUALIFIED if qualified else _IDENTIFIER
        if not rule.match(id):
            raise TemplateEnvironmentError(
                f"profile id {id!r} must match {rule.pattern} to be nameable by a task's 'profile:'"
            )
        if parsed.providers is not None:
            providers = {p: ProviderEntry(e.model, e.effort) for p, e in parsed.providers.items()}
        else:
            providers = {
                p: ProviderEntry(m, parsed.effort) for p, m in (parsed.model or {}).items()
            }
        unknown = sorted(set(providers) - set(harnesses))
        if unknown:
            raise TemplateEnvironmentError(
                f"profiles.{id}: model names {unknown}, which is no installed provider; "
                f"known: {sorted(harnesses)}"
            )
        if parsed.providers is not None:
            for p, entry in providers.items():
                if entry.effort is not None and not harnesses[p].value_ok("effort", entry.effort):
                    raise TemplateEnvironmentError(
                        f"profiles.{id}.providers.{p}: effort {entry.effort!r} is not accepted "
                        f"by provider {p!r}"
                    )
        elif parsed.effort is not None and not any(
            harnesses[p].value_ok("effort", parsed.effort) for p in providers
        ):
            raise TemplateEnvironmentError(
                f"profiles.{id}: effort {parsed.effort!r} is accepted by none of its providers "
                f"{sorted(providers)}"
            )
        return cls(id=id, providers=providers, fallback=tuple(parsed.fallback or ()))


# ── `harnesses.yaml`, the V1 file these types are read from ──────────────────
#
# `from_yaml` may do boundary I/O; `from_input` stays pure. It translates a
# read or parse failure into this module's own error type, so a caller catches
# one exception for the file rather than `OSError`/`YAMLError`/
# `ValidationError` from three layers down.


def _first_error(exc: ValidationError) -> str:
    """A pydantic failure as one line naming the field that failed. Its own
    three lines rather than an import: `templates.models.first_error` joins
    with `PATH_SEPARATOR`, which is that module's, and this module cannot
    import it (`models` imports *this* one, so the reverse is a cycle)."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else error["msg"]


def _section(data: object, section: str, path: Path) -> dict[str, object]:
    """One top-level mapping section of a parsed file, or `{}` when it is absent.

    An absent section is not an error, but a section present and not a mapping
    is, because the keys are the identifiers everything else references.
    """
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise TemplateEnvironmentError(f"{path}: expected a mapping at the top level")
    raw = data.get(section)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TemplateEnvironmentError(
            f"{path}: {section!r} must be a mapping keyed by id, not "
            f"{type(raw).__name__} -- each entry is keyed by the id the rest of "
            f"the configuration references it as"
        )
    return raw


def _agent_profiles(
    section: Mapping[str, object],
    path: Path,
    harnesses: Mapping[str, Harness],
    namespace: str | None = None,
) -> dict[str, AgentProfile]:
    """One file's `profiles:` section as agent profiles; under `namespace:` for
    an add-on's."""
    found: dict[str, AgentProfile] = {}
    for name, body in section.items():
        id = f"{namespace}:{name}" if namespace else name
        try:
            parsed = AgentProfileInput.model_validate(body or {})
            found[id] = AgentProfile.from_input(
                id, parsed, harnesses=harnesses, qualified=namespace is not None
            )
        except ValidationError as exc:
            raise TemplateEnvironmentError(f"{path}: profiles.{name}: {_first_error(exc)}") from exc
        except TemplateEnvironmentError as exc:
            raise TemplateEnvironmentError(f"{path}: {exc}") from exc
    return found


def _addon_profiles(
    addon: InstalledAddon,
    harnesses: Mapping[str, Harness],
    addons: Sequence[InstalledAddon],
) -> dict[str, AgentProfile]:
    """An add-on's `profiles.yaml`, under its namespace. Only its `profiles:`
    section is read: an add-on never chooses an executable, so a `harnesses:`
    section in it has no reader here. A child's fallback naming its parent
    (`<parent>:fast`) is re-pointed to the ancestor that defines the profile
    (`inherit.requalify`), found among `addons`."""
    # Imported here: `kraft.config` imports this module.
    from kraft.addons import fetch
    from kraft.addons.load import qualify
    from kraft.config import bounded_yaml

    path = addon.root / "profiles.yaml"
    try:
        # Through the store's digest: the bytes parsed are the bytes checked.
        raw = fetch.open_store(addon.root).read("profiles.yaml", optional=True)
        if raw is None:
            return {}
        written = bounded_yaml(raw.decode(), [20_000])
        # A re-pointed file is final: `qualify` would read an ancestor installed
        # as the child's own name as the child's.
        repointed = None
        if addon.parent is not None:
            repointed = _name_the_defining_ancestor(written, addon, addons)
        data = repointed if repointed is not None else qualify(written, addon)
    except fetch.StoreMismatch as exc:
        raise TemplateEnvironmentError(f"{path}: {exc}") from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise TemplateEnvironmentError(f"{path}: cannot read/parse: {exc}") from exc
    return _agent_profiles(_section(data, "profiles", path), path, harnesses, addon.namespace)


def _name_the_defining_ancestor(
    written: object, addon: InstalledAddon, addons: Sequence[InstalledAddon]
) -> object | None:
    """`written`, a child's `profiles.yaml`, with each profile it names by its
    parent's name (`platform-dept:fast`) set to the namespace of the ancestor
    that defines it, as `TemplateLibrary` does for a `profile:` in a chain. A
    child whose ancestry does not read gives None, and so does a file that names
    no profile by its own or its parent's name (nothing to re-point, decided
    before any ancestor's store is read): the caller qualifies `written` itself.
    The library load says why an ancestry does not read (the same errors
    `inherit.inherited` leaves to it)."""
    # Imported here: `kraft.config` imports this module.
    from kraft.addons import fetch, inherit
    from kraft.templates.library import TemplateLibraryError

    try:
        chain = inherit.ancestry(addon, addons)
        if not _names_a_profile_of(written, {addon.name, chain[1].name}):
            return None
        seen = inherit.visible(chain)
    except (inherit.InheritanceError, fetch.StoreMismatch, TemplateLibraryError, OSError):
        return None

    def profile_owner(name: str, depth: int) -> str:
        return seen.owner("profiles", name, depth) or (
            f"{chain[min(depth, len(chain) - 1)].namespace}:{name}"
        )

    # profiles.yaml has no skill or steering keys, so those two never fire.
    return inherit.requalify(
        written,
        top=addon.namespace,
        depth=0,
        author=addon,
        parent_name=chain[1].name,
        skill_owner=lambda name, depth: name,
        profile_owner=profile_owner,
        steering_owner=lambda name, depth: name,
    )


def _names_a_profile_of(node: object, qualifiers: set[str]) -> bool:
    """Whether `node` holds a `profile:` written as `<qualifier>:<name>` for one
    of `qualifiers`: the only references `requalify` re-points in a
    `profiles.yaml`."""
    if isinstance(node, list):
        return any(_names_a_profile_of(item, qualifiers) for item in node)
    if not isinstance(node, Mapping):
        return False
    return any(
        (key == "profile" and isinstance(value, str) and value.partition(":")[0] in qualifiers)
        or _names_a_profile_of(value, qualifiers)
        for key, value in node.items()
    )


@dataclass(frozen=True)
class HarnessProfileTable:
    """One `harnesses.yaml`: the configured provider instances an agent task's
    `harness:` selects from (`harness-profile-has-safe-instance-
    configuration`), and the agent profiles a task's `profile:` selects from."""

    profiles: dict[str, HarnessProfile]
    #: `profiles:`, the named model tiers (Kraft-ps1ao). Absent is `{}`.
    agent_profiles: dict[str, AgentProfile] = field(default_factory=dict)

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        harnesses: Mapping[str, Harness],
        addons: Sequence[InstalledAddon] = (),
    ) -> HarnessProfileTable:
        """Read `harnesses:` from `path` and validate each profile against the
        provider it names.

        `harnesses` is the code-owned capability declaration
        (`kraft.harness.load(...).valid`), so a profile's `defaults:` are
        checked against what the provider actually accepts rather than against
        a second copy of that list kept here
        (`provider-declares-harness-capabilities`).
        """
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise TemplateEnvironmentError(f"{path}: cannot read/parse: {exc}") from exc
        return cls.from_mapping(data, path, harnesses=harnesses, addons=addons)

    @classmethod
    def from_mapping(
        cls,
        data: object,
        path: str | Path,
        *,
        harnesses: Mapping[str, Harness],
        addons: Sequence[InstalledAddon] = (),
    ) -> HarnessProfileTable:
        """`from_yaml` for a file's already-parsed content -- a candidate the
        Harnesses screen would save as `path` -- so a save is checked by the
        very parse a load makes (Kraft-archr)."""
        path = Path(path)
        profiles: dict[str, HarnessProfile] = {}
        for id, body in _section(data, "harnesses", path).items():
            try:
                parsed = HarnessProfileInput.model_validate(body or {})
            except ValidationError as exc:
                raise TemplateEnvironmentError(
                    f"{path}: harnesses.{id}: {_first_error(exc)}"
                ) from exc
            harness = harnesses.get(parsed.provider)
            if harness is None:
                raise TemplateEnvironmentError(
                    f"{path}: harnesses.{id}: provider {parsed.provider!r} is not an "
                    f"installed harness; known: {sorted(harnesses)}"
                )
            try:
                profiles[id] = HarnessProfile.from_input(id, parsed, harness=harness)
            except TemplateEnvironmentError as exc:
                raise TemplateEnvironmentError(f"{path}: {exc}") from exc
        agent_profiles = _agent_profiles(_section(data, "profiles", path), path, harnesses)
        for addon in addons:
            if addon.left_out is None:
                agent_profiles |= _addon_profiles(addon, harnesses, addons)
        # A child's table is its parent's too: each profile it inherits, whole,
        # under the child's name (its fallback still names the ancestor). Every
        # add-on's own profiles are in first, so the nearest definition is
        # the one copied.
        # Imported here: `kraft.config` imports this module.
        from kraft.addons import inherit

        for addon in addons:
            for name, owner in inherit.inherited("profiles", addon, addons).items():
                agent_profiles[f"{addon.namespace}:{name}"] = replace(
                    agent_profiles[f"{owner}:{name}"], id=f"{addon.namespace}:{name}"
                )
        # A repo library left out of this table (another repository's, or a
        # connected id with none), or an add-on left out by a fault (a store
        # edited or gone), is known: a fallback into it is not a broken file,
        # and the launch that walks into it stops with its own reason
        # (`resolve_agent_task`). One disabled or not installed on purpose is
        # not: naming it is the file's mistake.
        known = {
            f"{a.namespace}:"
            for a in addons
            if a.left_out and (a.repo_id is not None or not a.quiet)
        }
        for p in agent_profiles.values():
            for n, entry in enumerate(p.fallback):
                if entry.profile is not None and entry.profile.startswith(tuple(known)):
                    continue
                if entry.profile is not None and entry.profile not in agent_profiles:
                    raise TemplateEnvironmentError(
                        f"{path}: profiles.{p.id}.fallback[{n}]: profile {entry.profile!r} "
                        f"is not defined; known are {sorted(agent_profiles)}"
                    )
        return cls(profiles=profiles, agent_profiles=agent_profiles)

    def pairing_detail(
        self, name: str, harness: HarnessProfile, providers: Mapping[str, Harness]
    ) -> PairingProblem | None:
        """`pairing_problem` with what it is about: the provider and the field
        (`model` or `effort`) that does not pair."""
        profile = self.agent_profiles.get(name)
        provider, at = harness.provider, f"(harness {harness.id!r})"
        if profile is None:
            return PairingProblem(
                name,
                provider,
                None,
                f"profile {name!r} is not defined in harnesses.yaml; "
                f"known are {sorted(self.agent_profiles)}",
            )
        entry = profile.providers.get(provider)
        if entry is None:
            return PairingProblem(
                name,
                provider,
                "model",
                f"profile {name!r} has no model for provider {provider!r} {at}",
            )
        for option, value in (("model", entry.model), ("effort", entry.effort)):
            if value is not None and not providers[provider].value_ok(option, value):
                return PairingProblem(
                    name,
                    provider,
                    option,
                    f"profile {name!r}: provider {provider!r} takes no {option} {value!r} {at}",
                )
        return None

    def pairing_problem(
        self, name: str, harness: HarnessProfile, providers: Mapping[str, Harness]
    ) -> str | None:
        """Why agent profile `name` cannot run a task on `harness`, or None: the
        one reason text the Settings view, doctor and the launch all give
        (`adapters.agent.resolve_profile`). No model is inferred for a
        provider the profile does not name."""
        found = self.pairing_detail(name, harness, providers)
        return found.message if found else None


@dataclass(frozen=True)
class PairingProblem:
    """Why a profile does not pair with a harness: `field` is `model` or
    `effort`, None when the profile is not defined."""

    profile: str | None
    provider: str
    field: str | None
    message: str
