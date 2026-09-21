"""A retry's policy-bounded override: the runtime shape of the first half of
`retry-overrides-are-policy-bounded`.

**Caller: Task 8b's retry route.** It hands `validate_retry_override` the item's
materialized chain, the canonical path it retries, and the operator's proposed
task configuration and policy override; it gets back the validated effective
override -- including the fork's copy of the chain with that override written
in -- or a `RetryOverrideError` naming the one field refused. Storing the fork
and running it are the route's; nothing here writes anything.

The override may change a task's configuration and any scope's policy. It may
not change the chain's structure, identifiers, order or task kinds, and it may
not exceed the policy bounds that already hold at `path`: it is layered on the
policy the scope runs under today, so a safety field only tightens, an
operational one stays within the administrator maximum, and every scope below
`path` must still resolve under the result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from kraft.policy import PolicyError, TaskPolicyOverride, TemplatePolicyOverride
from kraft.templates.models import (
    MAIN_STEP,
    PATH_SEPARATOR,
    Chain,
    MaterializedChain,
    ResolvedChain,
)

#: Task fields a retry cannot touch: identity and kind, the structure hanging
#: off a task, its fan-out, and its policy -- which has its own door, so it is
#: bounded as policy rather than slipped in as configuration.
_STRUCTURAL = frozenset({"id", "kind", "on_failure", "scope", "policy"})


class RetryOverrideError(ValueError):
    """A retry override refused, about exactly one `field`: `path`,
    `task_config`, `task_config.<name>`, or `policy.<name>`."""

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


@dataclass(frozen=True)
class RetryOverride:
    """The validated effective override for one retry."""

    path: str
    #: The task fields the retry sets, as validated. Empty for a node or step.
    task_config: dict[str, object]
    #: The scope's whole `policy:` after the retry -- what it declared, with
    #: the retry's fields applied (`deny_tools` accumulated) -- or None when
    #: neither sets one.
    policy: TaskPolicyOverride | None
    #: The fork's chain: `chain` with both written in at `path`. Same nodes,
    #: steps, tasks, paths and kinds; its policy resolves like any snapshot's.
    chain: MaterializedChain


def validate_retry_override(
    chain: MaterializedChain,
    path: str,
    *,
    task_config: dict[str, object] | None = None,
    policy: dict[str, object] | TaskPolicyOverride | None = None,
) -> RetryOverride:
    """Validate a retry's override at canonical `path` (`node`, `node.step` or
    `node.step.task`, handler and control-task paths included) against
    `chain`, and return the effective override. Raises `RetryOverrideError`.
    """
    task_config = dict(task_config or {})
    raw_policy = (
        policy.model_dump(exclude_none=True)
        if isinstance(policy, TaskPolicyOverride)
        else dict(policy or {})
    )
    try:
        current = chain.policy_at(path)
    except LookupError as exc:
        raise RetryOverrideError(str(exc), field="path") from exc

    authored = json.loads(chain.chain.chain.model_dump_json())
    kind, scope = _authored(authored, path)
    if kind == "main" and raw_policy:
        raise RetryOverrideError(
            f"{path!r} is the step a `tasks:` group resolves to and has no policy of its own; "
            "retry its node or one of its tasks",
            field="path",
        )
    if task_config and kind != "task":
        raise RetryOverrideError(
            f"task configuration applies to a task, and {path!r} is a {kind}", field="task_config"
        )
    for name in sorted(task_config):
        if name in _STRUCTURAL:
            raise RetryOverrideError(
                f"a retry cannot change a task's {name!r}; it keeps the chain's structure, "
                "identifiers, order and task kinds",
                field=f"task_config.{name}",
            )

    shape = TemplatePolicyOverride if kind == "node" else TaskPolicyOverride
    try:
        proposed = shape.model_validate(raw_policy) if raw_policy else None
    except ValidationError as exc:
        raise RetryOverrideError(_explain(exc), field=f"policy.{_where(exc)}") from exc
    if proposed is not None:
        try:
            current.apply_template_override(proposed)
        except PolicyError as exc:
            raise RetryOverrideError(str(exc), field=f"policy.{exc.field}") from exc
        merged = {**(scope.get("policy") or {}), **proposed.model_dump(exclude_none=True)}
        if proposed.deny_tools is not None:
            inherited = (scope.get("policy") or {}).get("deny_tools") or []
            merged["deny_tools"] = list(dict.fromkeys([*inherited, *proposed.deny_tools]))
        scope["policy"] = {k: v for k, v in merged.items() if v is not None}
    scope.update(task_config)

    try:
        patched = Chain.model_validate_json(json.dumps(authored))
    except ValidationError as exc:
        field = _where(exc)
        raise RetryOverrideError(
            _explain(exc), field=f"task_config.{field}" if task_config else f"policy.{field}"
        ) from exc
    resolved = ResolvedChain.from_chain(patched, steering=chain.chain.steering)
    try:
        resolved.check_scopes(chain.policy)
    except PolicyError as exc:
        field = (
            "task_config.harness"
            if exc.field == "allowed_harnesses" and "harness" in task_config
            else f"policy.{exc.field}"
        )
        raise RetryOverrideError(str(exc), field=field) from exc

    effective = scope.get("policy")
    return RetryOverride(
        path=path,
        task_config=task_config,
        policy=shape.model_validate(effective) if effective else None,
        chain=MaterializedChain(
            chain=resolved,
            target=chain.target,
            policy=chain.policy,
            repository_policies=chain.repository_policies,
        ),
    )


def _authored(chain: dict, path: str) -> tuple[str, dict]:
    """The authored mapping `path` names in `chain` (a chain's JSON form), and
    what it is: `node` (execution), `gate`, `step`, `main` (the step a `tasks:`
    group resolves to, which is the enclosing shape itself) or `task`. `path`
    already resolved (`MaterializedChain.policy_at`), so every lookup here
    finds what it looks for."""
    first, *rest = path.split(PATH_SEPARATOR)
    obj = next(n for n in chain["nodes"] if n["id"] == first)
    kind = "gate" if obj["kind"] == "gate" else "node"
    segments = iter(rest)
    for segment in segments:
        if kind == "gate":  # `auto_review`, the one slot a gate has
            obj, kind = obj[segment], "task"
        elif kind == "node" and segment in ("on_failure", "on_base_changed", "fix_loop"):
            obj = obj[segment]
            if segment == "on_base_changed":
                obj = obj[next(segments)]  # on_conflict
            elif segment == "fix_loop" and rest[-1] == "judge" and len(rest) == 2:
                obj, kind = obj["judge"], "task"
                break
            kind = "shape"
        elif kind == "node" and segment == "escalation":
            obj, kind = obj["escalation"], "task"
            next(segments)  # its own id
        elif kind in ("node", "shape"):
            if obj.get("steps") is not None:
                obj, kind = next(s for s in obj["steps"] if s["id"] == segment), "step"
            else:
                kind = "main" if segment == MAIN_STEP else kind
        elif segment == "on_failure":  # a step's or a task's recovery plan
            obj, kind = obj["on_failure"], "shape"
        else:  # a task, in a step or in a `tasks:` group
            obj, kind = next(t for t in obj["tasks"] if t["id"] == segment), "task"
    return kind, obj


def _where(exc: ValidationError) -> str:
    """The deepest named field in a pydantic error's location."""
    names = [str(p) for p in exc.errors()[0]["loc"] if isinstance(p, str)]
    return names[-1] if names else "?"


def _explain(exc: ValidationError) -> str:
    error = exc.errors()[0]
    return f"{error['msg']} (got {error.get('input')!r})"
