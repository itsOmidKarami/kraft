"""The start queue: one scheduler that starts `queued` work items as slots free.

A door that would start a walk and finds every `max_concurrent` slot busy
queues the item instead of refusing (`routes.lifecycle.queued_answer`). The
item keeps the request it was asked: the verb, its body and who asked.

An item started while something it comes after is unfinished is `blocked`
(`routes.lifecycle.blocked_answer`) and holds the same saved request. Each
pass first moves every blocked item whose dependencies have all completed
into the queue (`store.release_blocked`), so there is one path to `active`.

`tick` takes the earliest queued item while a slot is free, puts it back in
the status it came from, and makes that request again through the same route
function, with a `Request` rebuilt from the saved caller. So a queued start
runs every check, the rebase, the counter clearing and the steer delivery a
fresh request would, against the item as it is now. Four ends:

* it starts, and the door writes its own event;
* the door now refuses it, or fails: the item stays where it came from, and
  `work_item_dequeued` carries the refusal or the error;
* another request took the slot or the checkout first: it is queued again,
  and given back the place it had;
* the server shuts down before it is claimed: it is queued again, in its
  place, and the next server starts it.

A door that spawns its work before that work claims the item or writes its
session (an escalation, a reply, an escalation's own retry, Kraft's own
agents at a stop) has not started it when it returns: the queue waits for
the claim or the session (`_SPAWNS`).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from starlette.requests import Request

from kraft import events, storage, store
from kraft.vocab import WorkItemEvent, WorkItemStatus

logger = logging.getLogger(__name__)

#: How often the queue is looked at: the wait scheduler's cadence.
_INTERVAL_S = 10


def _request(app, headers: dict[str, str]) -> Request:
    """The saved caller as the request a door reads: the app, and the headers
    that say who asked (`deps.CALLER_HEADERS`)."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "app": app,
        }
    )


def starting(app) -> set[str]:
    """The ids `_start_one` is starting right now. Between its take and the
    door's claim the row reads `paused` or `needs_human`, so this is the only
    place that says a start is under way; `pause` reads it. In memory: the
    server is one process, and a restart ends every start in flight."""
    ids = getattr(app.state, "_starting", None)
    if ids is None:
        ids = app.state._starting = set()
    return ids


async def tick(app) -> list[str]:
    """One pass. Returns the work item ids started, which is usually none."""
    st = app.state
    limit = st.policy.max_concurrent if st.policy else 1
    # Before the queue: an item whose dependencies have all completed joins
    # it, and is started in this same pass when a slot is free.
    await st.db.write(store.release_blocked)
    started: list[str] = []
    for wid in st.db.read(store.queued_ids):
        if st.db.read(store.active_count) >= limit and not _slotless(st, wid):
            continue
        if storage.holds(st, wid):
            # Keeps its place, and is not asked again each tick (its door would
            # write another `work_item_storage_held`). A start behind it that
            # has a worktree still goes.
            continue
        if st.db.read(lambda c, wid=wid: store.checkout_holder(c, wid)):
            continue  # keeps its place until the in-place item using its checkout stops
        if await _start_one(app, wid):
            started.append(wid)
    return started


#: Verbs that start an agent beside the walk, never a walk: they take no slot.
_SLOTLESS = ("escalate", "reply")


def _slotless(st, wid: str) -> bool:
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    return (store.queued_request_of(row) or {}).get("verb") in _SLOTLESS


async def _start_one(app, wid: str) -> bool:
    starting(app).add(wid)  # before the take, so no pause can read the row in between
    try:
        return await _take_and_start(app, wid)
    finally:
        starting(app).discard(wid)


