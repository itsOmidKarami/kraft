from __future__ import annotations

import dataclasses
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, ClassVar, Literal, get_args

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.dataclasses import dataclass as model

from kraft.cap_levels import (
    BUDGET_FIELDS,
    CAP_FIELDS,
    CAP_LEVELS,
    SCOPE_CAP_FIELDS,
    CapLevels,
    PositiveInt,
    PositiveUsd,
)
from kraft.findings import SEVERITIES

logger = logging.getLogger(__name__)


class PolicyError(ValueError):
    """Bad policy: an unreadable `policy.yaml`, or an override past a ceiling.

    A `ValueError`, so every intake door's existing `except ValueError` answers
    a chain `policy:` over the instance maxima as the refusal it is (a 422, a
    skipped trigger), not an unhandled 500 (Kraft-ib2af).

    `field` names the policy field an override refusal is about, as data
    (`policy-override-rules-are-field-specific`), so a caller such as the
    retry-override validator can point at it without parsing the message.
    `None` for a refusal of a whole file."""

    def __init__(self, message: str, *, field: str | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.field = field
        #: The canonical path of the scope refused, when a chain check made
        #: the refusal (`ResolvedChain.check_scopes`).
        self.path = path


DEFAULT_LOOP_SEVERITIES = frozenset({"critical", "important"})
#: The harness an escalation turn runs on when no policy layer names one
#: (Kraft-wge0e): what it always was, so an install that sets nothing changes
#: nothing.
DEFAULT_ESCALATION_HARNESS = "claude"
#: An `escalation_harness` value meaning "the harness this item's own work ran
#: on" (`escalate.item_harness`), not a `harnesses.yaml` profile id.
FOLLOW_ITEM = "item"
#: What an escalation turn is granted when `defaults.escalation_grants` is
#: unset (Kraft-4in7z): it has to be able to rebase the branch and push it.
DEFAULT_ESCALATION_GRANTS = ("git-commit", "git-rebase", "git-push")
DEFAULT_AUTO_ESCALATE_STUCK_CAP = 3


@model(frozen=True, config=ConfigDict(extra="forbid"))
class Cap:
    """One loop's bound. Unknown keys are refused: a cap field that binds
    nothing reads as a limit and limits nothing (`escalate_after`, retired with
    the legacy hook model, was one)."""

    attempts: PositiveInt
    wall_clock_s: PositiveInt


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


class AutoCleanupInput(BaseModel):
    """`storage.worktrees.auto_cleanup`: its presence lets Kraft archive
    finished items for the limit without a person (`kraft.storage.tick`),
    never one that ended less than `min_age` ago."""

    model_config = ConfigDict(extra="forbid")

    min_age: str = "24h"

    @field_validator("min_age", mode="before")
    @classmethod
    def _is_an_age(cls, value):
        age_seconds(value)
        return str(value).strip()


class StorageWorktreesInput(BaseModel):
    """`storage.worktrees`: over `limit`, a start that needs a new worktree
    waits until a person cleans up; over `quota` Kraft only warns. `quota`
    defaults to 80% of `limit`."""

    model_config = ConfigDict(extra="forbid")

    limit: str | None = None
    quota: str | None = None
    auto_cleanup: AutoCleanupInput | None = None

    @field_validator("limit", "quota", mode="before")
    @classmethod
    def _is_a_size(cls, value):
        if value is None:
            return None
        size_bytes(value)
        return str(value).strip()

    @model_validator(mode="after")
    def _quota_is_below_limit(self) -> StorageWorktreesInput:
        if self.auto_cleanup is not None and self.limit is None:
            raise ValueError("storage.worktrees.auto_cleanup needs a limit beside it")
        if self.quota is None:
            return self
        if self.limit is None:
            raise ValueError("storage.worktrees.quota needs a limit beside it")
        if size_bytes(self.quota) >= size_bytes(self.limit):
            raise ValueError(
                f"storage.worktrees.quota {self.quota} must be below limit {self.limit}"
            )
        return self


class StorageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worktrees: StorageWorktreesInput | None = None


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
    #: Pre-2.0: schedules live in `intake.yaml` (`config.Intake.schedules`).
    #: Still read, so an upgraded file keeps firing; `doctor` names the move.
    triggers: list[TriggerInput] = Field(default_factory=list)
    max_concurrent: PositiveInt = 3
    archive: ArchiveInput | None = None
    storage: StorageInput | None = None
    auto_escalate_stuck: StrictBool = True
    auto_escalate_stuck_cap: PositiveInt = DEFAULT_AUTO_ESCALATE_STUCK_CAP
    auto_escalate_delay_s: Annotated[StrictInt, Field(ge=0)] = 0
    auto_review_attempts: PositiveInt = 1
    forge_cli_timeout_s: Annotated[StrictFloat | StrictInt, Field(gt=0)] = 120
    #: Seconds between the MR-closed poller's ticks (`mr_poller.py`, B8): how
    #: often it asks the forge about every item parked at an MR node. Bounded
    #: well above a wait/cap poller's interval -- this one calls `gh`/`glab`
    #: once per item per tick, where those read only the database.
    forge_poll_s: Annotated[StrictInt, Field(ge=30)] = 300
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
        raw.setdefault("max_concurrent", 3)
        try:
            parsed = cls.model_validate(raw)
        except ValidationError as exc:
            raise _field_error(path.name, exc) from exc
        _check_escalation_harness(parsed.defaults.escalation_harness, path.name)
        return parsed


def _check_escalation_harness(value: str | None, name: str) -> None:
    """Refuse a `defaults.escalation_harness` that names no `harnesses.yaml`
    profile, with the ones it could name (Kraft-wge0e): read now, not at the
    first stuck item. The live table, the one a launch selects from
    (`adapters.profiles.harness_table`). An item's own override is checked at
    launch instead, by that same selection."""
    if value is None or value == FOLLOW_ITEM:
        return
    # Late: `adapters.profiles` imports the template models, which import this.
    from kraft import harness as _harness
    from kraft.adapters.profiles import HarnessUnavailable, harness_table

    try:
        table, where = harness_table(_harness.load(None))
    except HarnessUnavailable as exc:
        raise PolicyError(
            f"{name}: 'defaults.escalation_harness' {value!r} cannot be checked: {exc}",
            field="escalation_harness",
        ) from exc
    if value not in table.profiles:
        raise PolicyError(
            f"{name}: 'defaults.escalation_harness' {value!r} is not a profile in {where}; "
            f"known: {sorted(table.profiles)}, or {FOLLOW_ITEM!r}",
            field="escalation_harness",
        )


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
    #: `storage.worktrees.limit` in bytes; None is no limit, and then nothing
    #: is measured, warned about or held (`kraft.storage`).
    storage_limit_bytes: int | None = None
    #: `storage.worktrees.quota` in bytes, or 80% of the limit. Set whenever
    #: the limit is.
    storage_quota_bytes: int | None = None
    #: `storage.worktrees.auto_cleanup.min_age` in seconds. None is off: the
    #: limit then archives nothing and a person cleans up.
    storage_auto_cleanup_min_age_s: int | None = None
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
    #: and raised as a `ForgeError`. Bounds a single invocation, not a wait --
    #: that is the task's own `wait:` (`kraft.waits`).
    forge_cli_timeout_s: float = 120.0
    #: Mirrors `PolicyInput.forge_poll_s` (B8's MR-closed poller cadence).
    forge_poll_s: int = 300

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
        worktrees = parsed.storage.worktrees if parsed.storage else None
        limit = size_bytes(worktrees.limit) if worktrees and worktrees.limit else None
        quota = (
            None
            if limit is None
            else size_bytes(worktrees.quota)
            if worktrees.quota
            else limit * 8 // 10
        )
        cleanup = worktrees.auto_cleanup if worktrees else None
        return cls(
            loops=parsed.loops,
            default=parsed.default,
            loop_severities=severities,
            budget=parsed.budget or NO_BUDGET,
            archive_after_days=parsed.archive.after_days if parsed.archive else None,
            storage_limit_bytes=limit,
            storage_quota_bytes=quota,
            storage_auto_cleanup_min_age_s=age_seconds(cleanup.min_age) if cleanup else None,
            rate_limit_retries=parsed.rate_limit_retries,
            triggers=_triggers(name, parsed.triggers),
            max_concurrent=parsed.max_concurrent,
            auto_escalate_stuck=parsed.auto_escalate_stuck,
            auto_escalate_stuck_cap=parsed.auto_escalate_stuck_cap,
            auto_escalate_delay_s=parsed.auto_escalate_delay_s,
            auto_review_attempts=parsed.auto_review_attempts,
            forge_cli_timeout_s=float(parsed.forge_cli_timeout_s),
            forge_poll_s=parsed.forge_poll_s,
        )

    def cap_for(self, key: str, override: CapOverride | dict | None = None) -> Cap:
        """The `Cap` for one loop `key`, `default` if unnamed, with `override`
        replacing only the fields it sets (`policy-override-rules-are-field-
        specific`: this is a sparse patch, not a merge of two `Cap`s)."""
        return with_cap_override(self.loops.get(key, self.default), override)


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


#: Each cron field's name and the values it may hold, in field order. Day of
#: week takes 0-7, both 0 and 7 meaning Sunday, as cron's own does.
_CRON_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)


