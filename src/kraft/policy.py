from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class Cap:
    attempts: int
    wall_clock_s: int


@dataclass(frozen=True)
class Policy:
    loops: dict[str, Cap]
    default: Cap


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
    return Cap(attempts=attempts, wall_clock_s=wall_clock_s)


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"{path.name}: cannot read/parse: {exc}") from exc
    if not isinstance(data, dict) or "default" not in data:
        raise PolicyError(f"{path.name}: expected a mapping with a 'default' cap")
    loops_raw = data.get("loops") or {}
    if not isinstance(loops_raw, dict):
        raise PolicyError(f"{path.name}: 'loops' must be a mapping")
    loops = {k: _cap(f"loops.{k}", v) for k, v in loops_raw.items()}
    return Policy(loops=loops, default=_cap("default", data["default"]))


def resolve_cap(policy: Policy, key: str) -> Cap:
    return policy.loops.get(key, policy.default)


def check(*, count: int, started_at: str, cap: Cap, now: str) -> str:
    if count > cap.attempts:
        return "breached"
    elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds()
    if elapsed >= cap.wall_clock_s:
        return "breached"
    return "ok"
