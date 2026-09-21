"""The paths operator controls address, and the run forks a retry creates
(docs/templates-v1-design.md "Operator controls and run forks").

* `ChainPath` -- a canonical `node`, `node.step` or `node.step.task` path,
  checked against one work item's materialized chain. Retry, skip and steer all
  address work this way.
* `RetryOverride` -- the task configuration and policy a retry may carry, and
  how it is applied to a fork's copy of the chain. *Validating* one is
  `validate_retry_override`, which is a stub until Task 8a's policy-bounded
  validator is wired in its place.
* `RunFork` -- one retry: the run it forked from, what it retried, and its own
  immutable copy of the materialization (`retry-creates-an-immutable-run-fork`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from kraft.policy import TemplatePolicyOverride
from kraft.templates.models import (
    PATH_SEPARATOR,
    Chain,
    ExecNode,
    GateNode,
    MaterializedChain,
    ResolvedChain,
    ResolvedNode,
    ResolvedStep,
    ResolvedTask,
    first_error,
)


class ControlScope(StrEnum):
    """What an operator control addressed: the whole work item, or one
    canonical path's node, step or task."""

    WORK_ITEM = "work_item"
    NODE = "node"
    STEP = "step"
    TASK = "task"


class PathError(ValueError):
    """A path that does not name a node, a step or a task of this chain."""


@dataclass(frozen=True)
class ChainPath:
    """One canonical path in a work item's chain, resolved.

    Only a node's *own* work is addressable: `node`, `node.step` or
    `node.step.task`, where a `tasks` shorthand's step is `main`. A recovery
    pass, a fix loop, a judge or an escalation is work *about* a node, and an
    operator controls it through the node that owns it.
    """

    path: str
    node_index: int
    node: ResolvedNode
    step_index: int = 0
    step: ResolvedStep | None = None
    task: ResolvedTask | None = None

    @property
    def scope(self) -> ControlScope:
        if self.task is not None:
            return ControlScope.TASK
        return ControlScope.STEP if self.step is not None else ControlScope.NODE

    @classmethod
    def parse(cls, chain: MaterializedChain, path: str) -> ChainPath:
        segments = path.split(PATH_SEPARATOR)
        nodes = chain.chain.nodes
        index = next((i for i, n in enumerate(nodes) if n.id == segments[0]), None)
        if index is None or len(segments) > 3:
            raise PathError(
                f"{path!r} is not a node, node.step or node.step.task path of this chain"
            )
        node = nodes[index]
        if len(segments) == 1:
            return cls(path=path, node_index=index, node=node)
        if isinstance(node.node, GateNode):
            raise PathError(f"{path!r}: {node.id!r} is a gate, which has no steps")
        s_index = next((i for i, s in enumerate(node.steps) if s.id == segments[1]), None)
        if s_index is None:
            raise PathError(f"{path!r}: node {node.id!r} has no step {segments[1]!r}")
        step = node.steps[s_index]
        if len(segments) == 2:
            return cls(path=path, node_index=index, node=node, step_index=s_index, step=step)
        task = next((t for t in step.tasks if t.task.id == segments[2]), None)
        if task is None:
            raise PathError(f"{path!r}: step {step.path!r} has no task {segments[2]!r}")
        return cls(path=path, node_index=index, node=node, step_index=s_index, step=step, task=task)

    @property
    def skippable(self) -> bool:
        """Whether the addressed component allows a skip
        (`task-step-and-node-are-skippable-by-default`). Only its own flag: a
        step that allows skipping does not make a task in it skippable."""
        if self.task is not None:
            return self.task.task.skippable
        if self.step is not None:
            authored = self.node.node.steps if isinstance(self.node.node, ExecNode) else None
            # A `tasks` shorthand's `main` step has no authored step to say no.
            return authored[self.step_index].skippable if authored else True
        return self.node.node.skippable

    def contains(self, task_path: str) -> bool:
        """Whether `task_path` is this path or inside it."""
        return task_path == self.path or task_path.startswith(self.path + PATH_SEPARATOR)


