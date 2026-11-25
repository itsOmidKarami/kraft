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
* `external_wait_ended` -- the outcome: `settled`, `timed_out`, `error`, or
  `superseded` (the wait was for another thing than the one now asked about:
  `ci.pipeline`'s writer re-ran and left another id).
* `external_wait_rebounded` -- the wait's bounds changed while it was open:
  its item's policy override was changed (Kraft-ab1bh). The new timeout
  counts from the wait's own start, and binds from the observation that
  wrote this.

So no wait ends without a trace (Kraft-vzq2q). A pending observation leaves
the task `waiting`; the walk parks the item (`status = 'waiting'`, `retry_at`
= the earliest next observation, `stops.stop_for_waiting`) and returns,
releasing its worker (`external-wait-does-not-hold-an-active-worker`).
`tick` -- the one scheduler, for every wait kind -- re-enters whatever is due
through `executor.run` with no position -- the walk resumes at the item's
own cursor, the waiting step (Kraft-c3dab) -- and that re-entry makes the next
observation.

A wait instance is one task path's run of observations from its start to its
outcome, or to a restart of the item under it (`RESTARTS_RUN`: a retry, a run
fork, a base-change restart): its clock belongs to it alone, so a second pass over
the same node starts fresh (Kraft-3r9fe).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from kraft import events, store, when_free
from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.templates.models import WaitBounds
from kraft.vocab import RESTARTS_RUN, ForgeEvent, WorkItemStatus
from kraft.vocab.sql import in_list

logger = logging.getLogger(__name__)

#: How often the scheduler looks for a due wait. A wait is never observed
#: more finely than this; the smallest seeded interval is 30s.
_INTERVAL_S = 10

# `RESTARTS_RUN` ends every open wait on the item: a restart is a fresh budget.
# The same set `store.sessions` reads as "this item started over". A `/retry`
# writes both `work_item_retried` and `run_forked` (`executor.retry`); the
# rate-limit relaunch writes only the first, a base-change restart the last.

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
        """The bounds it started under, or was last rebounded to."""
        return WaitBounds.from_seconds(
            timeout=self.started["timeout_s"],
            initial=self.started["initial_interval_s"],
            maximum=self.started["max_interval_s"],
        )


def open_wait(conn, work_item_id: str, task: str) -> OpenWait | None:
    """`task`'s open wait instance, or None when its last one ended."""
    wait_events = in_list(
        (
            ForgeEvent.EXTERNAL_WAIT_STARTED,
            ForgeEvent.EXTERNAL_WAIT_OBSERVED,
            ForgeEvent.EXTERNAL_WAIT_REBOUNDED,
            ForgeEvent.EXTERNAL_WAIT_ENDED,
        )
    )
    rows = conn.execute(
        "SELECT type, payload FROM events WHERE work_item_id = ? AND ("
        f"(type IN ({wait_events}) "
        "AND json_extract(payload, '$.task') = ?)"
        f" OR type IN ({', '.join('?' * len(RESTARTS_RUN))})) ORDER BY seq DESC",
        (work_item_id, task, *RESTARTS_RUN),
    ).fetchall()
    last = rebound = None
    for row in rows:
        if row["type"] == ForgeEvent.EXTERNAL_WAIT_OBSERVED:
            last = last or json.loads(row["payload"])
        elif row["type"] == ForgeEvent.EXTERNAL_WAIT_REBOUNDED:
            rebound = rebound or json.loads(row["payload"])
        elif row["type"] == ForgeEvent.EXTERNAL_WAIT_STARTED:
            return OpenWait({**json.loads(row["payload"]), **(rebound or {})}, last)
        else:
            return None
    return None


def pushed_at(conn, work_item_id: str, *, repo: str, sha: str) -> datetime | None:
    """When Kraft pushed `sha` to `repo`'s branch (`branch_pushed`), or None for
    a head it has no record of: one an older Kraft pushed, or a person."""
    row = conn.execute(
        "SELECT created_at FROM events WHERE work_item_id = ? AND type = ? "
        "AND json_extract(payload, '$.repo') = ? AND json_extract(payload, '$.sha') = ? "
        "ORDER BY seq DESC LIMIT 1",
        (work_item_id, ForgeEvent.BRANCH_PUSHED, repo, sha),
    ).fetchone()
    return datetime.fromisoformat(row["created_at"]) if row else None


def now() -> datetime:
    """The clock a wait reads, as a datetime: one read for a caller that has
    to make a decision and then hand the same instant to `observe`."""
    return datetime.fromisoformat(_now())


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
    session_id: str | None = None,
    now: datetime | None = None,
    next_at: datetime | None = None,
    extra: dict | None = None,
    started_extra: Mapping[str, object] | None = None,
    deadline: datetime | None = None,
) -> Literal["pending", "settled", "timed_out", "error"]:
    """Record one observation of `task`'s wait, in the caller's transaction,
    and say what it amounts to. `state` is what the forge saw: `pending`,
    `settled` (on whatever `result` -- green or red is the task's business,
    not the wait's), or `error` when the observation itself could not be
    made. A pending observation at or past the deadline is `timed_out`
    (`external-wait-timeout-needs-human`). `bounds` are what the task's
    policy resolves to now: they start a new instance, and an open one whose
    bounds differ -- its item's policy override changed (Kraft-ab1bh) -- is
    rebounded to them from this observation on, its deadline counted from
    its own start. `session_id` is the session the observation ran in,
    recorded on the outcome.

    The seam for a wait that decides for itself when to look again and what to
    remember: `now` is the instant the caller decided at, so the deadline test
    here and its decision agree (it reads the clock itself when not given);
    `next_at` is the next observation the caller wants, kept ahead of the
    backoff but never past the deadline; `extra` is merged into the observation
    and so comes back in `OpenWait.last`. `started_extra` is what the wait is
    *about*, kept in its start: an open wait that was started about something
    else (`ci.pipeline` re-resolving another id) ends `superseded` and a new one
    starts, with its own clock. `deadline`, when given, is the instant a pending
    observation is tested against and clamped to, in place of the wait's start
    plus its timeout: a wait whose limit counts from something else (a review
    wait from the push) says so here, and no other wait changes."""
    now = now or datetime.fromisoformat(_now())
    ids = {"task": task, "node_id": node_id}
    wait = open_wait(conn, work_item_id, task)
    if (
        wait is not None
        and started_extra
        and any(wait.started.get(k) != v for k, v in started_extra.items())
    ):
        events.append(
            conn,
            work_item_id,
            ForgeEvent.EXTERNAL_WAIT_ENDED,
            {
                **ids,
                "outcome": "superseded",
                "result": "superseded",
                "observations": wait.observations,
                "session_id": session_id,
            },
        )
        wait = None
    if wait is None:
        started = {
            **ids,
            "kind": kind,
            "timeout_s": bounds.timeout.total_seconds(),
            "initial_interval_s": bounds.initial_interval.total_seconds(),
            "max_interval_s": bounds.max_interval.total_seconds(),
            "started_at": now.isoformat(),
            "deadline": (deadline or now + bounds.timeout).isoformat(),
            **(started_extra or {}),
        }
        events.append(conn, work_item_id, ForgeEvent.EXTERNAL_WAIT_STARTED, started)
        wait = OpenWait(started, None)
    elif bounds != wait.bounds:
        start = datetime.fromisoformat(wait.started["started_at"])
        changed = {
            **ids,
            "timeout_s": bounds.timeout.total_seconds(),
            "initial_interval_s": bounds.initial_interval.total_seconds(),
            "max_interval_s": bounds.max_interval.total_seconds(),
            "deadline": (deadline or start + bounds.timeout).isoformat(),
        }
        events.append(conn, work_item_id, ForgeEvent.EXTERNAL_WAIT_REBOUNDED, changed)
        wait = OpenWait({**wait.started, **changed}, wait.last)
    n = wait.observations + 1

    def end(outcome: str) -> None:
        events.append(
            conn,
            work_item_id,
            ForgeEvent.EXTERNAL_WAIT_ENDED,
            {
                **ids,
                "outcome": outcome,
                "result": result,
                "observations": n,
                # Which session the wait ended in: a `timed_out` one exits
                # `capped_out`, and this is how usage tells it from a fix
                # loop's cap (`store.wait_timed_out_sessions`).
                "session_id": session_id,
            },
        )

    if state == "error":
        end("error")
        return "error"
    seen = {
        **ids,
        "observation": n,
        "condition": condition,
        "state": state,
        "result": result,
        **(extra or {}),
    }
    due_by = deadline or datetime.fromisoformat(wait.started["deadline"])
    if state == "settled" or now >= due_by:
        events.append(conn, work_item_id, ForgeEvent.EXTERNAL_WAIT_OBSERVED, seen)
        outcome = "settled" if state == "settled" else "timed_out"
        end(outcome)
        return outcome
    due = min(next_at or now + wait.bounds.interval_after(n), due_by)
    events.append(
        conn,
        work_item_id,
        ForgeEvent.EXTERNAL_WAIT_OBSERVED,
        {**seen, "next_observation_at": due.isoformat()},
    )
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


