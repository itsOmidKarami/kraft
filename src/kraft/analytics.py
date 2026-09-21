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
* **`median_lead_ms`** (`work_item_created` → `work_item_completed`) now
  includes post-merge CI time. `post_merge_watch` (Kraft-43kw) is this
  chain's terminal node, and it can itself sit in `"waiting"` on the target
  branch's own pipeline -- time this figure did not count before, when
  `merge` was the last node.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import UTC, datetime, timedelta

from kraft.store._common import session_wall_ms, wait_timed_out_sessions

RANGES = {"7d": 7, "30d": 30, "90d": 90, "8w": 56, "all": None}

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


def _node_roles(row) -> tuple[set[str], set[str]]:
    """This item's (merge nodes, fix-loop nodes), by what each node does and
    never by its name (Kraft-hicln): a V1 row's `chain_definition` is `"{}"`
    and no seeded V1 node is called `verify`.

    V1 reads the materialized chain: a merge node runs a forge task targeting
    `mr.merge`, a fix-loop node declares a `fix_loop`. A legacy row, filed
    before V1 and still in the database, keeps the legacy reading of the same
    two facts: an `on.merge` task, a `fix_loop` key.
    """
    from kraft.store.chain import materialized_chain_of
    from kraft.templates.models import ForgeAction, ForgeTask

    chain = materialized_chain_of(row)
    if chain is not None:
        merge = {
            n.id
            for n in chain.chain.nodes
            if any(
                isinstance(t.task, ForgeTask) and t.task.target is ForgeAction.MR_MERGE
                for t in n.tasks()
            )
        }
        return merge, {n.id for n in chain.chain.nodes if n.fix_loop}
    nodes = json.loads(row["chain_definition"] or "{}").get("nodes", [])
    return (
        {n["id"] for n in nodes if "on.merge" in (n.get("tasks") or [])},
        {n["id"] for n in nodes if n.get("fix_loop")},
    )


def _week_start(ts: str) -> str | None:
    """The Monday of `ts`'s week, as a date string."""
    d = _parse(ts)
    return (d - timedelta(days=d.weekday())).date().isoformat() if d else None


def _completed_count_between(
    conn: sqlite3.Connection, *, start: str | None, end: str, repo: str | None, template: str | None
) -> int:
    """Completed items in `[start, end)`, same repo/template filter as the caller's
    main window. A dedicated query, not a slice of `items`: the caller's `items` list
    is bounded to the *current* window and this one looks one window further back."""
    where = ["status = 'completed'", "created_at < ?"]
    args: list[object] = [end]
    if start:
        where.append("created_at >= ?")
        args.append(start)
    if repo:
        where.append("repo = ?")
        args.append(repo)
    if template:
        where.append("chain_template = ?")
        args.append(template)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM work_items WHERE {' AND '.join(where)}", args
    ).fetchone()
    return row["n"]


#: Duplicated from `kraft.executor.gates._RUN_BOUNDARY` -- edit both
#: together. See that module's own docstring for the full account of why
#: each of these, and only these, counts as a human closing out a run of
#: stuck-ness; not imported here to keep this module free of the
#: executor/agent import stack for the sake of nine strings.
_RUN_BOUNDARY = (
    "work_item_retried",
    "work_item_resumed",
    "work_item_created",
    "pause_requested",
    "gate_approved",
    "gate_rejected",
    "work_item_completed",
    "work_item_abandoned",
    "work_item_restored",
)

#: Kraft-s15p0's own words -- the first (so far only) known orchestrator
#: defect signature, kept next to the classifier that reads it so a future
#: fixed defect's signature is added in the same place. `startswith`, not
#: `==`: a signature is a stable prefix, not a promise the rest of the
#: reason text never changes.
_DEFECT_SIGNATURES = ("resume: current-node session did not resolve cleanly",)

#: A `needs_context:` reason naming a blocker another item hasn't
#: merged/finished yet -- the ordering-conflict class Kraft-tsfpk removes for
#: the bd-dependency case specifically, still possible for conflicts bd
#: doesn't model (two items editing the same file). A heuristic on free
#: text, same idiom as the `capped`/`needs_context:` checks below it --
#: stated as such, not claimed exact.
_BLOCKER_PHRASES = ("blocked on", "not merged", "depends on")

