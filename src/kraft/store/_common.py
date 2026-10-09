from __future__ import annotations

from datetime import UTC, datetime

from kraft import events

# ENDED: an item never leaves these (Kraft-dncfg)
from kraft.vocab import ENDED, ForgeEvent, WorkItemEvent, WorkItemStatus
from kraft.vocab.sql import marks


def write_status(conn, sql: str, params: tuple) -> bool:
    """Run `sql`, an `UPDATE work_items ... WHERE ...` that moves an item to a
    status other than an ending one, unless the item has already ended. True
    when it wrote; a caller appends its event only then (Kraft-y6f08).

    Every non-ending status write in `store/` goes through here. `sql` stays a
    whole literal at its call site, so `dev/check_claim_handoff.py` still sees
    each claim. The ending writes (`mark_completed`, `MANUAL_ENDS`,
    `abandon_work_item`) do not: ending is the one move an item may always make.
    """
    wrote = (
        conn.execute(f"{sql} AND status NOT IN ({marks(ENDED)})", (*params, *ENDED)).rowcount == 1
    )
    if wrote:
        _end_overtaken_holds(conn)
    return wrote


def _end_overtaken_holds(conn) -> None:
    """A row that carries a start request and is not `queued` or `blocked` was
    held, and something other than the start queue has just moved it: a stop,
    a gate review's verdict, a skip. The start it held is over, so the request
    goes and its timeline says so, ahead of the mover's own event.

    Here, not at each write: every non-ending status write passes through
    `write_status`, the next one written included. The queue's own moves clear
    the request in the same `UPDATE` (`work_items._PUT_BACK`), so they never
    match. An ended row is left alone: its ending event already says what
    happened. One scan of `work_items`, a row per work item, per status write."""
    apart = (WorkItemStatus.QUEUED, WorkItemStatus.BLOCKED, *ENDED)
    for wid, status in conn.execute(
        "SELECT id, status FROM work_items "
        f"WHERE queued_request IS NOT NULL AND status NOT IN ({marks(apart)})",
        apart,
    ).fetchall():
        conn.execute("UPDATE work_items SET queued_request = NULL WHERE id = ?", (wid,))
        events.append(
            conn, wid, WorkItemEvent.DEQUEUED, {"why": "superseded", "detail": None, "to": status}
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _span_ms(start: str | None, end: str) -> int | None:
    """Milliseconds between two ISO timestamps, or None if either is unusable.

    TypeError alongside ValueError: a naive and an aware timestamp can't be
    subtracted, and a row written outside `_now()`'s own format (a raw SQL
    fixture using sqlite's `datetime('now')`, say) can carry one. Same
    contract either way -- unusable is unusable, not a crash.
    """
    if not start:
        return None
    try:
        return int(
            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
        )
    except (ValueError, TypeError):
        return None


def session_wall_ms(row, now: str | None = None) -> int | None:
    """A session's wall time: the recorded one, or derived from its stamps.

    `wall_ms` is only ever written by `session_exited`, and a session that is
    paused, skipped, capped, lost or still running never reaches it -- 82 rows
    on this machine, every one of them carrying the two stamps `session_exited`
    would itself have used (Kraft-s7c04.18). The column is a cache of a value
    the row already holds, so a missing one is derived rather than counted as
    zero. That is also why this is one read-side derivation and not a `wall_ms`
    write bolted onto each of the five paths that reach a terminal status: a
    write fixes nothing retroactively, and no write at all can describe a
    session that has not stopped.

    A still-running row has no `exited_at` and derives against `now`, which is
    what makes a live node report the time it has actually spent instead of
    `0s` (Kraft-s7c04.47's totals half).

    None, never 0, when there is no usable start: zero is an answer, and it
    would be a wrong one.
    """
    if row["wall_ms"] is not None:
        return row["wall_ms"]
    return _span_ms(row["started_at"] or row["created_at"], row["exited_at"] or now or _now())


def wait_sessions(conn, work_item_ids) -> set[str]:
    """The sessions among `work_item_ids`' that ran an external wait (a forge
    task watching CI, a review, a merge): each observation it made names it in
    an `external_wait_observed` record (`kraft.waits`). Such a session waits;
    it does no work of its own."""
    ids = list(work_item_ids)
    if not ids:
        return set()
    rows = conn.execute(
        "SELECT DISTINCT json_extract(payload, '$.session_id') AS sid FROM events "
        f"WHERE work_item_id IN ({','.join('?' * len(ids))}) "
        "AND type = ?",
        (*ids, ForgeEvent.EXTERNAL_WAIT_OBSERVED),
    ).fetchall()
    return {r["sid"] for r in rows if r["sid"]}


def wait_timed_out_sessions(conn, work_item_ids) -> set[str]:
    """The sessions among `work_item_ids`' that ended because an external
    wait timed out. Such a session exits `capped_out`, the status a fix loop's
    cap also writes; the wait's own `external_wait_ended` record (`kraft.waits`)
    is what tells the two apart (Kraft-uwbc8), so no status was added."""
    ids = list(work_item_ids)
    if not ids:
        return set()
    rows = conn.execute(
        "SELECT json_extract(payload, '$.session_id') AS sid FROM events "
        f"WHERE work_item_id IN ({','.join('?' * len(ids))}) "
        "AND type = ? AND json_extract(payload, '$.outcome') = 'timed_out'",
        (*ids, ForgeEvent.EXTERNAL_WAIT_ENDED),
    ).fetchall()
    return {r["sid"] for r in rows if r["sid"]}
