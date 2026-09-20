from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
)
from pydantic.dataclasses import dataclass as model

from kraft.findings import SEVERITIES


class PolicyError(Exception):
    pass


DEFAULT_LOOP_SEVERITIES = frozenset({"critical", "important"})
DEFAULT_AUTO_ESCALATE_STUCK_CAP = 3


#: Field constraints live on the type, so a `Cap` cannot be built invalid --
#: not by `load_policy`, and not by a caller constructing one directly.
PositiveInt = Annotated[StrictInt, Field(gt=0)]


@model(frozen=True)
class Cap:
    attempts: PositiveInt
    wall_clock_s: PositiveInt
    #: Fix cycles past this one launch on the hook's `escalate_model` instead of
    #: its `model` (sub-project G spec 6). `None` is "never escalate", which is
    #: the behaviour of every policy.yaml written before this existed.
    escalate_after: PositiveInt | None = None


@dataclass(frozen=True)
class Trigger:
    """One policy.yaml `triggers:` entry -- a cron schedule that files a
    paused work item (Kraft-859). Never resumes it: an agent cannot start
    work here any more than it can from manual intake."""

    cron: str
    repo: str
    chain: str
    title: str
    description: str = ""


@model(frozen=True)
class Budget:
    """Spend caps, in dollars. `None` is "no cap", never "zero".

    Not a field on `Cap`: a `Cap` is per-loop and is snapshotted per
    `(work_item_id, key)` row in `retry_counters`, while a budget is per work
    item and per day and spans every loop in the chain.
    """

    work_item_usd: Annotated[StrictFloat | StrictInt, Field(ge=0)] | None = None
    daily_usd: Annotated[StrictFloat | StrictInt, Field(ge=0)] | None = None

    def __post_init__(self) -> None:
        # An int in the YAML is a legal dollar figure and becomes a float.
        for name in ("work_item_usd", "daily_usd"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, float(v))


#: What a caller with no policy at all evaluates against.
NO_BUDGET = Budget()


class TriggerInput(BaseModel):
    cron: StrictStr
    repo: StrictStr
    chain: StrictStr
    title: StrictStr
    description: StrictStr = ""


class FindingsInput(BaseModel):
    loop_severities: list[Literal["critical", "important", "minor"]] | None = None


class ArchiveInput(BaseModel):
    after_days: Annotated[StrictInt, Field(ge=0)] | None = None


class PolicyInput(BaseModel):
    """Static policy.yaml schema; runtime conversion remains in ``load_policy``."""

    loops: dict[str, Cap] = Field(default_factory=dict)
    default: Cap
    findings: FindingsInput = Field(default_factory=FindingsInput)
    budget: Budget | None = None
    rate_limit_retries: PositiveInt = 5
    triggers: list[TriggerInput] = Field(default_factory=list)
    max_concurrent: PositiveInt = 3
    archive: ArchiveInput | None = None
    auto_escalate_stuck: StrictBool = True
    auto_escalate_stuck_cap: PositiveInt = DEFAULT_AUTO_ESCALATE_STUCK_CAP
    auto_escalate_delay_s: Annotated[StrictInt, Field(ge=0)] = 0
    forge_cli_timeout_s: Annotated[StrictFloat | StrictInt, Field(gt=0)] = 120


@dataclass(frozen=True)
class Policy:
    loops: dict[str, Cap]
    default: Cap
    loop_severities: frozenset[str] = DEFAULT_LOOP_SEVERITIES
    budget: Budget = NO_BUDGET
    #: How many times `rate_limit_retry.poller` may auto-relaunch the same node
    #: after a rejected rate limit before it falls back to `needs_human`. Not a
    #: `Cap`: a rate-limit wait can run for hours, and `Cap.wall_clock_s` would
    #: read that as an immediate breach.
    rate_limit_retries: int = 5
    #: Days after completion/abandonment before `archive_poller` archives an
    #: item automatically, `archived_by: "auto"`. None or 0 disables it — a
    #: fresh install ships with no auto-archive rather than a guessed default
    #: (UI v2 · 03).
    archive_after_days: int | None = None
    triggers: list[Trigger] = field(default_factory=list)
    #: How many work items may be `status == 'active'` at once, across every
    #: repo, however they were started. Moved here from intake.yaml (UI v2 ·
    #: settings-how-work-runs, notes 10 "Concurrency") — auto-intake was never
    #: the only door onto a running item, so the cap belongs where every door
    #: (`resume`, `retry`) can read the same number.
    max_concurrent: int = 3
    #: Whether a `needs_human` stop for a reason other than a pending gate
    #: auto-dispatches an escalation turn (Kraft-lpdd). Independent of
    #: `auto_escalate` (gate review) -- different mechanism, different trigger,
    #: must stay independently toggleable. Defaults on: ships opt-out, not
    #: opt-in.
    auto_escalate_stuck: bool = True
    #: Attempts an item may be auto-escalated within one `needs_human` run
    #: before this feature leaves it for a human, same posture as a fix loop's
    #: `Cap.attempts` but counted over `escalation_message` events tagged
    #: `{"auto": true}` rather than a `retry_counters` row.
    auto_escalate_stuck_cap: int = DEFAULT_AUTO_ESCALATE_STUCK_CAP
    #: Seconds to wait after the triggering event (`gate_requested` for
    #: `auto_escalate`, `work_item_needs_human` for `auto_escalate_stuck`)
    #: before either mechanism fires, so a human about to look at the item
    #: anyway isn't preempted by the agent (Kraft-vyk8). 0, the default, is
    #: today's immediate-fire behaviour, unchanged.
    auto_escalate_delay_s: int = 0
    #: Seconds one forge CLI call (`gh`/`glab`/`git`) may run before it is killed
    #: and raised as a `ForgeError`. Bounds a single invocation, not a pipeline
    #: wait -- that is `loops.ci_wait`.
    forge_cli_timeout_s: float = 120.0