class LegacyCronError(PolicyError):
    """A cron that 1.4 loaded, since it checked only that each value passed
    `str.isdigit`, and that a schedule now refuses. A `policy.yaml` trigger
    holding one is skipped with a warning (`_triggers`); `intake.yaml` refuses
    it. `skipped` ends that warning."""

    skipped = ""


class CronRangeError(LegacyCronError):
    """A cron value outside its field (minute 61, day of week 8). Such a
    trigger never fired in 1.4."""

    skipped = "as 1.4 never ran it"


class CronDigitError(LegacyCronError):
    """A cron number in digits other than 0-9 (`²`, `٣`), which `isdigit`
    takes and `int` then crashes on or reads as another number."""

    skipped = "as a cron number is the digits 0-9"


#: What a cron number is: ASCII digits. `str.isdigit` also takes `²` and `٣`.
_DIGITS = re.compile(r"[0-9]+")


def _cron_values(name: str, part: str, label: str, lo: int, hi: int) -> frozenset[int]:
    """The values one cron field matches: `*`, an integer, a range `A-B`, a
    step `*/N`, `A-B/N` or `A/N` (A to the field's top), or a comma-separated
    list of those. Anything else, or a value outside `lo`-`hi`, is refused."""
    shape = (
        f"{name}: field {part!r} must be '*', an integer, a range (1-5), a step (*/15), "
        "or a comma-separated list of those"
    )
    out: set[int] = set()
    for token in part.split(","):
        if token.isdigit() and not _DIGITS.fullmatch(token):
            raise CronDigitError(f"{name}: field {part!r}: {token!r} is not the digits 0-9")
        base, slash, step_text = token.partition("/")
        if slash and not (_DIGITS.fullmatch(step_text) and int(step_text) > 0):
            raise PolicyError(f"{name}: field {part!r}: a step must be a positive integer")
        step = int(step_text) if slash else 1
        start_text, dash, end_text = base.partition("-")
        if base == "*":
            start, end = lo, hi
        elif dash and _DIGITS.fullmatch(start_text) and _DIGITS.fullmatch(end_text):
            start, end = int(start_text), int(end_text)
        elif _DIGITS.fullmatch(base):
            start = int(base)
            end = hi if slash else start
        else:
            raise PolicyError(shape)
        for value in (start, end):
            if not lo <= value <= hi:
                raise CronRangeError(
                    f"{name}: field {part!r}: {label} {value} is outside {lo}-{hi}"
                )
        if start > end:
            raise PolicyError(f"{name}: field {part!r}: the range {start}-{end} runs backwards")
        out.update(range(start, end + 1, step))
    return frozenset(out)


def _cron_sets(name: str, expr: str) -> tuple[frozenset[int], ...]:
    """Each field's matching values, in field order (`_CRON_FIELDS`). Day of
    week's 7 is folded onto 0, Sunday."""
    parts = expr.split()
    if len(parts) != 5:
        raise PolicyError(f"{name}: cron expression must have exactly 5 fields: {expr!r}")
    sets = [
        _cron_values(name, part, label, lo, hi)
        for part, (label, lo, hi) in zip(parts, _CRON_FIELDS, strict=True)
    ]
    sets[4] = frozenset(0 if day == 7 else day for day in sets[4])
    return tuple(sets)


def _cron_fields(name: str, expr: str) -> CronFields:
    """A 5-field cron expression, checked: each field is what `_cron_values`
    reads, with every value in its field's range. Refused with a
    `PolicyError` naming `name`, the way a policy.yaml trigger was in 1.4,
    and every reader of a schedule (`config.Schedule`, the draft, the tick)
    calls this one."""
    _cron_sets(name, expr)
    minute, hour, day, month, weekday = expr.split()
    return CronFields(minute, hour, day, month, weekday)


def cron_due(expr: str, dt: datetime) -> bool:
    """True when `dt` (minute resolution) matches a 5-field cron expression.
    A day of month and a day of week must both match, as they always have
    here (cron's own takes either when both are set)."""
    minute, hour, day, month, weekday = _cron_sets("cron", expr)
    return (
        dt.minute in minute
        and dt.hour in hour
        and dt.day in day
        and dt.month in month
        and dt.isoweekday() % 7 in weekday  # cron: 0 = Sunday
    )


def _trigger(name: str, raw: TriggerInput) -> Trigger:
    _cron_fields(f"{name}.cron", raw.cron)
    return Trigger(**raw.model_dump())


def _triggers(name: str, raws: list[TriggerInput]) -> list[Trigger]:
    """`policy.yaml`'s pre-2.0 `triggers:`, each checked as a schedule is. One
    whose cron names a value outside its field (`0 24 * * *`) loaded in 1.4,
    which never ran it: it is skipped with a warning, not a refusal of the
    whole file, which would stop every intake door (R12 review P1-1).
    `kraft admin doctor`'s `moved keys` row names it. So is a number in
    other digits (`²`), which 1.4 also took (`LegacyCronError`). Any other
    bad cron is refused, as in 1.4."""
    out = []
    for i, raw in enumerate(raws):
        try:
            out.append(_trigger(f"{name}: triggers[{i}]", raw))
        except LegacyCronError as exc:
            logger.warning("%s; that trigger is skipped, %s", exc, exc.skipped)
    return out


def skipped_trigger(entry: object) -> str:
    """Why `_triggers` skips this pre-2.0 `policy.yaml` trigger (its cron is a
    `LegacyCronError`), or "" when it is read or refused. Said by the first
    start's line and `kraft admin doctor`, of a trigger that stays."""
    cron = entry.get("cron") if isinstance(entry, dict) else None
    if not isinstance(cron, str):
        return ""
    try:
        _cron_fields("cron", cron)
    except LegacyCronError as exc:
        return exc.skipped
    except PolicyError:
        return ""
    return ""


class CapOverride(BaseModel):
    """A sparse patch onto one `Cap`, as a stored node override supplies it
    (`store.node_overrides_of(row).get(node_id)`). That row also carries
    fields no `Cap` owns (`model`, `effort`, `auto_escalate`, ...), already
    validated elsewhere (`kraft.overrides.validate_node_override_fields`), so
    unknown keys are ignored here rather than re-litigated."""

    model_config = ConfigDict(extra="ignore")

    attempts: PositiveInt | None = None
    wall_clock_s: PositiveInt | None = None


def _cap_override(raw: CapOverride | dict | None) -> CapOverride | None:
    if raw is None or raw == {}:
        return None
    return raw if isinstance(raw, CapOverride) else CapOverride.model_validate(raw)


