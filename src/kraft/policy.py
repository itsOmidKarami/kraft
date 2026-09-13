from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from kraft.findings import SEVERITIES


class PolicyError(Exception):
    pass


DEFAULT_LOOP_SEVERITIES = frozenset({"critical", "important"})
DEFAULT_AUTO_ESCALATE_STUCK_CAP = 3


@dataclass(frozen=True)
class Cap:
    attempts: int
    wall_clock_s: int
    #: Fix cycles past this one launch on the hook's `escalate_model` instead of
    #: its `model` (sub-project G spec 6). `None` is "never escalate", which is
    #: the behaviour of every policy.yaml written before this existed.
    escalate_after: int | None = None


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


@dataclass(frozen=True)
class Budget:
    """Spend caps, in dollars. `None` is "no cap", never "zero".

    Not a field on `Cap`: a `Cap` is per-loop and is snapshotted per
    `(work_item_id, key)` row in `retry_counters`, while a budget is per work
    item and per day and spans every loop in the chain.
    """

    work_item_usd: float | None = None
    daily_usd: float | None = None


#: What a caller with no policy at all evaluates against.
NO_BUDGET = Budget()


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


def _cap(name: str, raw: object) -> Cap:
    if not isinstance(raw, dict):
        raise PolicyError(f"{name}: expected a mapping with 'attempts' and 'wall_clock_s'")
    try:
        attempts = raw["attempts"]
        wall_clock_s = raw["wall_clock_s"]
    except KeyError as exc:
        raise PolicyError(f"{name}: missing 'attempts' or 'wall_clock_s'") from exc
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise PolicyError(f"{name}: 'attempts' must be a positive int")
    if not isinstance(wall_clock_s, int) or isinstance(wall_clock_s, bool) or wall_clock_s < 1:
        raise PolicyError(f"{name}: 'wall_clock_s' must be a positive int")
    escalate_after = raw.get("escalate_after")
    if escalate_after is not None and (
        not isinstance(escalate_after, int)
        or isinstance(escalate_after, bool)
        or escalate_after < 1
    ):
        raise PolicyError(f"{name}: 'escalate_after' must be a positive int, or absent")
    return Cap(attempts=attempts, wall_clock_s=wall_clock_s, escalate_after=escalate_after)


def _usd(name: str, raw: object) -> float | None:
    if raw is None:
        return None
    if not isinstance(raw, int | float) or isinstance(raw, bool) or raw < 0:
        raise PolicyError(f"{name}: must be a non-negative number of dollars, or null")
    return float(raw)


def _budget(name: str, raw: object) -> Budget:
    if raw is None:
        return NO_BUDGET
    if not isinstance(raw, dict):
        raise PolicyError(f"{name}: expected a mapping")
    return Budget(
        work_item_usd=_usd(f"{name}.work_item_usd", raw.get("work_item_usd")),
        daily_usd=_usd(f"{name}.daily_usd", raw.get("daily_usd")),
    )


def _archive_after_days(name: str, raw: object) -> int | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PolicyError(f"{name}: expected a mapping")
    after_days = raw.get("after_days")
    if after_days is None:
        return None
    if not isinstance(after_days, int) or isinstance(after_days, bool) or after_days < 0:
        raise PolicyError(f"{name}: 'after_days' must be a non-negative int, or absent")
    return after_days


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


