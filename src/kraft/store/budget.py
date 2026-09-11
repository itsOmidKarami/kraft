from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def usage_rollup(conn: sqlite3.Connection, work_item_id: str) -> dict:
    """Per-node and per-item usage for one work item (handoff spec §8).

    Rounds count *distinct* fix cycles seen on the node, not sessions: a node
    that ran three tasks in one pass has run one round, not three.

    `cost_complete` is false when some session spent tokens but reported no cost
    — cost comes only from the agent, so a sum over those is a floor, not a
    total. Treating a missing cost as zero would quietly under-report the bill,
    which is the one thing a cost figure must not do.
    """
    rows = conn.execute(
        "SELECT node_id, round, tokens_in, tokens_out, cost_usd, wall_ms, status "
        "FROM worker_sessions WHERE work_item_id = ?",
        (work_item_id,),
    ).fetchall()
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
                "cost_complete": True,
            },
        )
        node["tokens_in"] += r["tokens_in"] or 0
        node["tokens_out"] += r["tokens_out"] or 0
        node["cost_usd"] += r["cost_usd"] or 0.0
        # a session that spent tokens but reported no cost makes the sum a floor
        if r["cost_usd"] is None and (r["tokens_in"] or r["tokens_out"]):
            node["cost_complete"] = False
        node["wall_ms"] += r["wall_ms"] or 0
        node["sessions"] += 1
        node["capped_out"] += 1 if r["status"] == "capped_out" else 0
        rounds.setdefault(r["node_id"], set()).add(r["round"] or 0)
    for node_id, seen in rounds.items():
        by_node[node_id]["rounds"] = len(seen)

    nodes = list(by_node.values())
    total = {
        k: sum(n[k] for n in nodes)
        for k in ("tokens_in", "tokens_out", "cost_usd", "wall_ms", "sessions", "capped_out")
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