async def _take_and_start(app, wid: str) -> bool:
    st = app.state
    saved = await st.db.write(lambda c: store.take_queued(c, wid))
    if saved is None:
        return False  # paused, ended or started since `tick` selected it
    request = _request(app, saved["headers"])
    claimed = asyncio.Event()
    replay = store.REPLAY_CLAIMED.set(claimed) if saved["verb"] in _SPAWNS else None
    work = None
    try:
        try:
            await _replay(wid, saved, request)
        finally:
            if replay is not None:
                store.REPLAY_CLAIMED.reset(replay)
        work = st.tasks.get(wid) if replay is not None else None
        if work is not None:
            await _until_claimed(work, claimed)
    except asyncio.CancelledError:
        # The server is shutting down mid-start. An item the door has not
        # claimed goes back in the queue, in its place, for the next server to
        # start: left where it came from, its request would be lost unsaid.
        # Work it spawned that has not claimed it is stopped with it.
        if work is not None and not claimed.is_set():
            work.cancel()
        await st.db.write(lambda c: _requeue(c, wid, saved))
        raise
    except HTTPException as exc:
        await _dequeued(st, wid, "refused", str(exc.detail))
        return False
    except Exception as exc:  # noqa: BLE001 -- it has left the queue: say so, and let the pass go on
        logger.exception("start queue: starting %s failed", wid)
        await _dequeued(st, wid, "failed", f"{type(exc).__name__}: {exc}")
        return False
    status = st.db.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()[
            "status"
        ]
    )
    if status == WorkItemStatus.QUEUED:
        # Another request took the slot or the checkout between `tick`'s look
        # and the claim, and the request was queued again: not to the back.
        await st.db.write(lambda c: store.keep_place(c, wid, saved["at"]))
        return False
    return True


#: Verbs whose door spawns the work and returns before that work claims the
#: item or writes its session. The queue waits for one or the other
#: (`store.replay_claimed`), or for the work to end, before it counts the
#: item started: a claim lost meanwhile keeps its place, and a shutdown
#: meanwhile puts the request back.
_SPAWNS = ("self_retry", "escalate", "reply", "follow_up")


async def _until_claimed(work: asyncio.Task, claimed: asyncio.Event) -> None:
    waiting = asyncio.ensure_future(claimed.wait())
    try:
        await asyncio.wait({work, waiting}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiting.cancel()


async def _replay(wid: str, saved: dict, request: Request) -> None:
    """Make the saved request again through the door that queued it."""
    # deferred: kraft.api starts this module's poller
    from kraft.api.routes import lifecycle, review

    if saved["verb"] == "retry":
        await lifecycle._retry(wid, lifecycle.Retry(**saved["body"]), request)
    elif saved["verb"] in ("continue", "start"):
        await lifecycle.continue_held(wid, saved["body"], request)
    elif saved["verb"] == "self_retry":
        await lifecycle.self_retry_held(wid, saved["body"], request)
    elif saved["verb"] == "escalate":
        await lifecycle.escalate_work_item(wid, lifecycle.Escalate(**saved["body"]), request)
    elif saved["verb"] == "follow_up":
        await lifecycle.follow_up_held(wid, saved["body"], request)
    elif saved["verb"] == "reply":
        await review.reply_held(wid, saved["body"], request)
    else:
        await lifecycle.resume_work_item(wid, lifecycle.Resume(**saved["body"]), request)


def _requeue(conn, wid: str, saved: dict) -> None:
    """Queue `saved` again, as the door does when it finds the slot gone. Only
    while the item is still where the take left it: a claimed item is running."""
    if store.queue_work_item(
        conn,
        wid,
        verb=saved["verb"],
        body=saved["body"],
        headers=saved["headers"],
        from_statuses=[saved["from"]],
        held_by=store.checkout_holder(conn, wid),
    ):
        store.keep_place(conn, wid, saved["at"])


async def _dequeued(st, wid: str, why: str, detail: str) -> None:
    """The item was put back and did not start: its timeline says why. `to` is
    the status it is in now, which an open board shows: where it came from, or
    the stop of a door that failed after its claim (`stops.claimed_or_stopped`)."""

    def write(c):
        to = c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()["status"]
        events.append(c, wid, WorkItemEvent.DEQUEUED, {"why": why, "detail": detail, "to": to})

    await st.db.write(write)


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- one bad pass must not end the queue
            logger.exception("start queue: tick failed")