def with_cap_override(cap: Cap, override: CapOverride | dict | None) -> Cap:
    """`cap` with `override` replacing only the fields it sets."""
    parsed = _cap_override(override)
    if parsed is None:
        return cap
    return dataclasses.replace(
        cap,
        attempts=parsed.attempts if parsed.attempts is not None else cap.attempts,
        wall_clock_s=parsed.wall_clock_s if parsed.wall_clock_s is not None else cap.wall_clock_s,
    )


def load_policy(path: str | Path) -> Policy:
    """Delegator kept for its callers (`executor/`, ...).
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
#: None since Ruling 198: a budget is a scope's own cap, bounded by `maxima`
#: here and kept under its parent scope's by `ResolvedChain._check_caps`.
_SAFETY_NUMERIC_FIELDS: tuple[str, ...] = ()
#: Fields that may move freely in either direction, bounded only by an
#: administrator maximum when one is explicitly configured
#: (`template-policy-may-replace-operational-defaults`,
#: `work-item-policy-may-exceed-default-ceilings-within-admin-maximum`).
_OPERATIONAL_NUMERIC_FIELDS = ("timeout_minutes", "max_attempts")
#: Retired by Ruling 196: a wait's timeout is its task's own
#: `total_time_cap_minutes`.
RETIRED_WAIT_TIMEOUT = "wait_timeout_minutes"


_DEPRECATIONS_SAID: set[str] = set()


def deprecated(message: str, *args: object) -> None:
    """Warn that a retired key was read, once per message per process: a
    snapshot is read on every dispatch, and one warning says it."""
    text = message % args if args else message
    if text not in _DEPRECATIONS_SAID:
        _DEPRECATIONS_SAID.add(text)
        logger.warning("%s", text)


def _carry_retired(data: object, where: str, replacement: str) -> object:
    """`data` with a retired `wait_timeout_minutes` read as `replacement`
    (Ruling 196), warning that it is deprecated. A `None` one -- every snapshot
    dumped one -- goes quietly."""
    if not isinstance(data, dict) or RETIRED_WAIT_TIMEOUT not in data:
        return data
    data = dict(data)
    value = data.pop(RETIRED_WAIT_TIMEOUT)
    if value is not None:
        deprecated(
            "%s.%s is deprecated (Ruling 196) and read as %s.%s; rename it",
            where,
            RETIRED_WAIT_TIMEOUT,
            where,
            replacement,
        )
        data.setdefault(replacement, value)
    return data


#: Same rule as `_OPERATIONAL_NUMERIC_FIELDS`, for a list-valued field: bounded
#: by `maxima`, not by the current inherited value, so a `defaults:` entry
#: narrower than `maxima:` doesn't permanently lower the real ceiling. The
#: design's `policy.yaml` lists `allowed_harnesses` under *both* `defaults:`
#: and `maxima:` -- an operational default plus an administrator maximum,
#: exactly what `work-item-policy-may-exceed-default-ceilings-within-admin-
#: maximum` describes -- unlike `allowed_tools`/`token_budget`, which appear
#: under `maxima:` only.
_OPERATIONAL_LIST_FIELDS = ("allowed_harnesses",)


#: What the permission gate can match: it compares a tool name exactly
#: (`sessions.permission_request`), so a list entry is a bare tool (`Bash`) or
#: one exact MCP tool (`mcp__server__tool`), never a rule.
_TOOL_NAME = re.compile(r"(?!mcp__)[A-Za-z][\w-]*|mcp__[\w-]+?__[\w.-]+")


#: Validation context for reading back what Kraft itself froze (a snapshot, a
#: fork's override record): a tool list there is not refused, because a
#: snapshot frozen before the refusal existed must still read (Kraft-9ct4q).
#: `adapters.agent.run_agent_task` refuses the rule at launch instead.
FROZEN = {"frozen": True}


def tool_name_refusal(field: str, names: Iterable[str]) -> str | None:
    """Why `names` is not a tool list the permission gate can match, or None
    (Kraft-9i6xy): a scoped rule (`Bash(git *)`) or a glob (`mcp__x__*`)
    would never match an ask, and the harness can only restrict a bare tool,
    so it would bound nothing it claims to."""
    for name in names:
        if _TOOL_NAME.fullmatch(name):
            continue
        if "(" in name:
            what = "a permission rule, not a tool name"
            bare = name.split("(", 1)[0].strip()
            fix = f"list the bare tool {bare!r}, which allows all of its use"
        elif name.startswith("mcp__"):
            what = "not an exact MCP tool name"
            fix = "list each MCP tool by its exact name, mcp__<server>__<tool>"
        else:
            what = "not a tool name"
            fix = "list each tool by its exact tool name"
        return (
            f"{field}: {name!r} is {what}. Kraft's permission gate matches exact "
            f"tool names, so {fix}"
        )
    return None


def _tool_names(names: list[str], info: ValidationInfo) -> list[str]:
    if not (info.context or {}).get("frozen") and (
        why := tool_name_refusal(info.field_name, names)
    ):
        raise ValueError(why)
    return names


#: A policy's `allowed_tools`/`deny_tools`: tool names, never rules.
ToolNames = Annotated[list[StrictStr], AfterValidator(_tool_names)]

#: Named operations a task is guaranteed, whatever a harness's own
#: classifier or sandbox would decide (Kraft-4in7z). Names, never command
#: patterns: each harness matches a name to its own calls (`kraft.grants`).
GRANTS: tuple[str, ...] = ("git-commit", "git-rebase", "git-push")
#: One line per grant, for the editors that offer them (`kraft.grants` is
#: what each one actually matches).
GRANT_SUMMARIES: dict[str, str] = {
    "git-commit": "A plain git commit",
    "git-rebase": "A plain git rebase",
    "git-push": "A plain git push to the item's own branch",
}


def _grant_names(names: list[str]) -> list[str]:
    unknown = [n for n in names if n not in GRANTS]
    if unknown:
        raise ValueError(f"grants: {unknown!r} are not grants; known: {list(GRANTS)}")
    return list(dict.fromkeys(names))


GrantNames = Annotated[list[StrictStr], AfterValidator(_grant_names)]


class PolicyDefaultsInput(CapLevels):
    """`policy.yaml`'s `defaults:` -- inheritable operational starting
    points, no safety meaning of their own. A cap default is per level
    (Ruling 211): it applies to every scope of its kind that nothing -- the
    chain, a node, step or task, or the item's own override -- set a value
    for. A default, not a ceiling (Ruling 198): any scope may set more, up to
    its level's maximum."""

    section: ClassVar[str] = "defaults"

    timeout_minutes: PositiveInt | None = None
    max_attempts: PositiveInt | None = None
    allowed_harnesses: list[StrictStr] | None = None
    #: The `harnesses.yaml` profile an escalation turn runs on, or `"item"`
    #: (`FOLLOW_ITEM`). Operational: any layer may change it, and the
    #: resolved profile still has to be in `allowed_harnesses` at launch.
    escalation_harness: StrictStr | None = None
    #: Grants every escalation turn holds on top of its node's own
    #: (`DEFAULT_ESCALATION_GRANTS` when unset; `[]` grants it nothing extra).
    escalation_grants: GrantNames | None = None


