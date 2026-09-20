"""V1 template schema: the typed tasks, steps, nodes and chains a template
library is written in, and the canonical execution paths a resolved chain
assigns to them.

Concrete YAML shapes live in docs/templates-v1-design.md ("Library
components", "Chain example", "Resolution and execution"); the behaviour these
types enforce is docs/intent/templates-v1.md. Loading files and expanding
`extends` is `kraft.templates.library`'s job -- this module is the vocabulary it
validates into, and knows nothing about the filesystem.

Three stages, deliberately distinct (`authored-resolved-and-materialized-
chains-are-distinct`):

* `Chain` and friends -- one authored chain, after `extends` expansion, with
  every local identifier validated against its own siblings;
* `ResolvedChain` -- the same chain with `tasks` shorthand normalized into a
  `main` step and a canonical path on every step and task;
* `MaterializedChain` -- a resolved chain bound to one work item's immutable
  target and effective policy.

Nothing executes any of this yet: Phase 1 is the schema, and the executor
still reads the legacy `kraft.templates` shapes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StrictStr,
    ValidationError,
    model_validator,
)

from kraft.policy import InstancePolicy, TemplatePolicyOverride
from kraft.templates.environment import Identifier, WorkItemTarget

#: Step identifiers Kraft generates itself, so an author cannot occupy one and
#: make a resolved path ambiguous (docs/templates-v1-design.md "Resolution and
#: execution"; `task-group-shorthand-resolves-to-one-step`).
RESERVED_SEGMENTS = frozenset(
    {"main", "on_failure", "fix_loop", "judge", "escalation", "on_base_changed", "on_conflict"}
)

#: The separator between canonical path segments. An authored identifier cannot
#: contain it (see `Identifier`), which is what makes per-container sibling
#: checks sufficient for global uniqueness.
PATH_SEPARATOR = "."

#: The step a `tasks` shorthand resolves to, and the canonical segment of a fix
#: loop's dedicated judge. Both are members of `RESERVED_SEGMENTS`.
MAIN_STEP = "main"
JUDGE_SEGMENT = "judge"
#: The canonical segment of a gate's own reviewing task
#: (`GateNode.auto_review`). Not in `RESERVED_SEGMENTS`: a gate declares no
#: execution shape at all, so there is no authored step id that could collide
#: with it.
AUTO_REVIEW_SEGMENT = "auto_review"

#: `resolved-chain-identifiers-are-unique` (no `.`, no whitespace, nothing else
#: that would make a resolved path ambiguous) is `Identifier`, imported from
#: `kraft.templates.environment` so that both sides of every reference share one
#: rule -- a task's `harness:` and the profile id it names. Not imported from
#: `kraft.templates`, whose equivalent is private to a module V1 replaces.

_DURATION = re.compile(r"^(\d+)(s|m|h|d)$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _duration(value: object) -> object:
    """`90m`, `30s`, `7d` -- the forms docs/templates-v1-design.md writes."""
    if not isinstance(value, str):
        return value
    match = _DURATION.match(value)
    if match is None:
        raise ValueError(f"{value!r} must be a whole number of s/m/h/d, such as '90m'")
    amount = int(match[1])
    if amount == 0:
        raise ValueError(f"{value!r} must be a positive duration, not zero")
    return timedelta(seconds=amount * _DURATION_UNITS[match[2]])


def _duration_text(value: timedelta) -> str:
    """Back to the authored form, largest exact unit first.

    Symmetry with `_duration`, and the whole reason it is here: pydantic's own
    JSON form for a `timedelta` is ISO-8601 (`PT1H30M`), which `_duration` then
    refuses. A stored chain has to re-read, so the grammar has to round-trip.

    Not total symmetry: a zero duration serializes to `"0d"`, which `_duration`
    rejects. That asymmetry is the rule working, not a gap -- `Duration` refuses
    zero on purpose, because a zero polling interval is a hot loop and a zero
    timeout stops an item before any pipeline could settle. No validated model
    can hold one, so nothing round-trippable reaches this branch.
    """
    total = int(value.total_seconds())
    unit = next(u for u in ("d", "h", "m", "s") if total % _DURATION_UNITS[u] == 0)
    return f"{total // _DURATION_UNITS[unit]}{unit}"


#: A duration as templates write it, as the stdlib type the runtime wants.
#: `when_used="json"` so only the stored form changes: a reader holding the
#: model still sees a `timedelta`.
Duration = Annotated[
    timedelta,
    BeforeValidator(_duration),
    PlainSerializer(_duration_text, return_type=str, when_used="json"),
]


def first_error(exc: ValidationError) -> str:
    """A pydantic failure as one line naming the field that failed.

    Here rather than in `kraft.templates.library` because `PATH_SEPARATOR` is
    this module's, and both this module's `MaterializedChain.from_json` and that
    module's steering-profile load need the same shape.
    """
    error = exc.errors()[0]
    location = PATH_SEPARATOR.join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else error["msg"]


def _not_reserved(id: str) -> str:
    if id in RESERVED_SEGMENTS:
        raise ValueError(f"{id!r} is a reserved path segment: {sorted(RESERVED_SEGMENTS)}")
    return id


def _unique(kind: str, ids: list[str]) -> None:
    """Sibling uniqueness, per container. Global uniqueness follows from this
    plus `Identifier`, so nothing here consults a registry of every path in the
    chain (docs/templates-v1-design.md "Resolution and execution")."""
    seen: set[str] = set()
    for id in ids:
        if id in seen:
            raise ValueError(f"duplicate {kind} id {id!r} in one container")
        seen.add(id)


class TaskKind(StrEnum):
    BUILTIN = "builtin"
    AGENT = "agent"
    SUBPROCESS = "subprocess"
    FORGE = "forge"


class NodeKind(StrEnum):
    EXEC = "exec"
    GATE = "gate"


class BuiltinAction(StrEnum):
    """What a `kind: builtin` task's `ref` may name
    (`builtin-task-references-code-owned-actions`: Kraft owns this vocabulary,
    so an unsupported reference is rejected by the type). Grows one member per
    action a later phase actually wires up."""

    VERIFY_CHANGED_TEST_SCOPES = "kraft.verify_changed_test_scopes"


class ForgeAction(StrEnum):
    """The merge-request lifecycle points a `kind: forge` task may target
    (docs/templates-v1-design.md library.yaml; `external-wait-covers-merge-
    request-lifecycle`)."""

    MR_OPEN_DRAFT = "mr.open_draft"
    MR_SYNC = "mr.sync"
    MR_CI = "mr.ci"
    MR_AUTOMATED_REVIEW = "mr.automated_review"
    MR_MARK_READY = "mr.mark_ready"
    MR_EXTERNAL_APPROVAL = "mr.external_approval"
    MR_MERGE = "mr.merge"
    MR_POST_MERGE_CI = "mr.post_merge_ci"


class TaskScope(StrEnum):
    """`task-may-explicitly-fan-out-by-repository`: fan-out is opt-in, so
    `ONCE` -- the work item's ordinary execution context -- is the default."""

    ONCE = "once"
    EACH_REPOSITORY = "each_repository"