# --- a forge the network cut off -----------------------------------------------

#: How many tries a task gets while the forge does not answer, spaced by
#: doubling from `_NO_ANSWER_FIRST_S` up to `_NO_ANSWER_MAX_S`: about 90 minutes
#: of a machine that is awake and offline.
NO_ANSWER_TRIES = 10
#: The tries for a repo whose forge has never answered Kraft: a wrong host, port or
#: `api_url` fails the same way as a dropped network (an offline machine cannot
#: resolve a name either), so only the history tells them apart. About 3.5 minutes.
FIRST_CONTACT_TRIES = 3
_NO_ANSWER_FIRST_S = 30
_NO_ANSWER_MAX_S = 900
#: A try that starts this long after it was due: the machine slept or the server
#: was down, so the gap says nothing about the network and the try is not spent.
_NO_ANSWER_LATE = timedelta(minutes=2)


def _last_no_answer(conn, work_item_id: str, task: str) -> dict | None:
    """`task`'s standing `forge_no_answer`, or None once the forge answered it
    or the run restarted (a person's retry is a fresh budget)."""
    row = conn.execute(
        "SELECT type, payload FROM events WHERE work_item_id = ? AND ("
        "(type IN (?, ?) AND json_extract(payload, '$.task') = ?)"
        f" OR type IN ({', '.join('?' * len(RESTARTS_RUN))})) ORDER BY seq DESC LIMIT 1",
        (work_item_id, ForgeEvent.FORGE_NO_ANSWER, ForgeEvent.FORGE_ANSWERED, task, *RESTARTS_RUN),
    ).fetchone()
    return json.loads(row["payload"]) if row and row["type"] == ForgeEvent.FORGE_NO_ANSWER else None


