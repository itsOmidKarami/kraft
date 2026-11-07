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
    due = []
    if after_days:
        cutoff = (datetime.fromisoformat(_now()) - timedelta(days=after_days)).isoformat()
        due = st.db.read(
            lambda c: c.execute(
                f"SELECT * FROM work_items WHERE status IN ({in_list(admitting(Verb.ARCHIVE))}) "
                "AND archived_at IS NULL AND updated_at <= ?",
                (cutoff,),
            ).fetchall()
        )
    due = [r for r in due if r["parent_id"] is None]
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
    # A slice's child keeps its worktree until its parent has joined it, however
    # long it has been finished; after the join, or once its parent is archived
    # (an abandoned or cancelled one, which never joins), it archives at once.
    for row in st.db.read(_archivable_children):
        try:
            await _archive_one(app, row, "auto")
        except Exception:  # noqa: BLE001
            logger.exception("auto-archive %s failed", row["id"])
            continue
        archived.append(row["id"])
    return archived


def _archivable_children(conn) -> list:
    """Finished children of a generation the parent's group has left behind
    (joined, or started over), or whose parent is archived: a child is archived with
    its parent, never alone before the join."""
    from kraft import store

    rows = conn.execute(
        f"SELECT * FROM work_items WHERE parent_id IS NOT NULL AND archived_at IS NULL "
        f"AND status IN ({in_list(admitting(Verb.ARCHIVE))})"
    ).fetchall()
    out = []
    for row in rows:
        parent = conn.execute(
            "SELECT archived_at FROM work_items WHERE id = ?", (row["parent_id"],)
        ).fetchone()
        state = store.group_state(conn, row["parent_id"])
        # A generation is finished once the parent is past its join, or has started
        # over: a rewind to the plan puts the group back to `deciding` (or `prelude`),
        # with the old generation's children left behind, dropped or joined.
        finished = state is not None and not (
            state.phase in ("forked", "joining")
            and state.generation <= (row["slice_generation"] or 0)
        )
        if finished or (parent is not None and parent["archived_at"]):
            out.append(row)
    return out


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
