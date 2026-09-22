"""Per-scope time caps (Rulings 194, 195, 196): measuring them, and stopping.

A time cap may sit on the work item (instance, repository, chain or the item's
own override), a node, a step or a task. Each one caps **its own scope**, and a
child's may not exceed its parent's (`ResolvedChain._check_caps` refuses that
when the item is filed). Two clocks:

* `time_cap_minutes` -- running time: the union of the scope's task runs, its
  sessions' spans. So paused, gate, external-wait and rate-limited time never
  counts: no session of the scope is running then. A wait task's own session
  spans its parks, so wait tasks are left out of every running clock (a wait
  has its own timeout), and so is a human's escalation chat, which runs only
  while the item is stopped. An automatic escalation turn is the node running
  (Kraft-8en38): it counts as its node's.
* `total_time_cap_minutes` -- wall clock from the scope's start, less only a
  manual pause (`pause_requested` until the next resume or retry).

Where each scope's clock starts:

* the work item: its first `node_started` since a human's last `/retry`;
* a node: its first `node_started` since then, or since it last completed or a
  gate rejection sent the walk back through it -- so its recovery, fix loop and
  escalation all count;
* a step: its first task run in that node pass, re-measures included;
* a task: its own run. Its total is its running time, except a wait's: that
  is the wait's timeout (`ForgeTask.wait_bounds`), enforced by `kraft.waits`.

Only a human's `/retry` (a `run_forked` its automation did not write) starts
every clock afresh: a rate-limit relaunch and a stuck escalation's own retry
are the machinery continuing, and must not reset a cap meant to bound it.

A cap binds as the tightest remaining one at a task's launch
(`at_launch`): refused outright when already spent, otherwise the running
process is killed at the deadline (`adapters.subprocess.run_task`). A cap that
runs out while nothing runs -- parked in a wait, a rate limit, or at a gate,
and a gate's own `timeout` -- is `tick`'s. Every hit writes `time_cap_reached`
and stops for a human under its own reason, spends no recovery or fix attempt,
and is outside the stuck set (Ruling 176).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from kraft import events, store
from kraft.policy import BUDGET_FIELDS, CAP_FIELDS

logger = logging.getLogger(__name__)

#: A task's status when a time cap stopped it or refused its launch. The
#: session row exits `capped_out` (no status migration); `REACHED` names it.
TIME_CAPPED = "time_capped"
REACHED = "time_cap_reached"
#: How often `poller` looks at parked items.
_INTERVAL_S = 30

_WHAT = {
    "time_cap_minutes": "time cap",
    "total_time_cap_minutes": "total time cap",
}


@dataclass(frozen=True)
class Hit:
    """The tightest cap over one launch or one parked item: the scope that set
    it (`""` for the work item), which field, and how long is left."""

    scope: str
    field: str
    minutes: int
    remaining_s: float

    @property
    def reason(self) -> str:
        where = f"`{self.scope}`" if self.scope else "the work item"
        if self.field == "timeout":
            return f"gate `{self.scope}` waited past its timeout of {self.minutes} minutes"
        return f"{where} hit its {_WHAT[self.field]} of {self.minutes} minutes"

    def payload(self, **extra) -> dict:
        return {
            "scope": self.scope,
            "field": self.field,
            "minutes": self.minutes,
            "reason": self.reason,
            **extra,
        }


def monotonic() -> float:
    """The clock a launch's deadline is set and checked on. A seam, so a
    test can run a minute's cap in a second without touching the event
    loop's own clock."""
    return time.monotonic()


