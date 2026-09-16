from __future__ import annotations

from datetime import UTC, datetime


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
    except ValueError, TypeError:
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