#: Buckets that count as an unplanned touch whether or not an escalation
#: happened to clear them -- the stop itself is the defect (or a spend cap a
#: human always has to raise), not who noticed it. Every other non-gate
#: bucket instead asks `_resolved_without_human`.
#:
#: Counts per stop *event*, not deduplicated per underlying incident
#: (plan-review finding 5): the same orchestrator defect auto-escalating
#: five times with nobody paged reads as five unplanned touches here, not
#: one. Deliberate for a MINOR-severity ask -- collapsing repeats of "the
#: same defect" needs a notion of incident identity (a signature match plus
#: some adjacency window) this event log does not carry today, on top of
#: the `_DEFECT_SIGNATURES` heuristic already guessing at "same defect" from
#: free text. Stated here rather than silently undercounted.
_ALWAYS_UNPLANNED = frozenset({"orchestrator/infra defect", "preventable dispatch", "budget"})


def _episode_bucket(e: sqlite3.Row) -> str | None:
    """Which of the five stop buckets one `gate_requested`/
    `work_item_needs_human` event belongs to, or None if `e` is not a stop
    event at all (design 35, widened by Kraft-ishe from three buckets to
    five).

    `budget` and `capped` are mutually exclusive tags `mark_needs_human`
    sets itself and are checked ahead of the free-text heuristics below them,
    so a budget stop never falls through to `task failure`'s catch-all.
    """
    payload = json.loads(e["payload"])
    if e["type"] == "gate_requested":
        return f"gate · {payload.get('gate', 'unknown')}"
    if e["type"] != "work_item_needs_human":
        return None
    reason = payload.get("reason") or ""
    if payload.get("budget") is not None:
        return "budget"
    if payload.get("capped") is not None:
        return "cap breach"
    if any(reason.startswith(sig) for sig in _DEFECT_SIGNATURES):
        return "orchestrator/infra defect"
    if reason.startswith("needs_context:") and any(p in reason.lower() for p in _BLOCKER_PHRASES):
        return "preventable dispatch"
    return "task failure"


def _stop_reasons(events: list[sqlite3.Row]) -> list[dict]:
    """'Why items stopped for a person' (design 35, widened by Kraft-ishe):
    five buckets -- see `_episode_bucket`. Reasons that fit no bucket
    (nothing does, today) would be dropped; there is no such reason left to
    drop since `task failure` is now the catch-all."""
    counts: dict[str, int] = {}
    for e in events:
        label = _episode_bucket(e)
        if label is None:
            continue
        counts[label] = counts.get(label, 0) + 1
    return sorted(({"label": label, "n": n} for label, n in counts.items()), key=lambda x: -x["n"])


def _resolved_without_human(item_events: list[sqlite3.Row], stop_index: int) -> bool:
    """True iff the stop at `item_events[stop_index]` was cleared by the
    auto-escalate machinery alone -- nobody paged.

    Forward scan to the first `_RUN_BOUNDARY`-shaped event after the stop:
    the same idiom `kraft.executor.gates._auto_dispatch_count` walks in
    reverse from "now" to answer "has this still-open run already been
    escalated" -- here walked forward once per already-closed historical
    stop instead of backward from the live edge. The first boundary decides
    it either way: a self-retry or an agent's own gate decision reads
    identically to a real one except for the tag on that one event.
    """
    for e in item_events[stop_index + 1 :]:
        t = e["type"]
        if t not in _RUN_BOUNDARY:
            continue
        payload = json.loads(e["payload"])
        if t == "work_item_retried" and payload.get("escalated"):
            return True
        # `== "agent"` and deliberately not `in ("agent", "assistant")`
        # (Kraft-s7c04.43). `agent` is Kraft's own gate auto-review -- the
        # machinery unblocking itself, nobody paged. An `assistant` cleared the
        # gate because a person told it to, so a person was paged and this is
        # the touch the metric exists to count.
        if t in ("gate_approved", "gate_rejected") and payload.get("by") == "agent":
            return True
        return False
    return False


