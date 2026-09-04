"""Aggregates behind GET /analytics (design 6b).

Everything here is derived from the three tables that already exist — work
items, worker sessions and the event log — so the view needs no new writes
beyond the usage capture in `kraft.usage`.

Two derivations are worth stating outright, because neither is a stored fact:

* **A merge** is a completed node that ran the `on.merge` hook. There is no
  merge-request record in Kraft; the closest true statement is "the node whose
  job was to merge finished", so that is what is counted.
* **Human wait** is the time an item spent stopped on a person: from a
  `gate_requested` or `work_item_needs_human` to whatever unblocked it. It is
  reported separately from wall time so a slow reviewer never reads as a slow
  agent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

RANGES = {"7d": 7, "30d": 30, "90d": 90, "all": None}

#: Events that stop an item on a person, and the ones that start it again.
_BLOCKS = ("gate_requested", "work_item_needs_human")
_UNBLOCKS = ("gate_approved", "gate_rejected", "work_item_retried", "node_started")


def cutoff(range_: str, now: datetime | None = None) -> str | None:
    """ISO timestamp `range_` ago, or None for the whole history."""
    days = RANGES.get(range_, 7)
    if days is None:
        return None
    return ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat()


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _week_start(ts: str) -> str | None:
    """The Monday of `ts`'s week, as a date string."""
    d = _parse(ts)
    return (d - timedelta(days=d.weekday())).date().isoformat() if d else None


def _human_wait_ms(events: list[sqlite3.Row]) -> int:
    """Sum of the stopped-on-a-human spans in one item's event stream."""
    total = 0
    blocked_at: datetime | None = None
    for e in events:
        if e["type"] in _BLOCKS:
            # a second block while already blocked keeps the earlier start
            blocked_at = blocked_at or _parse(e["created_at"])
        elif e["type"] in _UNBLOCKS and blocked_at is not None:
            end = _parse(e["created_at"])
            if end is not None:
                total += max(0, int((end - blocked_at).total_seconds() * 1000))
            blocked_at = None
    return total


def compute(
    conn: sqlite3.Connection,
    *,
    range_: str = "7d",
    repo: str | None = None,
    template: str | None = None,
    now: datetime | None = None,
) -> dict:
    where = ["1 = 1"]
    args: list[object] = []
    since = cutoff(range_, now)
    if since:
        where.append("created_at >= ?")
        args.append(since)
    if repo:
        where.append("repo = ?")
        args.append(repo)
    if template:
        where.append("chain_template = ?")
        args.append(template)

    items = conn.execute(
        f"SELECT id, repo, chain_template, status, chain_definition FROM work_items "
        f"WHERE {' AND '.join(where)}",
        args,
    ).fetchall()
    ids = [r["id"] for r in items]

    totals = {
        "work_items": len(items),
        "by_status": {},
        "mrs_merged": 0,
        "wall_ms": 0,
        "human_wait_ms": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        # false once a session has spent tokens without reporting a cost: the sum
        # is then a floor, and the view says so rather than showing a total that
        # is quietly too low
        "cost_complete": True,
        "rounds": 0,
        "capped_out": 0,
    }
    by_node: dict[str, dict] = {}
    by_repo: dict[str, dict] = {}
    weekly: dict[str, int] = {}
    if not ids:
        return {"totals": totals, "weekly_merged": [], "by_node": [], "by_repo": []}

    for r in items:
        totals["by_status"][r["status"]] = totals["by_status"].get(r["status"], 0) + 1
        by_repo.setdefault(
            r["repo"],
            {
                "repo": r["repo"],
                "items": 0,
                "mrs": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "cost_complete": True,
            },
        )["items"] += 1

    # ── sessions: tokens, cost, wall time, rounds, caps ──────────────────────
    holes = ",".join("?" * len(ids))
    sessions = conn.execute(
        f"SELECT work_item_id, node_id, round, tokens_in, tokens_out, cost_usd, wall_ms, status "
        f"FROM worker_sessions WHERE work_item_id IN ({holes})",
        ids,
    ).fetchall()
    repo_of = {r["id"]: r["repo"] for r in items}
    node_rounds: dict[str, set[tuple[str, int]]] = {}
    item_node_rounds: dict[str, set[tuple[str, int]]] = {}
    for s in sessions:
        node = by_node.setdefault(
            s["node_id"],
            {
                "node": s["node_id"],
                "runs": 0,
                "wall_ms": 0,
                "avg_ms": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "cost_complete": True,
                "rounds": 0,
                "capped_out": 0,
            },
        )
        tok = (s["tokens_in"] or 0) + (s["tokens_out"] or 0)
        node["runs"] += 1
        node["wall_ms"] += s["wall_ms"] or 0
        node["tokens"] += tok
        node["cost_usd"] += s["cost_usd"] or 0.0
        node["capped_out"] += 1 if s["status"] == "capped_out" else 0
        if s["cost_usd"] is None and tok:
            node["cost_complete"] = False
            totals["cost_complete"] = False
            by_repo[repo_of[s["work_item_id"]]]["cost_complete"] = False
        node_rounds.setdefault(s["node_id"], set()).add((s["work_item_id"], s["round"] or 0))
        item_node_rounds.setdefault(s["work_item_id"], set()).add((s["node_id"], s["round"] or 0))

        totals["tokens_in"] += s["tokens_in"] or 0
        totals["tokens_out"] += s["tokens_out"] or 0
        totals["cost_usd"] += s["cost_usd"] or 0.0
        totals["wall_ms"] += s["wall_ms"] or 0
        totals["capped_out"] += 1 if s["status"] == "capped_out" else 0

        rr = by_repo[repo_of[s["work_item_id"]]]
        rr["tokens"] += tok
        rr["cost_usd"] += s["cost_usd"] or 0.0

    for node_id, seen in node_rounds.items():
        by_node[node_id]["rounds"] = len(seen)
    for node in by_node.values():
        node["avg_ms"] = node["wall_ms"] // node["runs"] if node["runs"] else 0
    # an item's rounds is the deepest single node's loop count, summed over items
    totals["rounds"] = sum(
        max((r for _, r in seen), default=0) for seen in item_node_rounds.values()
    )

    # ── events: merges and human wait ────────────────────────────────────────
    merge_nodes = {
        r["id"]: {
            n["id"]
            for n in json.loads(r["chain_definition"]).get("nodes", [])
            if "on.merge" in (n.get("tasks") or [])
        }
        for r in items
    }
    events = conn.execute(
        f"SELECT work_item_id, type, payload, created_at FROM events "
        f"WHERE work_item_id IN ({holes}) ORDER BY seq",
        ids,
    ).fetchall()
    per_item: dict[str, list[sqlite3.Row]] = {}
    for e in events:
        per_item.setdefault(e["work_item_id"], []).append(e)
        if e["type"] != "node_completed":
            continue
        node_id = json.loads(e["payload"]).get("node_id")
        if node_id in merge_nodes.get(e["work_item_id"], ()):
            totals["mrs_merged"] += 1
            by_repo[repo_of[e["work_item_id"]]]["mrs"] += 1
            week = _week_start(e["created_at"])
            if week:
                weekly[week] = weekly.get(week, 0) + 1
    for rows in per_item.values():
        totals["human_wait_ms"] += _human_wait_ms(rows)

    return {
        "totals": totals,
        "weekly_merged": [{"week_start": w, "n": n} for w, n in sorted(weekly.items())],
        "by_node": sorted(by_node.values(), key=lambda n: n["cost_usd"], reverse=True),
        "by_repo": sorted(by_repo.values(), key=lambda r: r["items"], reverse=True),
    }
