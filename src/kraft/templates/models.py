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

import math
import re
from collections.abc import Container, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

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

from kraft.cap_levels import LEVEL_OF
from kraft.policy import (
    FROZEN,
    RETIRED_WAIT_TIMEOUT,
    SCOPE_CAP_FIELDS,
    InstancePolicy,
    PolicyError,
    SandboxPolicy,
    TaskPolicyOverride,
    TemplatePolicyOverride,
    WorkItemPolicy,
    deprecated,
)
from kraft.templates.environment import FallbackEntry, Identifier, WorkItemTarget

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
    #: `builtins.mr_rebase` -- rebasing the worktree onto the item's base
    #: branch, right before a draft merge request opens (Kraft-3llig). Pure
    #: local git, no forge CLI call, which is why this is a builtin and not a
    #: `ForgeAction`: the node's other task (`mr.open_draft`) is the one that
    #: talks to the forge.
    MR_REBASE = "kraft.mr_rebase"


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

    @property
    def waits(self) -> bool:
        """Whether this target observes an external condition, and so runs as
        an external wait (`external-wait-covers-merge-request-lifecycle`).
        `mr.merge` is one: its pipeline, its approval and its landing are all
        conditions outside Kraft."""
        return self in _WAIT_TARGETS


_WAIT_TARGETS = frozenset(
    {
        ForgeAction.MR_CI,
        ForgeAction.MR_AUTOMATED_REVIEW,
        ForgeAction.MR_EXTERNAL_APPROVAL,
        ForgeAction.MR_MERGE,
        ForgeAction.MR_POST_MERGE_CI,
    }
)


class AgentInput(StrEnum):
    """What Kraft hands an agent task beyond its prompt, when the task asks for
    it by name. Declared on the task rather than keyed on a task's name or
    role, which is the indirection V1 deletes (Ruling 47).

    * `review_package` -- the change under review written out to a file, its
      path in `$KRAFT_REVIEW_PACKAGE`: the whole branch since `base_ref`, then
      from a task's second session on only what changed since its previous
      one (`review-package-is-delivered-to-a-task-that-declares-it`).
    * `carried_findings` -- the findings the node's last measurement reported,
      each with its stable tag, so a reworded repeat can be reported as the
      same finding (`carried-findings-are-delivered-to-a-reviewing-task`).
    * `previous_review` -- the task's own previous completed session's result
      file and summary, by path
      (`continuity-note-is-delivered-to-a-resumed-reviewer`)."""

    REVIEW_PACKAGE = "review_package"
    CARRIED_FINDINGS = "carried_findings"
    PREVIOUS_REVIEW = "previous_review"


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
    """`external-wait-has-configurable-timeout-and-polling`: a wait's polling.
    Its timeout is its task's own `policy: total_time_cap_minutes` (Ruling
    196); a `timeout:` written here before that is read as one
    (`ForgeTask._retired_wait_timeout`)."""

    model_config = _CONFIG

    polling: PollingPolicy = Field(default_factory=PollingPolicy)


@dataclass(frozen=True)
class WaitBounds:
    """One external wait's resolved timeout and polling bounds
    (`external-wait-has-configurable-timeout-and-polling`).

    The next observation comes `initial_interval` after the first, then
    doubles, never past `max_interval` (`external-waits-use-a-shared-due-
    scheduler`)."""

    timeout: timedelta
    initial_interval: timedelta
    max_interval: timedelta

    @classmethod
    def from_seconds(cls, *, timeout: float, initial: float, maximum: float) -> WaitBounds:
        return cls(
            timedelta(seconds=timeout), timedelta(seconds=initial), timedelta(seconds=maximum)
        )

    def interval_after(self, observation: int) -> timedelta:
        """The gap after the `observation`-th (1-based) pending observation."""
        return min(self.initial_interval * 2 ** (observation - 1), self.max_interval)


#: What a wait that authors no `wait:` runs under. 90 minutes (Kraft-7xpv4):
#: the 30-minute default this replaces stopped real pipelines that would have
#: gone green -- this repository's own CI finishes near 30 minutes with a
#: queued or retried job eating the margin, and its author raised a live
#: install to 60. 90 is the design's own seeded CI timeout, three times the
#: observed run, and a stop that comes too late costs less than one that pages
#: a human over a pipeline about to pass.
DEFAULT_WAIT = WaitBounds(
    timeout=timedelta(minutes=90),
    initial_interval=timedelta(seconds=30),
    max_interval=timedelta(minutes=5),
)


