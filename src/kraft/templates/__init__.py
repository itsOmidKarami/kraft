from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, get_args

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from kraft import skill as _skill
from kraft.config import first_error
from kraft.paths import default_skills_dir
from kraft.worker import sandbox as _sandbox
from kraft.worker import steering as _steering

if TYPE_CHECKING:
    from kraft import harness

_VALID_KINDS = {"builtin", "agent", "subprocess", "forge"}
# Config files that share the templates directory but are not chain templates.
# One definition: `load_templates` skips them, and the registry save copies the
# templates around them. Without this, every settings file the UI writes would be
# read as a malformed template and show up as degraded health.
CONFIG_FILES = frozenset(
    {
        "registry.yaml",
        # The V1 reusable-component library (`kraft.templates.library`). It has
        # no top-level `id`, so a loader that scanned it would report the
        # shipped library as a malformed chain template. Its chains live in
        # `chains/`, which this glob does not descend into.
        "library.yaml",
        "policy.yaml",
        "repos.yaml",
        "access.yaml",
        "notify.yaml",
        "intake.yaml",
        "theme.yaml",
    }
)
CHAIN_REVIEW_GATE = "chain_finalized"


class _DictLike(BaseModel):
    """A model that reads like the dict it replaced -- `x.get("kind")`, `x["k"]`,
    `"k" in x`, `x == {...}` -- because dispatch, doctor, the routes and most
    tests were written against dicts. Only keys the file actually set count as
    present, so `x.get("steering", [])` still means "did the operator say"."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return dict(self.items()) == other
        return super().__eq__(other)

    __hash__ = None  # type: ignore[assignment]

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key) if key in self else default

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            raise KeyError(key)
        return getattr(self, key)

    def __contains__(self, key: object) -> bool:
        return key in self.model_fields_set

    def keys(self):
        return [k for k in [*type(self).model_fields, *(self.model_extra or {})] if k in self]

    def items(self):
        return [(k, getattr(self, k)) for k in self.keys()]


_ORDERED_REFS = ("reject_to", "rebase_bounce_to")


class ChainNodeIn(_DictLike):
    """A chain node as an operator writes it: `tasks` XOR `steps`, never both.
    Unknown keys ride along -- chain-review artifacts carry a few this layer
    has never read, and a round trip must not drop them."""

    model_config = ConfigDict(frozen=True, extra="allow")

    id: str
    tasks: list[str] | None = None
    steps: list[list[str]] | None = None
    gate_after: str | None = None
    fix_loop: Any = None
    on_failure: Any = None
    reject_to: Any = None
    rebase_bounce_to: Any = None
    auto_escalate: Any = None
    auto_escalate_stuck: Any = None
    auto_escalate_delay_s: Any = None

    @model_validator(mode="after")
    def _one_or_the_other(self) -> ChainNodeIn:
        if self.tasks is not None and self.steps is not None:
            raise ValueError(
                f"node {self.id!r} declares both 'steps' and 'tasks'; a node has one or "
                "the other -- 'tasks' is the one-group shorthand"
            )
        return self


class TemplateDocument(BaseModel):
    """The authored YAML document and its composition rules."""

    model_config = ConfigDict(extra="allow")

    id: str
    nodes: list[ChainNodeIn] | None = None
    extends: str | None = None
    remove: list[str] = Field(default_factory=list)
    insert_before: dict[str, list[ChainNodeIn]] = Field(default_factory=dict)
    insert_after: dict[str, list[ChainNodeIn]] = Field(default_factory=dict)

    @staticmethod
    def _without_redundant_tasks(node: Any) -> Any:
        if not isinstance(node, dict) or "tasks" not in node or "steps" not in node:
            return node
        steps = node["steps"]
        if not isinstance(steps, list):
            return node
        flattened = [task for group in steps if isinstance(group, list) for task in group]
        if node["tasks"] == [] or node["tasks"] == flattened:
            return {key: value for key, value in node.items() if key != "tasks"}
        return node

    @field_validator("nodes", mode="before")
    @classmethod
    def _canonicalize_root_nodes(cls, nodes: Any) -> Any:
        if isinstance(nodes, list):
            return [cls._without_redundant_tasks(node) for node in nodes]
        return nodes

    @field_validator("insert_before", "insert_after", mode="before")
    @classmethod
    def _canonicalize_inserted_nodes(cls, inserts: Any) -> Any:
        if isinstance(inserts, dict):
            return {
                anchor: [cls._without_redundant_tasks(node) for node in nodes]
                if isinstance(nodes, list)
                else nodes
                for anchor, nodes in inserts.items()
            }
        return inserts

    def resolve(
        self, documents: dict[str, TemplateDocument], *, visiting: frozenset[str] = frozenset()
    ) -> list[ChainNodeIn]:
        """Resolve inheritance into fresh node models for one template."""
        if self.extends is None:
            if not self.nodes:
                raise RegistryError(f"template {self.id!r}: 'nodes' must be a non-empty list")
            used = sorted(key for key in _COMPOSITION_KEYS if key in self.model_fields_set)
            if used:
                raise RegistryError(f"template {self.id!r}: {used} require 'extends'")
            return [node.model_copy(deep=True) for node in self.nodes]

        if "nodes" in self.model_fields_set:
            raise RegistryError(f"template {self.id!r}: cannot set both 'extends' and 'nodes'")
        if self.extends not in documents:
            raise RegistryError(f"template {self.id!r}: extends unknown template {self.extends!r}")
        if self.extends in visiting:
            raise RegistryError(f"template {self.id!r}: 'extends' cycle at {self.extends!r}")

        nodes = documents[self.extends].resolve(documents, visiting=visiting | {self.id})
        base_ids = {node.id for node in nodes}
        unknown_remove = sorted(set(self.remove) - base_ids)
        if unknown_remove:
            raise RegistryError(
                f"template {self.id!r}: 'remove' names unknown node id(s) {unknown_remove}"
            )
        nodes = [node for node in nodes if node.id not in self.remove]

        for name, inserts in (
            ("insert_before", self.insert_before),
            ("insert_after", self.insert_after),
        ):
            for anchor, extra in inserts.items():
                ids = [node.id for node in nodes]
                if anchor not in ids:
                    raise RegistryError(
                        f"template {self.id!r}: {name!r} names unknown anchor node id {anchor!r}"
                    )
                offset = ids.index(anchor) + (name == "insert_after")
                nodes[offset:offset] = [node.model_copy(deep=True) for node in extra]

        ids = [node.id for node in nodes]
        dupes = sorted({node_id for node_id in ids if ids.count(node_id) > 1})
        if dupes:
            raise RegistryError(f"template {self.id!r}: duplicate node id(s) {dupes}")
        return nodes


class ChainNode(ChainNodeIn):
    """A chain node as everything downstream reads it: `steps` and `tasks` both
    always set, in agreement.

    The two keys are one fact in two shapes, on purpose. Ordering has exactly
    one consumer (`executor.dispatch.measure_node`); the flat list has twenty,
    across the backend and the SPA. Emitting both means the ordering consumer
    reads `steps` and nothing else has to learn that groups exist -- a
    `steps`-only node would make `n.tasks ?? []` evaluate to `[]` all over the
    frontend, silently, and nesting inside `tasks` would make `n.tasks.length`
    report a group count as a task count.

    Normalizes on construction, so a call site cannot forget to. Unlike
    `ChainNodeIn` it accepts both keys when they agree -- that is what an
    already-normalized node (the chain-review splice re-validates a normalized
    tail) looks like.
    """

    steps: list[list[str]]
    tasks: list[str]
    carryover_fields: ClassVar[tuple[str, ...]] = tuple(
        field
        for field in ChainNodeIn.model_fields
        if field not in {"id", "tasks", "steps", "gate_after", "fix_loop"}
    )
    non_proposable_carryover_fields: ClassVar[tuple[str, ...]] = tuple(
        field for field in ChainNodeIn.model_fields if field.startswith("auto_escalate")
    )

    @classmethod
    def gate_names(cls, nodes: list[dict]) -> frozenset[str]:
        return frozenset(node["gate_after"] for node in nodes if node.get("gate_after"))

    @classmethod
    def strip_non_proposable_carryover_fields(cls, nodes: list[dict]) -> list[dict]:
        for node in nodes:
            for field in cls.non_proposable_carryover_fields:
                node.pop(field, None)
        return nodes

    @classmethod
    def carry_forward_fields(cls, old_nodes: list[dict], new_nodes: list[dict]) -> list[dict]:
        old_by_id = {
            node["id"]: node
            for node in old_nodes
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        for node in new_nodes:
            old = old_by_id.get(node.get("id")) or {}
            for field in cls.carryover_fields:
                if field not in node:
                    node[field] = old.get(field)
            old_steps = old.get("steps")
            if (
                old_steps
                and "steps" not in node
                and node.get("tasks") == [task for group in old_steps for task in group]
            ):
                node["steps"] = old_steps
                node.pop("tasks")
        return new_nodes

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("steps"):
            groups = [list(g) for g in data["steps"]]
        else:
            groups = [list(data.get("tasks") or [])]
        return {**data, "steps": groups, "tasks": [t for g in groups for t in g]}

    @model_validator(mode="after")
    def _one_or_the_other(self) -> ChainNode:
        return self

    @model_validator(mode="after")
    def _cross_document(self, info: ValidationInfo) -> ChainNode:
        """The two rules a shape cannot express, read from validation context:
        `registry` (every hook the node names must exist) and `preceding` (the
        ids `reject_to`/`rebase_bounce_to` may name -- the caller includes the
        node's own id). A key absent from the context skips its rule, so a
        node built by hand in a test validates as it always did."""
        ctx = info.context or {}
        registry = ctx.get("registry")
        if registry is not None:
            # A malformed `on_failure` fails its own shape check first; the
            # `isinstance` only keeps a bare string from being misread as a
            # list of one-character "hooks".
            named = [*self.tasks, *(self.on_failure if isinstance(self.on_failure, list) else [])]
            unknown = sorted({t for t in named if t not in registry.hooks})
            if unknown:
                raise PydanticCustomError(
                    "unknown_hooks",
                    "hook(s) {hooks} are not in the registry",
                    {"hooks": unknown},
                )
        preceding = ctx.get("preceding")
        if preceding is not None:
            for field in _ORDERED_REFS:
                ref = getattr(self, field)
                if ref is not None and (not isinstance(ref, str) or ref not in preceding):
                    raise PydanticCustomError(
                        "ordered_ref",
                        "{detail}",
                        {
                            "field": field,
                            "detail": (
                                f"node {self.id!r} {field!r} must name a node at or before it"
                            ),
                        },
                    )
        return self

    def materialized_dict(self) -> dict:
        """Return the stable chain-definition shape with fresh mutable values."""
        return with_steps(
            {
                "id": self.id,
                "tasks": list(self.tasks),
                "steps": [list(group) for group in self.steps],
                "gate_after": self.gate_after,
                "fix_loop": self.fix_loop,
                "on_failure": list(self.on_failure) if self.on_failure else None,
                "reject_to": self.reject_to,
                "rebase_bounce_to": self.rebase_bounce_to,
                "auto_escalate": self.auto_escalate,
                "auto_escalate_stuck": self.auto_escalate_stuck,
                "auto_escalate_delay_s": self.auto_escalate_delay_s,
            }
        )


# Compatibility imports for callers that still hold node dicts.
# Built-in template names retained for compatibility; gates are validated from
# each materialized chain, not against this set.
GATE_NAMES = frozenset(
    {"spec_approval", "plan_approval", "chain_finalized", "human_review_approval"}
)
NODE_CARRYOVER_FIELDS = ChainNode.carryover_fields
NON_PROPOSABLE_CARRYOVER_FIELDS = ChainNode.non_proposable_carryover_fields


def strip_non_proposable_carryover_fields(nodes: list[dict]) -> list[dict]:
    return ChainNode.strip_non_proposable_carryover_fields(nodes)


def with_steps(node: dict) -> dict:
    """`node` with both `steps` and `tasks` populated, whichever it was given.
    A thin wrapper over `ChainNode`, kept for the callers that hold dicts."""
    return ChainNode.model_validate(node).model_dump(exclude_unset=True)


#: The one hook the repo's own test scopes may replace the command of. The
#: legacy fallback in `with_inputs` is the only thing that still couples
#: behavior to a hook's *name*; every other binding says what it wants in
#: `inputs:`.
TEST_HOOK = "on.test.run"

#: What a binding may declare it is fed, and on which channel. A closed
#: vocabulary: `load_registry` rejects anything else at config load.
VALID_INPUTS = {
    "review_package": {"env"},
    "carried_findings": {"env"},
    "test_scopes": {"argv"},
}


def with_inputs(binding: dict, task_hook: str) -> dict:
    """The binding's resolved input table.

    An explicit `inputs:` is authoritative: exactly what is declared, nothing
    implied. Absent, today's hardcoded rules are reproduced, because
    `registry.yaml` is seeded once and never overwritten, so every existing
    install has an `on.test.run` with no `inputs:`; reading the table strictly
    would run the registry's command against every repo (Kraft-579/9wzy).

    The fallback is also the Kraft-ouoqx fix: a non-test subprocess hook
    resolves to `{}` and runs its own command. What it carries forward is the
    hook-*name* coupling; declaring `inputs:` is its remedy.
    """
    if "inputs" in binding:
        return binding["inputs"] or {}
    if binding.get("kind") == "subprocess" and task_hook == TEST_HOOK:
        return {"test_scopes": {"channel": "argv"}}
    return {}


def carry_forward_node_fields(old_nodes: list[dict], new_nodes: list[dict]) -> list[dict]:
    return ChainNode.carry_forward_fields(old_nodes, new_nodes)


#: What an intake attachment stands in for (Kraft-dgh). Keyed on the gate rather
#: than the node id: gate names are a validated closed vocabulary, node ids are
#: free text a custom template chooses.
ATTACHMENT_GATES = {"spec": "spec_approval", "plan": "plan_approval"}
#: An `artifact:` value becomes a path segment (`.engineering/<kind>s/<id>.md`),
#: so it is a bare lowercase identifier — not a path, not a pattern.
_ARTIFACT_KIND = re.compile(r"[a-z][a-z0-9_-]*")
#: The levels an agent override's `effort` may take when it is validated with
#: no harness in scope (a work item's own override, a node override) --
#: `load_registry`'s own per-binding `effort` check is against the named
#: harness's own `values:` instead (leak 10 in the design spec), since
#: `minimal` is real for codex and invalid for claude. Checked at config load
#: rather than at dispatch: a typo reaching the CLI fails the node *after* the
#: work item has already paid for a worktree and a session (Kraft-tff).
Effort = Literal["low", "medium", "high", "xhigh", "max"]
_EFFORT_LEVELS = set(get_args(Effort))


class BindingKind(StrEnum):
    BUILTIN = "builtin"
    AGENT = "agent"
    SUBPROCESS = "subprocess"
    FORGE = "forge"


class Capability(StrEnum):
    MODEL = "model"
    EFFORT = "effort"
    DENY_TOOLS = "deny_tools"
    ALLOWED_TOOLS = "allowed_tools"
    PERMISSION_MODE = "permission_mode"


class _Binding(_DictLike):
    """One `registry.yaml` hook binding. The value checks that need the world
    (a harness's declared capabilities, the steering and skill files, the forge
    handler set) still run in `load_registry`; what a kind *may contain* is
    here, so a new kind or key is one field, not an if/elif and a comment.

    See `_DictLike` for why it reads like a dict.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # `interactive`/`timeout`/`repos` are UI-facing rather than dispatch-facing
    # (02 §13): the registry round-trips them through Settings ahead of the
    # screen/executor that will read them.
    interactive: StrictBool | None = None
    timeout: Annotated[StrictInt | StrictFloat, Field(gt=0, allow_inf_nan=False)] | None = None
    repos: dict[str, dict] | None = None
    sandbox: Any = None
    #: Kind-agnostic on purpose (spec §4): the motivating repair hangs off
    #: `on.ci.poll`, which is `kind: forge`. Not an agent-only key, so not
    #: settable through `defaults.agent` either -- a repair silently inherited
    #: by every agent binding is the opposite of what a per-task repair is for.
    on_failure: Annotated[list[StrictStr], Field(min_length=1, strict=True)] | None = None

    @field_validator("interactive", "timeout", mode="before")
    @classmethod
    def _reject_explicit_null_values(cls, value, info):
        if value is None:
            raise ValueError(f"{info.field_name} may not be null")
        return value


class BuiltinBinding(_Binding):
    kind: Literal["builtin"]
    handler: StrictStr


class SubprocessBinding(_Binding):
    kind: Literal["subprocess"]
    command: Annotated[list[StrictStr], Field(strict=True)]
    inputs: dict[str, dict] | None = None


class ForgeBinding(_Binding):
    kind: Literal["forge"]
    handler: Literal["open_mr", "sync_mr", "ci_poll", "merge", "merge_watch"]
    backend: Literal["auto", "glab", "gh", "fake"]
    poll_timeout: Annotated[StrictInt | StrictFloat, Field(ge=0, allow_inf_nan=False)] | None = None
    poll_interval: Annotated[StrictInt | StrictFloat, Field(gt=0, allow_inf_nan=False)] | None = (
        None
    )

    @field_validator("poll_timeout", "poll_interval", mode="before")
    @classmethod
    def _reject_explicit_null_poll_values(cls, value, info):
        if value is None:
            raise ValueError(f"{info.field_name} must be a number")
        return value


class AgentBinding(_Binding):
    kind: Literal["agent"]
    command: StrictStr | None = None
    profile: str | None = None
    harness: str | None = None
    model: StrictStr | None = None
    escalate_model: StrictStr | None = None
    deny_tools: Annotated[list[StrictStr], Field(strict=True)] = Field(default_factory=list)
    steering: Annotated[list[StrictStr], Field(strict=True)] = Field(default_factory=list)
    skill: Any = None
    artifact: Annotated[StrictStr, Field(pattern=f"^{_ARTIFACT_KIND.pattern}$")] | None = None
    #: A validator in `load_registry`, not a `Literal`: its legal values come
    #: from the named harness's own `values:` (`minimal` is real for codex and
    #: invalid for claude).
    effort: Any = None
    allowed_tools: Annotated[list[StrictStr], Field(strict=True)] = Field(default_factory=list)
    permission_mode: Any = None

    @field_validator("command", "artifact", mode="before")
    @classmethod
    def _reject_explicit_null_agent_values(cls, value, info):
        if value is None:
            raise ValueError(f"{info.field_name} may not be null")
        return value

    # The harness-facing requirements belong to the binding model, while the
    # loader remains responsible for checking them against the live harness.
    required_capabilities: ClassVar[dict[str, Capability]] = {
        "model": Capability.MODEL,
        "escalate_model": Capability.MODEL,
        "effort": Capability.EFFORT,
        "deny_tools": Capability.DENY_TOOLS,
        "allowed_tools": Capability.ALLOWED_TOOLS,
        "permission_mode": Capability.PERMISSION_MODE,
    }


class AgentDefaults(BaseModel):
    """The optional agent fields shared by every agent binding."""

    model_config = ConfigDict(extra="forbid")

    interactive: StrictBool | None = None
    timeout: Annotated[StrictInt | StrictFloat, Field(gt=0, allow_inf_nan=False)] | None = None
    command: StrictStr | None = None
    profile: str | None = None
    harness: str | None = None
    model: StrictStr | None = None
    escalate_model: StrictStr | None = None
    deny_tools: Annotated[list[StrictStr], Field(strict=True)] | None = None
    steering: Annotated[list[StrictStr], Field(strict=True)] | None = None
    skill: Any = None
    artifact: Annotated[StrictStr, Field(pattern=f"^{_ARTIFACT_KIND.pattern}$")] | None = None
    effort: Any = None
    allowed_tools: Annotated[list[StrictStr], Field(strict=True)] | None = None
    permission_mode: Any = None

    @field_validator("interactive", "timeout", "command", "artifact", mode="before")
    @classmethod
    def _reject_explicit_null_values(cls, value, info):
        if value is None:
            raise ValueError(f"{info.field_name} may not be null")
        return value

    def merge(self, binding: AgentBinding | dict) -> dict:
        result = (
            binding.model_dump(exclude_unset=True)
            if isinstance(binding, BaseModel)
            else dict(binding)
        )
        list_keys = {"deny_tools", "steering", "allowed_tools"}
        for key in self.model_fields_set:
            value = getattr(self, key)
            if key in list_keys:
                own = result.get(key, [])
                merged = []
                for item in [*(value or []), *own]:
                    if item not in merged:
                        merged.append(item)
                result[key] = merged
            elif key not in result:
                result[key] = value
        return result


HookBinding = Annotated[
    BuiltinBinding | AgentBinding | SubprocessBinding | ForgeBinding,
    Field(discriminator="kind"),
]
_BINDING = TypeAdapter(HookBinding)
_BINDING_MODELS = {
    "builtin": BuiltinBinding,
    "agent": AgentBinding,
    "subprocess": SubprocessBinding,
    "forge": ForgeBinding,
}

#: The keys a `defaults.agent` block may set -- the agent-only ones, derived
#: from the model so this list and the defaults-merge pre-pass cannot drift. No
#: `_PERMISSION_MODES` beside it: the modes a binding may name are the named
#: harness's own `values:` (leak 9 in the design spec), since `yolo` is real
#: for gemini and invalid for claude.
_AGENT_ONLY_KEYS = frozenset(AgentDefaults.model_fields)
#: The `_AGENT_ONLY_KEYS` that merge as a list -- default's items first, then
#: the binding's own, deduped -- rather than binding-wins-or-not.
_AGENT_LIST_KEYS = frozenset({"deny_tools", "steering", "allowed_tools"})


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Registry:
    hooks: dict
    #: `hooks`, before the per-binding defaults (`harness: claude`, etc.) this
    #: loader normalises in -- what the settings API hands back so a GET/PUT
    #: round trip, and a `reload` that keeps the last good config, both echo
    #: exactly what was on disk rather than growing keys nobody wrote. `None`
    #: for a `Registry` built by hand (most tests): dispatch and doctor never
    #: read this field, only the settings routes do.
    raw: dict | None = None


@dataclass(frozen=True)
class Template:
    id: str
    nodes: list
    #: What the file actually says (`list[ChainNodeIn]`), for
    #: `GET /templates/{tid}`. The same job `Registry.raw` does for
    #: `GET /registry` -- a round trip must echo what was on disk rather than
    #: growing keys nobody wrote -- with a type instead of an untyped dict.
    #: `None` for a `Template` built by hand (most tests). Note
    #: `TemplateDocument.resolve` applies `extends`/`remove`/`insert_*` *before*
    #: this, so it is the resolved-but-not-normalized form.
    authored: list | None = None

    def materialize(
        self,
        *,
        satisfied_gates: frozenset[str] = frozenset(),
        skip_nodes: frozenset[str] = frozenset(),
    ) -> dict:
        return {
            "template_id": self.id,
            "nodes": [
                ChainNode.model_validate(node).materialized_dict()
                for node in self.nodes
                if node.get("gate_after") not in satisfied_gates and node["id"] not in skip_nodes
            ],
        }


@dataclass(frozen=True)
class TemplateSet:
    valid: dict
    invalid: dict


def _harnesses() -> harness.HarnessSet:
    # Function-local, same reason the old `_agent_profiles` was: a config
    # loader importing the adapter layer at module scope invites a cycle.
    from kraft import harness

    return harness.load(None)


#: Binding keys that name a capability, and so are checked against the
#: harness's own declaration. `escalate_model` is checked as `model`: it is
#: the same capability used on a different turn.
_CAPABILITY_KEYS = {
    key: capability.value for key, capability in AgentBinding.required_capabilities.items()
}


def _merge_agent_defaults(data: dict, path: Path) -> None:
    """Merge a registry's `defaults.agent` block into every `kind: agent`
    binding under `hooks`, in place, before `load_registry`'s own per-binding
    validation runs below -- so a typo in a default fails at load, the same
    as a typo in the binding itself already does. Scalars: the binding's own
    value wins over the default. Lists: the default's items first, then the
    binding's own, deduped.
    """
    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict) or (set(defaults) - {"agent"}):
        raise RegistryError(f"{path.name}: 'defaults' takes only an 'agent' key")
    agent_defaults = defaults.get("agent") or {}
    if not isinstance(agent_defaults, dict):
        raise RegistryError(f"{path.name}: 'defaults.agent' must be a mapping")
    try:
        defaults_model = AgentDefaults.model_validate(agent_defaults)
    except ValidationError as exc:
        extras = [e["loc"][-1] for e in exc.errors() if e["type"] == "extra_forbidden"]
        if extras:
            raise RegistryError(
                f"{path.name}: 'defaults.agent' has unknown key(s) {sorted(extras)}"
            ) from exc
        key = str(exc.errors()[0]["loc"][-1])
        raise RegistryError(f"{path.name}: 'defaults.agent' {key!r} is invalid") from exc
    for key in _AGENT_LIST_KEYS:
        if key in defaults_model.model_fields_set and getattr(defaults_model, key) is None:
            raise RegistryError(f"{path.name}: 'defaults.agent' {key!r} must be a list of strings")
    if not agent_defaults:
        return
    for hook, binding in data["hooks"].items():
        if not isinstance(binding, dict) or binding.get("kind") != "agent":
            continue
        for key, _dval in defaults_model.model_dump(exclude_unset=True).items():
            if (
                key in _AGENT_LIST_KEYS
                and key in binding
                and not (
                    isinstance(binding[key], list) and all(isinstance(x, str) for x in binding[key])
                )
            ):
                raise RegistryError(f"{path.name}: hook {hook!r} {key!r} must be a list of strings")
        try:
            binding.update(defaults_model.merge(binding))
        except (TypeError, ValueError) as exc:
            raise RegistryError(f"{path.name}: hook {hook!r} defaults could not be merged") from exc