class ExecutionMode(StrEnum):
    """`changed-test-scope-verification-is-sequential-by-default`."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


#: Every V1 model: no unknown keys (a typo is an error, not a silently ignored
#: field) and no coercion, except where a field explicitly opts out to accept a
#: YAML string for a closed vocabulary.
_CONFIG = ConfigDict(strict=True, extra="forbid")
_LOOSE = Field(strict=False)


class SteeringProfile(BaseModel):
    """One `library.yaml` `steering:` entry: composable guidance a task
    selects by name (`agent-task-may-select-one-skill` keeps the *skill* single;
    steering is where additional guidance goes)."""

    model_config = _CONFIG

    instructions: StrictStr = Field(min_length=1)


class PollingPolicy(BaseModel):
    model_config = _CONFIG

    initial_interval: Duration | None = None
    max_interval: Duration | None = None

    @model_validator(mode="after")
    def _initial_within_max(self) -> Self:
        if (
            self.initial_interval is not None
            and self.max_interval is not None
            and self.initial_interval > self.max_interval
        ):
            raise ValueError(
                f"initial_interval {self.initial_interval} must not exceed "
                f"max_interval {self.max_interval}"
            )
        return self


class WaitPolicy(BaseModel):
    """`external-wait-has-configurable-timeout-and-polling`."""

    model_config = _CONFIG

    timeout: Duration | None = None
    polling: PollingPolicy = Field(default_factory=PollingPolicy)


class TaskBase(BaseModel):
    """What every task kind carries. `kind` itself is declared by each concrete
    model as a `Literal`, which is what makes the union discriminated
    (`task-kinds-are-discriminated`)."""

    model_config = _CONFIG

    id: Identifier
    scope: Annotated[TaskScope, _LOOSE] = TaskScope.ONCE
    steering: list[Identifier] = Field(default_factory=list)


class BuiltinTask(TaskBase):
    kind: Literal[TaskKind.BUILTIN]
    ref: Annotated[BuiltinAction, _LOOSE]
    execution: Annotated[ExecutionMode, _LOOSE] = ExecutionMode.SEQUENTIAL


class AgentTask(TaskBase):
    kind: Literal[TaskKind.AGENT]
    harness: Identifier
    prompt: StrictStr = Field(min_length=1)
    #: One skill, never a list (`agent-task-may-select-one-skill`). Not an
    #: `Identifier`: a skill name is a plugin-qualified `plugin:skill`.
    skill: StrictStr | None = None
    #: The Kraft-owned output contract, supplied before skill and steering
    #: (`agent-task-contract-precedes-skill-and-steering`).
    produces: Identifier | None = None
    model: StrictStr | None = None
    effort: StrictStr | None = None


class SubprocessTask(TaskBase):
    kind: Literal[TaskKind.SUBPROCESS]
    command: StrictStr = Field(min_length=1)


class ForgeTask(TaskBase):
    kind: Literal[TaskKind.FORGE]
    target: Annotated[ForgeAction, _LOOSE]
    wait: WaitPolicy | None = None


AnyTask = Annotated[
    BuiltinTask | AgentTask | SubprocessTask | ForgeTask, Field(discriminator="kind")
]


class Step(BaseModel):
    """One ordered group whose tasks run concurrently
    (`exec-node-orders-concurrent-task-groups`)."""

    model_config = _CONFIG

    id: Identifier
    tasks: list[AnyTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _local_identifiers(self) -> Self:
        _not_reserved(self.id)
        _unique("task", [t.id for t in self.tasks])
        return self


class ExecutionShape(BaseModel):
    """A container that runs tasks: an execution node, a recovery plan, or a
    fix loop. Exactly one of `tasks` or `steps`, never both and never neither
    (`exec-node-requires-one-execution-shape`, `recovery-plan-supports-task-
    groups-or-steps`, `fix-loop-supports-one-ordered-repair-shape`). Both
    default to `None` rather than `[]`, so "present but empty" stays
    distinguishable from "not this shape"."""

    model_config = _CONFIG

    tasks: list[AnyTask] | None = None
    steps: list[Step] | None = None

    @model_validator(mode="after")
    def _one_execution_shape(self) -> Self:
        present = [name for name in ("tasks", "steps") if getattr(self, name) is not None]
        if len(present) != 1:
            raise ValueError(
                "define exactly one of 'tasks' or 'steps'"
                + (f", not {present}" if present else ", not neither")
            )
        if present == ["tasks"]:
            if not self.tasks:
                raise ValueError("'tasks' must not be empty")
            _unique("task", [t.id for t in self.tasks])
        else:
            if not self.steps:
                raise ValueError("'steps' must not be empty")
            _unique("step", [s.id for s in self.steps])
        return self

    def resolve_steps(self, prefix: str) -> tuple[ResolvedStep, ...]:
        """This container's ordered steps and their tasks, each at its canonical
        path under `prefix`, with a `tasks` group normalized to one step named
        `main` (`task-group-shorthand-resolves-to-one-step`). `Step` itself
        refuses the reserved id, so that `main` step is built past validation --
        deliberately: the reservation exists exactly so that this construction
        is the only thing that can occupy it."""
        steps = (
            self.steps
            if self.steps is not None
            else [Step.model_construct(id=MAIN_STEP, tasks=list(self.tasks or ()))]
        )
        return tuple(
            ResolvedStep(
                path=(path := f"{prefix}{PATH_SEPARATOR}{step.id}"),
                id=step.id,
                tasks=tuple(
                    ResolvedTask(path=f"{path}{PATH_SEPARATOR}{task.id}", task=task)
                    for task in step.tasks
                ),
            )
            for step in steps
        )


class RecoveryPlan(ExecutionShape):
    """An `on_failure` handler. No gates, no nested handler, no fix loop --
    by omission, not by a runtime check (`recovery-plan-supports-task-groups-
    or-steps`)."""


class FixLoop(ExecutionShape):
    model_config = _CONFIG

    #: Optional (`fix-loop-judge-is-optional`), and its runtime configuration
    #: is an ordinary task's (`judge-runtime-is-independent-from-fixer-
    #: runtime`). A named slot, not a step: its canonical segment is the slot's
    #: own name, `judge`, so the authored id never becomes a path segment and
    #: cannot collide with anything. No reserved check belongs here.
    judge: AnyTask | None = None
    max_attempts: Annotated[int, Field(gt=0)] | None = None


class ExecNode(ExecutionShape):
    """An ordered node that runs work. Gate-only fields are absent here, so a
    `gate_after` or a `chain_finalized` on an execution node is a schema error
    rather than a silently ignored key (`gate-is-an-ordered-node`)."""

    model_config = _CONFIG

    id: Identifier
    kind: Literal[NodeKind.EXEC]
    on_failure: RecoveryPlan | None = None
    fix_loop: FixLoop | None = None
    #: A bounded escalation task (`stuck-escalation-is-an-exec-node-control`).
    #: `escalation` is the container segment and the task's own id follows it,
    #: so -- unlike a fix loop's judge -- this id *is* a path segment.
    escalation: AnyTask | None = None

    @model_validator(mode="after")
    def _escalation_identifier(self) -> Self:
        # The one dedicated task whose authored id becomes a segment of its own
        # (`node.escalation.<id>`), so it obeys the reserved-segment rule.
        if self.escalation is not None:
            _not_reserved(self.escalation.id)
        return self


class GateNode(BaseModel):
    """A human decision point (`gate-is-an-ordered-node`). It owns every
    gate-specific field and declares no execution shape at all
    (`gate-owns-gate-behaviour`, `gate-control-does-not-generate-review-work`:
    the artifact it shows is produced by an earlier execution node)."""

    model_config = _CONFIG

    id: Identifier
    kind: Literal[NodeKind.GATE]
    message: StrictStr | None = None
    artifact: Identifier | None = None
    reject_to: Identifier | None = None
    timeout: Duration | None = None
    #: The task that reviews this gate before a human sees it
    #: (`gate-auto-review-is-explicit-and-bounded`). One field, not a boolean
    #: plus a name: a bare `auto_escalate: true` could only mean "Kraft's own
    #: default reviewer", which is exactly the name indirection V1 deletes.
    #: None means human-only, and no per-item override can arm it -- the
    #: override permits or suppresses a declared task, it cannot name one
    #: (`work_items.node_overrides`, `store.effective_nodes`).
    #:
    #: Not a delay or an attempt count: both bounds are policy-owned
    #: (`policy.auto_escalate_delay_s`, `policy.auto_review_attempts`) and
    #: folded through `store.effective_auto_escalate_delay_s`. A copy here
    #: would be a second source of truth for one number.
    auto_review: AnyTask | None = None
    #: `chain-finalized-remains-a-dedicated-marker`.
    chain_finalized: StrictBool = False


AnyNode = Annotated[ExecNode | GateNode, Field(discriminator="kind")]


class Chain(BaseModel):
    """One authored chain file, after `extends` expansion. `id` is optional
    because `POST /templates/resolve` accepts an unsaved candidate; a chain
    loaded from disk always has one."""

    model_config = _CONFIG

    id: Identifier | None = None
    nodes: list[AnyNode] = Field(min_length=1)
    policy: TemplatePolicyOverride | None = None

    @model_validator(mode="after")
    def _nodes_are_addressable(self) -> Self:
        ids = [n.id for n in self.nodes]
        _unique("node", ids)
        index = {n.id: i for i, n in enumerate(self.nodes)}
        for position, node in enumerate(self.nodes):
            target = node.reject_to if isinstance(node, GateNode) else None
            if target is None:
                continue
            where = self.id or "this chain"
            target_position = index.get(target)
            if (
                target_position is None
                or not isinstance(self.nodes[target_position], ExecNode)
                or target_position >= position
            ):
                raise ValueError(
                    f"node {node.id!r}: reject_to {target!r} must name an earlier "
                    f"execution node in {where}"
                )
        return self


@dataclass(frozen=True)
class ResolvedTask:
    """One task occurrence, at its complete canonical execution path
    (`component-identifiers-are-qualified-by-node-instance`)."""

    path: str
    task: BuiltinTask | AgentTask | SubprocessTask | ForgeTask


@dataclass(frozen=True)
class ResolvedStep:
    path: str
    id: str
    tasks: tuple[ResolvedTask, ...]


@dataclass(frozen=True)
class ResolvedNode:
    """One node occurrence. Every execution shape here is steps, including the
    `main` step a `tasks` shorthand became, so the runtime sees one shape."""

    id: str
    node: ExecNode | GateNode
    steps: tuple[ResolvedStep, ...] = ()
    on_failure: tuple[ResolvedStep, ...] = ()
    fix_loop: tuple[ResolvedStep, ...] = ()
    judge: ResolvedTask | None = None
    escalation: ResolvedTask | None = None
    #: A gate's own reviewing task (`GateNode.auto_review`), at `<gate>.auto_
    #: review`. A named slot like a fix loop's judge, so the authored id never
    #: becomes a path segment; a gate has no steps, so nothing else can occupy
    #: that path.
    auto_review: ResolvedTask | None = None

    def tasks(self) -> Iterator[ResolvedTask]:
        for group in (self.steps, self.on_failure, self.fix_loop):
            for step in group:
                yield from step.tasks
        for dedicated in (self.judge, self.escalation, self.auto_review):
            if dedicated is not None:
                yield dedicated

    def produces(self) -> frozenset[str | None]:
        """What this node's *own* steps declare they produce -- `None` as a
        member for every task declaring nothing, and for a task kind that has
        no `produces` field at all.

        The node's own steps only: a recovery pass or a fix loop repairs the
        node's output, it does not decide what the node is *for*. A set rather
        than a value, because both readers need to tell "all of them produce
        this one kind" from "some of them do" -- `trim_for_attachments` drops
        the node only in the first case, and `TemplateLibrary.resolve_chain`
        refuses the second outright so the distinction cannot arise at runtime.
        """
        return frozenset(
            getattr(t.task, "produces", None) for step in self.steps for t in step.tasks
        )


def _redundant_given(node: ResolvedNode, kinds: frozenset[str]) -> bool:
    """Whether an attachment of one of `kinds` makes `node` redundant --
    the whole of `trim_for_attachments`' rule, in one predicate so that both
    halves (the deciding gate, the producing node) are read side by side."""
    if isinstance(node.node, GateNode):
        return not node.node.chain_finalized and node.node.artifact in kinds
    produces = node.produces()
    return len(produces) == 1 and next(iter(produces)) in kinds


@dataclass(frozen=True)
class ResolvedChain:
    """A chain with `extends` expanded and a canonical path on every step and
    task -- reusable across work items, so it carries no work-item state."""

    chain: Chain
    nodes: tuple[ResolvedNode, ...]

    @property
    def id(self) -> str | None:
        return self.chain.id

    @property
    def task_paths(self) -> tuple[str, ...]:
        return tuple(t.path for node in self.nodes for t in node.tasks())

    @classmethod
    def from_chain(cls, chain: Chain) -> ResolvedChain:
        nodes = []
        for node in chain.nodes:
            if isinstance(node, GateNode):
                nodes.append(
                    ResolvedNode(
                        id=node.id,
                        node=node,
                        auto_review=(
                            ResolvedTask(
                                path=f"{node.id}{PATH_SEPARATOR}{AUTO_REVIEW_SEGMENT}",
                                task=node.auto_review,
                            )
                            if node.auto_review is not None
                            else None
                        ),
                    )
                )
                continue
            loop = node.fix_loop
            nodes.append(
                ResolvedNode(
                    id=node.id,
                    node=node,
                    steps=node.resolve_steps(node.id),
                    on_failure=(
                        node.on_failure.resolve_steps(f"{node.id}{PATH_SEPARATOR}on_failure")
                        if node.on_failure is not None
                        else ()
                    ),
                    fix_loop=(
                        loop.resolve_steps(f"{node.id}{PATH_SEPARATOR}fix_loop")
                        if loop is not None
                        else ()
                    ),
                    judge=(
                        ResolvedTask(
                            path=f"{node.id}{PATH_SEPARATOR}fix_loop{PATH_SEPARATOR}{JUDGE_SEGMENT}",
                            task=loop.judge,
                        )
                        if loop is not None and loop.judge is not None
                        else None
                    ),
                    escalation=(
                        ResolvedTask(
                            path=f"{node.id}{PATH_SEPARATOR}escalation"
                            f"{PATH_SEPARATOR}{node.escalation.id}",
                            task=node.escalation,
                        )
                        if node.escalation is not None
                        else None
                    ),
                )
            )
        return cls(chain=chain, nodes=tuple(nodes))

    def materialize(
        self,
        target: WorkItemTarget,
        effective_policy: InstancePolicy,
        attachment_kinds: frozenset[str] = frozenset(),
    ) -> MaterializedChain:
        """Bind this chain to one work item: its immutable target and the
        policy its tasks run under, with the chain's own override layered on
        (`policy-is-layered-by-execution-scope`,
        `materialized-chain-is-immutable-work-item-input`).

        `attachment_kinds` are the artifact kinds this item arrives with
        already written (an intake `--spec`/`--plan`), and they trim the chain
        here rather than at a route handler -- see `trim_for_attachments`.
        """
        policy = effective_policy
        if self.chain.policy is not None:
            policy = policy.apply_template_override(self.chain.policy)
        return MaterializedChain(
            chain=self.trim_for_attachments(attachment_kinds), target=target, policy=policy
        )

    def trim_for_attachments(self, kinds: frozenset[str]) -> ResolvedChain:
        """This chain without the nodes an attachment of each kind in `kinds`
        makes redundant (`attachment-behaviour-is-explicit-gate-configuration`).

        Both ends are declared, so nothing is inferred from position and no
        kind-to-gate-name table exists anywhere:

        * the gate whose own `artifact` names the kind -- it has nothing left
          to decide, because the document arrived decided; and
        * any execution node **all** of whose own tasks declare
          `produces: <kind>` -- legacy got this for free, because the producing
          node and its gate were one node; in V1 they are two, so without this
          an attached spec is handed to a spec author to write again.

        Two edges the plain rule does not cover (Ruling 35):

        * A node mixing tasks with and without `produces` would survive the
          node-level test and re-author the attachment. Refused at load
          instead, by `TemplateLibrary.resolve_chain`, so trimming here is
          unambiguous: a node either wholly produces a kind or does not.
        * A gate marked `chain_finalized` is **never** dropped, whatever its
          `artifact` says. It is the chain's sole final-review marker
          (`chain-finalized-remains-a-dedicated-marker`), and the rule is a
          string match -- the day an attachment kind is called `review_brief`
          the chain would otherwise lose its only one.

        A surviving gate whose `reject_to` named a dropped node keeps the gate
        but loses the target: `gates.reject_target` then falls back the same way
        it does for a gate that declared none. Left dangling instead, the stored
        snapshot would no longer re-validate as a `Chain` when it is read back.
        """
        if not kinds:
            return self
        dropped = {n.id for n in self.nodes if _redundant_given(n, kinds)}
        if not dropped:
            return self
        if len(dropped) == len(self.nodes):
            raise ValueError(
                f"attachments {sorted(kinds)} would leave chain {self.id!r} with no nodes"
            )
        kept = [
            node.model_copy(update={"reject_to": None})
            if isinstance(node, GateNode) and node.reject_to in dropped
            else node
            for node in self.chain.nodes
            if node.id not in dropped
        ]
        return ResolvedChain.from_chain(self.chain.model_copy(update={"nodes": kept}))


class _StoredMaterialization(BaseModel):
    """`MaterializedChain`'s stored form, so the mapping between the snapshot
    and its column lives on the model rather than in whatever writes the row.

    Not `_CONFIG`: this validates Kraft's own output, where a `policy` tuple
    arrives from JSON as a list and has to coerce back. The strict, extra-
    forbidding models are for *authored* input.
    """

    model_config = ConfigDict(extra="forbid")

    chain: Chain
    target: WorkItemTarget
    policy: InstancePolicy


@dataclass(frozen=True)
class MaterializedChain:
    """One work item's snapshot: a resolved chain plus the decisions that do
    not change while it runs."""

    chain: ResolvedChain
    target: WorkItemTarget
    policy: InstancePolicy

    # Fork lineage is deliberately NOT a field here. `work_items.run_fork_parent`
    # is the one place a fork's parent is recorded: a field as well would be a
    # second source of truth that nothing keeps equal to the column, and the
    # column is the one a query can reach.

    @property
    def task_paths(self) -> tuple[str, ...]:
        return self.chain.task_paths

    def to_json(self) -> str:
        """The immutable work-item input, as one JSON document: the authored
        chain, the effective policy, and the typed target.

        The *authored* chain is what is stored, never the resolved paths -- they
        are derived from it deterministically, so storing both would let the two
        disagree.

        What is NOT frozen here, and a consumer has to know it: steering profile
        bodies and harness profile configuration. Both are referenced by name --
        a task carries `steering: [project-standards]` and `harness: codex_default`,
        and resolution only checks that the names exist. So editing a steering
        profile's `instructions` in `library.yaml`, or a harness profile's
        defaults, changes what an already-materialized item's later tasks do.
        That is intended: `materialized-chain-is-immutable-work-item-input` names
        effective policy values and intake-specific decisions, and installation-
        wide guidance prose is neither.
        """
        return _StoredMaterialization(
            chain=self.chain.chain,
            target=self.target,
            policy=self.policy,
        ).model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> MaterializedChain:
        from kraft.templates.library import TemplateLibraryError

        try:
            stored = _StoredMaterialization.model_validate_json(raw)
        except ValidationError as exc:
            raise TemplateLibraryError(f"not a materialized chain: {first_error(exc)}") from exc
        return cls(
            chain=ResolvedChain.from_chain(stored.chain),
            target=stored.target,
            policy=stored.policy,
        )