class PolicyMaximaInput(CapLevels):
    """`policy.yaml`'s `maxima:` -- the administrator ceiling no policy
    override may exceed, checked when a layer is applied (at load, at
    materialization, and on a retry override); the resolved values it bounds
    are what a task launch reads (`MaterializedChain.policy_for`).
    `timeout_minutes`/`max_attempts` here are optional administrator maxima on
    the operational fields of the same name; the design doc's example omits
    them because most installs never set one.

    A cap's maximum is per level (Ruling 211), and one set on a level bounds
    every narrower level too (`nearest`). `tasks.total_time_cap_minutes` is
    also the longest any external wait may wait (Ruling 196: a wait's timeout
    is its task's total cap), so it replaces the retired
    `wait_timeout_minutes`."""

    section: ClassVar[str] = "maxima"

    allowed_tools: ToolNames | None = None
    allowed_harnesses: list[StrictStr] | None = None
    timeout_minutes: PositiveInt | None = None
    max_attempts: PositiveInt | None = None

    @model_validator(mode="before")
    @classmethod
    def _retired_wait_timeout(cls, data: object) -> object:
        """A `policy.yaml` or a snapshot written before Ruling 196 still reads:
        its `wait_timeout_minutes` is now `tasks.total_time_cap_minutes`."""
        if not isinstance(data, dict) or RETIRED_WAIT_TIMEOUT not in data:
            return data
        data = dict(data)
        if (value := data.pop(RETIRED_WAIT_TIMEOUT)) is not None:
            deprecated(
                "maxima.%s is deprecated (Ruling 196) and read as "
                "maxima.tasks.total_time_cap_minutes; rename it",
                RETIRED_WAIT_TIMEOUT,
            )
            tasks = dict(data.get("tasks") or {})
            tasks.setdefault("total_time_cap_minutes", value)
            data["tasks"] = tasks
        return data


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
        for level in CAP_LEVELS:
            for name in SCOPE_CAP_FIELDS:
                value = getattr(getattr(self.defaults, level), name)
                bound = self.maxima.nearest(level, name)
                if value is not None and bound is not None and value > bound[1]:
                    raise ValueError(
                        f"defaults.{level}.{name} {value} exceeds maxima.{bound[0]}.{name} "
                        f"{bound[1]}"
                    )
        for name in _OPERATIONAL_LIST_FIELDS:
            value, ceiling = getattr(self.defaults, name), getattr(self.maxima, name)
            if value is not None and ceiling is not None and not set(value) <= set(ceiling):
                extra = sorted(set(value) - set(ceiling))
                raise ValueError(
                    f"defaults.{name} {extra} is not permitted by maxima.{name} {sorted(ceiling)}"
                )
        return self


#: `resources.memory` as `docker run --memory` takes it: a whole number of
#: bytes, or of `k`, `m` or `g` (binary units, as docker reads them).
_MEMORY = re.compile(r"[1-9][0-9]*[bkmg]?")
_MEMORY_UNITS = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
#: The smallest memory limit docker starts a container under.
MIN_SANDBOX_MEMORY = 6 * 1024**2


def memory_bytes(value: str) -> int:
    """`resources.memory` (already validated) as a byte count."""
    unit = value[-1] if value[-1] in _MEMORY_UNITS else "b"
    return int(value.rstrip("bkmgt")) * _MEMORY_UNITS[unit]


#: A `storage` size: `1000M`, `10G`, `1T`. The unit is required, unlike
#: `resources.memory`: a bare `10` would be a 10-byte limit that holds every start.
_SIZE = re.compile(r"[1-9][0-9]*[kmgt]b?")


def size_bytes(value) -> int:
    """A `storage` size as a byte count (binary units, as `memory_bytes`)."""
    text = str(value).strip().lower()
    if not _SIZE.fullmatch(text):
        raise ValueError(
            f"size {value!r} needs a whole number and a unit K, M, G or T, as in `10G`"
        )
    return memory_bytes(text.removesuffix("b"))


#: `auto_cleanup.min_age`: `12h`, `24h`, `2d`. Days are the largest unit, and
#: the unit is required for the reason a size's is.
_AGE = re.compile(r"(0|[1-9][0-9]*)[hd]")


def age_seconds(value) -> int:
    """An age (`24h`, `2d`) as seconds."""
    text = str(value).strip().lower()
    if not _AGE.fullmatch(text):
        raise ValueError(f"age {value!r} needs a whole number and a unit h or d, as in `24h`")
    return int(text[:-1]) * (3600 if text[-1] == "h" else 86400)


def _memory(value: str) -> str:
    value = value.strip().lower()
    if not _MEMORY.fullmatch(value):
        raise ValueError(
            f"memory {value!r} is not a size: a whole number with an optional unit "
            "b, k, m or g (`512m`, `4g`)"
        )
    if memory_bytes(value) < MIN_SANDBOX_MEMORY:
        raise ValueError(f"memory {value!r} is under 6m, the least a container starts with")
    return value


def _without_unset(handler, value) -> dict:
    return {k: v for k, v in handler(value).items() if v is not None}


class SandboxResources(BaseModel):
    """What a sandboxed launch may use: `cpu` (CPUs, fractions allowed),
    `memory` (a hard limit, swap too where it can be) and `pids` (processes and
    threads). A limit that is set is applied or the launch is refused
    (`sandbox-limits-never-silently-drop`); unset `pids` is 4096 wherever
    the runtime can enforce it. Frozen, like `SandboxPolicy`, which is hashed
    whole."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    #: CPUs the sandbox may use (`--cpus`): `2`, `0.5`; at least `0.01`.
    cpu: StrictFloat | None = Field(default=None, ge=0.01)
    #: A hard memory limit (`--memory`): `512m`, `4g`. Swap is held to it
    #: wherever the runtime can limit swap. A session it kills ends as a
    #: configuration error naming it.
    memory: Annotated[StrictStr, AfterValidator(_memory)] | None = None
    #: Processes and threads at once (`--pids-limit`). Unset: 4096.
    pids: StrictInt | None = Field(default=None, ge=1)

    @model_serializer(mode="wrap")
    def _dump(self, handler) -> dict:
        return _without_unset(handler, self)


#: A network-policy@1 host: exact, `host:port`, `*.example.com` (the `*`
#: stands for one label), `*` or `**` (everything). No CIDRs, no URLs.
_HOST_PATTERN = re.compile(
    r"(\*\*|\*|(?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*)(?::\d{1,5})?"
)


def host_pattern(value: str) -> str:
    """`value` as a network-policy@1 host, or `ValueError` saying what one is.
    Shared by `sandbox.network` and a harness's `network.requires`."""
    value = value.strip()
    if not _HOST_PATTERN.fullmatch(value):
        raise ValueError(
            f"{value!r} is not a network-policy@1 host: an exact host, "
            "'host:port', '*.example.com' (one label), '*' or '**'"
        )
    return value


HostPattern = Annotated[StrictStr, AfterValidator(host_pattern)]


class NetworkPhase(BaseModel):
    """One phase's allow and deny lists. Deny wins; an empty allow grants
    nothing."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # Written as YAML lists, held as tuples so the policy hashes.
    allow: tuple[HostPattern, ...] = Field(default=(), strict=False)
    deny: tuple[HostPattern, ...] = Field(default=(), strict=False)

    @model_serializer(mode="wrap")
    def _dump(self, handler) -> dict:
        """As written: lists (YAML's safe dumper refuses a tuple), empty
        ones left out."""
        return {k: list(v) for k, v in handler(self).items() if v}


class SandboxNetwork(BaseModel):
    """`network:`: deny-by-default egress for a sandboxed launch.
    `install` binds the setup command, `runtime` an agent session, which also
    gets its harness's own `network.requires` hosts (unioned at launch, never
    stored here). A phase left out grants nothing. Frozen, with tuples, like
    `SandboxResources`: `SandboxPolicy` is hashed and locked whole."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    install: NetworkPhase = NetworkPhase()
    runtime: NetworkPhase = NetworkPhase()

    @model_serializer(mode="wrap")
    def _dump(self, handler) -> dict:
        """A phase written out stays, even empty: `network: {runtime: {}}`
        denies everything, and must not reload as `network: {}` (open)."""
        return {k: v for k, v in handler(self).items() if v or k in self.model_fields_set}


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: An HTTP header name (RFC 9110 token).
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _env_name(value: str) -> str:
    if not _ENV_NAME.fullmatch(value):
        raise ValueError(f"{value!r} is not an environment variable name")
    return value


def _header_name(value: str) -> str:
    if not _HEADER_NAME.fullmatch(value):
        raise ValueError(f"{value!r} is not an HTTP header name")
    return value


def _exact_host(value: str) -> str:
    value = host_pattern(value)
    if "*" in value or ":" in value:
        raise ValueError(f"{value!r} is not an exact host: a credential goes to one host by name")
    return value


