"""Auto-archive a completed/abandoned item once it has aged past
`policy.archive_after_days` (UI v2 · 03).

Same shape as `rate_limit_retry.poller`/`waits.poller`: a fixed-interval
tick, not a per-item timer. The interval is coarser than either of those
(they wake on a wall-clock deadline seconds away; `after_days` is day-grained,
so nothing here needs finer than an hour).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

from kraft.store import _now as _now  # test seam for wall-clock checks
from kraft.vocab import Verb, admitting
from kraft.vocab.sql import in_list

logger = logging.getLogger(__name__)

_INTERVAL_S = 3600


async def tick(app) -> list[str]:
    """One poll. Returns the work item ids archived, usually none."""
    from kraft.api.routes.lifecycle import (
        _archive_one,  # deferred: kraft.api <-> kraft.archive cycle
    )

    st = app.state
    after_days = st.policy.archive_after_days if st.policy is not None else None
    if not after_days:
        return []
    cutoff = (datetime.fromisoformat(_now()) - timedelta(days=after_days)).isoformat()
    due = st.db.read(
        lambda c: c.execute(
            f"SELECT * FROM work_items WHERE status IN ({in_list(admitting(Verb.ARCHIVE))}) "
            "AND archived_at IS NULL AND updated_at <= ?",
            (cutoff,),
        ).fetchall()
    )
    archived = []
    for row in due:
        # One row's failure must not skip every later due row until the next
        # hour's tick, which would meet the same row first again.
        try:
            await _archive_one(app, row, "auto")
        except Exception:  # noqa: BLE001
            logger.exception("auto-archive %s failed", row["id"])
            continue
        archived.append(row["id"])
    return archived


def wake(st) -> None:
    """Tick now rather than at the hour: a published `archive_after_days`
    archives what it makes due at once. Before the poller starts, nothing."""
    if (event := getattr(st, "archive_wake", None)) is not None:
        event.set()


async def poller(app) -> None:
    """`tick` on a fixed interval, or as soon as `wake` asks, until cancelled."""
    event = app.state.archive_wake = asyncio.Event()
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), _INTERVAL_S)
        event.clear()
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("archive tick failed")
