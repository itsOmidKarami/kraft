"""The `policy` draft area (W13 F): `policy.yaml`, and what each value resolves to.

`resolved` answers one `{value, source}` per key (a cap's `source` is the level
that sets it; a dollar cap's `binding` names the cap that bounds), so the page
does not re-derive layering. Triggers are the intake draft's; `harnesses` and
`other` are shown here read-only."""

from __future__ import annotations

import re

from kraft import render, storage
from kraft.api import config_check
from kraft.cap_levels import CAP_LEVELS, SCOPE_CAP_FIELDS
from kraft.drafts import config, harnesses, policy_caps, store
from kraft.drafts.ops import OpError
from kraft.executor.walk import _loop_key
from kraft.findings import SEVERITIES
from kraft.policy import PolicyError
from kraft.templates.models import PATH_SEPARATOR, ExecNode

NAME = "policy"
FILE = "policy.yaml"
FILES = (FILE,)

ESCALATION = (
    "auto_escalate_stuck",
    "auto_escalate_stuck_cap",
    "auto_escalate_delay_s",
    "auto_review_attempts",
)
RETRIES = ("rate_limit_retries", "forge_cli_timeout_s")
#: The `default:` block, `max_concurrent` and `archive`, which the shipped Policy
#: page edits and `defaults:`/`maxima:` do not hold, and the findings that burn a cycle.
LOOP_DEFAULT = ("default.attempts", "default.wall_clock_s")
HOUSEKEEPING = (
    "max_concurrent",
    "archive.after_days",
    "storage.worktrees.limit",
    "storage.worktrees.quota",
    "storage.worktrees.auto_cleanup.min_age",
)
FINDINGS = ("findings.loop_severities",)
SECTION_FIELDS = ("max_attempts", "timeout_minutes")
HARNESS_FIELDS = ("allowed_harnesses", "allowed_tools", "escalation_harness", "escalation_grants")


def working(st, key, files, *, exists):
    return config.ConfigDraft(st, key, files, exists=exists, names=FILES)


# ── ops ──


def _editable(scope: str, key: str) -> bool:
    parts = key.split(PATH_SEPARATOR)
    if scope == "escalation":
        return key in ESCALATION
    if scope == "retries":
        return key in RETRIES
    if scope == "loops":
        return key in LOOP_DEFAULT
    if scope == "housekeeping":
        return key in HOUSEKEEPING
    if scope == "findings":
        return key in FINDINGS
    if scope != "limits":
        return False
    if parts[0] == "budget":
        return len(parts) == 2 and parts[1] in ("work_item_usd", "daily_usd")
    if parts[0] not in ("defaults", "maxima"):
        return False
    if len(parts) == 2:
        return parts[1] in SECTION_FIELDS
    return len(parts) == 3 and parts[1] in CAP_LEVELS and parts[2] in SCOPE_CAP_FIELDS


def set_value(d, scope: str, key: str, value=None) -> None:
    """A fragment at `key`'s path; null removes it and any section it leaves empty."""
    if not _editable(scope, key):
        raise OpError(f"{key} is not a {scope} setting this draft edits")
    *parents, leaf = key.split(PATH_SEPARATOR)
    trail = [d.file(FILE)]
    for name in parents:
        nxt = trail[-1].get(name)
        if nxt is None and value is not None:
            nxt = trail[-1][name] = {}
        if not isinstance(nxt, dict):
            if value is None:
                return
            raise OpError(f"{FILE}: {name} is not a mapping")
        trail.append(nxt)
    if value is not None:
        trail[-1][leaf] = value
        return
    trail[-1].pop(leaf, None)
    for depth in range(len(parents), 0, -1):
        if trail[depth]:
            break
        del trail[depth - 1][parents[depth - 1]]


def _loops(d) -> dict:
    loops = d.file(FILE).setdefault("loops", {})
    if not isinstance(loops, dict):
        raise OpError(f"{FILE}: loops is not a mapping")
    return loops


def set_loop(d, key: str, max_attempts: int | None = None, wall_clock_s: int | None = None) -> None:
    """The loop's own entry, starting from `default:`. A key that names no live
    loop is kept and shown as a problem."""
    loops = _loops(d)
    entry = dict(loops.get(key) or d.read(FILE).get("default") or {})
    if max_attempts is not None:
        entry["attempts"] = max_attempts
    if wall_clock_s is not None:
        entry["wall_clock_s"] = wall_clock_s
    loops[key] = entry


def remove_loop(d, key: str) -> None:
    loops = _loops(d)
    if key not in loops:
        raise OpError(f"{key} is not in loops")
    del loops[key]


