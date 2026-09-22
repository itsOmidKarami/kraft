"""Plan-driven chain revision (Kraft-oydes, Ruling 208).

A `revise_chain` agent reads the approved spec and plan and writes a
`chain_revision` artifact: a *change set* against the nodes that have not run
yet, never a whole new tail. The pre-V1 splice replaced the tail wholesale and
lost what the reviewer did not re-emit (`on_failure`, gates: Kraft-eod0,
Kraft-gnn1); a change set cannot drop what it does not name.

* `ChangeSet` -- the one strict shape the artifact's body holds (`parse`).
"""

from __future__ import annotations

import json
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from kraft.templates.environment import Identifier
from kraft.templates.models import first_error

#: The artifact kind a `revise_chain` task produces and a revision gate shows.
CHAIN_REVISION = "chain_revision"

_STRICT = ConfigDict(strict=True, extra="forbid")


class RevisionError(ValueError):
    """A chain revision that cannot be read, or cannot be applied: the reason is
    the message, written for the person at the gate."""


class _Change(BaseModel):
    model_config = _STRICT

    #: The line of the spec or plan that asks for this change. Required: a
    #: change with no stated evidence makes the gate decorative.
    evidence: StrictStr = Field(min_length=1)


class AddedTask(BaseModel):
    """A task of an added node: a library task by name, and nothing else, so a
    revision can only run work an administrator already wrote."""

    model_config = _STRICT

    id: Identifier
    extends: Identifier


class AddedStep(BaseModel):
    model_config = _STRICT

    id: Identifier
    tasks: list[AddedTask] = Field(min_length=1)


class AddedNode(BaseModel):
    """An execution node built from library components only: a library node
    (`extends`), or `tasks`/`steps` of library tasks."""

    model_config = _STRICT

    id: Identifier
    extends: Identifier | None = None
    tasks: list[AddedTask] | None = None
    steps: list[AddedStep] | None = None

    @model_validator(mode="after")
    def _one_shape(self) -> AddedNode:
        shapes = [n for n in ("extends", "tasks", "steps") if getattr(self, n) is not None]
        if len(shapes) != 1:
            raise ValueError(
                f"an added node declares exactly one of extends, tasks or steps, not {shapes}"
            )
        return self

    def authored(self) -> dict:
        """The node as a chain file would write it. Always `kind: exec`: a
        library gate extended here is refused as a kind change, so a revision
        never adds a gate either."""
        return {**self.model_dump(exclude_none=True), "kind": "exec"}


class Skip(_Change):
    node: Identifier


class Add(_Change):
    #: The node the added one runs right after: the revision's own gate, or a
    #: node after it.
    after: Identifier
    node: AddedNode


class Override(_Change):
    """What a revision may set at one canonical path: an agent task's `model`
    and `effort`, and the operational policy values -- bounded, when applied,
    by the administrator maxima and every enclosing scope's cap, exactly as a
    retry's override is. No safety field (tools, sandbox) and no harness: a
    revision decides how much effort the work gets, not what it may touch."""

    model: StrictStr | None = None
    effort: StrictStr | None = None
    timeout_minutes: StrictInt | None = None
    max_attempts: StrictInt | None = None
    time_cap_minutes: StrictInt | None = None
    total_time_cap_minutes: StrictInt | None = None
    token_budget: StrictInt | None = None
    budget_usd: StrictFloat | StrictInt | None = None

    @model_validator(mode="after")
    def _sets_something(self) -> Override:
        if not self.task_config() and not self.policy():
            raise ValueError("an override that sets nothing")
        return self

    def task_config(self) -> dict:
        return {k: v for k in ("model", "effort") if (v := getattr(self, k)) is not None}

    def policy(self) -> dict:
        fields = self.model_dump(exclude_none=True)
        return {k: v for k, v in fields.items() if k not in ("model", "effort", "evidence")}


class ChangeSet(BaseModel):
    """What a `chain_revision` artifact proposes. Empty -- a rationale and no
    change -- is the common, correct answer, and advances without a human."""

    model_config = _STRICT

    rationale: StrictStr = Field(min_length=1)
    skip: list[Skip] = Field(default_factory=list)
    add: list[Add] = Field(default_factory=list)
    #: Keyed by canonical path (`node` or `node.step.task`).
    overrides: dict[str, Override] = Field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.skip or self.add or self.overrides)


#: The whole body after the front matter: one ```json fence, only whitespace
#: around it. Anything the human should read goes inside, in `rationale` and
#: each change's `evidence`.
_BODY = re.compile(r"\A\s*```json\n(.*)\n```\s*\Z", re.DOTALL)


def _refuse_duplicates(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        twice = next(k for k in keys if keys.count(k) > 1)
        raise ValueError(f"key {twice!r} appears twice")
    return dict(pairs)


def parse(text: str) -> ChangeSet:
    """The change set in a `chain_revision` artifact, read strictly: the whole
    body is one fenced JSON object and nothing else, every key is known, and
    no key appears twice. Raises `RevisionError` naming what is wrong."""
    from kraft.index.ingest import split_front_matter

    _, body = split_front_matter(text)
    match = _BODY.match(body)
    if match is None:
        raise RevisionError("the body must be exactly one ```json fenced block and nothing else")
    try:
        data = json.loads(match[1], object_pairs_hook=_refuse_duplicates)
    except ValueError as exc:
        raise RevisionError(f"the change set is not JSON: {exc}") from exc
    try:
        return ChangeSet.model_validate(data)
    except ValidationError as exc:
        raise RevisionError(f"the change set is not valid: {first_error(exc)}") from exc