def retired_keys(data: object, at: str = "") -> list[str]:
    """Where `data` -- an authored chain or library mapping -- still writes a
    key Ruling 196 retired: a wait's `timeout` and `wait_timeout_minutes`.
    They still read, with a warning; a write that sets one is refused
    (`PUT /templates/chains/{id}`), naming what replaced it."""
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            where = f"{at}.{key}" if at else str(key)
            if key == RETIRED_WAIT_TIMEOUT or (
                key == "timeout" and at.rsplit(".", 1)[-1] == "wait"
            ):
                found.append(where)
            found += retired_keys(value, where)
    elif isinstance(data, list):
        for i, value in enumerate(data):
            found += retired_keys(value, f"{at}[{i}]")
    return found


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

    @model_validator(mode="before")
    @classmethod
    def _read_only_is_not_a_task_field(cls, data: object) -> object:
        # Named, not left to `extra="forbid"`: the fix is one level up.
        if isinstance(data, dict) and "read_only" in data:
            raise ValueError(
                "set read_only on the step: tasks in a step share one worktree, "
                "so a task-level check would fail on a sibling's writes"
            )
        return data


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
    #: The two routes to a model, one per task: `profile:` (an agent profile
    #: in `harnesses.yaml`, read live at launch -- Kraft-ps1ao), or its own
    #: `model:`/`effort:`. `extends` keeps them apart (`displaced_route`).
    profile: Identifier | None = None
    model: StrictStr | None = None
    effort: StrictStr | None = None
    #: Inputs Kraft delivers to this task (`AgentInput`), e.g.
    #: `inputs: [review_package]`.
    inputs: list[Annotated[AgentInput, _LOOSE]] = Field(default_factory=list)
    #: Tried in order when a launch is rate-limited or its harness unavailable
    #: (`fallback-is-opt-in`). `None` is unset; `[]` is "none".
    fallback: list[FallbackEntry] | None = None

    @model_validator(mode="after")
    def _one_route(self) -> Self:
        if self.profile is not None and (self.model is not None or self.effort is not None):
            raise ValueError(
                f"selects profile {self.profile!r} and sets model/effort itself; a task "
                "takes its model from one or the other"
            )
        return self


#: An agent task's two routes to a model (`AgentTask.profile`).
_ROUTES = (("profile",), ("model", "effort"))


def displaced_route(nearer: Mapping[str, object]) -> tuple[str, ...]:
    """The keys an inherited task loses under `nearer`, a layer that picks a
    route: the nearer layer's route wins whole, so a `profile:` drops the
    inherited `model`/`effort` and either of those drops the inherited
    `profile` (Kraft-ps1ao). Empty when `nearer` picks neither."""
    for route, other in (_ROUTES, _ROUTES[::-1]):
        if any(k in nearer for k in route):
            return other
    return ()


class SubprocessTask(TaskBase):
    kind: Literal[TaskKind.SUBPROCESS]
    command: StrictStr = Field(min_length=1)


class ForgeTask(TaskBase):
    kind: Literal[TaskKind.FORGE]
    target: Annotated[ForgeAction, _LOOSE]
    wait: WaitPolicy | None = None

    @model_validator(mode="before")
    @classmethod
    def _retired_wait_timeout(cls, data: object) -> object:
        """A `wait: timeout:` written before Ruling 196 -- an installed
        `library.yaml` seeded before it, a snapshot frozen before it -- reads
        as the task's own `policy: total_time_cap_minutes`, rounded up to a
        whole minute, unless the task already sets one. `PUT /templates`
        refuses it in a chain being saved (`retired_keys`)."""
        if not isinstance(data, dict) or not isinstance(data.get("wait"), dict):
            return data
        wait = dict(data["wait"])
        if "timeout" not in wait:
            return data
        raw = wait.pop("timeout")
        data = {**data, "wait": wait}
        if raw is None:
            return data
        minutes = math.ceil(_duration(raw).total_seconds() / 60)
        deprecated(
            "%s: wait.timeout is deprecated (Ruling 196) and read as "
            "policy.total_time_cap_minutes %s; move it there",
            data.get("id", "a forge task"),
            minutes,
        )
        policy = data.get("policy")
        policy = dict(policy) if isinstance(policy, dict) else {}
        policy.setdefault("total_time_cap_minutes", minutes)
        return {**data, "policy": policy}

    def wait_bounds(self, policy: InstancePolicy) -> WaitBounds:
        """This wait's bounds under `policy`, the task's own resolved policy.
        Its timeout is the task's `total_time_cap_minutes` (Ruling 196), which
        the ratchet already held under every enclosing cap, and which
        `InstancePolicy.at_level` already filled from the tasks' default or
        maximum; a wait with none anywhere takes `DEFAULT_WAIT`'s."""
        wait = self.wait or WaitPolicy()
        timeout = (
            timedelta(minutes=policy.total_time_cap_minutes)
            if policy.total_time_cap_minutes is not None
            else DEFAULT_WAIT.timeout
        )
        initial = wait.polling.initial_interval or DEFAULT_WAIT.initial_interval
        maximum = wait.polling.max_interval or max(DEFAULT_WAIT.max_interval, initial)
        return WaitBounds(timeout=timeout, initial_interval=initial, max_interval=maximum)


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
    #: The step's tasks must leave the worktree as they found it, checked
    #: around all of them together (`executor.read_only`).
    read_only: StrictBool = False

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
                    own=step.policy,
                    read_only=step.read_only,
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
        if step.read_only:
            raise ValueError(f"{where}: step {step.id!r} cannot be read_only: it writes by design")
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
    #: The node's own steps must leave the worktree as they found it, checked
    #: from before its first step to after its last (`executor.read_only`).
    read_only: StrictBool = False

    @model_validator(mode="after")
    def _read_only_has_no_fix_loop(self) -> Self:
        # A fix loop writes by design.
        if self.read_only and self.fix_loop is not None:
            raise ValueError(f"{self.id}: a node with a fix_loop cannot be read_only")
        return self

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
        if self.auto_review is not None and self.auto_review.fallback:
            # `gate_review` launches its reviewer once; a list it never walks
            # would read as a fallback that is not there.
            raise ValueError(
                f"a gate's auto_review task {self.auto_review.id!r} cannot declare a "
                "fallback list: a gate review never falls back"
            )
        return self


