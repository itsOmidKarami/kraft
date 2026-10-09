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
fresh request would, against the item as it is now. Three ends:

* it starts, and the door writes its own event;
* the door now refuses it, or fails: the item stays where it came from, and
  `work_item_dequeued` carries the refusal or the error;
* another request took the slot first: the door queues it again, and it is
  given back the place it had.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from starlette.requests import Request

from kraft import events, store
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


async def tick(app) -> list[str]:
    """One pass. Returns the work item ids started, which is usually none."""
    st = app.state
    limit = st.policy.max_concurrent if st.policy else 1
    # Before the queue: an item whose dependencies have all completed joins
    # it, and is started in this same pass when a slot is free.
    await st.db.write(store.release_blocked)
    started: list[str] = []
    for wid in st.db.read(store.queued_ids):
        if st.db.read(store.active_count) >= limit:
            break
        if await _start_one(app, wid):
            started.append(wid)
    return started


async def _start_one(app, wid: str) -> bool:
    from kraft.api.routes import lifecycle  # deferred: kraft.api starts this module's poller

    st = app.state
    saved = await st.db.write(lambda c: store.take_queued(c, wid))
    if saved is None:
        return False  # paused, ended or started since `tick` selected it
    request = _request(app, saved["headers"])
    try:
        if saved["verb"] == "retry":
            await lifecycle._retry(wid, lifecycle.Retry(**saved["body"]), request)
        else:
            await lifecycle.resume_work_item(wid, lifecycle.Resume(**saved["body"]), request)
    except HTTPException as exc:
        await _dequeued(st, wid, saved, "refused", str(exc.detail))
        return False
    except Exception as exc:  # noqa: BLE001 -- it has left the queue: say so, and let the pass go on
        logger.exception("start queue: starting %s failed", wid)
        await _dequeued(st, wid, saved, "failed", f"{type(exc).__name__}: {exc}")
        return False
    status = st.db.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()[
            "status"
        ]
    )
    if status == WorkItemStatus.QUEUED:
        # Another request took the slot between `tick`'s look and the door's
        # claim, and the door queued this one again: not to the back.
        await st.db.write(lambda c: store.keep_place(c, wid, saved["at"]))
        return False
    return True


async def _dequeued(st, wid: str, saved: dict, why: str, detail: str) -> None:
    """The item was put back and did not start: its timeline says why."""
    await st.db.write(
        lambda c: events.append(
            c, wid, WorkItemEvent.DEQUEUED, {"why": why, "detail": detail, "to": saved["from"]}
        )
    )


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