def load_registry(
    path: str | Path,
    *,
    steering_dir: Path | None = None,
    skills_dir: Path | None = None,
    harnesses: harness.HarnessSet | None = None,
) -> Registry:
    path = Path(path)
    steering_dir = steering_dir if steering_dir is not None else path.parent / "steering"
    # Not `path.parent / "skills"`: a method file is shipped in the package and
    # only *overlaid* from $KRAFT_HOME, so the default is the home directory,
    # not a sibling of whichever registry file is being validated.
    skills_dir = skills_dir if skills_dir is not None else default_skills_dir()
    try:
        data = yaml.safe_load(path.read_text())
    # ValueError covers the UnicodeDecodeError `read_text()` raises on a file
    # that is not UTF-8: it is a ValueError, not an OSError.
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise RegistryError(f"{path.name}: cannot read/parse: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        raise RegistryError(f"{path.name}: expected a top-level 'hooks' mapping")
    # Snapshot before anything below normalises defaults into each binding in
    # place -- `defaults.agent` here, `harness: claude` in the loop. This is
    # what `raw` hands back untouched, and the registry PUT carries the
    # `defaults` block over from disk itself, so the round trip stays exact.
    raw = copy.deepcopy(data["hooks"])
    _merge_agent_defaults(data, path)
    for hook, binding in data["hooks"].items():
        if not isinstance(binding, dict) or "kind" not in binding:
            raise RegistryError(f"{path.name}: hook {hook!r} is missing 'kind'")
        kind = binding["kind"]
        if kind not in _VALID_KINDS:
            raise RegistryError(f"{path.name}: hook {hook!r} has unknown kind {kind!r}")
        if kind == "agent":
            try:
                _BINDING.validate_python(binding)
            except ValidationError as exc:
                err = exc.errors()[0]
                key = ".".join(str(x) for x in err["loc"][1:])
                if key == "command":
                    raise RegistryError(
                        f"{path.name}: agent hook {hook!r} 'command' must be a string "
                        "(it overrides argv[0] only)"
                    ) from exc
                raise RegistryError(f"{path.name}: hook {hook!r} {key!r}: {err['msg']}") from exc
        if kind == "forge":
            handler = binding.get("handler")
            for key in ("poll_timeout", "poll_interval"):
                if key not in binding:
                    continue
                if handler != "ci_poll":
                    raise RegistryError(
                        f"{path.name}: forge hook {hook!r} has {key!r}, which applies "
                        "only to a ci_poll handler"
                    )

        if kind == "agent":
            hs = harnesses if harnesses is not None else _harnesses()
            # `profile:` is the pre-harness spelling. Kept for one release so
            # an install that edited registry.yaml still loads; `harness:`
            # wins when both are present.
            hid = binding.get("harness") or binding.get("profile") or "claude"
            if hid in hs.invalid:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} names harness {hid!r}, which failed "
                    f"to load: {hs.invalid[hid]}"
                )
            if hid not in hs.valid:
                raise RegistryError(
                    f"{path.name}: hook {hook!r} has unknown harness {hid!r}; "
                    f"known: {sorted(hs.valid)}"
                )
            h = hs.valid[hid]
            # Normalised here so no consumer re-derives the default.
            binding["harness"] = hid
            try:
                _steering.validate(steering_dir, binding.get("steering", []), where=path.name)
            except _steering.SteeringError as exc:
                raise RegistryError(str(exc)) from exc
            if "skill" in binding:
                try:
                    _skill.validate(skills_dir, binding["skill"], where=path.name)
                except _skill.SkillError as exc:
                    raise RegistryError(str(exc)) from exc
            # Fail at load, no emulation: a binding naming a capability its
            # harness does not declare, or a value outside that capability's
            # own `values:`, is rejected here rather than at 3am.
            for key, capability in _CAPABILITY_KEYS.items():
                if key not in binding or binding[key] is None:
                    continue
                if not h.supports(capability):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} sets {key!r}, but harness "
                        f"{hid!r} ({h.path}) declares no {capability!r} capability"
                    )
                given = binding[key]
                values = given if isinstance(given, list) else [given]
                for one in values:
                    if not isinstance(one, str):
                        raise RegistryError(
                            f"{path.name}: hook {hook!r} {key!r} must be a string "
                            "or list of strings"
                        )
                    if not h.value_ok(capability, one):
                        raise RegistryError(
                            f"{path.name}: hook {hook!r} {key!r}={one!r} is not accepted "
                            f"by harness {hid!r} ({h.path})"
                        )

        if "timeout" in binding:
            if kind == "builtin":
                raise RegistryError(
                    f"{path.name}: hook {hook!r} is kind 'builtin'; 'timeout' applies only to "
                    "a subprocess, agent, or forge hook"
                )
        if "repos" in binding:
            repos_raw = binding["repos"]
            if not isinstance(repos_raw, dict):
                raise RegistryError(
                    f"{path.name}: hook {hook!r} 'repos' must be a mapping of repo path to override"
                )
            for repo_path, override in repos_raw.items():
                if not isinstance(override, dict) or "enabled" not in override:
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'repos'[{repo_path!r}] "
                        "needs at least 'enabled'"
                    )
                if not isinstance(override["enabled"], bool):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'repos'[{repo_path!r}] "
                        "'enabled' must be a boolean"
                    )
                cmd = override.get("command")
                if cmd is not None and not (
                    isinstance(cmd, str)
                    or (isinstance(cmd, list) and all(isinstance(x, str) for x in cmd))
                ):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} 'repos'[{repo_path!r}] 'command' must be "
                        "a string or list of strings"
                    )

        if "sandbox" in binding:
            if kind not in ("agent", "subprocess"):
                raise RegistryError(
                    f"{path.name}: hook {hook!r} is kind {kind!r}; 'sandbox' applies only to "
                    "a subprocess or agent hook"
                )
            try:
                _sandbox.validate(binding["sandbox"], where=f"{path.name}: hook {hook!r}")
            except _sandbox.SandboxError as exc:
                raise RegistryError(str(exc)) from exc

        inputs = binding.get("inputs")
        if inputs is not None:
            if not isinstance(inputs, dict):
                raise RegistryError(f"{path.name}: hook {hook!r} 'inputs' must be a mapping")
            for name, cfg in inputs.items():
                if name not in VALID_INPUTS:
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} declares unknown input {name!r}; "
                        f"known: {sorted(VALID_INPUTS)}"
                    )
                if not isinstance(cfg, dict):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} input {name!r} must be a mapping"
                    )
                channel = cfg.get("channel")
                if channel not in VALID_INPUTS[name]:
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} input {name!r} cannot use channel "
                        f"{channel!r}; supported: {sorted(VALID_INPUTS[name])}"
                    )
                if channel == "env" and not isinstance(cfg.get("name"), str):
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} input {name!r} on the env channel "
                        "needs a string 'name'"
                    )
        try:
            data["hooks"][hook] = _BINDING.validate_python(binding)
        except ValidationError as exc:
            extras = [e["loc"][-1] for e in exc.errors() if e["type"] == "extra_forbidden"]
            if extras:
                # A key nobody reads is a setting that silently does nothing --
                # a `deny_tool:` typo denies no tool and fails nowhere.
                agent_only = [k for k in extras if k in _AGENT_ONLY_KEYS]
                if agent_only and kind != "agent":
                    raise RegistryError(
                        f"{path.name}: hook {hook!r} is kind {kind!r}; {agent_only[0]!r} "
                        "applies only to an agent hook"
                    ) from exc
                takes = sorted({"kind", *_BINDING_MODELS[kind].model_fields})
                raise RegistryError(
                    f"{path.name}: hook {hook!r} has unknown key(s) {sorted(extras)}; "
                    f"a {kind} hook takes {takes}"
                ) from exc
            err = exc.errors()[0]
            key = ".".join(str(x) for x in err["loc"][1:])
            field = key.split(".")[0]
            if field == "handler" and kind == "forge":
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} has unknown handler "
                    f"{binding.get('handler')!r}"
                ) from exc
            if field == "backend" and kind == "forge":
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} has unknown backend "
                    f"{binding.get('backend')!r}"
                ) from exc
            if field == "poll_timeout":
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} poll_timeout must be a non-negative number"
                ) from exc
            if field == "poll_interval":
                raise RegistryError(
                    f"{path.name}: forge hook {hook!r} poll_interval must be a positive number"
                ) from exc
            if field == "timeout":
                raise RegistryError(
                    f"{path.name}: hook {hook!r} 'timeout' must be a positive number of minutes"
                ) from exc
            if field == "command" and kind == "agent":
                raise RegistryError(
                    f"{path.name}: agent hook {hook!r} 'command' must be a string "
                    "(it overrides argv[0] only)"
                ) from exc
            if field == "on_failure":
                raise RegistryError(
                    f"{path.name}: hook {hook!r} 'on_failure' must be a non-empty list of strings"
                ) from exc
            raise RegistryError(f"{path.name}: hook {hook!r} {key!r}: {err['msg']}") from exc
    # Binding-level `on_failure` (spec §4): a repair that travels with the task
    # rather than with whichever node happens to run it. Validated here, after
    # the per-binding loop rather than inside it, because a repair hook is
    # itself a hook in this same file -- the names it points at are only all
    # known once every binding has been read.
    for hook, binding in data["hooks"].items():
        repair = binding.get("on_failure")
        if repair is None:
            continue
        # A hook naming itself would dispatch its own repair, fail again, and
        # do it forever. The general cycle (a -> b -> a) is not checked: a
        # repair's own `on_failure` is never dispatched (`dispatch.measure_node`
        # runs one repair layer only), so self-reference is the only shape that
        # can actually loop.
        if hook in repair:
            raise RegistryError(
                f"{path.name}: hook {hook!r} names itself in 'on_failure'; "
                "a hook cannot be its own repair"
            )
        missing = sorted(set(repair) - set(data["hooks"]))
        if missing:
            raise RegistryError(
                f"{path.name}: hook {hook!r} 'on_failure' names unknown hook(s) {missing}"
            )

    return Registry(hooks=data["hooks"], raw=raw)