AnyNode = Annotated[ExecNode | GateNode, Field(discriminator="kind")]


#: The forge targets that take a merge request past draft.
_PUBLISHING = frozenset({ForgeAction.MR_MARK_READY, ForgeAction.MR_MERGE})


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

    @model_validator(mode="after")
    def _publishes_after_the_final_gate(self) -> Self:
        """A draft merge request may open before the final gate, but nothing
        may mark it ready or merge it ahead of that gate's approval
        (`draft-merge-request-enables-external-checks`, `final-gate-governs-
        merge-request-readiness`) -- from any task position the executor can
        run in a node before it. The positions come from `ResolvedNode.tasks`,
        the one walker of them (node and step tasks, every recovery plan down
        to a task's own, the fix loop and its judge, escalation, on_conflict,
        a gate's reviewer), never a second list here that could drift from it
        (Kraft-nwonj). A chain with no final gate declares no approval to wait
        for, and is not constrained here (Kraft-8tjh7)."""
        final = next(
            (i for i, n in enumerate(self.nodes) if isinstance(n, GateNode) and n.chain_finalized),
            None,
        )
        if final is None:
            return self
        for node in ResolvedChain.from_chain(self).nodes[:final]:
            for task in node.tasks():
                if isinstance(task.task, ForgeTask) and task.task.target in _PUBLISHING:
                    raise ValueError(
                        f"{task.path}: {task.task.target.value} runs before the final gate "
                        f"{self.nodes[final].id!r} approves it"
                    )
        return self


def _dedicated(path: str, task: AnyTask | None, scopes: Scopes) -> ResolvedTask | None:
    """A named-slot task (judge, escalation, gate reviewer) at `path`, in its
    node's `scopes` plus its own override."""
    if task is None:
        return None
    return ResolvedTask(path=path, task=task, scopes=(*scopes, *_own(task.policy)))


def _scoped(
    scope: ResolvedNode | ResolvedStep | ResolvedTask,
    policy: InstancePolicy,
    item: WorkItemPolicy | None = None,
) -> InstancePolicy:
    """The policy `scope` runs under: `policy` layered with every override
    enclosing it, then `item`'s layers at its path, its caps read at its level
    (`InstancePolicy.at_level`, Ruling 211). A refusal is prefixed by the
    scope's path."""
    try:
        policy = policy.layered(scope.scopes)
        if item is not None:
            policy = item.apply_to(policy, scope.path, scope.scopes)
        return policy.at_level(scope.level)
    except PolicyError as exc:
        raise PolicyError(f"{scope.path}: {exc}", field=exc.field, path=scope.path) from exc


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

    #: The level its caps' defaults and maxima come from (Ruling 211).
    level: ClassVar[str] = "tasks"

    @property
    def own(self) -> TaskPolicyOverride | None:
        """The override this task sets itself."""
        return self.task.policy


