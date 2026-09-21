"""External waits: the record every wait keeps, and the one due scheduler.

A forge task that watches something outside Kraft -- a pipeline, an automated
review, an external approval, a merge landing, the target branch's pipeline
after it -- makes one observation per dispatch and hands it to `observe`,
which keeps the wait's record as events:

* `external_wait_started` -- the condition (task, kind), its timeout and
  polling bounds, and its deadline. Written by an instance's first
  observation.
* `external_wait_observed` -- one per observation: what was seen, and for a
  pending one the next observation time.
* `external_wait_ended` -- the outcome: `settled`, `timed_out` or `error`.

So no wait ends without a trace (Kraft-vzq2q). A pending observation leaves
the task `waiting`; the walk parks the item (`status = 'waiting'`, `retry_at`
= the earliest next observation, `stops.stop_for_waiting`) and returns,
releasing its worker (`external-wait-does-not-hold-an-active-worker`).
`tick` -- the one scheduler, for every wait kind -- re-enters whatever is due
through `executor.run` with no position -- the walk resumes at the item's
own cursor, the waiting step (Kraft-c3dab) -- and that re-entry makes the next
observation.

A wait instance is one task path's run of observations from its start to its
outcome, or to a restart of the item under it (`work_item_retried`,
`base_change_restart`): its clock belongs to it alone, so a second pass over
the same node starts fresh (Kraft-3r9fe).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from kraft import events, store
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.templates.models import WaitBounds

logger = logging.getLogger(__name__)

#: How often the scheduler looks for a due wait. A wait is never observed
#: more finely than this; the smallest seeded interval is 30s.
_INTERVAL_S = 10

STARTED = "external_wait_started"
OBSERVED = "external_wait_observed"
ENDED = "external_wait_ended"
#: Events that end every open wait on the item: a restart is a fresh budget.
#: The same pair `store.sessions` reads as "this item started over".
_RESTARTS = ("work_item_retried", "base_change_restart")

State = Literal["pending", "settled", "error"]


@dataclass(frozen=True)
class OpenWait:
    """A wait instance with no outcome yet: its start, and its latest
    observation (None before the first)."""

    started: dict
    last: dict | None

    @property
    def observations(self) -> int:
        return self.last["observation"] if self.last else 0

    @property
    def bounds(self) -> WaitBounds:
        """The bounds it started under -- a wait keeps them to its end."""
        return WaitBounds.from_seconds(
            timeout=self.started["timeout_s"],
            initial=self.started["initial_interval_s"],
            maximum=self.started["max_interval_s"],
        )


def open_wait(conn, work_item_id: str, task: str) -> OpenWait | None:
    """`task`'s open wait instance, or None when its last one ended."""
    rows = conn.execute(
        "SELECT type, payload FROM events WHERE work_item_id = ? AND ("
        f"(type IN ('{STARTED}', '{OBSERVED}', '{ENDED}') AND json_extract(payload, '$.task') = ?)"
        f" OR type IN {_RESTARTS}) ORDER BY seq DESC",
        (work_item_id, task),
    ).fetchall()
    last = None
    for row in rows:
        if row["type"] == OBSERVED:
            last = last or json.loads(row["payload"])
        elif row["type"] == STARTED:
            return OpenWait(json.loads(row["payload"]), last)
        else:
            return None
    return None


def observe(
    conn,
    work_item_id: str,
    *,
    node_id: str,
    task: str,
    kind: str,
    bounds: WaitBounds,
    condition: str,
    state: State,
    result: str,
) -> Literal["pending", "settled", "timed_out", "error"]:
    """Record one observation of `task`'s wait, in the caller's transaction,
    and say what it amounts to. `state` is what the forge saw: `pending`,
    `settled` (on whatever `result` -- green or red is the task's business,
    not the wait's), or `error` when the observation itself could not be
    made. A pending observation at or past the deadline is `timed_out`
    (`external-wait-timeout-needs-human`). `bounds` apply to a new instance
    only; an open one keeps the bounds it started with."""
    now = datetime.fromisoformat(_now())
    ids = {"task": task, "node_id": node_id}
    wait = open_wait(conn, work_item_id, task)
    if wait is None:
        started = {
            **ids,
            "kind": kind,
            "timeout_s": bounds.timeout.total_seconds(),
            "initial_interval_s": bounds.initial_interval.total_seconds(),
            "max_interval_s": bounds.max_interval.total_seconds(),
            "started_at": now.isoformat(),
            "deadline": (now + bounds.timeout).isoformat(),
        }
        events.append(conn, work_item_id, STARTED, started)
        wait = OpenWait(started, None)
    n = wait.observations + 1

    def end(outcome: str) -> None:
        events.append(
            conn,
            work_item_id,
            ENDED,
            {**ids, "outcome": outcome, "result": result, "observations": n},
        )

    if state == "error":
        end("error")
        return "error"
    seen = {**ids, "observation": n, "condition": condition, "state": state, "result": result}
    deadline = datetime.fromisoformat(wait.started["deadline"])
    if state == "settled" or now >= deadline:
        events.append(conn, work_item_id, OBSERVED, seen)
        outcome = "settled" if state == "settled" else "timed_out"
        end(outcome)
        return outcome
    due = min(now + wait.bounds.interval_after(n), deadline)
    events.append(conn, work_item_id, OBSERVED, {**seen, "next_observation_at": due.isoformat()})
    return "pending"