def validate_nodes(
    nodes: list, registry: Registry, *, preceding_ids: frozenset[str] = frozenset()
) -> list[str]:
    """The per-node rules a chain's node list is held to: shape, hook set
    a non-empty `gate_after`, and a `fix_loop` node needing at
    least one task. Takes a bare node list and a registry rather than a whole
    `Template` so the chain-review splice path (Kraft-hm0) can run the same
    checks over an agent's revised tail before it reaches `chain_definition`
    -- one rule set, two callers, not a second, drifting copy of it
    (Kraft-unk). `load_templates` below calls it once per template at load
    time; `kraft.api.routes.gates._splice_chain_review` calls it once per
    `revised_chain_nodes` payload at gate-approval time -- that caller only
    ever hands it the not-yet-run tail, so `preceding_ids` names the ids of
    already-run nodes (outside `nodes`) that still count as "at or before"
    for `reject_to`/`rebase_bounce_to` (Kraft-df4tc).

    Returns error strings, empty if valid. Stops at the first failing rule
    category rather than collecting every node's every problem -- the same
    one-message-at-a-time shape `load_templates` produced before this was
    factored out of it.
    """
    if not isinstance(nodes, list) or not all(
        isinstance(n, dict)
        and isinstance(n.get("id"), str)
        and (
            (isinstance(n.get("tasks"), list) and all(isinstance(t, str) for t in n["tasks"]))
            or n.get("steps") is not None
        )
        for n in nodes
    ):
        return ["each node needs a string 'id' and a list-of-strings 'tasks' (or 'steps')"]

    # A node already run through `with_steps` (the splice path re-validates a
    # normalized tail) carries both keys in agreement, not in conflict -- only
    # a `tasks` that diverges from the flattened `steps` means an author
    # actually wrote both.
    both = next(
        (
            n["id"]
            for n in nodes
            if n.get("steps")
            and n.get("tasks")
            and isinstance(n["steps"], list)
            and n["tasks"] != [t for g in n["steps"] if isinstance(g, list) for t in g]
        ),
        None,
    )
    if both is not None:
        return [
            f"node {both!r} declares both 'steps' and 'tasks'; a node has one or "
            "the other -- 'tasks' is the one-group shorthand"
        ]
    bad_steps = next(
        (
            n["id"]
            for n in nodes
            if "steps" in n
            and n["steps"] is not None
            and (
                not isinstance(n["steps"], list)
                or not n["steps"]
                or not all(
                    isinstance(g, list) and g and all(isinstance(t, str) for t in g)
                    for g in n["steps"]
                )
            )
        ),
        None,
    )
    if bad_steps is not None:
        return [
            f"node {bad_steps!r} 'steps' must be a non-empty list of non-empty lists of strings"
        ]

    # One code path sees one shape from here on: a `steps` node's hooks reach
    # the unknown-hook scan below via its normalized `tasks`, same as a plain
    # `tasks` node's.
    nodes = [with_steps(n) for n in nodes]

    bad_gates = sorted(
        repr(n.get("gate_after"))
        for n in nodes
        if n.get("gate_after") is not None
        and (not isinstance(n.get("gate_after"), str) or not n["gate_after"])
    )
    if bad_gates:
        return [f"gate_after must be a non-empty string, got {bad_gates}"]

    empty_loop = next((n["id"] for n in nodes if n.get("fix_loop") and not n["tasks"]), None)
    if empty_loop is not None:
        return [f"node {empty_loop!r} has 'fix_loop' but no tasks to measure"]

    # on_failure (Kraft-df4tc point 1) is reviewer-settable but only ever
    # meant as a non-empty list of hook names -- a bare string ("a.b.c")
    # is iterable too and would otherwise sail through the unknown-hook scan
    # below (which only looks inside a list) and reach walk.py's
    # `list(node["on_failure"])`, which yields the string's characters as
    # hook names.
    bad_on_failure = next(
        (
            n["id"]
            for n in nodes
            if "on_failure" in n
            and n["on_failure"] is not None
            and (
                not isinstance(n["on_failure"], list)
                or not n["on_failure"]
                or not all(isinstance(t, str) for t in n["on_failure"])
            )
        ),
        None,
    )
    if bad_on_failure is not None:
        return [f"node {bad_on_failure!r} 'on_failure' must be a non-empty list of strings"]

    # The registry and ordering rules live on `ChainNode`; each node is checked
    # with the ids it may point back at, and the results are folded into the
    # same one-message shape this function has always returned.
    ids = [n["id"] for n in nodes]
    unknown: set[str] = set()
    misordered: list[tuple[int, int, str]] = []
    for i, n in enumerate(nodes):
        context = {"registry": registry, "preceding": {*preceding_ids, *ids[: i + 1]}}
        try:
            ChainNode.model_validate(n, context=context)
        except ValidationError as exc:
            for err in exc.errors():
                if err["type"] == "unknown_hooks":
                    unknown.update(err["ctx"]["hooks"])
                elif err["type"] == "ordered_ref":
                    misordered.append((_ORDERED_REFS.index(err["ctx"]["field"]), i, err["msg"]))
                else:
                    return [first_error(exc, "node")]
    if unknown:
        return [f"hook(s) {sorted(unknown)} are not in the registry"]
    if misordered:
        return [min(misordered)[2]]

    return []


