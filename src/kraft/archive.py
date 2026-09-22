"""Auto-archive a completed/abandoned item once it has aged past
`policy.archive_after_days` (UI v2 · 03).

Same shape as `rate_limit_retry.poller`/`waits.poller`: a fixed-interval
tick, not a per-item timer. The interval is coarser than either of those
(they wake on a wall-clock deadline seconds away; `after_days` is day-grained,
so nothing here needs finer than an hour).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from kraft.store import _now as _now  # test seam for wall-clock checks

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
            "SELECT * FROM work_items WHERE status IN ('completed', 'abandoned') "
            "AND archived_at IS NULL AND updated_at <= ?",
            (cutoff,),
        ).fetchall()
    )
    archived = []
    for row in due:
        await _archive_one(app, row, "auto")
        archived.append(row["id"])
    return archived


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("archive tick failed")
