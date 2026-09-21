"""The shapes an operator's per-item override is held to: a work item's own
model/effort override (`agent_overrides`) and a per-node override
(`node_overrides`). Each route that accepts one validates it here, so two doors
cannot drift into two ideas of a valid field."""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

#: The levels an override's `effort` may take. Checked at the API rather than at
#: dispatch: a typo reaching the CLI fails the node *after* the work item has
#: already paid for a worktree and a session (Kraft-tff).
Effort = Literal["low", "medium", "high", "xhigh", "max"]
_EFFORT_LEVELS = set(get_args(Effort))


class _ModelEffortOverride(BaseModel):
    """`None` means an explicit reset for the model fields: callers dump with
    `exclude_unset=True`, so an override that was not supplied is absent."""

    model_config = ConfigDict(extra="forbid")

    model: StrictStr | None = None
    escalate_model: StrictStr | None = None
    effort: Effort = Field(default=None)


class _NodeOverride(_ModelEffortOverride):
    auto_escalate: StrictBool = Field(default=None)
    auto_escalate_stuck: StrictBool = Field(default=None)
    auto_escalate_delay_s: Annotated[StrictInt, Field(ge=0)] = Field(default=None)
    attempts: Annotated[StrictInt, Field(ge=1)] = Field(default=None)
    wall_clock_s: Annotated[StrictInt, Field(ge=1)] = Field(default=None)


def _errors(
    schema: type[BaseModel], values: object, *, unknown: str, object_name: str | None = None
) -> list[str]:
    """Typed-schema errors as the terse API errors each caller returns."""
    try:
        schema.model_validate(values)
    except ValidationError as exc:
        errors = exc.errors()
        extras = sorted(
            str(error["loc"][0]) for error in errors if error["type"] == "extra_forbidden"
        )
        if extras:
            return [unknown.format(extras=extras)]
        if not isinstance(values, dict):
            return [f"{object_name} must be an object" if object_name else "must be an object"]
        error = errors[0]
        field = str(error["loc"][0])
        if field in {"model", "escalate_model"}:
            return [f"{field!r} must be a string or null"]
        if field == "effort":
            return [f"'effort' must be one of {sorted(_EFFORT_LEVELS)}; got {values[field]!r}"]
        if field in {"auto_escalate", "auto_escalate_stuck"}:
            return [f"{field} must be a boolean"]
        if field == "auto_escalate_delay_s":
            return ["auto_escalate_delay_s must be a non-negative int"]
        return [f"{field} must be a positive int"]
    return []


def validate_node_override_fields(fields: dict) -> list[str]:
    """One node's `node_overrides` patch: keys inside
    `kraft.store.OVERRIDABLE_NODE_FIELDS` only, each with the right shape. Both
    doors that accept one -- intake (POST) and the PATCH route -- call this, so a
    field neither meant to allow cannot reach `store.set_node_overrides` through
    whichever forgets to check (Kraft-df4tc). Errors carry no node-id prefix;
    each caller's own message already names the node."""
    return _errors(_NodeOverride, fields, unknown="cannot override {extras}")


def validate_agent_overrides(overrides: dict) -> list[str]:
    """A work item's own model/effort override (Kraft-4k6l): keys a subset of
    `{"model", "escalate_model", "effort"}`, `model`/`escalate_model` strings or
    `None`, `effort` one of `Effort`. Returns error strings, empty if valid."""
    errors = _errors(
        _ModelEffortOverride,
        overrides,
        unknown=(
            "agent_overrides has unknown key(s) {extras}; only model, "
            "escalate_model, effort are allowed"
        ),
        object_name="agent_overrides",
    )
    return [
        f"agent_overrides {error}" if not error.startswith("agent_overrides") else error
        for error in errors
    ]