class ModelEffortOverride(BaseModel):
    """The reusable model/effort override shape. Defaults are deliberately
    omitted by callers with `exclude_unset=True`, so `None` means an explicit
    reset for the model fields rather than an override that was not supplied."""

    model_config = ConfigDict(extra="forbid")

    model: StrictStr | None = None
    escalate_model: StrictStr | None = None
    effort: Effort = Field(default=None)


class AgentOverride(ModelEffortOverride):
    pass


class ProposedNodeOverride(ModelEffortOverride):
    pass


class NodeOverride(ModelEffortOverride):
    auto_escalate: StrictBool = Field(default=None)
    auto_escalate_stuck: StrictBool = Field(default=None)
    auto_escalate_delay_s: Annotated[StrictInt, Field(ge=0)] = Field(default=None)
    attempts: Annotated[StrictInt, Field(ge=1)] = Field(default=None)
    wall_clock_s: Annotated[StrictInt, Field(ge=1)] = Field(default=None)


def _override_errors(
    schema: type[BaseModel], values: object, *, unknown: str, object_name: str | None = None
) -> list[str]:
    """Translate typed-schema errors into the terse, caller-owned API errors."""
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


def validate_model_effort_fields(overrides: dict) -> list[str]:
    return _override_errors(ModelEffortOverride, overrides, unknown="cannot override {extras}")