def _tries(conn, work_item_id: str) -> int:
    """`NO_ANSWER_TRIES` once any item in this item's repo opened a merge request
    or heard back after a drop; `FIRST_CONTACT_TRIES` before. Not `branch_pushed`:
    a push over ssh says nothing about an `api_url`."""
    answered = conn.execute(
        "SELECT 1 FROM events e JOIN work_items w ON w.id = e.work_item_id"
        " WHERE e.type IN (?, ?)"
        " AND w.repo = (SELECT repo FROM work_items WHERE id = ?) LIMIT 1",
        (ForgeEvent.MR_OPENED, ForgeEvent.FORGE_ANSWERED, work_item_id),
    ).fetchone()
    return NO_ANSWER_TRIES if answered else FIRST_CONTACT_TRIES


def _busy(error: str) -> bool:
    """Whether a parked try was the forge still merging (`MergeBusy`), not the network."""
    from kraft.adapters.forge.models import MergeBusy  # the adapters import this module

    return error.startswith(MergeBusy.PREFIX)


def note_no_answer(
    conn, work_item_id: str, *, node_id: str, task: str, error: str, started: datetime
) -> bool:
    """Record a forge call the network cut off; True to park and try again, False
    when the tries are spent. `started` is when this try began: a call can itself
    take minutes to time out, and that must not read as a machine that slept."""
    last = _last_no_answer(conn, work_item_id, task)
    attempt = 1
    if last is not None:
        late = (
            "retry_at" in last
            and started - datetime.fromisoformat(last["retry_at"]) > _NO_ANSWER_LATE
        )
        attempt = last["attempt"] + (0 if late else 1)
    ids = {"node_id": node_id, "task": task, "attempt": attempt, "error": error}
    tries = _tries(conn, work_item_id)
    if attempt > tries:
        if _busy(error):
            reason = (
                f"{task}: the forge was still merging the merge request at each of {tries}"
                f" tries: {error}\nIt has held the merge without finishing it. Look at the"
                " merge request on the forge, then retry."
            )
        elif tries == NO_ANSWER_TRIES:
            reason = (
                f"{task} got no answer from the forge in {tries} tries: {error}\n"
                "The Kraft server's machine could not reach the forge. Check its network,"
                " then retry."
            )
        else:
            reason = (
                f"{task} got no answer from the forge in {tries} tries: {error}\n"
                "This repo's forge has never answered Kraft. The network may just have been"
                " down, and then retrying is all it needs. Otherwise check its setup: the"
                " forge account's api_url, the remote's host and port, and ssh access."
            )
        events.append(conn, work_item_id, ForgeEvent.FORGE_NO_ANSWER, {**ids, "reason": reason})
        return False
    delay = min(_NO_ANSWER_FIRST_S * 2 ** (attempt - 1), _NO_ANSWER_MAX_S)
    # From now, not from when it was due: a machine that wakes spends one try.
    retry_at = datetime.fromisoformat(_now()) + timedelta(seconds=delay)
    events.append(
        conn,
        work_item_id,
        ForgeEvent.FORGE_NO_ANSWER,
        {**ids, "retry_at": retry_at.isoformat()},
    )
    return True