OPS = {"set_value": set_value, "set_loop": set_loop, "remove_loop": remove_loop}


# ── resolve ──


def _at(raw, *path):
    for p in path:
        raw = raw.get(p) if isinstance(raw, dict) else None
    return raw


def _leaf(raw: dict, value, *path) -> dict:
    return {"value": value, "source": "policy" if _at(raw, *path) is not None else "default"}


def _caps(parsed) -> dict:
    """Per level and cap field, the default (that level's own) and the maximum
    (the nearest level at or above it that sets one; null is "no bound")."""
    out = {}
    for level in CAP_LEVELS:
        out[level] = {}
        for name in SCOPE_CAP_FIELDS:
            own = getattr(getattr(parsed.defaults, level), name)
            bound = parsed.maxima.nearest(level, name)
            out[level][name] = {
                "default": {"value": own, "source": level if own is not None else None},
                "maximum": {
                    "value": bound[1] if bound else None,
                    "source": bound[0] if bound else None,
                },
                "below": [],
            }
    return out


def _work_item_usd(parsed) -> dict:
    """Every dollar cap that bounds a work item; the lowest binds."""
    caps = {
        "budget.work_item_usd": parsed.budget.work_item_usd if parsed.budget else None,
        "maxima.work_item.budget_usd": parsed.maxima.work_item.budget_usd,
        "defaults.work_item.budget_usd": parsed.defaults.work_item.budget_usd,
    }
    set_ = {k: v for k, v in caps.items() if v is not None}
    if not set_:
        return {"value": None, "source": "default", "binding": None}
    key = min(set_, key=set_.__getitem__)
    return {"value": set_[key], "source": "policy", "binding": {"key": key, "value": set_[key]}}


def live_loops(st) -> list[dict]:
    """Every fix loop of every chain in the library, under the key
    `policy.yaml`'s `loops:` names it by."""
    library = getattr(st, "library", None)
    out = []
    for chain in library.chain_ids if library is not None else ():
        try:
            resolved = library.resolve_chain(chain)
        except Exception:  # noqa: BLE001 -- a chain that does not resolve is the lint's problem
            continue
        out += [
            {"key": _loop_key(n), "chain": resolved.chain, "id": chain, "node": n}
            for n in resolved.nodes
            if isinstance(n.node, ExecNode) and n.node.fix_loop is not None
        ]
    return out


def _first(layers: list[tuple[str, object]], fallback: tuple[str, object]) -> dict:
    layer, value = next(((n, v) for n, v in layers if v is not None), fallback)
    return {"value": value, "source": layer}


def _loop_view(live: dict, parsed) -> dict:
    """The loop's attempts and wall clock, the first layer that sets one
    winning (`walk.fix_loop_cap`; a work item's own override is not here). A
    wall clock has no loop-level layer."""
    node, chain = live["node"].node, live["chain"]
    npol, cpol = node.policy, chain.policy
    instance = parsed.instance_policy()
    cap = parsed.loops.get(live["key"])
    base = ("loops", cap) if cap else ("default", parsed.default)
    attempts_from = "defaults" if parsed.defaults.max_attempts is not None else "maxima"
    minutes_from = "defaults" if parsed.defaults.timeout_minutes is not None else "maxima"
    minutes = [
        ("node", npol.timeout_minutes if npol else None),
        ("chain", cpol.timeout_minutes if cpol else None),
        (minutes_from, instance.timeout_minutes),
    ]
    return {
        "key": live["key"],
        "chain": live["id"],
        "node": live["node"].id,
        "attempts": _first(
            [
                ("loop", node.fix_loop.max_attempts),
                ("node", npol.max_attempts if npol else None),
                ("chain", cpol.max_attempts if cpol else None),
                (attempts_from, instance.max_attempts),
            ],
            (base[0], base[1].attempts),
        ),
        "wall_clock_s": _first(
            [(n, v * 60 if v is not None else None) for n, v in minutes],
            (base[0], base[1].wall_clock_s),
        ),
    }


def _group(field: str | None) -> tuple[str, str | None]:
    """The group a config problem is about, and the cap level when it names one."""
    parts = (field or "").split(PATH_SEPARATOR)
    top = parts[0]
    level = parts[1] if len(parts) > 1 and parts[1] in CAP_LEVELS else None
    if top in ("loops", "default", "findings"):
        return "loops", None
    if top in ("max_concurrent", "archive", "storage"):
        return "housekeeping", None
    if top in ESCALATION:
        return "escalation", None
    if top in RETRIES:
        return "retries", None
    if top in ("defaults", "maxima"):
        return ("harnesses" if parts[-1] in HARNESS_FIELDS else "limits"), level
    return ("limits" if top == "budget" else "other"), None