#: The only fields a chain-review artifact's `proposed_node_overrides` may
#: set (Kraft-df4tc point 5) -- a strict subset of `OVERRIDABLE_NODE_FIELDS`.
#: A human can dial `auto_escalate`/`attempts`/`wall_clock_s` through the
#: PATCH route; an agent's chain-review proposal never can, no matter what it
#: invents -- the chain-review skill text promises exactly that.
PROPOSABLE_NODE_OVERRIDE_FIELDS = frozenset({"model", "escalate_model", "effort"})


def validate_proposed_node_overrides(proposed: dict) -> list[str]:
    """`proposed_node_overrides` on one node of a chain-review artifact
    (`kraft.api.routes.gates._splice_chain_review`): keys restricted to
    `PROPOSABLE_NODE_OVERRIDE_FIELDS`, on top of `validate_model_effort_fields`'s
    type checks. Anything else -- `auto_escalate` included -- is never the
    reviewer's to set (Kraft-df4tc).
    """
    return _override_errors(ProposedNodeOverride, proposed, unknown="cannot propose {extras}")


def validate_node_override_fields(fields: dict) -> list[str]:
    """Everything a `node_overrides`/`proposed_node_overrides` patch for one
    node must hold to: keys inside `kraft.store.OVERRIDABLE_NODE_FIELDS` only,
    each with the right shape. One rule set for the three callers that accept
    such a patch from outside the chain-definition schema --
    `kraft.api.routes.work_items.create_work_item` (POST),
    `_validate_node_overrides` (PATCH), and
    `kraft.api.routes.gates._splice_chain_review` (a chain-review artifact) --
    so a field none of them meant to allow (`auto_escalate`, `attempts`, ...)
    can't reach `store.set_node_overrides` through whichever caller forgets to
    check it (Kraft-df4tc).

    Returns error strings with no node-id prefix -- each caller's own message
    already carries that context.
    """
    return _override_errors(NodeOverride, fields, unknown="cannot override {extras}")