def _header_format(value: str) -> str:
    if value.count("%s") != 1:
        raise ValueError(f"{value!r} must hold '%s' once, where the credential goes")
    return value


class CredentialInject(BaseModel):
    """Where the egress proxy puts a credential: `header` on every request
    to exactly `domain`, set to `format` with the value at its `%s`
    (`Bearer %s` for `Authorization`; unset, the value alone)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    domain: Annotated[StrictStr, AfterValidator(_exact_host)]
    header: Annotated[StrictStr, AfterValidator(_header_name)]
    format: Annotated[StrictStr, AfterValidator(_header_format)] | None = None

    @model_serializer(mode="wrap")
    def _dump(self, handler) -> dict:
        return _without_unset(handler, self)


class SandboxCredential(BaseModel):
    """One proxy-managed credential (credential@1's apiKey subset, spec §6):
    the container's `env` holds `sentinel`, and the egress proxy puts the
    daemon's own value where `inject` says. Just `env:` names one a harness
    declares, which supplies the rest; a repository's own credential says
    it all. A name left out of `credentials` passes through as it always did."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    env: Annotated[StrictStr, AfterValidator(_env_name)]
    service: StrictStr | None = Field(default=None, min_length=1)
    #: What the container's `env` holds instead of the value. Unset: the
    #: harness's, else `DEFAULT_SENTINEL`.
    sentinel: StrictStr | None = Field(default=None, min_length=1)
    inject: tuple[CredentialInject, ...] = Field(default=(), strict=False)
    #: The phases whose launches get this credential: the setup command
    #: (`install`), an agent session (`runtime`). Unset: both. A launch in
    #: another phase gets neither its sentinel nor its value.
    phase: tuple[Literal["install", "runtime"], ...] | None = Field(
        default=None, min_length=1, strict=False
    )
    #: The daemon's own variable the value is read from (`os.environ`),
    #: never the worker env. Unset: the worker env's `env`.
    source: Annotated[StrictStr, AfterValidator(_env_name)] | None = None

    @field_validator("phase")
    @classmethod
    def _one_way_to_write_a_phase(cls, phase):
        """Sorted and unique."""
        return tuple(sorted(set(phase))) if phase else phase

    def phases(self) -> tuple[str, ...]:
        return self.phase or ("install", "runtime")

    @model_serializer(mode="wrap")
    def _dump(self, handler) -> dict:
        return {
            k: list(v) if isinstance(v, tuple) else v
            for k, v in _without_unset(handler, self).items()
            # () in Python mode, [] in JSON mode (`GET /repos`, which the
            # Settings screen saves back): unset either way.
            if v not in ((), [])
        }


#: The sentinel a credential that names none of its own is given.
DEFAULT_SENTINEL = "kraft-proxy-managed"


def _allows(phase: NetworkPhase, domain: str) -> bool:
    """`domain` is written in `phase`'s allow list by name (any port), not
    only reached through a wildcard, and not written in its deny list."""

    def named(hosts: tuple[str, ...]) -> bool:
        return any(h.lower().split(":")[0] == domain.lower() for h in hosts)

    return named(phase.allow) and not named(phase.deny)


#: A Kit reference pinned by digest: `<name>[:<tag>]@sha256:<64 hex>`.
KIT_REF = r"^[^@\s]+@sha256:[0-9a-f]{64}$"
#: What `kind: kit` refuses beside the Kit.
_KIT_REFUSES = ("image", "network", "resources", "credentials", "unrestricted_network")


class SandboxPolicy(BaseModel):
    """Where a task's process runs: `kind: docker` in `image`, within
    `resources` and reaching only what `network` allows
    (`kraft.worker.backends`), with `credentials` managed by the egress
    proxy; or `kind: kit`, a Kit pinned by digest that sets all of those
    itself and is lowered to `kind: docker` at launch (`worker.kit.lower`).
    A permission-shaped safety field (Ruling 105): once a layer sets one, no
    narrower layer may change or remove it -- the whole value, `resources`,
    `network`, `credentials` and a Kit's reference included."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    #: `docker` runs `image` under this policy's own fields; `kit` runs a
    #: Docker Sandbox Kit, lowered to `docker` at launch (`worker.kit`).
    kind: Literal["docker", "kit"]
    #: `kind: docker` only.
    image: StrictStr | None = Field(default=None, min_length=1)
    #: `kind: kit` only: the runtime that runs the Kit.
    runtime: Literal["docker"] | None = None
    #: `kind: kit` only: the Kit image, pinned by digest (spec §9.3).
    kit: StrictStr | None = Field(default=None, pattern=KIT_REF)
    #: CPU, memory and process limits on every sandboxed run, the setup
    #: command's included.
    resources: SandboxResources | None = None
    #: Egress, deny-by-default once set. Unset: open, as it always was.
    network: SandboxNetwork | None = None
    #: Knowingly launching with no `network:`: Kraft sets up no channel and
    #: does not restrict the container's network, so nothing keeps a worker
    #: from reaching Kraft's API and gates are not enforced. Without this or
    #: `network`, the launch is refused (`adapters.agent.run_agent_task`).
    unrestricted_network: StrictBool = False
    #: Credentials the container holds only as a sentinel, the egress proxy
    #: injecting the real value (spec §6). Needs `network`: without it
    #: there is no proxy to inject them.
    credentials: tuple[SandboxCredential, ...] | None = Field(default=None, strict=False)

    @field_validator("resources")
    @classmethod
    def _no_empty_resources(cls, value: SandboxResources | None) -> SandboxResources | None:
        """`resources: {}` sets no limit, so it is the same sandbox as none:
        equal under the equality lock, and dumped without the key."""
        return None if value == SandboxResources() else value

    @field_validator("network", mode="before")
    @classmethod
    def _no_empty_network(cls, value: object) -> object:
        """Only a literal `network: {}` is no policy (open, as if unset). A
        phase written out, even with empty lists, denies everything."""
        return None if value == {} else value

    @field_validator("credentials")
    @classmethod
    def _no_empty_credentials(cls, value: tuple | None) -> tuple | None:
        """`credentials: []` manages none: the same sandbox as no key."""
        return value or None

    @model_validator(mode="after")
    def _network_or_unrestricted(self) -> SandboxPolicy:
        if self.unrestricted_network and self.network is not None:
            raise ValueError(
                "'unrestricted_network' and 'network' contradict: the first accepts a "
                "container with no egress policy, the second is one"
            )
        return self

    @model_validator(mode="after")
    def _credentials_are_reachable(self) -> SandboxPolicy:
        """credential@1's rule (spec §6): a repository's own
        credential goes only to a host this policy itself names, in a phase
        it allows, never one reached through a wildcard or a harness's hosts
        alone. One named by `env:` alone relies on its harness instead."""
        if not self.credentials:
            return self
        if self.network is None:
            raise ValueError("'credentials' needs 'network': only the egress proxy injects one")
        # Per phase: credential@1 lets one name serve two entries whose
        # phases do not overlap. An unphased entry is in both.
        seen: set[tuple[str, str]] = set()
        targets: set[tuple[str, str, str]] = set()
        for cred in self.credentials:
            phases = cred.phases()
            if any((cred.env, p) in seen for p in phases):
                raise ValueError(f"credential {cred.env!r} is listed twice in one phase")
            seen.update((cred.env, p) for p in phases)
            for rule in cred.inject:
                # credential@1: a phased credential needs its host in every
                # phase it lists; an unphased one in either.
                allowed = [_allows(getattr(self.network, p), rule.domain) for p in phases]
                if not (all(allowed) if cred.phase else any(allowed)):
                    where = f"in its phase {', '.join(phases)}" if cred.phase else "in either phase"
                    raise ValueError(
                        f"credential {cred.env!r} goes to {rule.domain!r}, which 'network' "
                        f"does not allow by name {where}"
                    )
                target = (rule.domain.lower(), rule.header.lower())
                if any((*target, p) in targets for p in phases):
                    raise ValueError(
                        f"two credentials set header {rule.header!r} on {rule.domain!r}"
                    )
                targets.update((*target, p) for p in phases)
        return self

    @model_serializer(mode="wrap")
    def _dump(self, handler) -> dict:
        """Unset fields are left out, so a sandbox with no `resources` dumps
        (and is frozen into a snapshot, and saved back to repos.yaml) as it
        always did. `credentials` as a list: YAML's safe dumper refuses a
        tuple."""
        dumped = _without_unset(handler, self)
        if not dumped.get("unrestricted_network"):
            dumped.pop("unrestricted_network", None)
        if "credentials" in dumped:
            dumped["credentials"] = list(dumped["credentials"])
        return dumped

    @model_validator(mode="before")
    @classmethod
    def _said_plainly(cls, data: object) -> object:
        """The refusals an operator has always read for a malformed
        `sandbox:`, rather than pydantic's literal and union prose."""
        if isinstance(data, SandboxPolicy):
            return data
        if not isinstance(data, dict):
            raise ValueError(f"must be a mapping, not {data!r}")
        known = sorted(get_args(cls.model_fields["kind"].annotation))
        if data.get("kind") not in known:
            raise ValueError(f"kind {data.get('kind')!r} is not supported; known: {known}")
        if data["kind"] == "kit":
            # The Kit is the single source of what the sandbox reaches and
            # holds (spec §9.3, open question 4).
            if local := [k for k in _KIT_REFUSES if data.get(k) is not None]:
                raise ValueError(
                    f"kind: kit takes no {', '.join(map(repr, local))}: the Kit sets it"
                )
            if data.get("runtime") is None:
                raise ValueError("kind: kit needs 'runtime' (docker)")
            if not isinstance(data.get("kit"), str) or not re.fullmatch(KIT_REF, data["kit"]):
                raise ValueError(
                    f"kind: kit needs 'kit' pinned by digest, <name>[:<tag>]@sha256:<64 hex>, "
                    f"not {data.get('kit')!r}"
                )
            return data
        if local := [k for k in ("runtime", "kit") if data.get(k) is not None]:
            raise ValueError(f"kind: docker takes no {', '.join(map(repr, local))}")
        if not isinstance(data.get("image"), str) or not data["image"]:
            raise ValueError("needs a non-empty string 'image'")
        return data


class TaskPolicyOverride(BaseModel):
    """The policy fields a task consumes, and so the whole of what a step, a
    task or a gate may override: every field here is read when a task
    launches (`MaterializedChain.policy_for`). A sparse patch onto the policy
    above it; `policy-override-rules-are-field-specific` is enforced once, in
    `InstancePolicy.apply_template_override`, not reinvented per scope.

    No `max_attempts`/`timeout_minutes`: those bound an execution node's fix
    loop (`TemplatePolicyOverride`), and a scope with no fix loop of its own
    refuses them at load rather than accept a value nothing reads
    (Kraft-q55aw)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    allowed_harnesses: list[StrictStr] | None = None
    #: The tokens, input plus output, the launches inside this scope may
    #: spend before the next one is refused (Ruling 195: this scope's own
    #: spend, not the whole item's).
    token_budget: PositiveInt | None = None
    #: The same, in dollars. A launch whose cost the harness never reported
    #: is unknown spend, never free: a scope with some cannot be shown to be
    #: under this cap, so its next launch stops for a human.
    budget_usd: PositiveUsd | None = None
    allowed_tools: ToolNames | None = None
    #: Tools no task under this scope may use, on top of whatever
    #: `allowed_tools` permits (Ruling 105: the repository's `deny_tools`).
    #: Only ever accumulates down the layers.
    deny_tools: ToolNames | None = None
    #: Operations guaranteed to this scope's tasks (Kraft-4in7z). Only ever
    #: accumulates down the layers, like deny_tools.
    grants: GrantNames | None = None
    sandbox: SandboxPolicy | None = None
    #: This scope's running time: a task's one run, a step's or node's task
    #: runs since it started, the work item's since it started (`kraft.caps`).
    #: Paused, waiting, gate and rate-limited time never counts.
    time_cap_minutes: PositiveInt | None = None
    #: This scope's wall clock: its running time plus every wait, gate and
    #: rate limit, less only a manual pause. For a task other than a wait that
    #: is its running time; for a wait it is the wait's timeout (Ruling 196).
    total_time_cap_minutes: PositiveInt | None = None

    @model_validator(mode="before")
    @classmethod
    def _retired_wait_timeout(cls, data: object) -> object:
        """A template or a stored override written before Ruling 196 still
        reads: a scope's `wait_timeout_minutes` becomes that scope's
        `total_time_cap_minutes`. A work item's override refuses it on a write
        and maps its item-wide value itself (`WorkItemPolicy`)."""
        if issubclass(cls, WorkItemPolicy):
            return data
        return _carry_retired(data, "policy", "total_time_cap_minutes")


class TemplatePolicyOverride(TaskPolicyOverride):
    """A repository, work-item, chain or execution-node override: a task's
    fields plus the two that bound a node's fix loop (`walk.walk_node`) --
    `max_attempts`, its attempt cap, and `timeout_minutes`, its wall clock."""

    timeout_minutes: PositiveInt | None = None
    max_attempts: PositiveInt | None = None
    #: `PolicyDefaultsInput.escalation_harness`, for this scope: an escalation
    #: turn runs under the policy of the node its item stopped at.
    escalation_harness: StrictStr | None = None

    @classmethod
    def meet(cls, overrides: Iterable[TaskPolicyOverride]) -> TemplatePolicyOverride:
        """The one layer as tight as every one of `overrides` at once, field
        by field: allowlists intersect, deny lists union, numbers take their
        minimum. What binds a task in a workspace's assembled checkout, which
        holds every selected repository at once (Kraft-jc39p). Two different
        sandboxes have no meet -- no process runs in both -- so that refuses."""
        overrides = list(overrides)

        def present(name: str) -> list:
            return [v for o in overrides if (v := getattr(o, name, None)) is not None]

        def common(name: str) -> list[str] | None:
            lists = present(name)
            return (
                [t for t in lists[0] if all(t in other for other in lists[1:])] if lists else None
            )

        sandboxes = list(dict.fromkeys(present("sandbox")))
        if len(sandboxes) > 1:
            raise PolicyError(
                f"'sandbox': the repositories set different sandboxes "
                f"{[s.model_dump() for s in sandboxes]}, and a task cannot run in all of them",
                field="sandbox",
            )
        escalation = list(dict.fromkeys(present("escalation_harness")))
        if len(escalation) > 1:
            raise PolicyError(
                f"'escalation_harness': the repositories set different harnesses "
                f"{escalation}, and an item escalates on one",
                field="escalation_harness",
            )
        deny = [t for tools in present("deny_tools") for t in tools]
        return cls(
            allowed_tools=common("allowed_tools"),
            allowed_harnesses=common("allowed_harnesses"),
            deny_tools=list(dict.fromkeys(deny)) or None,
            # Unlike an allowlist, an unset grant list grants nothing, so a
            # repository that names none empties the meet.
            grants=[g for g in GRANTS if all(g in (o.grants or ()) for o in overrides)] or None
            if overrides
            else None,
            sandbox=sandboxes[0] if sandboxes else None,
            escalation_harness=escalation[0] if escalation else None,
            **{
                n: min(values) if (values := present(n)) else None
                for n in (*BUDGET_FIELDS, "timeout_minutes", "max_attempts", *CAP_FIELDS)
            },
        )


#: The safety fields an item layer meets rather than ratchets (Ruling 188):
#: `deny_tools` already only accumulates and `sandbox` already locks, so those
#: two go through `apply_template_override` unchanged. A time cap meets too: an
#: item's cap tightens every scope under it that set a looser one, and an item
#: cap above the one it lands on is refused where the item is filed
#: (`ResolvedChain.check_scopes`), never met.
_ORDERLESS_SAFETY_FIELDS = ("allowed_tools", "grants", *BUDGET_FIELDS, *CAP_FIELDS)


#: An item-wide `budget_usd` of `"none"`: no dollar cap on the work item, nor
#: on any scope under it the chain set none for, the way an item-wide number
#: replaces theirs -- still under `maxima`, which refuses it where one is set
#: (Kraft-tugdf.12). The one door past an unknown-spend stop from a chain,
#: repository or `policy.yaml` default cap. Resolved, it is infinity.
NO_CAP = "none"


def _cap(value: object) -> object:
    return math.inf if value == NO_CAP else value


class WorkItemPolicy(TemplatePolicyOverride):
    """One work item's own override (Kraft-ab1bh): item-wide fields, plus
    `paths` -- an override for one node, step or task, keyed by its canonical
    path. Set at intake or by a `PATCH`, and held on the item's row, never in
    its snapshot or its template: `MaterializedChain.with_item_policy`
    validates it, and `MaterializedChain.policy_for` applies it after every
    scope the chain authored (`apply_to`, Ruling 188)."""

    paths: dict[StrictStr, TemplatePolicyOverride] = Field(default_factory=dict)
    #: Item-wide it may also be `"none"` (`NO_CAP`), never on a path.
    budget_usd: PositiveUsd | Literal["none"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _retired_wait_timeout_item(cls, data: object, info: ValidationInfo) -> object:
        """`wait_timeout_minutes` is retired (Ruling 196). A write refuses it,
        item-wide or on a path, naming what replaced it. A stored override
        still reads: a path's value becomes that path's
        `total_time_cap_minutes` (`TaskPolicyOverride`), and the item-wide one
        -- which meant every wait -- is spread over the item's wait tasks by
        `store.policy_override_of`, which knows the chain; one still here is
        dropped with a warning."""
        if not isinstance(data, dict):
            return data
        paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
        where = [
            f"policy.{RETIRED_WAIT_TIMEOUT}" if RETIRED_WAIT_TIMEOUT in data else None,
            *(
                f"policy.paths.{p}.{RETIRED_WAIT_TIMEOUT}"
                for p, layer in paths.items()
                if isinstance(layer, dict) and RETIRED_WAIT_TIMEOUT in layer
            ),
        ]
        where = [w for w in where if w]
        if not where:
            return data
        if not (info.context or {}).get("frozen"):
            raise ValueError(
                f"{where[0]} is retired: a wait's timeout is its task's own "
                "total_time_cap_minutes, so set that on the wait task's path"
            )
        if RETIRED_WAIT_TIMEOUT in data:
            data = {k: v for k, v in data.items() if k != RETIRED_WAIT_TIMEOUT}
            logger.warning(
                "dropped an item-wide %s it had no chain to spread over", RETIRED_WAIT_TIMEOUT
            )
        return data

    def apply_to(
        self, policy: InstancePolicy, path: str, scopes: Iterable[TaskPolicyOverride] = ()
    ) -> InstancePolicy:
        """`policy` -- a scope's, every authored layer already applied -- with
        this item's layers at `path` on top (Ruling 188). Operational fields
        apply in order, last, so the item's value wins over the template's
        within the maxima. Safety fields combine in no order: an allowlist
        intersects, a deny list unions, a budget takes the minimum, and a
        sandbox locks -- so an item's safety value only ever tightens, and is
        never refused because a narrower scope already narrowed it.

        A time cap is the item's own at its level (Ruling 198): item-wide it
        is the work item's cap, replacing the one the repository or chain
        gave it -- and so every level's default (Ruling 211) -- for every
        scope in `scopes`, the chain's layers over `path`, that set none, and
        meeting any scope's own; on a path it only tightens."""
        own = {n for n in SCOPE_CAP_FIELDS if any(getattr(s, n) is not None for s in scopes)}
        for where, layer in self.layers_at(path):
            if where == "policy":
                for name in SCOPE_CAP_FIELDS:
                    value = _cap(getattr(layer, name))
                    if value is not None and name not in own:
                        policy = dataclasses.replace(policy, **{name: value})
            policy = policy.apply_template_override(
                layer.model_copy(update=dict.fromkeys(_ORDERLESS_SAFETY_FIELDS))
            )
            if layer.allowed_tools is not None:
                allowed = policy.allowed_tools
                policy = dataclasses.replace(
                    policy,
                    allowed_tools=tuple(
                        t
                        for t in (allowed if allowed is not None else layer.allowed_tools)
                        if t in layer.allowed_tools
                    ),
                )
            # Grants widen, so an item's own layer may only drop them, never
            # add one (Kraft-4in7z.7): what a task is granted is authored.
            if layer.grants is not None:
                policy = dataclasses.replace(
                    policy, grants=tuple(g for g in policy.grants if g in layer.grants)
                )
            for name in (*BUDGET_FIELDS, *CAP_FIELDS):
                value, current = _cap(getattr(layer, name)), getattr(policy, name)
                if value is not None:
                    policy = dataclasses.replace(
                        policy, **{name: min(current, value) if current is not None else value}
                    )
        return policy

    def layers_at(self, path: str) -> tuple[tuple[str, TaskPolicyOverride], ...]:
        """The layers that bind the scope at `path`, broadest first, each with
        the field prefix its refusal names: this item-wide override, then the
        override of every path enclosing `path` or equal to it. Its own
        `paths` are ignored when this is applied as a layer: the override
        engine reads only the fields it knows."""
        segments = path.split(".")
        enclosing = (".".join(segments[:i]) for i in range(1, len(segments) + 1))
        return (
            ("policy", self),
            *((f"policy.paths.{p}", self.paths[p]) for p in enclosing if p in self.paths),
        )

    def value_at(self, path: str, name: str) -> object:
        """The narrowest value of `name` this override sets for `path`, or None."""
        values = (getattr(layer, name, None) for _, layer in reversed(self.layers_at(path)))
        return next((v for v in values if v is not None), None)


@dataclass(frozen=True)
class InstancePolicy:
    """One resolved policy state: the effective value of every field, plus
    the untouched administrator maxima every later `apply_template_override`
    call must still respect. A ratchet-only safety field with no `defaults:`
    entry (`allowed_tools`) starts *at* its maximum -- there is nothing to
    narrow it from until the first override does. A cap (`SCOPE_CAP_FIELDS`)
    holds only what a layer set -- a repository, the chain, a node, step or
    task, the work item's override -- and `at_level` fills in the rest from
    its level's default, else its maximum (Ruling 211).
    `allowed_harnesses` is different: it is operational-with-a-maximum, not
    ratchet-only, so its bound for widening is always `maxima`, never the
    current inherited value (see `_OPERATIONAL_LIST_FIELDS`)."""

    timeout_minutes: int | None
    max_attempts: int | None
    allowed_harnesses: tuple[str, ...] | None
    token_budget: int | None
    allowed_tools: tuple[str, ...] | None
    maxima: PolicyMaximaInput
    budget_usd: float | None = None
    #: Instance policy sets neither: `maxima:` has no deny list and no sandbox,
    #: so both start empty and only a repository or narrower layer adds them.
    deny_tools: tuple[str, ...] = ()
    #: Named grants (`GRANTS`); like `deny_tools`, only a layer adds them.
    grants: tuple[str, ...] = ()
    sandbox: SandboxPolicy | None = None
    time_cap_minutes: int | None = None
    total_time_cap_minutes: int | None = None
    #: The per-level cap defaults (Ruling 211), for `at_level`.
    cap_defaults: CapLevels = field(default_factory=CapLevels)
    #: What an escalation turn at this scope runs on (Kraft-wge0e).
    escalation_harness: str = DEFAULT_ESCALATION_HARNESS
    #: `defaults.escalation_grants`, frozen with the snapshot like the rest.
    escalation_grants: tuple[str, ...] = DEFAULT_ESCALATION_GRANTS

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
            allowed_tools=tuple(m.allowed_tools) if m.allowed_tools is not None else None,
            # A cap holds only what a layer sets; `at_level` fills the rest in.
            token_budget=None,
            maxima=m,
            cap_defaults=CapLevels(**{level: getattr(d, level) for level in CAP_LEVELS}),
            escalation_harness=d.escalation_harness or DEFAULT_ESCALATION_HARNESS,
            escalation_grants=tuple(d.escalation_grants)
            if d.escalation_grants is not None
            else DEFAULT_ESCALATION_GRANTS,
        )

    def at_level(self, level: str) -> InstancePolicy:
        """This policy's caps as a scope of `level` runs under them (Ruling
        211): the value a layer set -- its own or one it inherits from the
        chain, an enclosing node, step or task, or the work item's override --
        else `level`'s default, else its maximum; and never past that
        maximum. A scope's own value past it is refused when the chain is
        checked (`ResolvedChain._check_caps`); an inherited one is held to it
        here."""
        updates = {}
        for name in SCOPE_CAP_FIELDS:
            value = getattr(self, name)
            if value is None:
                value = getattr(getattr(self.cap_defaults, level), name)
            bound = self.maxima.nearest(level, name)
            if bound is not None:
                value = bound[1] if value is None else min(value, bound[1])
            updates[name] = value
        return dataclasses.replace(self, **updates)

    def layered(self, overrides: Iterable[TaskPolicyOverride]) -> InstancePolicy:
        """This policy with each of `overrides` applied in turn, broadest
        first (`policy-is-layered-by-execution-scope`)."""
        policy = self
        for override in overrides:
            policy = policy.apply_template_override(override)
        return policy

    def apply_template_override(self, raw: TaskPolicyOverride | dict) -> InstancePolicy:
        """Layer `raw` onto this policy, field by field
        (`policy-override-rules-are-field-specific`): a ratchet-only safety
        field (`allowed_tools`, `token_budget`) may only narrow the inherited
        value; an operational field (`timeout_minutes`, `max_attempts`,
        `allowed_harnesses`) may move either way but not past an explicitly
        configured administrator maximum
        (`work-item-policy-may-exceed-default-ceilings-within-admin-maximum`)
        -- for `allowed_harnesses` that bound is always `maxima`, so a
        `defaults:` value narrower than `maxima:` can still be widened back
        up to it. `deny_tools` only accumulates, and a `sandbox` a broader
        layer set cannot be changed (Ruling 105). The same method resolves a
        repository override over the instance policy, a work-item override
        over that, and so on (`policy-is-layered-by-execution-scope`)."""
        override = (
            raw
            if isinstance(raw, TaskPolicyOverride)
            else TemplatePolicyOverride.model_validate(raw)
        )
        updates: dict[str, object] = {}

        if getattr(override, "escalation_harness", None) is not None:
            updates["escalation_harness"] = override.escalation_harness

        if override.deny_tools is not None:
            updates["deny_tools"] = tuple(dict.fromkeys((*self.deny_tools, *override.deny_tools)))
        if override.grants is not None:
            updates["grants"] = tuple(dict.fromkeys((*self.grants, *override.grants)))

        if override.sandbox is not None:
            if self.sandbox is not None and override.sandbox != self.sandbox:
                raise PolicyError(
                    f"'sandbox' cannot change the inherited sandbox {self.sandbox.model_dump()!r}",
                    field="sandbox",
                )
            updates["sandbox"] = override.sandbox

        for field_name in _SAFETY_LIST_FIELDS:
            value = getattr(override, field_name)
            if value is None:
                continue
            ceiling = getattr(self, field_name)
            if ceiling is not None and not set(value) <= set(ceiling):
                extra = sorted(set(value) - set(ceiling))
                verb = "is" if len(extra) == 1 else "are"
                raise PolicyError(
                    f"'{field_name}' cannot widen the inherited safety ceiling "
                    f"{sorted(ceiling)!r}; {extra} {verb} not allowed",
                    field=field_name,
                )
            updates[field_name] = tuple(value)

        for field_name in _SAFETY_NUMERIC_FIELDS:
            value = getattr(override, field_name)
            if value is None:
                continue
            ceiling = getattr(self, field_name)
            if ceiling is not None and value > ceiling:
                raise PolicyError(
                    f"'{field_name}' cannot exceed the inherited safety ceiling {ceiling}",
                    field=field_name,
                )
            updates[field_name] = value

        for field_name in _OPERATIONAL_NUMERIC_FIELDS:
            value = getattr(override, field_name, None)
            if value is None:
                continue
            admin_max = getattr(self.maxima, field_name)
            if admin_max is not None and value > admin_max:
                raise PolicyError(
                    f"'{field_name}' cannot exceed the administrator maximum {admin_max}",
                    field=field_name,
                )
            updates[field_name] = value

        for field_name in SCOPE_CAP_FIELDS:
            value = getattr(override, field_name)
            if value is None:
                continue
            bound = self.maxima.nearest("work_item", field_name)
            admin_max = bound[1] if bound is not None else None
            # Bounded by `maxima` alone here, as an operational value is: a
            # default is not a ceiling (Ruling 198). The work item's maximum is
            # the broadest; a narrower scope's own level maximum, and a
            # child's cap staying at or under its parent scope's, are
            # `ResolvedChain._check_caps`, which knows the scope and names it.
            if admin_max is not None and value > admin_max:
                raise PolicyError(
                    f"'{field_name}' {value} cannot exceed the administrator maximum {admin_max}",
                    field=field_name,
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
                    f"{sorted(admin_max)!r}; {extra} {verb} not allowed",
                    field=field_name,
                )
            updates[field_name] = tuple(value)

        return dataclasses.replace(self, **updates)


