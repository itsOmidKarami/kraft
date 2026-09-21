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
from collections.abc import Container, Iterator
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

from kraft.policy import InstancePolicy, PolicyError, TaskPolicyOverride, TemplatePolicyOverride
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


class AgentInput(StrEnum):
    """What Kraft hands an agent task beyond its prompt, when the task asks for
    it by name. Declared on the task rather than keyed on a task's name or
    role, which is the indirection V1 deletes (Ruling 47).

    * `review_package` -- the change under review written out to a file, its
      path in `$KRAFT_REVIEW_PACKAGE`: the whole branch since `base_ref`, then
      from a task's second session on only what changed since its previous
      one (`review-package-is-delivered-to-a-task-that-declares-it`)."""

    REVIEW_PACKAGE = "review_package"


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
    #: Task-level recovery: the nearest handler for this task's own failure
    #: (`nearest-recovery-handler-wins`). Only a task in one of an execution
    #: node's own steps may declare one; every other position refuses it
    #: (`_refuse_nested_handlers`), since a handler inside a handler, a fix
    #: loop or a dedicated slot has no failure of its own to recover.
    on_failure: RecoveryPlan | None = None
    #: The task scope's own policy, applied last
    #: (`policy-is-layered-by-execution-scope`). Its recovery plan inherits it.
    policy: TaskPolicyOverride | None = None
    #: `task-step-and-node-are-skippable-by-default`: an operator may skip this
    #: component unless it says `false` here.
    skippable: StrictBool = True


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
    #: Inputs Kraft delivers to this task (`AgentInput`), e.g.
    #: `inputs: [review_package]`.
    inputs: list[Annotated[AgentInput, _LOOSE]] = Field(default_factory=list)


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
    #: Step-level recovery: the handler for a failed task in this step that
    #: declares none of its own (`nearest-recovery-handler-wins`).
    on_failure: RecoveryPlan | None = None
    #: Applied between the node's policy and each task's; its recovery plan
    #: inherits it.
    policy: TaskPolicyOverride | None = None
    #: `task-step-and-node-are-skippable-by-default`: an operator may skip this
    #: component unless it says `false` here.
    skippable: StrictBool = True

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

    def resolve_steps(self, prefix: str, scopes: Scopes = ()) -> tuple[ResolvedStep, ...]:
        """This container's ordered steps and their tasks, each at its canonical
        path under `prefix`, with a `tasks` group normalized to one step named
        `main` (`task-group-shorthand-resolves-to-one-step`). `Step` itself
        refuses the reserved id, so that `main` step is built past validation --
        deliberately: the reservation exists exactly so that this construction
        is the only thing that can occupy it.

        `scopes` are the policy overrides enclosing this container, broadest
        first; each step and task appends its own, and a recovery plan inherits
        the scopes of the step or task that declares it."""
        steps = (
            self.steps
            if self.steps is not None
            else [
                Step.model_construct(
                    id=MAIN_STEP, tasks=list(self.tasks or ()), on_failure=None, policy=None
                )
            ]
        )
        resolved = []
        for step in steps:
            path = f"{prefix}{PATH_SEPARATOR}{step.id}"
            step_scopes = (*scopes, *_own(step.policy))
            tasks = []
            for task in step.tasks:
                task_path = f"{path}{PATH_SEPARATOR}{task.id}"
                task_scopes = (*step_scopes, *_own(task.policy))
                tasks.append(
                    ResolvedTask(
                        path=task_path,
                        task=task,
                        on_failure=_handler_steps(task.on_failure, task_path, task_scopes),
                        scopes=task_scopes,
                    )
                )
            resolved.append(
                ResolvedStep(
                    path=path,
                    id=step.id,
                    tasks=tuple(tasks),
                    on_failure=_handler_steps(step.on_failure, path, step_scopes),
                    scopes=step_scopes,
                )
            )
        return tuple(resolved)

    def own_tasks(self) -> Iterator[AnyTask]:
        """Every task this shape runs directly, whichever form it took."""
        yield from self.tasks or ()
        for step in self.steps or ():
            yield from step.tasks


