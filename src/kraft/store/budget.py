from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

from kraft import caps as _caps
from kraft import events
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.store._common import session_wall_ms, wait_timed_out_sessions


def usage_rollup(conn: sqlite3.Connection, work_item_id: str) -> dict:
    """Per-node and per-item usage for one work item (handoff spec §8).

    Rounds count *distinct* fix cycles seen on the node, not sessions: a node
    that ran three tasks in one pass has run one round, not three.

    `cost_complete` is false when some session spent tokens but reported no cost
    — cost comes only from the agent, so a sum over those is a floor, not a
    total. Treating a missing cost as zero would quietly under-report the bill,
    which is the one thing a cost figure must not do.

    `wall_ms` is derived when the column is NULL (`_common.session_wall_ms`) --
    unlike cost, time is knowable for a session that never reported, because
    the row carries the same two stamps `session_exited` would have used.
    """
    rows = conn.execute(
        "SELECT id, node_id, round, tokens_in, tokens_out, cost_usd, wall_ms, status, "
        "started_at, created_at, exited_at "
        "FROM worker_sessions WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchall()
    timed_out = wait_timed_out_sessions(conn, [work_item_id])
    # A time cap's stop is its own outcome too (Ruling 194), never a loop's cap.
    time_capped = _caps.time_capped_sessions(conn, [work_item_id])
    by_node: dict[str, dict] = {}
    rounds: dict[str, set[int]] = {}
    for r in rows:
        node = by_node.setdefault(
            r["node_id"],
            {
                "node": r["node_id"],
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "wall_ms": 0,
                "sessions": 0,
                "rounds": 0,
                "capped_out": 0,
                "wait_timed_out": 0,
                "time_capped": 0,
                "cost_complete": True,
            },
        )
        node["tokens_in"] += r["tokens_in"] or 0
        node["tokens_out"] += r["tokens_out"] or 0
        node["cost_usd"] += r["cost_usd"] or 0.0
        # a session that spent tokens but reported no cost makes the sum a floor
        if r["cost_usd"] is None and (r["tokens_in"] or r["tokens_out"]):
            node["cost_complete"] = False
        # Not `r["wall_ms"] or 0`: only `session_exited` writes that column, so
        # a paused, skipped, capped or still-running session contributed
        # nothing and a node that had been running 75 minutes summed to 0
        # (Kraft-s7c04.18, .47). The stamps are on the row either way.
        node["wall_ms"] += session_wall_ms(r) or 0
        node["sessions"] += 1
        # A wait that ran out is not a loop that ran out (Kraft-uwbc8).
        waited_out = r["id"] in timed_out
        cut = r["id"] in time_capped
        node["wait_timed_out"] += 1 if waited_out else 0
        node["time_capped"] += 1 if cut else 0
        node["capped_out"] += 1 if r["status"] == "capped_out" and not (waited_out or cut) else 0
        rounds.setdefault(r["node_id"], set()).add(r["round"] or 0)
    for node_id, seen in rounds.items():
        by_node[node_id]["rounds"] = len(seen)

    nodes = list(by_node.values())
    total = {
        k: sum(n[k] for n in nodes)
        for k in (
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "wall_ms",
            "sessions",
            "capped_out",
            "wait_timed_out",
            "time_capped",
        )
    }
    # An item's rounds is the deepest a single node had to loop, not the sum:
    # summing would read as "this item retried nine times" for nine clean nodes.
    total["rounds"] = max((n["rounds"] for n in nodes), default=0)
    total["cost_complete"] = all(n["cost_complete"] for n in nodes)
    return {"total": total, "by_node": sorted(nodes, key=lambda n: n["node"])}


def local_midnight_utc(now: datetime | None = None) -> str:
    """Midnight of `now`'s own day, in `now`'s own zone, as a UTC ISO string.

    "Daily" means the operator's day, not UTC's — a cap that rolls over at 5pm
    local is a cap nobody can reason about. Normalized to UTC on the way out so
    it compares as a string against the `created_at` values `_now()` writes.

    ponytail: the offset is the one in force *now*, not the one in force at
    midnight, so on a DST-transition day the window starts an hour early or
    late. A spend cap does not care; if something here ever does, resolve the
    offset at the midnight instant instead.
    """
    now = now or datetime.now().astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).isoformat()


def budget_spend(
    conn: sqlite3.Connection, work_item_id: str, *, since: str | None = None
) -> tuple[float, float]:
    """`(spent on this work item, spent instance-wide since `since`)`, in dollars.

    A query, not a counter: `worker_sessions.cost_usd` is already the source of
    truth and a parallel counter is a second thing to get wrong. A NULL
    `cost_usd` — a session still running, or a subprocess or builtin task that
    has no cost — contributes zero, so this is a floor on in-flight spend by
    construction.
    """
    item = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM worker_sessions WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchone()[0]
    if since is None:
        daily = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM worker_sessions").fetchone()[
            0
        ]
    else:
        daily = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM worker_sessions WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]
    return float(item), float(daily)


def effective_work_item_cap(row, policy_budget) -> tuple[float | None, str]:
    """`(cap_usd, source)` for one work item -- `source` is `"item"` when the
    item has its own cap (set or explicitly cleared to "no cap"), else
    `"policy"` for `policy_budget.work_item_usd` (UI v2 · 04, point 4).

    `row["budget_set"]` is what makes an explicit "no cap" (`budget_usd`
    NULL, `budget_set` 1) distinguishable from "never customized" (`budget_usd`
    NULL, `budget_set` 0, defer to policy) -- a bare nullable column alone
    cannot tell those apart.
    """
    if row["budget_set"]:
        return row["budget_usd"], "item"
    return policy_budget.work_item_usd, "policy"


def effective_budget(row, policy_budget):
    """`policy_budget` with `work_item_usd` replaced by this item's effective
    cap (`effective_work_item_cap`). `daily_usd` is always policy-wide --
    there is no per-item daily cap to override.
    """
    cap, _source = effective_work_item_cap(row, policy_budget)
    return replace(policy_budget, work_item_usd=cap)


def set_budget(conn: sqlite3.Connection, work_item_id: str, budget_usd: float | None) -> None:
    """Set this item's own spend cap: a number, or `None` for an explicit "no
    cap" (point 4). Always marks `budget_set`, so the item's choice -- even
    "no cap" -- is never confused with "not customized, use the policy
    default".
    """
    conn.execute(
        "UPDATE work_items SET budget_set = 1, budget_usd = ?, updated_at = ? WHERE id = ?",
        (budget_usd, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "budget_changed", {"budget_usd": budget_usd})


def raise_budget(conn: sqlite3.Connection, work_item_id: str, budget_usd: float | None) -> None:
    """Raise the cap and note it as its own event (Prototype `raiseBudget`,
    point 5) -- same write as `set_budget`, but distinguishing the timeline
    entry: this fires from the "raise budget and continue" action on a
    `needs_human`/budget item, `set_budget` from the Config tab.
    """
    conn.execute(
        "UPDATE work_items SET budget_set = 1, budget_usd = ?, updated_at = ? WHERE id = ?",
        (budget_usd, _now(), work_item_id),
    )
    events.append(conn, work_item_id, "budget_raised", {"budget_usd": budget_usd})