def note_answered(conn, work_item_id: str, *, node_id: str, task: str) -> None:
    """The forge answered `task`: an outage it was parked on is over."""
    if (last := _last_no_answer(conn, work_item_id, task)) is not None:
        events.append(
            conn,
            work_item_id,
            ForgeEvent.FORGE_ANSWERED,
            {"node_id": node_id, "task": task, "attempts": last["attempt"]},
        )


def no_answer_park(conn, work_item_id: str, tasks) -> tuple[str, str] | None:
    """`(retry_at, reason)` when one of `tasks` is parked on the network, the
    latest try's. A `retry_at` already past is an earlier outage's."""
    now = datetime.fromisoformat(_now())
    parked = [
        p
        for task in tasks
        if (p := _last_no_answer(conn, work_item_id, task)) is not None
        and "retry_at" in p
        and datetime.fromisoformat(p["retry_at"]) > now
    ]
    if not parked:
        return None
    p = max(parked, key=lambda x: datetime.fromisoformat(x["retry_at"]))
    waiting = (
        "waiting for the forge to finish a merge it is making"
        if _busy(p["error"])
        else "waiting for the network: no answer from the forge"
    )
    return p["retry_at"], f"{waiting} (try {p['attempt']} of {_tries(conn, work_item_id)})"


# --- the scheduler --------------------------------------------------------------


async def tick(app, *, now: str | None = None) -> list[str]:
    """One pass. Returns the work item ids re-entered, which is usually none.
    `now` is for a test that must not sleep out a backoff."""
    st = app.state
    if when_free.claimed():
        return []  # an update holds the moment: nothing starts until the restart
    due = st.db.read(
        lambda c: c.execute(
            # `materialized_chain` as well as `chain_definition`: the row is
            # handed to `store.node_index`, which reads the V1 snapshot first
            # and cannot find it in a column the SELECT never fetched.
            "SELECT id, repo, current_node_id, chain_definition, "
            "materialized_chain FROM work_items "
            "WHERE status = ? AND retry_at <= ?",
            (WorkItemStatus.WAITING, now or _now()),
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
        if not await st.db.write(lambda c: store.mark_reentered(c, wid)):
            return False  # ended since `tick` selected it (Kraft-dncfg), or its checkout is busy
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