#: The policy overrides enclosing one scope, broadest first: a node's own, then
#: its step's, then its task's. The chain's own and every broader layer are
#: already folded into `MaterializedChain.policy`.
Scopes = tuple[TaskPolicyOverride, ...]


def _own(policy: TaskPolicyOverride | None) -> Scopes:
    return (policy,) if policy is not None else ()


def _handler_steps(
    plan: RecoveryPlan | None, owner: str, scopes: Scopes = ()
) -> tuple[ResolvedStep, ...]:
    """`plan`'s steps at `<owner>.on_failure`, or none. The plan sits in its
    owner's scope, so it inherits `scopes`, the owner's."""
    return (
        plan.resolve_steps(f"{owner}{PATH_SEPARATOR}on_failure", scopes) if plan is not None else ()
    )


def _refuse_nested_handlers(where: str, shape: ExecutionShape | None) -> None:
    """A recovery plan, a fix loop and a conflict handler are themselves the
    response to a failure, so nothing inside one may declare `on_failure`
    (`recovery-plan-supports-task-groups-or-steps`: no nested recovery
    handlers). The one place a handler belongs is an execution node's own
    steps and their tasks."""
    if shape is None:
        return
    for step in shape.steps or ():
        if step.on_failure is not None:
            raise ValueError(f"{where}: step {step.id!r} cannot declare its own on_failure")
    for task in shape.own_tasks():
        if task.on_failure is not None:
            raise ValueError(f"{where}: task {task.id!r} cannot declare its own on_failure")


def _refuse_handler_on(where: str, task: TaskBase | None) -> None:
    if task is not None and task.on_failure is not None:
        raise ValueError(f"{where} {task.id!r} cannot declare its own on_failure")


class RecoveryPlan(ExecutionShape):
    """An `on_failure` handler. No gates and no fix loop by omission; no nested
    handler by `_refuse_nested_handlers` (`recovery-plan-supports-task-groups-
    or-steps`)."""

    @model_validator(mode="after")
    def _no_nested_handler(self) -> Self:
        _refuse_nested_handlers("a recovery plan", self)
        return self


class BaseChangePolicy(BaseModel):
    """`on_base_changed`: what an execution node does when its own work moves
    the worktree base (`base-change-restarts-a-declared-chain-span`).

    `restart_from` names the execution node the chain restarts at -- this one
    or an earlier one, never a later one or a gate (`base-change-restart-
    target-is-backward`, checked by `Chain`, which alone can see the order).

    `on_conflict` is the only way Kraft attempts to resolve a rebase conflict
    (`rebase-conflict-requires-explicit-handler`): a recovery-shaped handler,
    run when a task of this node fails on a conflict. Absent, a conflict is an
    ordinary task failure."""

    model_config = _CONFIG

    restart_from: Identifier
    on_conflict: RecoveryPlan | None = None


