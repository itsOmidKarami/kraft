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
    #: Appended to every agent task this node dispatches (Kraft-a7ers), never
    #: in place of the task's own prompt: `extra_prompt_note`.
    extra_prompt: StrictStr | None = None


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
        if field in {"model", "escalate_model", "extra_prompt"}:
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


def extra_prompt_note(text: str | None) -> str:
    """A node override's `extra_prompt` as it follows a task's instruction:
    after the task's own prompt and brief, so it adds to the task rather than
    replacing it. Empty when there is none."""
    if not text:
        return ""
    return f"\n\nThe operator added this note for this node, on top of the task above:\n\n{text}"


def harness_refusal(node, fields: dict) -> str | None:
    """Why the harness one of `node`'s agent tasks launches on would refuse
    this override's model/effort, or None (Kraft-a7ers): said at the override,
    not after the item has paid for a worktree. Every task `dispatch_node` runs
    for the node, so not its gate's `auto_review`, which takes item-wide
    overrides only (`gate_review`). A profile that cannot be resolved is left
    to the launch, which already stops on it in its own words."""
    from kraft import harness as _harness
    from kraft.adapters import agent as _agent
    from kraft.templates.models import AgentTask

    asked = {k: fields[k] for k in ("model", "escalate_model", "effort") if fields.get(k)}
    if not asked:
        return None
    harnesses = _harness.load(None)
    for resolved in node.tasks():
        if resolved is node.auto_review or not isinstance(resolved.task, AgentTask):
            continue
        try:
            h = harnesses.valid[_agent.harness_profile(resolved.task.harness, harnesses).provider]
        except _agent.HarnessUnavailable, KeyError:
            continue
        for key, value in asked.items():
            capability = "model" if key == "escalate_model" else key
            if not (h.supports(capability) and h.value_ok(capability, value)):
                return f"{key!r} {value!r} is refused by harness {h.id!r} ({resolved.path})"
    return None


def item_harness_refusal(nodes: dict, fields: dict) -> str | None:
    """Why some agent task across the item's whole materialized chain would
    refuse this item-wide `agent_overrides`' model/effort (Kraft-1qlgc): the
    same per-node check (`harness_refusal`) `node_overrides` already gets,
    run over every node instead of one, since an item-wide override reaches
    every agent task the chain dispatches. Reuses `harness_refusal` rather
    than duplicating its harness-resolution and value-checking; a node whose
    harness cannot be resolved yet (the launch-time fallback list) is skipped
    by that function already, left to the launch, same as the per-node door.
    `nodes` maps node id to `ResolvedNode`, as `_check_node_overrides` builds
    it. Errors carry the node id, since the field alone does not say which
    task refused it."""
    for node_id, node in nodes.items():
        if (why := harness_refusal(node, fields)) is not None:
            return f"node {node_id!r}: {why}"
    return None
