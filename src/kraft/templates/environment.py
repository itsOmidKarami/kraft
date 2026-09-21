"""V1 template-schema environment: real repositories, the workspaces built
from them, path-scoped areas inside one, and the harness profiles an agent
task selects from.

See docs/templates-v1-design.md's "Harness profiles" and "Repositories,
workspaces, and areas" sections for the concrete YAML shapes these types
model. Standalone by design (no import from `kraft.templates.models` /
`kraft.templates.library`, which do not exist yet): this is Phase 1, "types
only" (docs/intent/templates-v1.md `repositories-workspaces-and-areas-are-
distinct`, `workspace-declares-root-and-members`). Workspace and area
*behaviour* -- assembling a checkout, root-pointer updates, changed-test-scope
selection -- lands in Phase 6; this module exists so Phase 2's materializer
has a typed target to build into and freeze.
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
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from kraft.automated_review import AutomatedReview
from kraft.harness import Harness
from kraft.policy import TemplatePolicyOverride

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


def _safe_relative_file(rel: str) -> str:
    """A worktree file `git worktree add` cannot carry (`config.RepoEntry`'s
    `local_files` invariant): relative, literal, no traversal. Kept as a
    plain validator body, not a shared helper -- `RepoEntry.local_files` is
    the legacy V0 schema and this is its own V1 model; nothing here reuses
    `config.py` code, only the rule."""
    if rel.endswith("/"):
        raise ValueError(f"entry {rel!r} must name a file")
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError(f"entry {rel!r} must be a relative path inside the repo")
    if any(c in rel for c in "*?["):
        raise ValueError(f"entry {rel!r} must be a literal path, not a glob")
    return rel


class RootPointerPolicy(StrEnum):
    """How a workspace's root-repository submodule pointers are handled when
    its members change. `IGNORE` is the shipped default
    (`workspace-root-pointer-update-defaults-to-ignore`) -- a workspace work
    item lands only in the child repositories it touched."""

    IGNORE = "ignore"
    BUMP = "bump"


class ForgeTarget(BaseModel):
    """Where a repository's merge requests land. Only `Repository` carries
    this field; `Area` has no `forge` of its own to set, by omission, not by
    a runtime check (`repositories-workspaces-and-areas-are-distinct`)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    kind: StrictStr = Field(min_length=1)
    project: StrictStr = Field(min_length=1)


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


class WorktreeEnvironment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    set: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    pass_through: list[StrictStr] = Field(default_factory=list)


class Worktree(BaseModel):
    """How a repository's worktree is prepared before a task runs in it."""

    model_config = ConfigDict(strict=True, extra="forbid")

    setup: StrictStr | None = None
    local_files: list[StrictStr] = Field(default_factory=list)
    environment: WorktreeEnvironment = Field(default_factory=WorktreeEnvironment)

    @field_validator("local_files")
    @classmethod
    def _safe_local_files(cls, v: list[str]) -> list[str]:
        return [_safe_relative_file(rel) for rel in v]


