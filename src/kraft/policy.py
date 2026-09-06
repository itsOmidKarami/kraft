from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from kraft.findings import SEVERITIES


class PolicyError(Exception):
    pass


DEFAULT_LOOP_SEVERITIES = frozenset({"critical", "important"})


@dataclass(frozen=True)
class Cap:
    attempts: int
    wall_clock_s: int
    #: Fix cycles past this one launch on the hook's `escalate_model` instead of
    #: its `model` (sub-project G spec 6). `None` is "never escalate", which is
    #: the behaviour of every policy.yaml written before this existed.
    escalate_after: int | None = None


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
    return Policy(
        loops=loops,
        default=_cap("default", data["default"]),
        loop_severities=severities,
        budget=budget,
    )


def resolve_cap(policy: Policy, key: str) -> Cap:
    return policy.loops.get(key, policy.default)


def check(*, count: int, started_at: str, cap: Cap, now: str) -> str:
    if count > cap.attempts:
        return "breached"
    elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds()
    if elapsed >= cap.wall_clock_s:
        return "breached"
    return "ok"