_KEY_IN_MESSAGE = re.compile(r"\b(?:defaults|maxima|budget)\.[\w.]+")


def _problem(field: str, message: str, scope: str) -> dict:
    return {
        "path": field,
        "field": field,
        "message": message,
        "file": FILE,
        "line": 1,
        "col": 1,
        "scope": scope,
        "level": None,
    }


def resolve(st, key, raw, files, published) -> dict:
    out = config.resolve_files(st, files, published, FILES, keyed=True)
    for p in out["problems"]:
        # A cross-field refusal has no field, but its message names the key.
        named = _KEY_IN_MESSAGE.search(p["message"])
        p["scope"], p["level"] = _group(p["field"] or (named and named[0]))
    data = raw.get(FILE) if isinstance(raw.get(FILE), dict) else {}
    try:
        parsed, policy = config_check.validate_policy(data)
    except PolicyError:
        return {**out, "resolved": None}  # the problem above says why

    live = live_loops(st)
    keys = {x["key"] for x in live}
    for k in parsed.loops:
        if k not in keys:
            msg = f"loops.{k} names no fix loop in the library (a loop is <node id>.fix_loop)"
            out["problems"].append(_problem(f"loops{PATH_SEPARATOR}{k}", msg, "loops"))
    lint = harnesses._lint_problems(st, data)
    out["problems"] += [{**p, "scope": "limits", "level": None} for p in lint]

    worktrees = parsed.storage.worktrees if parsed.storage else None

    def scalar(name: str) -> dict:
        return _leaf(data, getattr(policy, name), name)

    caps = _caps(parsed)
    for (level, cap), layers in policy_caps.below_for(st, parsed.instance_policy()).items():
        caps[level][cap]["below"] = layers
    out["resolved"] = {
        "limits": {
            "caps": caps,
            "work_item_usd": _work_item_usd(parsed),
            "daily_usd": _leaf(data, policy.budget.daily_usd, "budget", "daily_usd"),
            **{
                f: {
                    "default": _leaf(data, getattr(parsed.defaults, f), "defaults", f),
                    "maximum": _leaf(data, getattr(parsed.maxima, f), "maxima", f),
                }
                for f in SECTION_FIELDS
            },
        },
        "loops": {
            "default": {
                "attempts": parsed.default.attempts,
                "wall_clock_s": parsed.default.wall_clock_s,
            },
            "entries": [
                {
                    "key": k,
                    "attempts": c.attempts,
                    "wall_clock_s": c.wall_clock_s,
                    "live": k in keys,
                }
                for k, c in parsed.loops.items()
            ],
            "live": [_loop_view(x, parsed) for x in live],
        },
        "escalation": {k: scalar(k) for k in ESCALATION},
        "retries": {k: scalar(k) for k in RETRIES},
        "housekeeping": {
            "max_concurrent": scalar("max_concurrent"),
            "archive_after_days": _leaf(data, policy.archive_after_days, "archive", "after_days"),
            "storage_limit": _leaf(
                data, worktrees.limit if worktrees else None, "storage", "worktrees", "limit"
            ),
            "storage_quota": _leaf(
                data, worktrees.quota if worktrees else None, "storage", "worktrees", "quota"
            ),
            # What a blank quota is: 80% of the limit, for the page to show.
            "storage_quota_default": (
                render.human_size(policy.storage_quota_bytes)
                if policy.storage_limit_bytes is not None and not (worktrees and worktrees.quota)
                else None
            ),
            # The age floor in force, or null: automatic clean-up is off.
            "storage_auto_cleanup": _leaf(
                data,
                worktrees.auto_cleanup.min_age if worktrees and worktrees.auto_cleanup else None,
                "storage",
                "worktrees",
                "auto_cleanup",
            ),
        },
        "findings": {
            "loop_severities": _leaf(
                data,
                [s for s in SEVERITIES if s in policy.loop_severities],
                "findings",
                "loop_severities",
            )
        },
        "harnesses": {
            "defaults": parsed.defaults.model_dump(include=set(HARNESS_FIELDS)),
            "maxima": parsed.maxima.model_dump(include=set(HARNESS_FIELDS)),
        },
        "other": {
            "findings": data.get("findings") or {},
            "archive": data.get("archive") or {},
            "max_concurrent": policy.max_concurrent,
        },
    }
    return out


async def after_publish(app, written) -> None:
    config.reload_policy(app.state)
    storage.kick(app)


AREA = store.Area(
    valid=lambda key: key == NAME,
    files=lambda key: FILES,
    working=working,
    ops=OPS,
    after_publish=after_publish,
)
