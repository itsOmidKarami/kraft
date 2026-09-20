"""V1 template-schema environment: real repositories, the workspaces built
from them, path-scoped areas inside one, and the harness profiles an agent
task selects from.

See docs/templates-v1-design.md's "Harness profiles" and "Repositories,
workspaces, and areas" sections for the concrete YAML shapes these types
model. Standalone by design (no import from `kraft.template_models` /
`kraft.template_library`, which do not exist yet): this is Phase 1, "types
only" (docs/intent/templates-v1.md `repositories-workspaces-and-areas-are-
distinct`, `workspace-declares-root-and-members`). Workspace and area
*behaviour* -- assembling a checkout, root-pointer updates, changed-test-scope
selection -- lands in Phase 6; this module exists so Phase 2's materializer
has a typed target to build into and freeze.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from kraft.harness import Harness

#: One identifier rule for both sides of every reference: the harness-profile
#: id an `AgentTask.harness` names, the repository id a workspace member
#: mounts, the chain id a repository defaults to. `kraft.template_models`
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
    default_chain: Identifier | None = None
    forge: ForgeTarget | None = None
    worktree: Worktree = Field(default_factory=Worktree)
    verification: Verification = Field(default_factory=Verification)
    steering: list[Identifier] = Field(default_factory=list)
    areas: dict[Identifier, Area] = Field(default_factory=dict)


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
    members: tuple[Identifier, ...] = ()
    include_root: bool = True
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