@dataclass(frozen=True)
class Deadline:
    """When the launch `hit` bounds must be killed, on `monotonic()`."""

    at: float
    hit: Hit


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class _Timeline:
    """One work item's clocks, read once."""

    def __init__(self, conn, work_item_id: str, snapshot, now: datetime) -> None:
        self.now = now
        rows = conn.execute(
            "SELECT seq, type, payload, created_at FROM events WHERE work_item_id = ? AND type IN "
            "('run_forked', 'work_item_retried', 'node_started', 'node_completed', "
            "'gate_rejected', 'pause_requested', 'work_item_resumed') ORDER BY seq",
            (work_item_id,),
        ).fetchall()
        evts = [(r["type"], json.loads(r["payload"]), r["created_at"]) for r in rows]
        # The last human retry: a `run_forked` whose `work_item_retried` its own
        # automation did not write (`escalated`).
        self.since = ""
        escalated = False
        for kind, payload, at in evts:
            if kind == "work_item_retried":
                escalated = bool(payload.get("escalated"))
            elif kind == "run_forked" and not escalated:
                self.since = at
        self.events = [(k, p, at) for k, p, at in evts if at >= self.since]
        self.pauses: list[tuple[datetime, datetime]] = []
        paused_at = None
        for kind, _, at in self.events:
            if kind == "pause_requested" and paused_at is None:
                paused_at = _dt(at)
            elif kind in ("work_item_resumed", "work_item_retried") and paused_at is not None:
                self.pauses.append((paused_at, _dt(at)))
                paused_at = None
        if paused_at is not None:
            self.pauses.append((paused_at, now))
        # An automatic escalation turn is its node running; a person's is not.
        auto_turns = {
            json.loads(p).get("session_id")
            for (p,) in conn.execute(
                "SELECT payload FROM events WHERE work_item_id = ? AND type = 'escalation_message' "
                "AND json_extract(payload, '$.auto') = 1",
                (work_item_id,),
            ).fetchall()
        }
        skip = {
            t.path
            for n in snapshot.chain.nodes
            for t in n.tasks()
            if getattr(t.task, "target", None) is not None and t.task.target.waits
        }
        self.sessions = [
            (
                r["node_id"] if r["hook_point"] == "escalation" else r["hook_point"],
                r["created_at"],
                _dt(r["started_at"] or r["created_at"]),
                _dt(r["exited_at"]) if r["exited_at"] else now,
            )
            for r in conn.execute(
                "SELECT id, node_id, hook_point, created_at, started_at, exited_at "
                "FROM worker_sessions WHERE work_item_id = ? AND created_at >= ?",
                (work_item_id, self.since),
            ).fetchall()
            if r["hook_point"] not in skip
            and (r["hook_point"] != "escalation" or r["id"] in auto_turns)
        ]

    def node_since(self, node_id: str) -> str:
        """Where `node_id`'s current pass began. Any `gate_rejected` of the
        item ends it: that holds while one node is active per item, which the
        walk keeps."""
        ends = [
            at
            for kind, p, at in self.events
            if (kind == "node_completed" and p.get("node_id") == node_id) or kind == "gate_rejected"
        ]
        return max(ends, default=self.since)

    def _under(self, path: str, since: str):
        return [
            s
            for s in self.sessions
            if s[1] >= since and (not path or s[0] == path or s[0].startswith(path + "."))
        ]

    def running(self, path: str, since: str) -> float:
        """Seconds at least one session under `path` ran, since `since`."""
        total, reach = 0.0, None
        for start, end in sorted((s[2], s[3]) for s in self._under(path, since)):
            if reach is not None:
                start = max(start, reach)
            total += max(0.0, (end - start).total_seconds())
            reach = end if reach is None else max(reach, end)
        return total

    def wall(self, start: datetime | None) -> float:
        """Seconds since `start`, less every manual pause."""
        if start is None:
            return 0.0
        paused = sum(
            max(0.0, (min(end, self.now) - max(begin, start)).total_seconds())
            for begin, end in self.pauses
        )
        return max(0.0, (self.now - start).total_seconds() - paused)

    def started(self, path: str, kind: str, since: str) -> datetime | None:
        """When the scope at `path` began its current pass."""
        if kind in ("node", "gate", "item"):
            first = next(
                (
                    at
                    for k, p, at in self.events
                    if k == "node_started"
                    and at >= since
                    and (kind == "item" or p.get("node_id") == path)
                ),
                None,
            )
            return _dt(first) if first else None
        runs = self._under(path, since)
        return min((s[2] for s in runs), default=None)


def _levels(snapshot, path: str) -> list[tuple[str, str]]:
    """`(path, kind)` of the work item and each node and step enclosing the
    task at `path`, broadest first -- the scopes whose caps it runs under."""
    kinds = {p: k for p, k, _ in snapshot.chain.cap_scopes()}
    segments = path.split(".")
    enclosing = (".".join(segments[:i]) for i in range(1, len(segments)))
    return [
        ("", "item"),
        *((p, kinds[p]) for p in enclosing if kinds.get(p) in ("node", "gate", "step")),
    ]


def _explicit(snapshot):
    """`snapshot` as its enclosing scopes' clocks read it: an instance or
    repository cap is a default, not a ceiling (Ruling 198), so the work item,
    its nodes and steps are bound only by a cap the chain or the item's own
    override set, or by `maxima`. The default binds a task's own run
    (`at_launch`), where nothing more specific was set."""
    chain_own = snapshot.chain.chain.policy
    base = dataclasses.replace(
        snapshot.policy,
        **{
            n: (
                getattr(chain_own, n)
                if chain_own is not None and getattr(chain_own, n) is not None
                else getattr(snapshot.policy.maxima, n)
            )
            for n in (*CAP_FIELDS, *BUDGET_FIELDS)
        },
    )
    return dataclasses.replace(snapshot, policy=base)