def next_observation(conn, work_item_id: str, tasks) -> str | None:
    """The earliest next observation among `tasks`' (paths) open waits, or
    None when none of them has one pending."""
    due = [
        wait.last["next_observation_at"]
        for task in tasks
        if (wait := open_wait(conn, work_item_id, task)) is not None
        and wait.last is not None
        and "next_observation_at" in wait.last
    ]
    return min(due, key=datetime.fromisoformat, default=None)


# --- the scheduler --------------------------------------------------------------


async def tick(app, *, now: str | None = None) -> list[str]:
    """One pass. Returns the work item ids re-entered, which is usually none.
    `now` is for a test that must not sleep out a backoff."""
    st = app.state
    due = st.db.read(
        lambda c: c.execute(
            # `materialized_chain` as well as `chain_definition`: the row is
            # handed to `store.node_index`, which reads the V1 snapshot first
            # and cannot find it in a column the SELECT never fetched.
            "SELECT id, repo, current_node_id, chain_definition, "
            "materialized_chain FROM work_items "
            "WHERE status = 'waiting' AND retry_at <= ?",
            (now or _now(),),
        ).fetchall()
    )
    reentered: list[str] = []
    for row in due:
        if await _re_enter_one(app, row):
            reentered.append(row["id"])
    return reentered


async def _re_enter_one(app, row) -> bool:
    from kraft.api import deps
    from kraft.executor import stops  # deferred, same cycle as `executor` below

    st = app.state
    wid = row["id"]
    node_id = row["current_node_id"]

    # Bracketed from *before* the claim to the hand-off. The claim makes this
    # item read `active`, and `tick`'s own SELECT filters `status = 'waiting'`,
    # so an exit from here that neither spawns a walk nor moves the status
    # again would leave it claimed and unowned, and *no later tick would ever
    # select it again*.
    async with stops.claimed_or_stopped(
        st.db,
        wid,
        node_id,
        reason="the wait scheduler re-entered this item but could not start a walk",
        handed_off=lambda: deps.task_is_live(app, wid),
    ):
        # Back to 'active' before anything else is awaited, so the next tick
        # cannot re-select this row (Kraft-ppk9). No cap is checked here: a
        # wait's timeout is its own, and the observation this re-entry makes
        # is what checks it (`observe`).
        await st.db.write(lambda c: store.mark_reentered(c, wid))
        # `store.node_index`, not `chain["nodes"]`: a V1 row's
        # `chain_definition` is `"{}"`. `None` means this node is not in this
        # item's chain at all -- a stop (the bracket's), not a restart at zero.
        if store.node_index(row, node_id) is None:
            logger.warning("waits: %s has no node %r in its chain, not re-entering", wid, node_id)
            return False
        from kraft import executor  # deferred: avoids a kraft.api <-> kraft.executor import cycle

        # No steer: there is no agent to address here.
        try:
            deps.spawn(
                app,
                wid,
                deps.guard(
                    st.db,
                    wid,
                    executor.run(
                        st.db,
                        st.run_dirs,
                        work_item_id=wid,
                        registry=st.registry,
                        bd_cwd=deps.bd_cwd(),
                        # No position: the walk resumes at the item's own
                        # cursor, the step group that is waiting.
                        policy=st.policy,
                        launch=deps.launch(st, row["repo"]),
                    ),
                ),
            )
        except deps.AlreadyRunning:
            logger.warning("waits: %s already has a live walk, skipping this tick", wid)
            return False
        return True


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the scheduler
            logger.exception("external-wait scheduler tick failed")