def _field_error(name: str, exc: ValidationError) -> PolicyError:
    """The message names the offending key, as the hand-rolled checks did."""
    err = exc.errors()[0]
    if err["type"] == "literal_error" and err["loc"][:2] == ("findings", "loop_severities"):
        return PolicyError(
            f"{name}: unknown severity {err['input']!r}; expected one of {SEVERITIES}"
        )
    key = ".".join(str(p) for p in err["loc"] if p != "args")
    return PolicyError(f"{name}: '{key}': {err['msg']}")


def _cron_fields(name: str, expr: str) -> tuple[str, str, str, str, str]:
    """ponytail: `*` or a comma-separated list of ints per field, no ranges
    or steps (`1-5`, `*/15`) -- add `croniter` as a dependency if a
    policy.yaml ever needs one."""
    parts = expr.split()
    if len(parts) != 5:
        raise PolicyError(f"{name}: cron expression must have exactly 5 fields: {expr!r}")
    for part in parts:
        for token in part.split(","):
            if token != "*" and not token.isdigit():
                raise PolicyError(
                    f"{name}: field {part!r} must be '*' or a comma-separated list of "
                    "integers; ranges and steps are not supported"
                )
    minute, hour, day, month, weekday = parts
    return minute, hour, day, month, weekday


def cron_due(expr: str, dt: datetime) -> bool:
    """True when `dt` (minute resolution) matches a 5-field cron expression."""
    minute, hour, day, month, weekday = _cron_fields("cron", expr)

    def matches(field: str, value: int) -> bool:
        return field == "*" or value in {int(x) for x in field.split(",")}

    return (
        matches(minute, dt.minute)
        and matches(hour, dt.hour)
        and matches(day, dt.day)
        and matches(month, dt.month)
        and matches(weekday, dt.isoweekday() % 7)  # cron: 0 = Sunday
    )


def _trigger(name: str, raw: TriggerInput) -> Trigger:
    _cron_fields(f"{name}.cron", raw.cron)
    return Trigger(**raw.model_dump())


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    try:
        # ValueError covers UnicodeDecodeError: a policy file with one invalid
        # byte is bad config, not a crash three frames up in `lifespan`.
        data = yaml.safe_load(path.read_text())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise PolicyError(f"{path.name}: cannot read/parse: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path.name}: expected a mapping with a 'default' cap")
    raw = dict(data)
    for key in ("loops", "findings", "triggers"):
        if raw.get(key) is None:
            raw.pop(key, None)
    raw_mc = raw.get("max_concurrent")
    if raw_mc is None:
        # Compat: an intake.yaml written before the move still names the
        # operator's real limit under the old key. Honour it once rather than
        # silently reverting every upgraded install to the default of 3.
        legacy_path = path.parent / "intake.yaml"
        if legacy_path.is_file():
            try:
                legacy = yaml.safe_load(legacy_path.read_text())
            except OSError, ValueError, yaml.YAMLError:
                legacy = None
            if isinstance(legacy, dict) and isinstance(legacy.get("max_concurrent"), int):
                raw_mc = legacy["max_concurrent"]
    if raw_mc is None:
        raw_mc = 3
    raw["max_concurrent"] = raw_mc
    try:
        parsed = PolicyInput.model_validate(raw)
    except ValidationError as exc:
        raise _field_error(path.name, exc) from exc
    severities = (
        frozenset(parsed.findings.loop_severities)
        if parsed.findings.loop_severities is not None
        else DEFAULT_LOOP_SEVERITIES
    )
    return Policy(
        loops=parsed.loops,
        default=parsed.default,
        loop_severities=severities,
        budget=parsed.budget or NO_BUDGET,
        archive_after_days=parsed.archive.after_days if parsed.archive else None,
        rate_limit_retries=parsed.rate_limit_retries,
        triggers=[
            _trigger(f"{path.name}: triggers[{i}]", t) for i, t in enumerate(parsed.triggers)
        ],
        max_concurrent=parsed.max_concurrent,
        auto_escalate_stuck=parsed.auto_escalate_stuck,
        auto_escalate_stuck_cap=parsed.auto_escalate_stuck_cap,
        auto_escalate_delay_s=parsed.auto_escalate_delay_s,
        forge_cli_timeout_s=float(parsed.forge_cli_timeout_s),
    )


def resolve_cap(policy: Policy, key: str, override: dict | None = None) -> Cap:
    cap = policy.loops.get(key, policy.default)
    if not override:
        return cap
    return dataclasses.replace(
        cap,
        attempts=override.get("attempts", cap.attempts),
        wall_clock_s=override.get("wall_clock_s", cap.wall_clock_s),
    )


def check(*, count: int, started_at: str, cap: Cap, now: str) -> str:
    if count > cap.attempts:
        return "breached"
    elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds()
    if elapsed >= cap.wall_clock_s:
        return "breached"
    return "ok"