class Area(BaseModel):
    """A path-scoped execution context inside one real repository. Never a
    forge target: it has no `forge` field to declare, and no `path` of its
    own -- only `paths` glob patterns inside its owning repository
    (`repositories-workspaces-and-areas-are-distinct`)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    paths: list[StrictStr] = Field(min_length=1)
    setup: StrictStr | None = None
    verification: Verification = Field(default_factory=Verification)


class Repository(BaseModel):
    """An independently-clonable Git repository -- the only thing that can
    own a forge merge request."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: Identifier
    path: StrictStr = Field(min_length=1)
    enabled: StrictBool = True
    #: A person connected or edited this repository, as opposed to Kraft
    #: detecting it as a workspace child (Ruling 165: bookkeeping, not
    #: policy). It keeps a detected child out of Settings' main list, out of
    #: `kraft repo list`, and out of the client's cwd-to-repository resolution.
    managed: StrictBool = True
    default_chain: Identifier | None = None
    #: The model an agent task runs with here, per harness profile id
    #: (Ruling 165) -- over the profile's `defaults:`, under the task's own.
    models: dict[Identifier, StrictStr] = Field(default_factory=dict)
    forge: ForgeTarget | None = None
    worktree: Worktree = Field(default_factory=Worktree)
    verification: Verification = Field(default_factory=Verification)
    steering: list[Identifier] = Field(default_factory=list)
    areas: dict[Identifier, Area] = Field(default_factory=dict)
    #: The repository policy layer: after the instance policy, before the
    #: work item's, and only ever tightening what it inherits
    #: (`repository-policy-cannot-relax-instance-safety`). Where the legacy
    #: entry's `deny_tools` and `sandbox` live in V1 (Ruling 105). Today's
    #: daemon still reads the legacy `repos:` list, whose `RepoEntry.policy`
    #: is this same type (`config.repository_override`).
    policy: TemplatePolicyOverride | None = None
    #: The automated reviewer `mr.automated_review` waits for, if any (Ruling
    #: 171). Mirrored on the legacy `config.RepoEntry`, which the daemon reads.
    automated_review: AutomatedReview | None = None


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
    #: `strict=False` here only: pydantic's strict mode never coerces a plain
    #: (decoded-JSON) list into a tuple, so a frozen target rehydrated as a
    #: dict -- the path `model_validate_json` skips but `json.loads()` then
    #: `model_validate(dict)` takes -- would otherwise fail on its own
    #: `model_dump()` output. `members` stays a tuple on the model either way.
    members: Annotated[tuple[Identifier, ...], Field(strict=False)] = ()
    #: Meaningless outside a workspace target, so it defaults off; only
    #: `kind="workspace"` may turn it on (`_kind_owns_its_fields` below).
    include_root: bool = False
    root_pointer_policy: Annotated[RootPointerPolicy, Field(strict=False)] = (
        RootPointerPolicy.IGNORE
    )

    @model_validator(mode="after")
    def _kind_owns_its_fields(self) -> WorkItemTarget:
        """Each `kind` carries exactly its own fields. The classmethods below
        build this correctly, but they are not in the path when Phase 2
        rehydrates a frozen target from stored JSON."""
        if self.kind == "repository":
            if self.repository is None:
                raise ValueError("a repository target must name a repository")
            if self.workspace is not None or self.members:
                raise ValueError("a repository target has no workspace or members")
            if self.include_root:
                raise ValueError("a repository target has no root to include")
        else:
            if self.workspace is None:
                raise ValueError("a workspace target must name a workspace")
            if self.repository is not None:
                raise ValueError("a workspace target has no repository")
        return self

    @classmethod
    def for_repository(cls, repository: Repository) -> WorkItemTarget:
        return cls(kind="repository", repository=repository.id)

    @classmethod
    def from_selection(
        cls,
        workspace: Workspace,
        *,
        members: Sequence[str],
        include_root: bool = True,
        root_pointer_policy: RootPointerPolicy | None = None,
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
            members=tuple(members),
            include_root=include_root,
            root_pointer_policy=root_pointer_policy or workspace.root_pointer_default,
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


# ── The two V1 files these types are read from ───────────────────────────────
#
# `from_yaml` may do boundary I/O; `from_input` stays pure. Each translates a
# read or parse failure into this module's own error type, so a caller catches
# one exception per configuration file rather than `OSError`/`YAMLError`/
# `ValidationError` from three layers down.


def _first_error(exc: ValidationError) -> str:
    """A pydantic failure as one line naming the field that failed. Its own
    three lines rather than an import: `templates.models.first_error` joins
    with `PATH_SEPARATOR`, which is that module's, and this module cannot
    import it (`models` imports *this* one -- the cycle the header names)."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else error["msg"]


def _read_mapping(path: Path, section: str) -> dict[str, object]:
    """One top-level mapping section of `path`, or `{}` when it is absent.

    An absent section is not an error -- a `repos.yaml` with repositories and
    no workspaces is the ordinary single-repository install -- but a section
    present and not a mapping is, because the keys are the identifiers
    everything else references.
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
class RepositoryTable:
    """One `repos.yaml`: the repositories on this instance and the workspaces
    assembled from them (`repositories-workspaces-and-areas-are-distinct`)."""

    repositories: dict[str, Repository]
    workspaces: dict[str, Workspace]

    @classmethod
    def from_yaml(cls, path: str | Path) -> RepositoryTable:
        """Read `repositories:` and `workspaces:` from `path`.

        **An existing install's `repos.yaml` is not in this shape and this
        loader will not read it.** Seeding only ever *creates* -- `seed_home`
        returns early when `templates/` exists (`cli/admin.py`), so a home
        written before V1 keeps its legacy list under `repos:` and comes back
        from here empty rather than wrong. Converting such a home is Task 11's
        `major-update-*` work, deliberately not this loader's: a reader that
        silently accepted both shapes is how the two start disagreeing.
        """
        path = Path(path)
        repositories: dict[str, Repository] = {}
        for id, body in _read_mapping(path, "repositories").items():
            try:
                repositories[id] = Repository.model_validate({"id": id, **(body or {})})
            except ValidationError as exc:
                raise TemplateEnvironmentError(
                    f"{path}: repositories.{id}: {_first_error(exc)}"
                ) from exc

        workspaces: dict[str, Workspace] = {}
        for id, body in _read_mapping(path, "workspaces").items():
            try:
                workspace = Workspace.model_validate({"id": id, **(body or {})})
            except ValidationError as exc:
                raise TemplateEnvironmentError(
                    f"{path}: workspaces.{id}: {_first_error(exc)}"
                ) from exc
            # Both ends of every reference, at load: a workspace mounting a
            # repository this file does not declare assembles an empty checkout
            # at run time, hours after the typo, and nothing before this point
            # would have said so.
            if workspace.root not in repositories:
                raise TemplateEnvironmentError(
                    f"{path}: workspaces.{id}: root {workspace.root!r} is not a declared repository"
                )
            for name, member in workspace.members.items():
                if member.repository not in repositories:
                    raise TemplateEnvironmentError(
                        f"{path}: workspaces.{id}.members.{name}: "
                        f"{member.repository!r} is not a declared repository"
                    )
            workspaces[id] = workspace
        return cls(repositories=repositories, workspaces=workspaces)


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