def _root_policy(snapshot):
    item = snapshot.item_policy
    return item.apply_to(snapshot.policy, "") if item is not None else snapshot.policy


def _tightest(
    snapshot, line: _Timeline, levels: list[tuple[str, str]], fields=CAP_FIELDS
) -> Hit | None:
    """The cap with the least time left over `levels`, or None. A level whose
    cap is no tighter than the one above it adds nothing -- its clock can only
    be the shorter one -- so the broadest scope holding a value is the one
    named."""
    best: Hit | None = None
    above = dict.fromkeys(fields)
    for path, kind in levels:
        policy = _root_policy(snapshot) if kind == "item" else snapshot.policy_at(path)
        since = line.since if kind == "item" else line.node_since(path.split(".")[0])
        for name in fields:
            cap = getattr(policy, name)
            if cap is None or (above[name] is not None and cap >= above[name]):
                continue
            above[name] = cap
            if name == "time_cap_minutes":
                spent = line.running(path, since)
            else:
                spent = line.wall(line.started(path, kind, since))
            hit = Hit(path, name, cap, cap * 60 - spent)
            if best is None or hit.remaining_s < best.remaining_s:
                best = hit
    return best


def at_launch(conn, row, task, *, now: str | None = None, ran_s: float = 0.0) -> Hit | None:
    """The tightest cap over a launch of `task` (a `ResolvedTask`) for the
    item `row`, or None when none applies. `remaining_s <= 0`: spent, refuse
    it. The task's own cap counts from zero: this launch is its one run. (A
    wait task's own total cap is its wait's timeout, which `kraft.waits`
    enforces from the wait's start; here it can only leave the whole cap.)"""
    snapshot = store.materialized_chain_of(row)
    if snapshot is None:
        return None
    line = _Timeline(conn, row["id"], snapshot, _dt(now or store._now()))
    return _with_own_run(snapshot, line, task.path, ran_s)


def _with_own_run(snapshot, line: _Timeline, path: str, ran_s: float) -> Hit | None:
    """The tightest cap over the task at `path`, its own run having gone
    `ran_s` so far."""
    explicit = _explicit(snapshot)
    levels = _levels(snapshot, path)
    best = _tightest(explicit, line, levels)
    parent = _root_policy(explicit) if len(levels) == 1 else explicit.policy_at(levels[-1][0])
    own = snapshot.policy_at(path)
    for name in CAP_FIELDS:
        cap, above = getattr(own, name), getattr(parent, name)
        if cap is not None and (above is None or cap < above):
            hit = Hit(path, name, cap, cap * 60.0 - ran_s)
            if best is None or hit.remaining_s < best.remaining_s:
                best = hit
    return best


def for_turn(conn, row, node_id: str, *, now: str | None = None) -> Hit | None:
    """The tightest cap over an automatic escalation turn of `node_id`: its
    node's and the work item's (Kraft-8en38)."""
    snapshot = store.materialized_chain_of(row)
    if snapshot is None or not node_id:
        return None
    line = _Timeline(conn, row["id"], snapshot, _dt(now or store._now()))
    return _tightest(_explicit(snapshot), line, _levels(snapshot, f"{node_id}.escalation"))


def for_session(conn, row, session, *, now: str | None = None) -> Hit | None:
    """The tightest cap over a session already running -- one adopted after a
    restart (Kraft-kx2fs) -- its own run counted from its start. None for a
    human's escalation chat, which no cap bounds."""
    snapshot = store.materialized_chain_of(row)
    if snapshot is None:
        return None
    at = _dt(now or store._now())
    path = session["hook_point"]
    if path == "escalation":
        auto = conn.execute(
            "SELECT 1 FROM events WHERE work_item_id = ? AND type = 'escalation_message' "
            "AND json_extract(payload, '$.session_id') = ? AND json_extract(payload, '$.auto') = 1",
            (row["id"], session["id"]),
        ).fetchone()
        return for_turn(conn, row, session["node_id"], now=now) if auto else None
    try:
        snapshot.policy_at(path)
    except LookupError:
        return None
    line = _Timeline(conn, row["id"], snapshot, at)
    ran = (at - _dt(session["started_at"] or session["created_at"])).total_seconds()
    return _with_own_run(snapshot, line, path, ran)