@dataclass(frozen=True)
class ResolvedStep:
    path: str
    id: str
    tasks: tuple[ResolvedTask, ...]
    #: The step's own recovery plan, at `<step path>.on_failure`.
    on_failure: tuple[ResolvedStep, ...] = ()
    scopes: Scopes = ()
    #: The override this step sets itself.
    own: TaskPolicyOverride | None = None
    read_only: bool = False

    level: ClassVar[str] = "steps"


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

    #: A gate is a node, for its caps' level too (Ruling 211).
    level: ClassVar[str] = "nodes"

    @property
    def path(self) -> str:
        return self.id

    @property
    def own(self) -> TaskPolicyOverride | None:
        """The override this node sets itself."""
        return self.node.policy

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

    @property
    def covered_by(self) -> str | None:
        """The attachment kind that makes this node redundant, if one does:
        the kind a gate's `artifact` decides, or the one kind every one of an
        execution node's own tasks produces. Both halves read side by side, so
        the intake preview (`store.chain.node_view`) and materialization
        (`trim_for_attachments`) cannot disagree about which nodes an
        attachment drops. A `chain_finalized` gate is never covered."""
        if isinstance(self.node, GateNode):
            return None if self.node.chain_finalized else self.node.artifact
        produces = self.produces()
        return next(iter(produces)) if len(produces) == 1 else None

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
    the whole of `trim_for_attachments`' rule."""
    return node.covered_by is not None and node.covered_by in kinds


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
        repository_policies: Mapping[str, InstancePolicy] | None = None,
        repository_steering: Mapping[str, Mapping[str, str]] | None = None,
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

        `repository_policies` are a workspace item's per-repository starting
        policies (instance plus that repository's layer, Kraft-jc39p), keyed
        by repository id; each gets this chain's layer too, and is what a task
        fanned out to that repository runs under. `effective_policy` is then
        the assembled checkout's: the tightest of them all.

        `repository_steering` is `MaterializedChain.repository_steering`,
        resolved by the caller (`api.deps.repository_steering`).
        """
        policy = self.chain_policy(effective_policy)
        per_repository = {
            rid: self.chain_policy(p) for rid, p in (repository_policies or {}).items()
        }
        dropped = {n.id for n in self.nodes if _redundant_given(n, attachment_kinds)} | (
            skip_nodes & {n.id for n in self.nodes}
        )
        materialized = MaterializedChain(
            chain=self.without_nodes(dropped),
            target=target,
            policy=policy,
            repository_policies=per_repository,
            untrimmed=self.without_nodes(skip_nodes).chain if attachment_kinds else None,
            repository_steering=repository_steering,
        )
        # Every door that builds a snapshot comes through here -- intake by
        # any route, a trigger, a chain template switch -- so the refusal is
        # made once, before anything is filed.
        refusal = materialized.sandbox_refusal()
        if refusal is not None:
            raise PolicyError(refusal, field="sandbox")
        return materialized

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

    def check_scopes(self, policy: InstancePolicy, item: WorkItemPolicy | None = None) -> None:
        """Raise `PolicyError`, naming the scope, unless every node, step and
        task scope resolves on top of `policy` -- the chain's own policy,
        already layered -- with a work `item`'s own layers last, and every
        agent task's harness and every fix loop's `max_attempts` is within
        it."""
        self._check_caps(policy, item)
        for node in self.nodes:
            _scoped(node, policy, item)
            loop = node.node.fix_loop if isinstance(node.node, ExecNode) else None
            ceiling = policy.maxima.max_attempts
            if loop is not None and loop.max_attempts is not None and ceiling is not None:
                if loop.max_attempts > ceiling:
                    raise PolicyError(
                        f"{node.id}: fix_loop.max_attempts {loop.max_attempts} cannot exceed "
                        f"the administrator maximum max_attempts {ceiling}",
                        field="max_attempts",
                        path=node.id,
                    )
            for step in node.steps_in():
                _scoped(step, policy, item)
            for task in node.tasks():
                task_policy = _scoped(task, policy, item)
                if isinstance(task.task, ForgeTask) and task.task.target.waits:
                    try:
                        task.task.wait_bounds(task_policy)
                    except PolicyError as exc:
                        raise PolicyError(
                            f"{task.path}: {exc}", field=exc.field, path=task.path
                        ) from exc
                allowed = task_policy.allowed_harnesses
                if isinstance(task.task, AgentTask) and allowed is not None:
                    # A fallback can never run where the task's policy refuses
                    # (`fallback-never-escapes-allowed-harnesses`).
                    fallback = [e.harness for e in task.task.fallback or () if e.harness]
                    for where, harness in [("harness", task.task.harness)] + [
                        ("fallback harness", h) for h in fallback
                    ]:
                        if harness not in allowed:
                            raise PolicyError(
                                f"{task.path}: {where} {harness!r} is not in its "
                                f"allowed_harnesses {sorted(allowed)!r}",
                                field="allowed_harnesses",
                                path=task.path,
                            )
        # A sandbox wraps the whole work item, not the scope that set it
        # (Ruling 189): once one task has run in it the worktree is untrusted
        # for every later launch, so two scopes cannot ask for two.
        sandboxes = list(self.scope_sandboxes(policy, item).items())
        if len(sandboxes) > 1:
            (first, where), (second, other) = sandboxes[:2]
            raise PolicyError(
                f"{where} runs in sandbox {first.model_dump()!r} and {other} in "
                f"{second.model_dump()!r}: a sandbox wraps the whole work item (Ruling 189), "
                "so every scope that sets one must set the same one",
                field="sandbox",
            )

    def cap_scopes(self) -> list[tuple[str, str, ResolvedNode | ResolvedStep | ResolvedTask]]:
        """`(path, kind, scope)` for every scope a time cap can sit on, a
        parent always before its children: every node, step and task."""
        found: list[tuple[str, str, ResolvedNode | ResolvedStep | ResolvedTask]] = []
        for node in self.nodes:
            found.append((node.id, "gate" if isinstance(node.node, GateNode) else "node", node))
            found += [(s.path, "step", s) for s in node.steps_in()]
            found += [(t.path, "task", t) for t in node.tasks()]
        return sorted(found, key=lambda f: f[0].count(PATH_SEPARATOR))

    def _check_caps(self, policy: InstancePolicy, item: WorkItemPolicy | None) -> None:
        """Refuse a time cap above its parent's (Rulings 194, 195), naming both
        scopes: "task build.main.impl sets time_cap_minutes 20 > its step
        build.main's 10". The parents are the chain's own scopes -- chain,
        node, step, task. A level's default is not a parent (Rulings 198,
        211): any scope may set more, up to its level's maximum, which a
        scope's own value is refused past here, naming the maximum. A work
        item's item-wide cap is the work item's own and may exceed the
        chain's, up to the `work_item` maximum; its cap on a path only
        tightens that scope. A gate's own `timeout` sits under the total cap
        its chain sets around it."""
        scopes = self.cap_scopes()
        kinds = {path: kind for path, kind, _ in scopes}

        def parent_of(path: str) -> str:
            segments = path.split(PATH_SEPARATOR)[:-1]
            while segments and PATH_SEPARATOR.join(segments) not in kinds:
                segments.pop()
            return PATH_SEPARATOR.join(segments)

        def named(path: str) -> str:
            return f"{kinds[path]} {path}" if path else "the chain"

        def past_maximum(path: str, name: str, value: object) -> str | None:
            bound = policy.maxima.nearest(LEVEL_OF[kinds[path]], name)
            if bound is None or value <= bound[1]:
                return None
            return (
                f"{named(path)} sets {name} {value} > the administrator maximum {bound[1]} "
                f"(maxima.{bound[0]}.{name}; Ruling 211)"
            )

        chain_own = self.chain.policy
        for name in SCOPE_CAP_FIELDS:
            root = getattr(chain_own, name) if chain_own is not None else None
            authored: dict[str, int | None] = {"": root}
            for path, _, scope in scopes:
                layers = scope.scopes
                value = next(
                    (v for layer in reversed(layers) if (v := getattr(layer, name)) is not None),
                    root,
                )
                parent = parent_of(path)
                if value is not None and authored[parent] is not None and value > authored[parent]:
                    whose = f"its {named(parent)}'s" if parent else "the chain's"
                    raise PolicyError(
                        f"{path}: {named(path)} sets {name} {value} > {whose} {authored[parent]}: "
                        "a scope's cap cannot exceed its parent's (Rulings 194, 195)",
                        field=name,
                        path=path,
                    )
                own = getattr(scope.own, name) if scope.own is not None else None
                if own is not None and (why := past_maximum(path, name, own)):
                    raise PolicyError(f"{path}: {why}", field=name, path=path)
                authored[path] = value
            if item is None:
                continue
            bound = policy.maxima.nearest("work_item", name)
            wide = getattr(item, name)
            if wide is not None and bound is not None and wide > bound[1]:
                raise PolicyError(
                    f"'{name}' {wide} cannot exceed the administrator maximum {bound[1]}",
                    field=name,
                    path="",
                )
            for path, _, scope in scopes:
                if path not in item.paths or getattr(item.paths[path], name) is None:
                    continue
                own = getattr(item.paths[path], name)
                # What the scope already has: its chain's value, else the
                # item's own item-wide one, and every enclosing path's.
                has = [
                    next(
                        (
                            v
                            for layer in reversed(scope.scopes)
                            if (v := getattr(layer, name)) is not None
                        ),
                        wide if wide is not None else root,
                    ),
                    *(
                        getattr(layer, name)
                        for where, layer in item.layers_at(path)[1:]
                        if where != f"policy.paths.{path}"
                    ),
                ]
                below = min((v for v in has if v is not None), default=None)
                if below is not None and own > below:
                    raise PolicyError(
                        f"the work item's override sets {name} {own} on {named(path)}, above the "
                        f"{below} it already has: a work item's override only tightens a cap "
                        "(Rulings 194, 195)",
                        field=name,
                        path=path,
                    )
                if why := past_maximum(path, name, own):
                    raise PolicyError(f"the work item's override: {why}", field=name, path=path)
        for node in self.nodes:
            if not isinstance(node.node, GateNode) or node.node.timeout is None:
                continue
            cap = next(
                (
                    v
                    for layer in reversed(node.scopes)
                    if (v := layer.total_time_cap_minutes) is not None
                ),
                chain_own.total_time_cap_minutes if chain_own is not None else None,
            )
            if cap is not None and node.node.timeout > timedelta(minutes=cap):
                raise PolicyError(
                    f"gate {node.id}'s timeout {_duration_text(node.node.timeout)} > its "
                    f"total_time_cap_minutes {cap}: a gate's timeout sits under the total caps "
                    "around it (Ruling 195)",
                    field="timeout",
                    path=node.id,
                )

    def scope_sandboxes(
        self, policy: InstancePolicy, item: WorkItemPolicy | None = None
    ) -> dict[SandboxPolicy, str]:
        """Each distinct sandbox a scope of this chain runs under on top of
        `policy`, a work `item`'s own layers last, with the first scope that
        runs under it: `"the chain"` when `policy` itself carries it. At most
        one for a chain `check_scopes` accepts (Ruling 189)."""
        found: dict[SandboxPolicy, str] = {}
        if policy.sandbox is not None:
            found[policy.sandbox] = "the chain"
        for node in self.nodes:
            for scope in (node, *node.tasks()):
                sandbox = _scoped(scope, policy, item).sandbox
                if sandbox is not None:
                    found.setdefault(sandbox, scope.path)
        return found

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

    @model_validator(mode="before")
    @classmethod
    def _retired_wait_timeout(cls, data: object) -> object:
        """A snapshot frozen before Ruling 196 resolved a `wait_timeout_minutes`
        into its policy. It meant "every wait", which a scope's total cap does
        not, so it is dropped: each wait keeps its own task's cap."""
        if not isinstance(data, dict):
            return data

        def drop(policy: object) -> object:
            if isinstance(policy, dict) and policy.get(RETIRED_WAIT_TIMEOUT) is not None:
                deprecated("a stored snapshot's %s is dropped (Ruling 196)", RETIRED_WAIT_TIMEOUT)
            return (
                {k: v for k, v in policy.items() if k != RETIRED_WAIT_TIMEOUT}
                if isinstance(policy, dict)
                else policy
            )

        if "policy" in data:
            data = {**data, "policy": drop(data["policy"])}
        if isinstance(data.get("repository_policies"), dict):
            data["repository_policies"] = {
                r: drop(p) for r, p in data["repository_policies"].items()
            }
        return data

    #: `ResolvedChain.steering`. Absent from a snapshot stored before steering
    #: was frozen, which reads back as `None`.
    steering: dict[str, str] | None = None
    #: `MaterializedChain.repository_policies`; absent for a single repository.
    repository_policies: dict[str, InstancePolicy] = {}
    #: `MaterializedChain.untrimmed`; absent when nothing is attached.
    untrimmed: Chain | None = None
    #: `MaterializedChain.repository_steering`. Absent from a snapshot stored
    #: before repository steering was frozen, which reads back as `None`.
    repository_steering: dict[str, dict[str, str]] | None = None


