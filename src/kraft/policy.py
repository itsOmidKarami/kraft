from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)
from pydantic.dataclasses import dataclass as model

from kraft.findings import SEVERITIES


class PolicyError(ValueError):
    """Bad policy: an unreadable `policy.yaml`, or an override past a ceiling.

    A `ValueError`, so every intake door's existing `except ValueError` answers
    a chain `policy:` over the instance maxima as the refusal it is (a 422, a
    skipped trigger), not an unhandled 500 (Kraft-ib2af)."""


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
    """Static policy.yaml schema; runtime conversion remains in ``load_policy``.

    `extra="forbid"`: a misspelled section (`maximum:` for `maxima:`) is refused
    by name rather than loading cleanly and bounding nothing (Kraft-sz4dh)."""

    model_config = ConfigDict(extra="forbid")

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
    auto_review_attempts: PositiveInt = 1
    forge_cli_timeout_s: Annotated[StrictFloat | StrictInt, Field(gt=0)] = 120
    #: V1's `defaults:`/`maxima:` sections, read by the *same* loader rather
    #: than a second one. `policy.yaml` is one file, and one filename with two
    #: live loaders is how two readers of it start disagreeing (Ruling 18/37):
    #: `InstancePolicyInput.from_yaml` deliberately does not exist. Forward
    #: references, because the V1 models are defined further down this module
    #: beside the override engine they belong to -- `model_rebuild()` at the
    #: bottom of the file resolves them.
    defaults: PolicyDefaultsInput = Field(default_factory=lambda: PolicyDefaultsInput())
    maxima: PolicyMaximaInput = Field(default_factory=lambda: PolicyMaximaInput())

    @model_validator(mode="after")
    def _v1_defaults_are_within_v1_maxima(self) -> PolicyInput:
        """The same coherence rule `InstancePolicyInput` enforces, applied
        where the file is actually read -- so a `defaults:` entry past a
        `maxima:` ceiling is refused at load rather than at first use."""
        InstancePolicyInput(defaults=self.defaults, maxima=self.maxima)
        return self

    def instance_policy(self) -> InstancePolicy:
        """This file's V1 instance policy: the resolved starting point every
        later `apply_template_override` layers onto."""
        return InstancePolicy.from_input(
            InstancePolicyInput(defaults=self.defaults, maxima=self.maxima)
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> PolicyInput:
        """Read and validate `path`'s policy.yaml. The only boundary I/O in
        this module: everything else works on an already-parsed `PolicyInput`
        or `Policy`. Raises `PolicyError`, never a raw `OSError`/`yaml.YAMLError`
        /`ValidationError` -- `lifespan` catches only the former."""
        path = Path(path)
        try:
            # ValueError covers UnicodeDecodeError: a policy file with one
            # invalid byte is bad config, not a crash three frames up in
            # `lifespan`.
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
            # operator's real limit under the old key. Honour it once rather
            # than silently reverting every upgraded install to the default
            # of 3.
            legacy_path = path.parent / "intake.yaml"
            if legacy_path.is_file():
                try:
                    legacy = yaml.safe_load(legacy_path.read_text())
                # PEP 758 (Python 3.14+): a parenthesized tuple here has no
                # `as` clause, so `ruff format` rewrites it straight back to
                # this bare form -- `just lint` fails on the parenthesized
                # version. The `except (...) as exc:` four lines up keeps its
                # parens only because `as` still requires them.
                except OSError, ValueError, yaml.YAMLError:
                    legacy = None
                if isinstance(legacy, dict) and isinstance(legacy.get("max_concurrent"), int):
                    raw_mc = legacy["max_concurrent"]
        if raw_mc is None:
            raw_mc = 3
        raw["max_concurrent"] = raw_mc
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise _field_error(path.name, exc) from exc


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
    #: Seconds to wait after the triggering event before either delayed
    #: mechanism fires, so a human about to look at the item anyway isn't
    #: preempted by the agent (Kraft-vyk8). 0, the default, is today's
    #: immediate-fire behaviour, unchanged.
    #:
    #: **One field, two mechanisms, two owners.** `auto_escalate_delay.py` is a
    #: single poller and this is the only delay either half reads, so read the
    #: name as "the delay before an unattended agent acts", not as belonging to
    #: the `auto_escalate*` family alone:
    #:
    #: * **gate auto-review** -- triggered by `gate_requested`, armed by a
    #:   gate's own `GateNode.auto_review` task and bounded by
    #:   `auto_review_attempts`. Owned by `gate-auto-review-is-explicit-and-
    #:   bounded` (Task 4b).
    #: * **stuck escalation** -- triggered by `work_item_needs_human`, armed by
    #:   `auto_escalate_stuck` and bounded by `auto_escalate_stuck_cap`. Owned
    #:   by `stuck-escalation-is-an-exec-node-control` (Task 7), which is the
    #:   task that may move or rename its half. Nothing in V1's chain schema
    #:   spells `auto_escalate` any more, so the name no longer says which.
    #:
    #: Deliberately not renamed: operators have it set in their `policy.yaml`.
    auto_escalate_delay_s: int = 0
    #: How many auto-review attempts one `gate_requested` may get before the
    #: gate is left for a human (`gate-auto-review-is-explicit-and-bounded`).
    #: Counted over `gate_auto_review_started`/`_skipped` events since the
    #: request, by `gates._gate_already_reviewed`. 1 -- the default -- is the
    #: one-attempt-per-request contract this bound replaces a hardcode with.
    auto_review_attempts: int = 1
    #: Seconds one forge CLI call (`gh`/`glab`/`git`) may run before it is killed
    #: and raised as a `ForgeError`. Bounds a single invocation, not a pipeline
    #: wait -- that is `loops.ci_wait`.
    forge_cli_timeout_s: float = 120.0

    @classmethod
    def from_input(cls, parsed: PolicyInput, *, source: str | Path) -> Policy:
        """Pure conversion from a validated `PolicyInput` -- no I/O. `source`
        is only used to prefix a bad-trigger `PolicyError` with the file it
        came from, the way `load_policy` always has."""
        name = Path(source).name
        severities = (
            frozenset(parsed.findings.loop_severities)
            if parsed.findings.loop_severities is not None
            else DEFAULT_LOOP_SEVERITIES
        )
        return cls(
            loops=parsed.loops,
            default=parsed.default,
            loop_severities=severities,
            budget=parsed.budget or NO_BUDGET,
            archive_after_days=parsed.archive.after_days if parsed.archive else None,
            rate_limit_retries=parsed.rate_limit_retries,
            triggers=[_trigger(f"{name}: triggers[{i}]", t) for i, t in enumerate(parsed.triggers)],
            max_concurrent=parsed.max_concurrent,
            auto_escalate_stuck=parsed.auto_escalate_stuck,
            auto_escalate_stuck_cap=parsed.auto_escalate_stuck_cap,
            auto_escalate_delay_s=parsed.auto_escalate_delay_s,
            auto_review_attempts=parsed.auto_review_attempts,
            forge_cli_timeout_s=float(parsed.forge_cli_timeout_s),
        )

    def cap_for(self, key: str, override: CapOverride | dict | None = None) -> Cap:
        """The `Cap` for one loop `key`, `default` if unnamed, with `override`
        replacing only the fields it sets (`policy-override-rules-are-field-
        specific`: this is a sparse patch, not a merge of two `Cap`s)."""
        cap = self.loops.get(key, self.default)
        parsed_override = _cap_override(override)
        if parsed_override is None:
            return cap
        return dataclasses.replace(
            cap,
            attempts=parsed_override.attempts
            if parsed_override.attempts is not None
            else cap.attempts,
            wall_clock_s=(
                parsed_override.wall_clock_s
                if parsed_override.wall_clock_s is not None
                else cap.wall_clock_s
            ),
        )


def _field_error(name: str, exc: ValidationError) -> PolicyError:
    """The message names the offending key, as the hand-rolled checks did."""
    err = exc.errors()[0]
    if err["type"] == "literal_error" and err["loc"][:2] == ("findings", "loop_severities"):
        return PolicyError(
            f"{name}: unknown severity {err['input']!r}; expected one of {SEVERITIES}"
        )
    key = ".".join(str(p) for p in err["loc"] if p != "args")
    if err["type"] == "extra_forbidden" and len(err["loc"]) == 1:
        known = sorted(PolicyInput.model_fields)
        return PolicyError(f"{name}: unknown key {key!r}; expected one of {known}")
    return PolicyError(f"{name}: '{key}': {err['msg']}")


@dataclass(frozen=True)
class CronFields:
    """A 5-field cron expression, parsed. Not an anonymous tuple: `cron_due`
    and `_trigger` both name a field by what it means, not by position."""

    minute: str
    hour: str
    day: str
    month: str
    weekday: str


def _cron_fields(name: str, expr: str) -> CronFields:
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
    return CronFields(minute, hour, day, month, weekday)


def cron_due(expr: str, dt: datetime) -> bool:
    """True when `dt` (minute resolution) matches a 5-field cron expression."""
    fields = _cron_fields("cron", expr)

    def matches(field: str, value: int) -> bool:
        return field == "*" or value in {int(x) for x in field.split(",")}

    return (
        matches(fields.minute, dt.minute)
        and matches(fields.hour, dt.hour)
        and matches(fields.day, dt.day)
        and matches(fields.month, dt.month)
        and matches(fields.weekday, dt.isoweekday() % 7)  # cron: 0 = Sunday
    )


def _trigger(name: str, raw: TriggerInput) -> Trigger:
    _cron_fields(f"{name}.cron", raw.cron)
    return Trigger(**raw.model_dump())


class CapOverride(BaseModel):
    """A sparse patch onto one `Cap`, as a stored node override supplies it
    (`store.node_overrides_of(row).get(node_id)`). That row also carries
    fields no `Cap` owns (`model`, `effort`, `auto_escalate`, ...), already
    validated elsewhere (`kraft.templates.validate_node_override_fields`), so
    unknown keys are ignored here rather than re-litigated."""

    model_config = ConfigDict(extra="ignore")

    attempts: PositiveInt | None = None
    wall_clock_s: PositiveInt | None = None


def _cap_override(raw: CapOverride | dict | None) -> CapOverride | None:
    if raw is None or raw == {}:
        return None
    return raw if isinstance(raw, CapOverride) else CapOverride.model_validate(raw)


def load_policy(path: str | Path) -> Policy:
    """Delegator kept for its 15 callers (`executor/`, `ci_wait.py`, ...).
    Real implementation: `PolicyInput.from_yaml` + `Policy.from_input`."""
    return Policy.from_input(PolicyInput.from_yaml(path), source=path)


def resolve_cap(policy: Policy, key: str, override: dict | None = None) -> Cap:
    """Delegator kept for its 11 callers. Real implementation: `Policy.cap_for`."""
    return policy.cap_for(key, override)


def check(*, count: int, started_at: str, cap: Cap, now: str) -> str:
    if count > cap.attempts:
        return "breached"
    elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds()
    if elapsed >= cap.wall_clock_s:
        return "breached"
    return "ok"


# ── V1 instance policy: defaults, administrator maxima, layered overrides ──
#
# Distinct from `PolicyInput`/`Policy` above, which is the legacy loop-cap,
# budget and trigger schema the executor still reads (Phase 1 leaves it
# untouched). This is the `defaults:`/`maxima:` shape from
# docs/templates-v1-design.md's "Policy" section -- types and the one
# override rule engine every scope (instance, repository, work item, and
# later chain/node/step/task) layers through, from broadest to narrowest
# (`policy-is-layered-by-execution-scope`).

#: Fields where an override may only narrow the *inherited* value, never
#: widen it (`template-policy-cannot-relax-safety-ceilings`: "budgets,
#: allowed tools, permissions, and repository access" -- not harnesses).
_SAFETY_LIST_FIELDS = ("allowed_tools",)
_SAFETY_NUMERIC_FIELDS = ("token_budget",)
#: Fields that may move freely in either direction, bounded only by an
#: administrator maximum when one is explicitly configured
#: (`template-policy-may-replace-operational-defaults`,
#: `work-item-policy-may-exceed-default-ceilings-within-admin-maximum`).
_OPERATIONAL_NUMERIC_FIELDS = ("timeout_minutes", "max_attempts")
#: Same rule as `_OPERATIONAL_NUMERIC_FIELDS`, for a list-valued field: bounded
#: by `maxima`, not by the current inherited value, so a `defaults:` entry
#: narrower than `maxima:` doesn't permanently lower the real ceiling. The
#: design's `policy.yaml` lists `allowed_harnesses` under *both* `defaults:`
#: and `maxima:` -- an operational default plus an administrator maximum,
#: exactly what `work-item-policy-may-exceed-default-ceilings-within-admin-
#: maximum` describes -- unlike `allowed_tools`/`token_budget`, which appear
#: under `maxima:` only.
_OPERATIONAL_LIST_FIELDS = ("allowed_harnesses",)


class PolicyDefaultsInput(BaseModel):
    """`policy.yaml`'s `defaults:` -- inheritable operational starting
    points, no safety meaning of their own."""

    model_config = ConfigDict(strict=True, extra="forbid")

    timeout_minutes: PositiveInt | None = None
    max_attempts: PositiveInt | None = None
    allowed_harnesses: list[StrictStr] | None = None


class PolicyMaximaInput(BaseModel):
    """`policy.yaml`'s `maxima:` -- the administrator ceiling no policy
    override may exceed. Checked at load and at materialization only: nothing
    reads it when a task launches yet (Kraft-q55aw). `timeout_minutes`/
    `max_attempts` here are optional administrator maxima on the operational
    fields of the same name; the design doc's example omits them because most
    installs never set one."""

    model_config = ConfigDict(strict=True, extra="forbid")

    token_budget: PositiveInt | None = None
    allowed_tools: list[StrictStr] | None = None
    allowed_harnesses: list[StrictStr] | None = None
    timeout_minutes: PositiveInt | None = None
    max_attempts: PositiveInt | None = None


class InstancePolicyInput(BaseModel):
    """The whole `defaults:`/`maxima:` document
    (`policy-has-defaults-and-administrator-maxima`)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    defaults: PolicyDefaultsInput = Field(default_factory=PolicyDefaultsInput)
    maxima: PolicyMaximaInput = Field(default_factory=PolicyMaximaInput)

    @model_validator(mode="after")
    def _defaults_within_maxima(self) -> InstancePolicyInput:
        """maxima means maxima: the instance cannot start above a ceiling it
        enforces on every override below it. Both sections are administrator-
        authored in one file, so this is coherence rather than privilege
        escalation -- but `policy-has-defaults-and-administrator-maxima` calls
        maxima non-overridable, and a `defaults:` entry past one is an
        override in all but name. An unset maximum is no bound at all."""
        for name in _OPERATIONAL_NUMERIC_FIELDS:
            value, ceiling = getattr(self.defaults, name), getattr(self.maxima, name)
            if value is not None and ceiling is not None and value > ceiling:
                raise ValueError(f"defaults.{name} {value} exceeds maxima.{name} {ceiling}")
        for name in _OPERATIONAL_LIST_FIELDS:
            value, ceiling = getattr(self.defaults, name), getattr(self.maxima, name)
            if value is not None and ceiling is not None and not set(value) <= set(ceiling):
                extra = sorted(set(value) - set(ceiling))
                raise ValueError(
                    f"defaults.{name} {extra} is not permitted by maxima.{name} {sorted(ceiling)}"
                )
        return self


class TemplatePolicyOverride(BaseModel):
    """A sparse patch a repository, work item, chain, node, step or task
    layer proposes onto the policy above it. Same shape at every scope --
    `policy-override-rules-are-field-specific` is enforced once, in
    `InstancePolicy.apply_template_override`, not reinvented per scope."""

    model_config = ConfigDict(strict=True, extra="forbid")

    timeout_minutes: PositiveInt | None = None
    max_attempts: PositiveInt | None = None
    allowed_harnesses: list[StrictStr] | None = None
    token_budget: PositiveInt | None = None
    allowed_tools: list[StrictStr] | None = None


@dataclass(frozen=True)
class InstancePolicy:
    """One resolved policy state: the effective value of every field, plus
    the untouched administrator maxima every later `apply_template_override`
    call must still respect. A ratchet-only safety field with no `defaults:`
    entry (`allowed_tools`, `token_budget`) starts *at* its maximum -- there
    is nothing to narrow it from until the first override does.
    `allowed_harnesses` is different: it is operational-with-a-maximum, not
    ratchet-only, so its bound for widening is always `maxima`, never the
    current inherited value (see `_OPERATIONAL_LIST_FIELDS`)."""

    timeout_minutes: int | None
    max_attempts: int | None
    allowed_harnesses: tuple[str, ...] | None
    token_budget: int | None
    allowed_tools: tuple[str, ...] | None
    maxima: PolicyMaximaInput

    @classmethod
    def from_input(cls, parsed: InstancePolicyInput) -> InstancePolicy:
        d, m = parsed.defaults, parsed.maxima
        return cls(
            timeout_minutes=d.timeout_minutes
            if d.timeout_minutes is not None
            else m.timeout_minutes,
            max_attempts=d.max_attempts if d.max_attempts is not None else m.max_attempts,
            allowed_harnesses=(
                tuple(d.allowed_harnesses)
                if d.allowed_harnesses is not None
                else (tuple(m.allowed_harnesses) if m.allowed_harnesses is not None else None)
            ),
            token_budget=m.token_budget,
            allowed_tools=tuple(m.allowed_tools) if m.allowed_tools is not None else None,
            maxima=m,
        )

    def apply_template_override(self, raw: TemplatePolicyOverride | dict) -> InstancePolicy:
        """Layer `raw` onto this policy, field by field
        (`policy-override-rules-are-field-specific`): a ratchet-only safety
        field (`allowed_tools`, `token_budget`) may only narrow the inherited
        value; an operational field (`timeout_minutes`, `max_attempts`,
        `allowed_harnesses`) may move either way but not past an explicitly
        configured administrator maximum
        (`work-item-policy-may-exceed-default-ceilings-within-admin-maximum`)
        -- for `allowed_harnesses` that bound is always `maxima`, so a
        `defaults:` value narrower than `maxima:` can still be widened back
        up to it. The same method resolves a repository override over the
        instance policy, a work-item override over that, and so on
        (`policy-is-layered-by-execution-scope`)."""
        override = (
            raw
            if isinstance(raw, TemplatePolicyOverride)
            else TemplatePolicyOverride.model_validate(raw)
        )
        updates: dict[str, object] = {}

        for field_name in _SAFETY_LIST_FIELDS:
            value = getattr(override, field_name)
            if value is None:
                continue
            ceiling = getattr(self, field_name)
            if ceiling is not None and not set(value) <= set(ceiling):
                extra = sorted(set(value) - set(ceiling))
                raise PolicyError(
                    f"'{field_name}' cannot widen the inherited safety ceiling "
                    f"{sorted(ceiling)!r}; {extra} {'is' if len(extra) == 1 else 'are'} not allowed"
                )
            updates[field_name] = tuple(value)

        for field_name in _SAFETY_NUMERIC_FIELDS:
            value = getattr(override, field_name)
            if value is None:
                continue
            ceiling = getattr(self, field_name)
            if ceiling is not None and value > ceiling:
                raise PolicyError(
                    f"'{field_name}' cannot exceed the inherited safety ceiling {ceiling}"
                )
            updates[field_name] = value

        for field_name in _OPERATIONAL_NUMERIC_FIELDS:
            value = getattr(override, field_name)
            if value is None:
                continue
            admin_max = getattr(self.maxima, field_name)
            if admin_max is not None and value > admin_max:
                raise PolicyError(
                    f"'{field_name}' cannot exceed the administrator maximum {admin_max}"
                )
            updates[field_name] = value

        for field_name in _OPERATIONAL_LIST_FIELDS:
            value = getattr(override, field_name)
            if value is None:
                continue
            admin_max = getattr(self.maxima, field_name)
            if admin_max is not None and not set(value) <= set(admin_max):
                extra = sorted(set(value) - set(admin_max))
                verb = "is" if len(extra) == 1 else "are"
                raise PolicyError(
                    f"'{field_name}' cannot exceed the administrator maximum "
                    f"{sorted(admin_max)!r}; {extra} {verb} not allowed"
                )
            updates[field_name] = tuple(value)

        return dataclasses.replace(self, **updates)


# `PolicyInput.defaults`/`.maxima` are forward references to the V1 models
# above, which are defined after it: one loader for `policy.yaml` (Ruling 37)
# means the legacy schema has to carry the V1 sections, and the V1 models sit
# with the override engine that reads them.
PolicyInput.model_rebuild()
