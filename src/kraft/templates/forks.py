"""The paths operator controls address, and the run forks a retry creates
(docs/templates-v1-design.md "Operator controls and run forks").

* `ChainPath` -- a canonical `node`, `node.step` or `node.step.task` path,
  checked against one work item's materialized chain. Retry, skip and steer all
  address work this way.
* `RunFork` -- one retry: the run it forked from, what it retried, and its own
  immutable copy of the materialization (`retry-creates-an-immutable-run-fork`).
  A retry's override is validated, and written into that copy, by
  `kraft.templates.retry.validate_retry_override`
  (`retry-overrides-are-policy-bounded`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, JsonValue

from kraft.templates.models import (
    PATH_SEPARATOR,
    ExecNode,
    GateNode,
    MaterializedChain,
    ResolvedNode,
    ResolvedStep,
    ResolvedTask,
)
from kraft.templates.retry import RetryOverride


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


class RunFork(BaseModel):
    """One retry of a work item: a new, immutable run that keeps every earlier
    run's data (`retry-creates-an-immutable-run-fork`).

    Lineage lives here: `parent` is the fork this one came from, `None` for the
    intake run. `materialized_chain` is the fork's own copy of the chain it
    runs, with its override applied; `after_seq` is the event it starts after,
    so everything up to it is the prior runs' record. `override` records what
    the retry changed -- its path, task configuration and scope policy -- and
    is None when it changed nothing. Frozen here and refused an UPDATE or a
    DELETE by the table (`db._RUN_FORK_TRIGGERS`).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    work_item_id: str
    parent: str | None
    scope: ControlScope
    path: str | None
    after_seq: int
    materialized_chain: str
    override: dict[str, JsonValue] | None = None

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
        intake snapshot when there is no parent. A validated `override` already
        carries the fork's copy, `chain` with the override written in at its
        path (`validate_retry_override`)."""
        copied = override.chain if override is not None else chain
        return cls(
            id=uuid.uuid4().hex,
            work_item_id=work_item_id,
            parent=parent.id if parent is not None else None,
            scope=target.scope if target is not None else ControlScope.WORK_ITEM,
            path=target.path if target is not None else None,
            after_seq=after_seq,
            materialized_chain=copied.to_json(),
            override=(
                {
                    "path": override.path,
                    "task_config": override.task_config,
                    "policy": (
                        override.policy.model_dump(mode="json", exclude_none=True)
                        if override.policy is not None
                        else None
                    ),
                }
                if override is not None
                else None
            ),
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
            json.dumps(self.override) if self.override is not None else None,
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
            override=json.loads(row["override"]) if row["override"] else None,
        )