def _trigger(name: str, raw: object) -> Trigger:
    if not isinstance(raw, dict):
        raise PolicyError(f"{name}: expected a mapping")
    try:
        cron = raw["cron"]
        repo = raw["repo"]
        chain = raw["chain"]
        title = raw["title"]
    except KeyError as exc:
        raise PolicyError(f"{name}: missing 'cron', 'repo', 'chain', or 'title'") from exc
    for field_name, value in (("cron", cron), ("repo", repo), ("chain", chain), ("title", title)):
        if not isinstance(value, str):
            raise PolicyError(f"{name}: '{field_name}' must be a string")
    _cron_fields(f"{name}.cron", cron)
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise PolicyError(f"{name}: 'description' must be a string")
    return Trigger(cron=cron, repo=repo, chain=chain, title=title, description=description)


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    try:
        # ValueError covers UnicodeDecodeError: a policy file with one invalid
        # byte is bad config, not a crash three frames up in `lifespan`.
        data = yaml.safe_load(path.read_text())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise PolicyError(f"{path.name}: cannot read/parse: {exc}") from exc
    if not isinstance(data, dict) or "default" not in data:
        raise PolicyError(f"{path.name}: expected a mapping with a 'default' cap")
    loops_raw = data.get("loops") or {}
    if not isinstance(loops_raw, dict):
        raise PolicyError(f"{path.name}: 'loops' must be a mapping")
    loops = {k: _cap(f"loops.{k}", v) for k, v in loops_raw.items()}
    findings_raw = data.get("findings") or {}
    if not isinstance(findings_raw, dict):
        raise PolicyError(f"{path.name}: 'findings' must be a mapping")
    sev_raw = findings_raw.get("loop_severities")
    if sev_raw is None:
        severities = DEFAULT_LOOP_SEVERITIES
    else:
        if not isinstance(sev_raw, list):
            raise PolicyError(f"{path.name}: 'findings.loop_severities' must be a list")
        unknown = [s for s in sev_raw if s not in SEVERITIES]
        if unknown:
            raise PolicyError(
                f"{path.name}: unknown severity {unknown[0]!r}; expected one of {SEVERITIES}"
            )
        severities = frozenset(sev_raw)
    budget = _budget(f"{path.name}: 'budget'", data.get("budget"))
    raw_retries = data.get("rate_limit_retries", 5)
    if not isinstance(raw_retries, int) or isinstance(raw_retries, bool) or raw_retries < 1:
        raise PolicyError(f"{path.name}: 'rate_limit_retries' must be a positive int")
    triggers_raw = data.get("triggers") or []
    if not isinstance(triggers_raw, list):
        raise PolicyError(f"{path.name}: 'triggers' must be a list")
    triggers = [_trigger(f"{path.name}: triggers[{i}]", t) for i, t in enumerate(triggers_raw)]
    raw_mc = data.get("max_concurrent")
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
    if not isinstance(raw_mc, int) or isinstance(raw_mc, bool) or raw_mc < 1:
        raise PolicyError(f"{path.name}: 'max_concurrent' must be a positive int")
    archive_after_days = _archive_after_days(f"{path.name}: 'archive'", data.get("archive"))
    raw_aes = data.get("auto_escalate_stuck", True)
    if not isinstance(raw_aes, bool):
        raise PolicyError(f"{path.name}: 'auto_escalate_stuck' must be a bool")
    raw_aes_cap = data.get("auto_escalate_stuck_cap", DEFAULT_AUTO_ESCALATE_STUCK_CAP)
    if not isinstance(raw_aes_cap, int) or isinstance(raw_aes_cap, bool) or raw_aes_cap < 1:
        raise PolicyError(f"{path.name}: 'auto_escalate_stuck_cap' must be a positive int")
    raw_delay = data.get("auto_escalate_delay_s", 0)
    if not isinstance(raw_delay, int) or isinstance(raw_delay, bool) or raw_delay < 0:
        raise PolicyError(f"{path.name}: 'auto_escalate_delay_s' must be a non-negative int")
    return Policy(
        loops=loops,
        default=_cap("default", data["default"]),
        loop_severities=severities,
        budget=budget,
        archive_after_days=archive_after_days,
        rate_limit_retries=raw_retries,
        triggers=triggers,
        max_concurrent=raw_mc,
        auto_escalate_stuck=raw_aes,
        auto_escalate_stuck_cap=raw_aes_cap,
        auto_escalate_delay_s=raw_delay,
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