class FixLoop(ExecutionShape):
    model_config = _CONFIG

    #: Optional (`fix-loop-judge-is-optional`), and its runtime configuration
    #: is an ordinary task's (`judge-runtime-is-independent-from-fixer-
    #: runtime`). A named slot, not a step: its canonical segment is the slot's
    #: own name, `judge`, so the authored id never becomes a path segment and
    #: cannot collide with anything. No reserved check belongs here.
    judge: AnyTask | None = None
    max_attempts: Annotated[int, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _no_nested_handler(self) -> Self:
        _refuse_nested_handlers("a fix loop", self)
        _refuse_handler_on("a fix loop's judge", self.judge)
        return self


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
    on_base_changed: BaseChangePolicy | None = None
    #: The node scope's policy. Everything the node runs inherits it -- its
    #: steps, its recovery plan, its fix loop and judge, its escalation task,
    #: its conflict handler -- and only here may `max_attempts`/
    #: `timeout_minutes` appear, because they bound this node's fix loop.
    policy: TemplatePolicyOverride | None = None
    #: `task-step-and-node-are-skippable-by-default`: an operator may skip this
    #: component unless it says `false` here.
    skippable: StrictBool = True

    @model_validator(mode="after")
    def _escalation_identifier(self) -> Self:
        # The one dedicated task whose authored id becomes a segment of its own
        # (`node.escalation.<id>`), so it obeys the reserved-segment rule.
        if self.escalation is not None:
            _not_reserved(self.escalation.id)
        _refuse_handler_on("the escalation task", self.escalation)
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
    #:
    #: `AgentTask`, not `AnyTask`. The contract is "report a `verdict` in your
    #: result file" -- a subprocess or a forge wait has no way to report one, so
    #: a chain declaring either here declares something the runtime cannot
    #: honour. Same call as `AgentTask.produces`: closed in validation rather
    #: than discovered by running it. `gate_review.review` keeps a defensive
    #: kind check for a snapshot that reached the row without passing through
    #: here, and that check is now genuinely defence rather than the only door.
    auto_review: AgentTask | None = None
    #: `chain-finalized-remains-a-dedicated-marker`.
    chain_finalized: StrictBool = False
    #: The gate scope's policy, inherited by `auto_review`. A task's fields
    #: only: a gate has no fix loop for `max_attempts` to bound.
    policy: TaskPolicyOverride | None = None
    #: `task-step-and-node-are-skippable-by-default`: an operator may skip this
    #: component unless it says `false` here.
    skippable: StrictBool = True

    @model_validator(mode="after")
    def _no_handler(self) -> Self:
        _refuse_handler_on("a gate's auto_review task", self.auto_review)
        return self


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
        where = self.id or "this chain"

        def backward_exec(target: str, limit: int) -> bool:
            at = index.get(target)
            return at is not None and isinstance(self.nodes[at], ExecNode) and at <= limit

        for position, node in enumerate(self.nodes):
            if isinstance(node, GateNode):
                if node.reject_to is not None and not backward_exec(node.reject_to, position - 1):
                    raise ValueError(
                        f"node {node.id!r}: reject_to {node.reject_to!r} must name an earlier "
                        f"execution node in {where}"
                    )
            elif node.on_base_changed is not None:
                # This node itself is allowed: restarting the node whose work
                # moved the base is the narrowest span there is.
                target = node.on_base_changed.restart_from
                if not backward_exec(target, position):
                    raise ValueError(
                        f"node {node.id!r}: on_base_changed.restart_from {target!r} must name "
                        f"this or an earlier execution node in {where}"
                    )
        return self


def _dedicated(path: str, task: AnyTask | None, scopes: Scopes) -> ResolvedTask | None:
    """A named-slot task (judge, escalation, gate reviewer) at `path`, in its
    node's `scopes` plus its own override."""
    if task is None:
        return None
    return ResolvedTask(path=path, task=task, scopes=(*scopes, *_own(task.policy)))


def _scoped(path: str, policy: InstancePolicy, scopes: Scopes) -> InstancePolicy:
    """`policy.layered(scopes)`, with a refusal prefixed by the scope's path."""
    try:
        return policy.layered(scopes)
    except PolicyError as exc:
        raise PolicyError(f"{path}: {exc}", field=exc.field) from exc


@dataclass(frozen=True)
class ResolvedTask:
    """One task occurrence, at its complete canonical execution path
    (`component-identifiers-are-qualified-by-node-instance`)."""

    path: str
    task: BuiltinTask | AgentTask | SubprocessTask | ForgeTask
    #: The task's own recovery plan, at `<task path>.on_failure`.
    on_failure: tuple[ResolvedStep, ...] = ()
    #: Every policy override enclosing this task, its own last
    #: (`MaterializedChain.policy_for`).
    scopes: Scopes = ()


@dataclass(frozen=True)
class ResolvedStep:
    path: str
    id: str
    tasks: tuple[ResolvedTask, ...]
    #: The step's own recovery plan, at `<step path>.on_failure`.
    on_failure: tuple[ResolvedStep, ...] = ()
    scopes: Scopes = ()


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
    #: `on_base_changed.on_conflict`, at `<node>.on_base_changed.on_conflict`.
    on_conflict: tuple[ResolvedStep, ...] = ()
    #: The node's own policy override, if it declares one. Its fix loop's
    #: bounds resolve here (`walk.walk_node`).
    scopes: Scopes = ()

    def steps_in(self) -> Iterator[ResolvedStep]:
        """Every step this node runs, handlers' steps included."""
        for group in (self.steps, self.on_failure, self.fix_loop, self.on_conflict):
            for step in group:
                yield step
                for handler in (step.on_failure, *(t.on_failure for t in step.tasks)):
                    yield from handler

    def tasks(self) -> Iterator[ResolvedTask]:
        for step in self.steps_in():
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
    #: Each steering profile the chain's tasks select, name to instructions,
    #: resolved out of the library (`TemplateLibrary.resolve_chain`) and frozen
    #: into the work item's snapshot with the chain. Steering is chain content,
    #: so dispatch reads it from here and never from `library.yaml` live
    #: (`materialized-chain-is-immutable-work-item-input`). `None` means "never
    #: resolved": a chain built without a library, or a snapshot stored before
    #: steering was frozen -- a task selecting steering then stops for a human.
    steering: dict[str, str] | None = None

    @property
    def id(self) -> str | None:
        return self.chain.id

    @property
    def task_paths(self) -> tuple[str, ...]:
        return tuple(t.path for node in self.nodes for t in node.tasks())

    @classmethod
    def from_chain(cls, chain: Chain, steering: dict[str, str] | None = None) -> ResolvedChain:
        nodes = []
        for node in chain.nodes:
            # Every handler and control task sits in its node's scope (or its
            # gate's): `node_scopes` is what each of them inherits before its
            # own step's or task's override.
            node_scopes = _own(node.policy)

            if isinstance(node, GateNode):
                nodes.append(
                    ResolvedNode(
                        id=node.id,
                        node=node,
                        auto_review=_dedicated(
                            f"{node.id}{PATH_SEPARATOR}{AUTO_REVIEW_SEGMENT}",
                            node.auto_review,
                            node_scopes,
                        ),
                        scopes=node_scopes,
                    )
                )
                continue
            loop = node.fix_loop
            nodes.append(
                ResolvedNode(
                    id=node.id,
                    node=node,
                    steps=node.resolve_steps(node.id, node_scopes),
                    on_failure=_handler_steps(node.on_failure, node.id, node_scopes),
                    fix_loop=(
                        loop.resolve_steps(f"{node.id}{PATH_SEPARATOR}fix_loop", node_scopes)
                        if loop is not None
                        else ()
                    ),
                    judge=_dedicated(
                        f"{node.id}{PATH_SEPARATOR}fix_loop{PATH_SEPARATOR}{JUDGE_SEGMENT}",
                        loop.judge if loop is not None else None,
                        node_scopes,
                    ),
                    escalation=(
                        _dedicated(
                            f"{node.id}{PATH_SEPARATOR}escalation{PATH_SEPARATOR}{node.escalation.id}",
                            node.escalation,
                            node_scopes,
                        )
                        if node.escalation is not None
                        else None
                    ),
                    on_conflict=(
                        node.on_base_changed.on_conflict.resolve_steps(
                            f"{node.id}{PATH_SEPARATOR}on_base_changed{PATH_SEPARATOR}on_conflict",
                            node_scopes,
                        )
                        if node.on_base_changed is not None
                        and node.on_base_changed.on_conflict is not None
                        else ()
                    ),
                    scopes=node_scopes,
                )
            )
        return cls(chain=chain, nodes=tuple(nodes), steering=steering)

    def materialize(
        self,
        target: WorkItemTarget,
        effective_policy: InstancePolicy,
        attachment_kinds: frozenset[str] = frozenset(),
        skip_nodes: frozenset[str] = frozenset(),
    ) -> MaterializedChain:
        """Bind this chain to one work item: its immutable target and the
        policy its tasks run under, with the chain's own override layered on
        (`policy-is-layered-by-execution-scope`,
        `materialized-chain-is-immutable-work-item-input`).

        `skip_nodes` are node ids the person filing the item chose to leave out
        (UI v2 · 04 point 6). Applied *with* the attachment trim in one drop,
        not one after the other: two sequential trims can each leave a chain
        non-empty while their union empties it, and only the combined check
        catches that.

        `attachment_kinds` are the artifact kinds this item arrives with
        already written (an intake `--spec`/`--plan`), and they trim the chain
        here rather than at a route handler -- see `trim_for_attachments`.
        """
        policy = self.chain_policy(effective_policy)
        dropped = {n.id for n in self.nodes if _redundant_given(n, attachment_kinds)} | (
            skip_nodes & {n.id for n in self.nodes}
        )
        return MaterializedChain(chain=self.without_nodes(dropped), target=target, policy=policy)

    def chain_policy(self, effective_policy: InstancePolicy) -> InstancePolicy:
        """`effective_policy` with this chain's own override on top, after
        checking every node, step and task scope under it resolves too
        (`policy-is-layered-by-execution-scope`,
        `template-policy-cannot-relax-safety-ceilings`). Raises `PolicyError`
        naming the scope that a broader one refuses, so a chain that could
        not run is refused when the item is filed, never mid-run -- by
        `materialize` and by `TemplateLibrary.lint` alike."""
        policy = effective_policy
        if self.chain.policy is not None:
            policy = policy.apply_template_override(self.chain.policy)
        self.check_scopes(policy)
        return policy

    def check_scopes(self, policy: InstancePolicy) -> None:
        """Raise `PolicyError`, naming the scope, unless every node, step and
        task scope resolves on top of `policy` -- the chain's own policy,
        already layered -- and every agent task's harness and every fix loop's
        `max_attempts` is within it."""
        for node in self.nodes:
            _scoped(node.id, policy, node.scopes)
            loop = node.node.fix_loop if isinstance(node.node, ExecNode) else None
            ceiling = policy.maxima.max_attempts
            if loop is not None and loop.max_attempts is not None and ceiling is not None:
                if loop.max_attempts > ceiling:
                    raise PolicyError(
                        f"{node.id}: fix_loop.max_attempts {loop.max_attempts} cannot exceed "
                        f"the administrator maximum max_attempts {ceiling}",
                        field="max_attempts",
                    )
            for step in node.steps_in():
                _scoped(step.path, policy, step.scopes)
            for task in node.tasks():
                task_policy = _scoped(task.path, policy, task.scopes)
                allowed = task_policy.allowed_harnesses
                if isinstance(task.task, AgentTask) and allowed is not None:
                    if task.task.harness not in allowed:
                        raise PolicyError(
                            f"{task.path}: harness {task.task.harness!r} is not in its "
                            f"allowed_harnesses {sorted(allowed)!r}",
                            field="allowed_harnesses",
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
        return self.without_nodes({n.id for n in self.nodes if _redundant_given(n, kinds)})

    def without_nodes(self, dropped: Container[str] | set[str]) -> ResolvedChain:
        """This chain minus `dropped`, re-resolved.

        One function for both reasons a node leaves a chain at intake -- an
        attachment made it redundant, or the person filing the item skipped it
        -- because the two have the same two consequences and had better not
        disagree about either: a chain emptied by the drop is refused, and a
        surviving gate whose `reject_to` named a dropped node has that target
        nulled rather than left dangling (a dangling target stops the stored
        snapshot re-validating as a `Chain` on read-back).
        """
        dropped = {n.id for n in self.nodes if n.id in dropped}
        if not dropped:
            return self
        if len(dropped) == len(self.nodes):
            raise ValueError(
                f"dropping {sorted(dropped)} would leave chain {self.id!r} with no nodes"
            )
        kept = [
            node.model_copy(update={"reject_to": None})
            if isinstance(node, GateNode) and node.reject_to in dropped
            else node
            for node in self.chain.nodes
            if node.id not in dropped
        ]
        return ResolvedChain.from_chain(
            self.chain.model_copy(update={"nodes": kept}), steering=self.steering
        )


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
    #: `ResolvedChain.steering`. Absent from a snapshot stored before steering
    #: was frozen, which reads back as `None`.
    steering: dict[str, str] | None = None


@dataclass(frozen=True)
class MaterializedChain:
    """One work item's snapshot: a resolved chain plus the decisions that do
    not change while it runs."""

    chain: ResolvedChain
    target: WorkItemTarget
    policy: InstancePolicy

    # Fork lineage is deliberately NOT a field here. `RunFork.parent`
    # (`kraft.templates.forks`, the `run_forks` table) is the one place a fork's
    # parent is recorded: a field as well would be a second source of truth
    # that nothing keeps equal to the fork's own row.

    @property
    def task_paths(self) -> tuple[str, ...]:
        return self.chain.task_paths

    def policy_for(self, scope: ResolvedNode | ResolvedStep | ResolvedTask) -> InstancePolicy:
        """The effective policy `scope` runs under: this item's policy with
        every enclosing node, step and task override applied, broadest first
        (`policy-is-layered-by-execution-scope`). Derived from the stored
        authored chain on every call, never stored beside it -- the two could
        only disagree. `materialize` already checked every scope resolves."""
        return self.policy.layered(scope.scopes)

    def policy_at(self, path: str) -> InstancePolicy:
        """`policy_for` the node, step or task at canonical `path`, or
        `LookupError`."""
        for node in self.chain.nodes:
            if node.id == path:
                return self.policy_for(node)
            for scope in (*node.steps_in(), *node.tasks()):
                if scope.path == path:
                    return self.policy_for(scope)
        raise LookupError(f"no node, step or task at {path!r} in this chain")

    def to_json(self) -> str:
        """The immutable work-item input, as one JSON document: the authored
        chain, the effective policy, and the typed target.

        The *authored* chain is what is stored, never the resolved paths -- they
        are derived from it deterministically, so storing both would let the two
        disagree.

        Steering profile bodies ARE frozen here (`ResolvedChain.steering`): a
        steering profile is a library component, chain content like a task, so
        editing its `instructions` in `library.yaml` reaches items filed
        afterwards and never one already running.

        What is NOT frozen: harness profile configuration. A task carries
        `harness: codex_default`, a `harnesses.yaml` profile id, and dispatch
        resolves it live (`adapters.agent.harness_profile`). That is intended:
        `harnesses.yaml` holds settings for this Kraft install -- which
        executable, which defaults -- not the chain's content.
        """
        return _StoredMaterialization(
            chain=self.chain.chain,
            target=self.target,
            policy=self.policy,
            steering=self.chain.steering,
        ).model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> MaterializedChain:
        from kraft.templates.library import TemplateLibraryError

        try:
            stored = _StoredMaterialization.model_validate_json(raw)
        except ValidationError as exc:
            raise TemplateLibraryError(f"not a materialized chain: {first_error(exc)}") from exc
        return cls(
            chain=ResolvedChain.from_chain(stored.chain, steering=stored.steering),
            target=stored.target,
            policy=stored.policy,
        )
