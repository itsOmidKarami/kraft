from __future__ import annotations

from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _span_ms(start: str | None, end: str) -> int | None:
    """Milliseconds between two ISO timestamps, or None if either is unusable."""
    if not start:
        return None
    try:
        return int(
            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
        )
    except ValueError:
        return None