class RetryOverrideError(ValueError):
    """A retry override refused, naming the one field it was refused for."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


class RetryOverride(BaseModel):
    """The runtime changes a retry may carry (`retry-overrides-are-policy-
    bounded`): a sparse patch of the retried task's own configuration, and a
    policy override for it. Validated by `validate_retry_override`, never
    here -- what is allowed at a path depends on the chain and its policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: dict[str, JsonValue] = Field(default_factory=dict)
    policy: TemplatePolicyOverride | None = None

    def is_empty(self) -> bool:
        return not self.task and (self.policy is None or not self.policy.model_fields_set)

    def apply(self, chain: MaterializedChain, target: ChainPath | None) -> MaterializedChain:
        """`chain` with this override applied to the task at `target`, as a new
        materialization -- the fork's copy. The stored snapshot is the authored
        chain, so the patch lands on the authored task and the whole chain is
        re-validated: an override that would break the chain is refused here as
        a last line, whatever the validator let through."""
        if self.is_empty():
            return chain
        if target is None or target.task is None:
            raise RetryOverrideError("path", "a retry override needs a node.step.task path")
        patch = dict(self.task)
        if self.policy is not None:
            patch["policy"] = self.policy.model_dump(exclude_unset=True)
        authored = chain.chain.chain.model_dump(mode="json", exclude_unset=True)
        node = authored["nodes"][target.node_index]
        steps = node.get("steps") or [{"tasks": node["tasks"]}]
        tasks = steps[target.step_index]["tasks"]
        at = next(i for i, t in enumerate(tasks) if t["id"] == target.task.task.id)
        tasks[at] = {**tasks[at], **patch}
        try:
            patched = Chain.model_validate(authored)
        except ValidationError as exc:
            raise RetryOverrideError("task", first_error(exc)) from exc
        return MaterializedChain(
            chain=ResolvedChain.from_chain(patched, steering=chain.chain.steering),
            target=chain.target,
            policy=chain.policy,
        )


def validate_retry_override(
    chain: MaterializedChain, path: str, override: RetryOverride
) -> RetryOverride:
    """The effective override for a retry of `path`, or a field-specific
    `RetryOverrideError`.

    TODO(8a): a STUB. Task 8a builds the real validator (the override may not
    change structure, ids, order or kind, and may not exceed the policy bounds at
    `path`) with this same signature. Whichever of the two PRs merges second
    replaces this body with a call to it. Until then every non-empty override
    is refused, so nothing unvalidated reaches a fork.
    """
    if override.is_empty():
        return override
    field = "task" if override.task else "policy"
    raise RetryOverrideError(
        field, "retry overrides are refused until the policy-bounded validator lands"
    )


class RunFork(BaseModel):
    """One retry of a work item: a new, immutable run that keeps every earlier
    run's data (`retry-creates-an-immutable-run-fork`).

    Lineage lives here: `parent` is the fork this one came from, `None` for the
    intake run. `materialized_chain` is the fork's own copy of the chain it
    runs, with its override applied; `after_seq` is the event it starts after,
    so everything up to it is the prior runs' record. Frozen here and refused
    an UPDATE or a DELETE by the table (`db._RUN_FORK_TRIGGERS`).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    work_item_id: str
    parent: str | None
    scope: ControlScope
    path: str | None
    after_seq: int
    materialized_chain: str
    override: RetryOverride | None = None

    @classmethod
    def from_retry(
        cls,
        *,
        work_item_id: str,
        parent: RunFork | None,
        chain: MaterializedChain,
        target: ChainPath | None,
        override: RetryOverride | None,
        after_seq: int,
    ) -> RunFork:
        """The fork a retry of `target` creates -- `None` restarts the work
        item from its first node (`work-item-restart-reruns-the-complete-
        chain`). `chain` is the run being forked from: the parent's copy, or the
        intake snapshot when there is no parent."""
        effective = override if override is not None and not override.is_empty() else None
        copied = effective.apply(chain, target) if effective is not None else chain
        return cls(
            id=uuid.uuid4().hex,
            work_item_id=work_item_id,
            parent=parent.id if parent is not None else None,
            scope=target.scope if target is not None else ControlScope.WORK_ITEM,
            path=target.path if target is not None else None,
            after_seq=after_seq,
            materialized_chain=copied.to_json(),
            override=effective,
        )

    @property
    def chain(self) -> MaterializedChain:
        return MaterializedChain.from_json(self.materialized_chain)

    @property
    def target(self) -> ChainPath | None:
        return ChainPath.parse(self.chain, self.path) if self.path is not None else None

    @property
    def start(self) -> tuple[int, int]:
        """Where the fork's walk begins: `(node index, step index)`."""
        target = self.target
        return (target.node_index, target.step_index) if target is not None else (0, 0)

    @property
    def preserved(self) -> frozenset[str]:
        """The completed work a task retry keeps: the retried task's siblings in
        its concurrent step (`task-retry-reruns-that-task-and-later-work`).
        Nothing for any wider scope, which reruns its whole span."""
        target = self.target
        if target is None or target.task is None:
            return frozenset()
        return frozenset(t.path for t in target.step.tasks if t.path != target.path)

    def row(self) -> tuple:
        """The `run_forks` columns, in table order after `created_at`'s absence."""
        return (
            self.id,
            self.work_item_id,
            self.parent,
            self.scope.value,
            self.path,
            self.after_seq,
            self.materialized_chain,
            self.override.model_dump_json() if self.override is not None else None,
        )

    @classmethod
    def from_row(cls, row) -> RunFork:
        return cls(
            id=row["id"],
            work_item_id=row["work_item_id"],
            parent=row["parent"],
            scope=ControlScope(row["scope"]),
            path=row["path"],
            after_seq=row["after_seq"],
            materialized_chain=row["materialized_chain"],
            override=RetryOverride.model_validate(json.loads(row["override"]))
            if row["override"]
            else None,
        )
