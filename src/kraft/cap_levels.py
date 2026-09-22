"""The cap fields, and their per-level defaults and maxima (Ruling 211).

Apart from `kraft.policy` only to keep that module's size in check; it
re-exports every name here, and is where they are read."""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationInfo,
    model_validator,
)

#: Field constraints live on the type, so a `Cap` cannot be built invalid --
#: not by `load_policy`, and not by a caller constructing one directly.
PositiveInt = Annotated[StrictInt, Field(gt=0)]

#: Per-scope time caps (Rulings 194-196): each scope that sets one caps its own
#: elapsed time -- `time_cap_minutes` its running time, `total_time_cap_minutes`
#: its wall clock less a manual pause (`kraft.caps`). A ratchet: a layer may
#: only lower what it inherits, never raise it, and never past `maxima`.
CAP_FIELDS = ("time_cap_minutes", "total_time_cap_minutes")
#: Per-scope spend caps (Ruling 195): each scope that sets one caps the spend
#: of the launches inside it -- `token_budget` its tokens, `budget_usd` its
#: dollars (`kraft.caps.budget_breach`). The same ratchet as a time cap.
BUDGET_FIELDS = ("token_budget", "budget_usd")
#: Every field a scope's own cap is checked on against its parent's, naming
#: both (`ResolvedChain._check_caps`).
SCOPE_CAP_FIELDS = (*CAP_FIELDS, *BUDGET_FIELDS)
#: A dollar figure: an int in YAML is a legal amount.
PositiveUsd = Annotated[StrictFloat | StrictInt, Field(gt=0)]


#: The kinds of scope a cap default or maximum is set for (Ruling 211),
#: broadest first: the work item (its chain), every node (a gate is one),
#: every step, every task.
CAP_LEVELS = ("work_item", "nodes", "steps", "tasks")
#: The level a scope kind (`ResolvedChain.cap_scopes`) reads its caps at.
LEVEL_OF = {"node": "nodes", "gate": "nodes", "step": "steps", "task": "tasks"}


class CapValues(BaseModel):
    """One level's caps in `defaults:` or `maxima:` (Ruling 211)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    time_cap_minutes: PositiveInt | None = None
    total_time_cap_minutes: PositiveInt | None = None
    token_budget: PositiveInt | None = None
    budget_usd: PositiveUsd | None = None


class CapLevels(BaseModel):
    """The four cap fields (`SCOPE_CAP_FIELDS`), per level of scope (Ruling
    211): `work_item`, `nodes`, `steps`, `tasks`. A narrower level's value
    may not exceed a broader one's, and the refusal names both."""

    model_config = ConfigDict(strict=True, extra="forbid")

    #: The `policy.yaml` section this is, for a refusal to name.
    section: ClassVar[str] = "caps"

    work_item: CapValues = Field(default_factory=CapValues)
    nodes: CapValues = Field(default_factory=CapValues)
    steps: CapValues = Field(default_factory=CapValues)
    tasks: CapValues = Field(default_factory=CapValues)

    @model_validator(mode="before")
    @classmethod
    def _caps_are_per_level(cls, data: object, info: ValidationInfo) -> object:
        """A flat cap (`defaults: time_cap_minutes: 30`) is refused: the form
        was rc-only (Rulings 195-198) and never shipped in a release. A
        snapshot frozen by an rc still reads: its flat maximum bounded every
        scope, which a `work_item` maximum does now (`nearest`)."""
        if not isinstance(data, dict):
            return data
        flat = [n for n in SCOPE_CAP_FIELDS if n in data]
        if not flat:
            return data
        if not (info.context or {}).get("frozen"):
            s, n = cls.section, flat[0]
            raise ValueError(
                f"{s}.{n} is set per level since Ruling 211: use {s}.tasks.{n} "
                "(or work_item, nodes, steps)"
            )
        data = dict(data)
        work_item = dict(data.get("work_item") or {})
        for name in flat:
            if (value := data.pop(name)) is not None:
                work_item.setdefault(name, value)
        return {**data, "work_item": work_item}

    @model_validator(mode="after")
    def _a_level_within_the_broader_ones(self) -> CapLevels:
        for name in SCOPE_CAP_FIELDS:
            above: tuple[str, float] | None = None
            for level in CAP_LEVELS:
                value = getattr(getattr(self, level), name)
                if value is None:
                    continue
                if above is not None and value > above[1]:
                    s = self.section
                    raise ValueError(
                        f"{s}.{level}.{name} {value} exceeds {s}.{above[0]}.{name} {above[1]}: "
                        "a narrower level's cap cannot exceed a broader one's (Ruling 211)"
                    )
                above = (level, value)
        return self

    def nearest(self, level: str, name: str) -> tuple[str, float] | None:
        """`(level, value)` of `name` set at `level` or the nearest broader
        level, or None. What bounds a level as a maximum: a broader level's
        maximum bounds every scope under it."""
        for at in reversed(CAP_LEVELS[: CAP_LEVELS.index(level) + 1]):
            value = getattr(getattr(self, at), name)
            if value is not None:
                return at, value
        return None