def parked(conn, row, *, gate: str | None, now: str | None = None) -> Hit | None:
    """The cap an item parked at `row['current_node_id']` has spent, if any:
    the work item's or the node's total (nothing runs, so no running cap can
    move), and a pending `gate`'s own timeout."""
    snapshot = store.materialized_chain_of(row)
    node_id = row["current_node_id"]
    if snapshot is None or not node_id:
        return None
    line = _Timeline(conn, row["id"], snapshot, _dt(now or store._now()))
    kinds = {p: k for p, k, _ in snapshot.chain.cap_scopes()}
    levels = [("", "item"), *([(node_id, kinds[node_id])] if node_id in kinds else [])]
    best = _tightest(_explicit(snapshot), line, levels, fields=("total_time_cap_minutes",))
    node = next((n for n in snapshot.chain.nodes if n.id == gate), None)
    timeout = getattr(node.node, "timeout", None) if node is not None else None
    if timeout is not None:
        requested = conn.execute(
            "SELECT created_at FROM events WHERE work_item_id = ? AND type = 'gate_requested' "
            "ORDER BY seq DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if requested is not None:
            left = timeout.total_seconds() - line.wall(_dt(requested["created_at"]))
            hit = Hit(gate, "timeout", int(timeout / timedelta(minutes=1)), left)
            if best is None or hit.remaining_s < best.remaining_s:
                best = hit
    return best if best is not None and best.remaining_s <= 0 else None


def budget_breach(conn, row, path: str) -> dict | None:
    """The first spend cap already reached over a launch at `path` (a task's,
    or a node's for its escalation turn), broadest scope first, or None
    (Ruling 195). A scope's spend is what the launches inside it have spent,
    since the item was filed: `token_budget` its tokens in and out (a running
    session's live count included), `budget_usd` its dollars. A finished
    launch that spent tokens and reported no cost is unknown spend, which is
    never counted as free: a scope with any is refused under a dollar cap,
    and the stop says why. A launch still running reports its cost when it
    exits, so it is not unknown yet; the overshoot is one launch, as with
    `budget.work_item_usd`.

    Concurrent launches in one scope (a parallel step's tasks) each pass this
    check before any of them has spent, so a shared scope's cap can be
    overshot by up to the cost of the launches that started together
    (Kraft-ib2sn). Nothing is reserved ahead of a launch.

    An escalation turn's session is its node's spend (Kraft-h8n21). An
    instance or repository budget is a default (Ruling 198): it binds a
    task's own launches where nothing more specific is set, never an
    enclosing scope."""
    snapshot = store.materialized_chain_of(row)
    if snapshot is None:
        return None
    kinds = {p: k for p, k, _ in snapshot.chain.cap_scopes()}
    segments = path.split(".")
    levels = [
        "",
        *(p for i in range(1, len(segments) + 1) if (p := ".".join(segments[:i])) in kinds),
    ]
    sessions = [
        {
            **dict(s),
            "hook_point": s["node_id"] if s["hook_point"] == "escalation" else s["hook_point"],
        }
        for s in conn.execute(
            "SELECT node_id, hook_point, status, tokens_in, tokens_out, cost_usd "
            "FROM worker_sessions WHERE work_item_id = ?",
            (row["id"],),
        ).fetchall()
    ]
    explicit = _explicit(snapshot)
    above = dict.fromkeys(BUDGET_FIELDS)
    for level in levels:
        if not level:
            policy = _root_policy(explicit)
        elif level == path and kinds[level] == "task":
            policy = snapshot.policy_at(level)
        else:
            policy = explicit.policy_at(level)
        under = [
            s
            for s in sessions
            if not level or s["hook_point"] == level or s["hook_point"].startswith(level + ".")
        ]
        for name in BUDGET_FIELDS:
            cap = getattr(policy, name)
            if cap is None or (above[name] is not None and cap >= above[name]):
                continue
            above[name] = cap
            if name == "token_budget":
                spent = sum((s["tokens_in"] or 0) + (s["tokens_out"] or 0) for s in under)
                if spent >= cap:
                    return {
                        "scope": "tokens",
                        "path": level,
                        "spent_tokens": spent,
                        "cap_tokens": cap,
                    }
                continue
            unknown = sum(
                1
                for s in under
                if s["cost_usd"] is None
                and ((s["tokens_in"] or 0) + (s["tokens_out"] or 0)) > 0
                and s["status"] not in ("pending", "running")
            )
            spent = sum(s["cost_usd"] or 0.0 for s in under)
            if unknown or spent >= cap:
                return {
                    "scope": "usd",
                    "path": level,
                    "spent_usd": spent,
                    "cap_usd": float(cap),
                    "unknown_launches": unknown,
                }
    return None


def reason_of(conn, work_item_id: str) -> str:
    """The newest `time_cap_reached` reason, for the stop it caused."""
    row = conn.execute(
        "SELECT payload FROM events WHERE work_item_id = ? AND type = ? ORDER BY seq DESC LIMIT 1",
        (work_item_id, REACHED),
    ).fetchone()
    return json.loads(row["payload"])["reason"] if row else "a time cap was reached"


def time_capped_sessions(conn, work_item_ids) -> set[str]:
    """The sessions a time cap stopped or refused: they exit `capped_out`, the
    status a fix loop's cap writes, and `REACHED` tells them apart."""
    ids = list(work_item_ids)
    if not ids:
        return set()
    rows = conn.execute(
        "SELECT json_extract(payload, '$.session_id') AS sid FROM events "
        f"WHERE work_item_id IN ({','.join('?' * len(ids))}) AND type = ?",
        (*ids, REACHED),
    ).fetchall()
    return {r["sid"] for r in rows if r["sid"]}


#: What closes a pending gate (`executor.gates._GATE_CLOSED`), and a cap's own
#: stop: an item at a gate is measured only while its newest such event is the
#: request.
_GATE_SETTLED = (
    "gate_approved",
    "gate_rejected",
    "node_skipped",
    "work_item_completed",
    "work_item_abandoned",
    REACHED,
)


async def tick(db, *, now: str | None = None) -> list[str]:
    """Stop, for a human, every parked item whose cap ran out while nothing
    of it ran: waiting on an external condition, rate limited, or at a gate
    -- a gate's own `timeout` included. Once per park: a gate stays pending
    and answerable, so one stop per request. Returns the items stopped."""
    marks = ", ".join("?" * len(_GATE_SETTLED))
    rows = db.read(
        lambda c: c.execute(
            "SELECT w.*, (SELECT CASE WHEN e.type = 'gate_requested' "
            "THEN json_extract(e.payload, '$.gate') END FROM events e "
            f"WHERE e.work_item_id = w.id AND e.type IN ('gate_requested', {marks}) "
            "ORDER BY e.seq DESC LIMIT 1) AS pending_gate "
            "FROM work_items w WHERE w.status IN ('waiting', 'rate_limited', 'needs_human')",
            _GATE_SETTLED,
        ).fetchall()
    )
    stopped = []
    for row in rows:
        gate = row["pending_gate"] if row["status"] == "needs_human" else None
        if row["status"] == "needs_human" and gate is None:
            continue
        try:
            hit = db.read(lambda c, r=row, g=gate: parked(c, r, gate=g, now=now))
        except Exception:  # noqa: BLE001 -- one unreadable item must not stop the rest
            logger.exception("time caps: cannot measure %s", row["id"])
            continue
        if hit is None:
            continue

        if await db.write(lambda c, r=row, h=hit: stop_if_still_parked(c, r, h)):
            stopped.append(row["id"])
    return stopped


def stop_if_still_parked(conn, seen, hit: Hit) -> bool:
    """Stop the item `seen` measured for `hit`, in the writer's transaction,
    only if it is still where it was measured: the same status, the same
    node, and the same pending gate (Kraft-l5fl2). A resume, an approval or
    a retry landing between the measurement and this write is left alone."""
    now = conn.execute(
        "SELECT w.status, w.current_node_id, (SELECT CASE WHEN e.type = 'gate_requested' "
        "THEN json_extract(e.payload, '$.gate') END FROM events e "
        f"WHERE e.work_item_id = w.id AND e.type IN ('gate_requested', "
        f"{', '.join('?' * len(_GATE_SETTLED))}) ORDER BY e.seq DESC LIMIT 1) AS pending_gate "
        "FROM work_items w WHERE w.id = ?",
        (*_GATE_SETTLED, seen["id"]),
    ).fetchone()
    if now is None or (now["status"], now["current_node_id"]) != (
        seen["status"],
        seen["current_node_id"],
    ):
        return False
    if seen["status"] == "needs_human" and now["pending_gate"] != seen["pending_gate"]:
        return False
    events.append(conn, seen["id"], REACHED, hit.payload(node_id=seen["current_node_id"]))
    store.mark_needs_human(conn, seen["id"], seen["current_node_id"], hit.reason)
    return True


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app.state.db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("time-cap poller tick failed")