def _unplanned_touches(item_events: list[sqlite3.Row]) -> int:
    """Unplanned human touches in one item's own timeline (Kraft-ishe): a
    stop nobody designed the chain to need, counted once per stop *event* --
    not deduplicated across repeats of what is really the same underlying
    incident (plan-review finding 5; see `_ALWAYS_UNPLANNED`'s own comment
    for why that dedup is not done here).

    `gate · <name>` never counts -- a gate is the chain's designed
    checkpoint, not a rescue. `orchestrator/infra defect`, `preventable
    dispatch` and `budget` count unconditionally (`_ALWAYS_UNPLANNED`).
    Every other bucket (`cap breach`, `task failure`) counts unless
    `_resolved_without_human` says nobody was paged.
    """
    touches = 0
    for i, e in enumerate(item_events):
        bucket = _episode_bucket(e)
        if bucket is None or bucket.startswith("gate · "):
            continue
        if bucket in _ALWAYS_UNPLANNED or not _resolved_without_human(item_events, i):
            touches += 1
    return touches


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
    days = RANGES.get(range_)
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
        f"SELECT id, repo, chain_template, status, chain_definition, materialized_chain, "
        f"created_at, updated_at FROM work_items WHERE {' AND '.join(where)}",
        args,
    ).fetchall()
    ids = [r["id"] for r in items]

    totals = {
        "work_items": len(items),
        # items with at least one worker_sessions row. `work_items` counts the
        # backlog too, and dividing money by the backlog understates the
        # average by every item that never started a node (Kraft-g2pi).
        "work_items_run": 0,
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
        "wait_timed_out": 0,
        "completed": 0,
        "median_lead_ms": 0,
        "human_wait_pct": 0,
        "fix_cycles": 0.0,
        "fix_cycles_capped": 0,
        "rejected_gates": 0,
        "unplanned_touches_per_item": 0.0,
        "open_mr_to_green_ci_ms": 0,
    }
    totals["completed_prev"] = None
    if days is not None and since is not None:
        now_ = now or datetime.now(UTC)
        prev_start = (now_ - timedelta(days=2 * days)).isoformat()
        totals["completed_prev"] = _completed_count_between(
            conn, start=prev_start, end=since, repo=repo, template=template
        )
    by_node: dict[str, dict] = {}
    by_repo: dict[str, dict] = {}
    weekly: dict[str, int] = {}
    if not ids:
        return {
            "totals": totals,
            "weekly_merged": [],
            "by_node": [],
            "by_repo": [],
            "rejected_gates_by_gate": [],
            "stop_reasons": [],
        }

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
                "done": 0,
                "cycles": 0.0,
            },
        )["items"] += 1
        if r["status"] == "completed":
            by_repo[r["repo"]]["done"] += 1

    completed_items = [r for r in items if r["status"] == "completed"]
    totals["completed"] = len(completed_items)

    # ── sessions: tokens, cost, wall time, rounds, caps ──────────────────────
    holes = ",".join("?" * len(ids))
    sessions = conn.execute(
        f"SELECT id, work_item_id, node_id, round, tokens_in, tokens_out, cost_usd, wall_ms, "
        f"status, started_at, created_at, exited_at "
        f"FROM worker_sessions WHERE work_item_id IN ({holes})",
        ids,
    ).fetchall()
    repo_of = {r["id"]: r["repo"] for r in items}
    # A wait that ran out also exits `capped_out`; it is counted on its own,
    # never as a fix loop's cap (Kraft-uwbc8).
    timed_out = wait_timed_out_sessions(conn, ids)
    node_rounds: dict[str, set[tuple[str, int]]] = {}
    item_node_rounds: dict[str, set[tuple[str, int]]] = {}
    node_capped_items: dict[str, set[str]] = {}
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
                "wait_timed_out": 0,
            },
        )
        tok = (s["tokens_in"] or 0) + (s["tokens_out"] or 0)
        # Derived when the column is NULL: only `session_exited` writes it, and
        # a paused session never gets there -- 71 rows, every one of them with
        # the stamps to answer with (Kraft-s7c04.18). This rollup is the one
        # rendered as "minutes per node", so the loss was visible.
        wall = session_wall_ms(s) or 0
        node["runs"] += 1
        node["wall_ms"] += wall
        node["tokens"] += tok
        node["cost_usd"] += s["cost_usd"] or 0.0
        waited_out = s["id"] in timed_out
        capped = s["status"] == "capped_out" and not waited_out
        node["capped_out"] += 1 if capped else 0
        node["wait_timed_out"] += 1 if waited_out else 0
        if capped:
            node_capped_items.setdefault(s["node_id"], set()).add(s["work_item_id"])
        if s["cost_usd"] is None and tok:
            node["cost_complete"] = False
            totals["cost_complete"] = False
            by_repo[repo_of[s["work_item_id"]]]["cost_complete"] = False
        node_rounds.setdefault(s["node_id"], set()).add((s["work_item_id"], s["round"] or 0))
        item_node_rounds.setdefault(s["work_item_id"], set()).add((s["node_id"], s["round"] or 0))

        totals["tokens_in"] += s["tokens_in"] or 0
        totals["tokens_out"] += s["tokens_out"] or 0
        totals["cost_usd"] += s["cost_usd"] or 0.0
        totals["wall_ms"] += wall
        totals["capped_out"] += 1 if capped else 0
        totals["wait_timed_out"] += 1 if waited_out else 0

        rr = by_repo[repo_of[s["work_item_id"]]]
        rr["tokens"] += tok
        rr["cost_usd"] += s["cost_usd"] or 0.0

    for node_id, seen in node_rounds.items():
        by_node[node_id]["rounds"] = len(seen)
    roles = {r["id"]: _node_roles(r) for r in items}
    loop_rounds = {
        (wid, node_id, rnd)
        for node_id, seen in node_rounds.items()
        for wid, rnd in seen
        if node_id in roles[wid][1]
    }
    if loop_rounds:
        loop_items = {wid for wid, _, _ in loop_rounds}
        totals["fix_cycles"] = round(len(loop_rounds) / len(loop_items), 1)
        totals["fix_cycles_capped"] = len(
            {
                wid
                for node_id, wids in node_capped_items.items()
                for wid in wids
                if node_id in roles[wid][1]
            }
        )
    for node in by_node.values():
        node["avg_ms"] = node["wall_ms"] // node["runs"] if node["runs"] else 0
    # an item's rounds is the deepest single node's loop count, summed over items
    totals["rounds"] = sum(
        max((r for _, r in seen), default=0) for seen in item_node_rounds.values()
    )
    totals["work_items_run"] = len(item_node_rounds)

    repo_item_rounds: dict[str, list[int]] = {}
    for wid, seen in item_node_rounds.items():
        repo_item_rounds.setdefault(repo_of[wid], []).append(max((r for _, r in seen), default=0))
    for repo_key, rr in by_repo.items():
        rl = repo_item_rounds.get(repo_key, [])
        rr["cycles"] = round(sum(rl) / len(rl), 1) if rl else 0.0

    # ── events: merges and human wait ────────────────────────────────────────
    merge_nodes = {wid: merge for wid, (merge, _) in roles.items()}
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

    # `updated_at` is not the completion time: `archive_work_item` bumps it on
    # archival, and a done item auto-archives after 30 days, so it would read
    # 30+ days of extra lead time. `work_item_completed`'s own timestamp is
    # the true end.
    lead_times_ms: list[int] = []
    for r in completed_items:
        c = _parse(r["created_at"])
        end = next(
            (
                _parse(e["created_at"])
                for e in per_item.get(r["id"], [])
                if e["type"] == "work_item_completed"
            ),
            None,
        )
        if c and end:
            lead_times_ms.append(max(0, int((end - c).total_seconds() * 1000)))
    if lead_times_ms:
        totals["median_lead_ms"] = int(statistics.median(lead_times_ms))

    completed_ids = {r["id"] for r in completed_items}
    completed_human_wait_ms = sum(_human_wait_ms(per_item.get(cid, [])) for cid in completed_ids)
    total_lead_ms = sum(lead_times_ms)
    if total_lead_ms:
        totals["human_wait_pct"] = round(100 * completed_human_wait_ms / total_lead_ms)

    total_unplanned = sum(_unplanned_touches(per_item.get(cid, [])) for cid in completed_ids)
    if completed_items:
        totals["unplanned_touches_per_item"] = round(total_unplanned / len(completed_items), 2)

    open_to_green_ms: list[int] = []
    for r in completed_items:
        evts = per_item.get(r["id"], [])
        start = next(
            (
                _parse(e["created_at"])
                for e in evts
                if e["type"] == "node_started"
                and json.loads(e["payload"]).get("node_id") == "open_mr"
            ),
            None,
        )
        end = next(
            (
                _parse(e["created_at"])
                for e in evts
                if e["type"] == "node_completed"
                and json.loads(e["payload"]).get("node_id") == "mr_checks"
            ),
            None,
        )
        if start and end:
            open_to_green_ms.append(max(0, int((end - start).total_seconds() * 1000)))
    if open_to_green_ms:
        totals["open_mr_to_green_ci_ms"] = int(statistics.median(open_to_green_ms))

    rejected_by_gate: dict[str, int] = {}
    for e in events:
        if e["type"] == "gate_rejected":
            gate = json.loads(e["payload"]).get("gate", "unknown")
            rejected_by_gate[gate] = rejected_by_gate.get(gate, 0) + 1
    totals["rejected_gates"] = sum(rejected_by_gate.values())
    rejected_gates_by_gate = sorted(
        ({"gate": g, "n": n} for g, n in rejected_by_gate.items()), key=lambda x: -x["n"]
    )
    stop_reasons = _stop_reasons(events)

    return {
        "totals": totals,
        "weekly_merged": [{"week_start": w, "n": n} for w, n in sorted(weekly.items())],
        "by_node": sorted(by_node.values(), key=lambda n: n["cost_usd"], reverse=True),
        "by_repo": sorted(by_repo.values(), key=lambda r: r["items"], reverse=True),
        "rejected_gates_by_gate": rejected_gates_by_gate,
        "stop_reasons": stop_reasons,
    }