# `PolicyInput.defaults`/`.maxima` are forward references to the V1 models
# above, which are defined after it: one loader for `policy.yaml` (Ruling 37)
# means the legacy schema has to carry the V1 sections, and the V1 models sit
# with the override engine that reads them.
PolicyInput.model_rebuild()


@dataclass(frozen=True)
class CarriedPolicy:
    """A pre-V1 `policy.yaml` moved onto the V1 seed's (Ruling 172): the seed,
    with the operator's value for every key the V1 schema still has, and every
    key it no longer has -- or whose value it refuses -- dropped and named with
    the value it had, so a major update never resets a spend cap or a timeout
    without saying so.

    `loops:` is the one section whose *keys* changed: a legacy fix-loop cap
    (`verify_fix_loop`, `rebase_bounce`, ...) names a loop V1 never runs, so an
    entry is carried only when the V1 seed names the same loop."""

    data: dict
    #: Dotted key -> the value it had.
    dropped: dict[str, object]

    @classmethod
    def from_legacy(cls, legacy: dict, seed: dict) -> CarriedPolicy:
        data = {key: value for key, value in seed.items()}
        dropped: dict[str, object] = {}
        live_loops = set(seed.get("loops") or {})
        for key, value in legacy.items():
            if value is None:
                continue
            if key not in PolicyInput.model_fields:
                dropped[key] = value
            elif key == "loops" and isinstance(value, dict):
                loops = dict(data.get("loops") or {})
                for name, cap in value.items():
                    if name in live_loops:
                        loops[name] = cap
                    else:
                        dropped[f"loops.{name}"] = cap
                data["loops"] = loops
            else:
                data[key] = value
        # A carried value V1 refuses goes too: the result always loads.
        while True:
            try:
                PolicyInput.model_validate(data)
                return cls(data, dropped)
            except ValidationError as exc:
                loc = exc.errors()[0]["loc"]
                if not loc or loc[0] not in legacy:
                    raise
                key, *rest = loc
                if key == "loops" and rest:
                    # One loop's cap, not the section: put back the seed's.
                    name = rest[0]
                    dropped[f"loops.{name}"] = data["loops"].pop(name)
                    if name in live_loops:
                        data["loops"][name] = seed["loops"][name]
                    continue
                dropped[key] = legacy[key]
                if key in seed:
                    data[key] = seed[key]
                else:
                    del data[key]
