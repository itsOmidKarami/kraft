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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

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

#: One identifier rule for both sides of every reference: the harness-profile
#: id an `AgentTask.harness` names, the repository id a workspace member
#: mounts, the chain id a repository defaults to. `kraft.templates.models`
#: imports `Identifier` from here rather than defining its own, because it
#: already imports this module and the reverse would be a cycle -- so nothing
#: definable here is unreferenceable there.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]*$")

Identifier = Annotated[StrictStr, Field(pattern=_IDENTIFIER.pattern)]


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
    def for_repository(cls, repository: str, *, base_branch: str | None = None) -> WorkItemTarget:
        """A single-repository target, by the repository's id."""
        return cls(kind="repository", repository=repository, base_branch=base_branch)

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


def _read_mapping(path: Path, section: str) -> dict[str, object]:
    """One top-level mapping section of `path`, or `{}` when it is absent.

    An absent section is not an error, but a section present and not a mapping
    is, because the keys are the identifiers everything else references.
    """
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise TemplateEnvironmentError(f"{path}: cannot read/parse: {exc}") from exc
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
            f"{type(raw).__name__} -- V1 keys each entry by the id the rest of "
            f"the configuration references it as"
        )
    return raw


@dataclass(frozen=True)
class HarnessProfileTable:
    """One `harnesses.yaml`: the configured provider instances an agent task's
    `harness:` selects from (`harness-profile-has-safe-instance-
    configuration`)."""

    profiles: dict[str, HarnessProfile]

    @classmethod
    def from_yaml(
        cls, path: str | Path, *, harnesses: Mapping[str, Harness]
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
        profiles: dict[str, HarnessProfile] = {}
        for id, body in _read_mapping(path, "harnesses").items():
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
        return cls(profiles=profiles)