def validate_agent_overrides(overrides: dict) -> list[str]:
    """The rules a work item's own model/effort override (Kraft-4k6l) is held
    to -- exactly what `load_registry` above already checks for an agent
    hook's own `model`/`escalate_model`/`effort`: keys are a subset of
    `{"model", "escalate_model", "effort"}`; `model` and `escalate_model` are
    strings or `None`; `effort` is one of `_EFFORT_LEVELS`. One rule set, one
    exported function -- `kraft.api.routes.work_items` calls this rather than reaching for the
    private `_EFFORT_LEVELS` itself, the same "validate in one place"
    reasoning `validate_nodes` gives for its own two callers, so a work item's
    override and a template's binding cannot drift into two different ideas
    of a valid `effort` (Kraft-unk).

    Returns error strings, empty if valid.
    """
    errors = _override_errors(
        AgentOverride,
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


#: The keys `TemplateDocument.resolve` treats as composition -- meaningless,
#: and rejected, on a template that does not `extends` (spec §2's "Hard load
#: errors" posture: a key that silently does nothing is worse than a
#: rejected one).
_COMPOSITION_KEYS = ("remove", "insert_before", "insert_after")


def load_templates(dir: str | Path, registry: Registry) -> TemplateSet:
    valid: dict[str, Template] = {}
    invalid: dict[str, str] = {}
    documents: dict[str, TemplateDocument] = {}

    for path in sorted(Path(dir).glob("*.yaml")):
        if path.name in CONFIG_FILES:
            continue
        stem = path.stem
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, ValueError, yaml.YAMLError) as exc:
            invalid[stem] = f"{path.name}: cannot read/parse: {exc}"
            continue

        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            invalid[stem] = f"{path.name}: missing a string 'id'"
            continue
        tid = data["id"]
        if tid in documents or tid in invalid:
            invalid[stem] = f"{path.name}: duplicate template id {tid!r}"
            continue
        try:
            documents[tid] = TemplateDocument.model_validate(data)
        except ValidationError as exc:
            invalid[tid] = first_error(exc, f"template {tid!r}")

    for tid, document in documents.items():
        try:
            authored = document.resolve(documents)
        except RegistryError as exc:
            invalid[tid] = str(exc)
            continue

        nodes = [node.model_dump(exclude_unset=True) for node in authored]

        node_errors = validate_nodes(nodes, registry)
        if node_errors:
            invalid[tid] = f"template {tid!r}: {node_errors[0]}"
            continue
        # A file carrying both keys in agreement loads (it always did); its
        # authored form is the `steps` one.
        authored = [
            ChainNodeIn.model_validate(
                {k: v for k, v in node.items() if not (k == "tasks" and node.get("steps"))}
            )
            for node in nodes
        ]
        # `validate_nodes` normalizes its own local copy to check a `steps`
        # node's shape; `Template.nodes` needs that same normalization; a
        # `steps`-only node otherwise reaches `materialize` (and every test
        # or caller reading `n["tasks"]` directly off `Template.nodes`) with
        # no `tasks` key at all.
        nodes = [ChainNode.model_validate(n) for n in nodes]

        bad_loop = next(
            (
                n["id"]
                for n in nodes
                if "fix_loop" in n
                and n["fix_loop"] is not None
                and not (isinstance(n["fix_loop"], str) and n["fix_loop"])
            ),
            None,
        )
        if bad_loop is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_loop!r} 'fix_loop' must be a non-empty string or null"
            )
            continue

        # The step between "a task in this node failed" and needs_human
        # (Kraft-rv6i): somewhere to put a blocker that is not the code -- a
        # merge request missing a label its pipeline requires -- and then let
        # the node measure itself again.
        bad_recover = next(
            (
                n["id"]
                for n in nodes
                if "on_failure" in n
                and n["on_failure"] is not None
                and not (
                    isinstance(n["on_failure"], list)
                    and n["on_failure"]
                    and all(isinstance(t, str) for t in n["on_failure"])
                )
            ),
            None,
        )
        if bad_recover is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_recover!r} 'on_failure' must be a "
                f"non-empty list of strings or null"
            )
            continue

        # Which gates an agent may review before a human sees them (Kraft-zr3s).
        # Only meaningful beside a gate: on a gateless node it would review
        # nothing, and a config key that silently does nothing is worse than a
        # rejected one.
        bad_auto = next(
            (
                n["id"]
                for n in nodes
                if n.get("auto_escalate") is not None
                and (not isinstance(n["auto_escalate"], bool) or not n.get("gate_after"))
            ),
            None,
        )
        if bad_auto is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_auto!r} 'auto_escalate' must be a bool "
                f"on a node that declares a 'gate_after'"
            )
            continue

        # Unlike auto_escalate, meaningful on every node -- a needs_human stop
        # has nothing to do with which node it happened on being a gate.
        bad_aes = next(
            (
                n["id"]
                for n in nodes
                if n.get("auto_escalate_stuck") is not None
                and not isinstance(n["auto_escalate_stuck"], bool)
            ),
            None,
        )
        if bad_aes is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_aes!r} 'auto_escalate_stuck' must be a bool"
            )
            continue

        # Same "meaningful everywhere" posture as auto_escalate_stuck: a
        # delay is a wait before whichever mechanism this node's gate or stop
        # already uses, not itself gate-specific.
        bad_delay = next(
            (
                n["id"]
                for n in nodes
                if n.get("auto_escalate_delay_s") is not None
                and (
                    not isinstance(n["auto_escalate_delay_s"], int)
                    or isinstance(n["auto_escalate_delay_s"], bool)
                    or n["auto_escalate_delay_s"] < 0
                )
            ),
            None,
        )
        if bad_delay is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_delay!r} 'auto_escalate_delay_s' "
                f"must be a non-negative int"
            )
            continue

        valid[tid] = Template(id=tid, nodes=nodes, authored=authored)

    return TemplateSet(valid=valid, invalid=invalid)


def materialize(
    template: Template,
    *,
    satisfied_gates: frozenset[str] = frozenset(),
    skip_nodes: frozenset[str] = frozenset(),
) -> dict:
    """The template's nodes, minus any whose gate an intake attachment already
    satisfies — the item genuinely has no spec node, rather than one skipped at
    runtime (Kraft-dgh) — and minus any in `skip_nodes` (UI v2 · 04 point 6):
    the intake-time click-to-skip, same "genuinely not in the chain" posture,
    including a gated node (the design strikes `spec` through in 10/m09 even
    though it gates) -- a human choosing this at intake is the same trust an
    attachment trim already gets, so a gate skipped this way is not a gate
    bypassed at runtime, it never existed for this item."""
    return template.materialize(satisfied_gates=satisfied_gates, skip_nodes=skip_nodes)