@dataclass(frozen=True)
class MaterializedChain:
    """One work item's snapshot: a resolved chain plus the decisions that do
    not change while it runs."""

    chain: ResolvedChain
    target: WorkItemTarget
    #: The policy of the item's ordinary execution context: for a workspace
    #: item, the assembled checkout's -- the tightest of every selected
    #: repository's layer (Kraft-jc39p).
    policy: InstancePolicy
    #: A workspace item's policy per selected repository id, what a task
    #: fanned out to that repository runs under. Empty for one repository.
    repository_policies: Mapping[str, InstancePolicy] = field(default_factory=dict)
    #: The work item's own override (Kraft-ab1bh), layered after every scope
    #: the chain authored (`policy_for`). Held on the item's row
    #: (`work_items.policy_override`) and attached when the row is read
    #: (`store.materialized_chain_of`), never written into the snapshot:
    #: the snapshot does not change while the item executes
    #: (`materialized-chain-is-immutable-work-item-input`), and a `PATCH` may
    #: change this.
    item_policy: WorkItemPolicy | None = None
    #: The chain before its attachment trim (skips applied), kept while the item
    #: has attachments so a not-yet-started item can drop one and get back the
    #: nodes it trimmed from its own snapshot, never the live template
    #: (Kraft-s7c04.29, Kraft-2fyjt). None when nothing is attached.
    untrimmed: Chain | None = None
    #: The steering each repository the item runs in selected in `repos.yaml`
    #: at intake, resolved against the library's profiles: repository path to
    #: an ordered name-to-instructions map, holding only repositories that
    #: named any. Frozen like task steering: editing a profile or a
    #: repository's `steering:` list reaches items filed afterwards. `None` is
    #: a snapshot stored before this was frozen, whose launches read the
    #: repository's names against the live library, the way they read the
    #: steering files they were filed with (`worker.steering.for_repository`).
    repository_steering: Mapping[str, Mapping[str, str]] | None = None

    # Fork lineage is deliberately NOT a field here. `RunFork.parent`
    # (`kraft.templates.forks`, the `run_forks` table) is the one place a fork's
    # parent is recorded: a field as well would be a second source of truth
    # that nothing keeps equal to the fork's own row.

    @property
    def task_paths(self) -> tuple[str, ...]:
        return self.chain.task_paths

    def item_sandbox(self) -> SandboxPolicy | None:
        """The one sandbox this item runs every launch in, whichever scope
        froze it (Ruling 189), or None. Raises `PolicyError` for a snapshot
        with two, which `check_scopes` refuses to build; only one filed before
        the ruling can carry them."""
        found = self.chain.scope_sandboxes(self.policy, self.item_policy)
        if len(found) > 1:
            raise PolicyError(
                "its scopes freeze different sandboxes "
                + ", ".join(f"{where}: {s.model_dump()!r}" for s, where in found.items())
                + "; a sandbox wraps the whole work item (Ruling 189)",
                field="sandbox",
            )
        return next(iter(found), None)

    def sandbox_refusal(self) -> str | None:
        """Why this snapshot cannot run, when it mounts submodules and any task
        it runs is sandboxed, whichever layer set the sandbox (Kraft-dshto):
        a repository, the work item, the chain, a node, a step or a task.
        None when the pairing is absent. A check rather than a construction
        invariant: an item already in flight rehydrates its snapshot to be
        stopped for a human, and that must not raise."""
        if not self.target.mounts:
            return None
        if self.policy.sandbox is None and not any(
            self.policy_for(t).sandbox is not None for n in self.chain.nodes for t in n.tasks()
        ):
            return None
        named = [r for r, p in self.repository_policies.items() if p.sandbox is not None]
        who = f"repository {', '.join(map(repr, named))}" if named else "the chain"
        who = f"workspace {self.target.workspace!r}: {who}"
        # Here, not at the top: `kraft.worker` is below `kraft.templates`.
        from kraft.worker.sandbox import submodule_refusal

        return submodule_refusal(who)

    def policy_for(
        self,
        scope: ResolvedNode | ResolvedStep | ResolvedTask,
        repository: str | None = None,
    ) -> InstancePolicy:
        """The effective policy `scope` runs under: this item's policy -- or,
        for a task fanned out to `repository`, that repository's -- with every
        enclosing node, step and task override applied, broadest first
        (`policy-is-layered-by-execution-scope`). Derived from the stored
        authored chain on every call, never stored beside it -- the two could
        only disagree. `materialize` already checked every scope resolves.
        `LookupError` for a repository this item did not select."""
        if repository is not None and repository not in self.target.repositories():
            raise LookupError(f"this work item selects no repository {repository!r}")
        # A repository with no policy of its own frozen falls back to the
        # item's, which is the meet of them all: never looser than its own.
        base = self.repository_policies.get(repository, self.policy) if repository else self.policy
        return _scoped(scope, base, self.item_policy)

    def work_item_policy(self) -> InstancePolicy:
        """The policy the work item as a whole runs under: this item's, with
        its own item-wide override on top and its caps read at the
        `work_item` level (Ruling 211) -- the caps its own clocks and spend
        are held to (`kraft.caps`)."""
        policy = self.policy
        if self.item_policy is not None:
            policy = self.item_policy.apply_to(policy, "")
        return policy.at_level("work_item")

    def with_item_policy(self, raw: WorkItemPolicy | dict | None) -> MaterializedChain:
        """This snapshot with a work item's own override `raw` layered on, once
        it is valid here (Kraft-ab1bh) -- the check every door that sets one
        makes: intake, a `PATCH`, a chain switch. Raises `PolicyError` whose
        `field` names the one field refused: `policy.<name>` for an item-wide
        field, `policy.paths.<path>[.<name>]` for one addressed by path.

        The same rules as every other layer, by the same engine: each path
        names a node, step or task of this chain (`ChainPath`); a fix loop's
        bounds sit on an execution node only; a safety value only tightens and
        an operational one stays within the administrator maxima, at every
        scope, for every repository a workspace item selects
        (`check_scopes`). Nothing else can be expressed: the override's model
        has no structural key, so it cannot change the chain's shape."""
        from kraft.templates.forks import ChainPath, ControlScope, PathError

        if raw is None or isinstance(raw, WorkItemPolicy):
            item = raw
        else:
            if retired := retired_keys(raw):
                where = f"policy.{retired[0]}"
                raise PolicyError(
                    f"{where}: retired (Ruling 196): a wait's timeout is its task's own "
                    "total_time_cap_minutes, so set that on the wait task's path",
                    field=where,
                )
            try:
                item = WorkItemPolicy.model_validate(raw)
            except ValidationError as exc:
                error = exc.errors()[0]
                where = ".".join(["policy", *(str(p) for p in error["loc"])])
                raise PolicyError(
                    f"{where}: {error['msg']} (a work item's policy override sets policy "
                    "fields only; it cannot change the chain's structure)",
                    field=where,
                ) from exc
        if item is None:
            return replace(self, item_policy=None)
        for path, layer in item.paths.items():
            where = f"policy.paths.{path}"
            try:
                at = ChainPath.parse(self, path)
            except PathError as exc:
                raise PolicyError(f"{where}: {exc}", field=where) from exc
            on_exec_node = at.scope is ControlScope.NODE and isinstance(at.node.node, ExecNode)
            for name in ("max_attempts", "timeout_minutes"):
                if getattr(layer, name) is not None and not on_exec_node:
                    raise PolicyError(
                        f"{where}.{name}: bounds an execution node's fix loop, and {path!r} "
                        "is not an execution node",
                        field=f"{where}.{name}",
                    )
        layered = replace(self, item_policy=item)
        try:
            for base in (self.policy, *self.repository_policies.values()):
                self.chain.check_scopes(base, item)
        except PolicyError as exc:
            where = next(
                (
                    w
                    for w, layer in reversed(item.layers_at(exc.path or ""))
                    if getattr(layer, exc.field or "", None) is not None
                ),
                "policy",
            )
            where = f"{where}.{exc.field}"
            raise PolicyError(f"{where}: {exc}", field=where, path=exc.path) from exc
        refusal = layered.sandbox_refusal()
        if refusal is not None:
            raise PolicyError(f"policy.sandbox: {refusal}", field="policy.sandbox")
        return layered

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
        `harness: codex`, a `harnesses.yaml` profile id, and dispatch
        resolves it live (`adapters.agent.harness_profile`). That is intended:
        `harnesses.yaml` holds settings for this Kraft install -- which
        executable, which defaults -- not the chain's content.
        """
        return _StoredMaterialization(
            chain=self.chain.chain,
            target=self.target,
            policy=self.policy,
            steering=self.chain.steering,
            repository_policies=dict(self.repository_policies),
            untrimmed=self.untrimmed,
            repository_steering=(
                None
                if self.repository_steering is None
                else {p: dict(t) for p, t in self.repository_steering.items()}
            ),
        ).model_dump_json(
            # A single-repository item with no attachment keeps its exact shape.
            exclude=({"repository_policies"} if not self.repository_policies else set())
            | ({"untrimmed"} if self.untrimmed is None else set())
            | ({"repository_steering"} if self.repository_steering is None else set())
        )

    @classmethod
    def from_json(cls, raw: str) -> MaterializedChain:
        from kraft.templates.library import TemplateLibraryError

        try:
            # Read leniently: a rule frozen before Kraft-9i6xy refused one still
            # reads, and its launch is refused instead (Kraft-9ct4q).
            stored = _StoredMaterialization.model_validate_json(raw, context=FROZEN)
        except ValidationError as exc:
            raise TemplateLibraryError(f"not a materialized chain: {first_error(exc)}") from exc
        return cls(
            chain=ResolvedChain.from_chain(stored.chain, steering=stored.steering),
            target=stored.target,
            policy=stored.policy,
            repository_policies=stored.repository_policies,
            untrimmed=stored.untrimmed,
            repository_steering=stored.repository_steering,
        )
