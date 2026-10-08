"""Enum values as SQL text. The one place a member becomes a quoted literal."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

_SAFE = re.compile(r"^[a-z_]+$")


def in_list(
    members: Iterable[str], *, start: int = 0, hang: int = 0, width: int | None = None
) -> str:
    """`'a', 'b', 'c'` for an `IN (...)` list.

    With `width`, wraps greedily the way the hand-written DDL did: the list
    begins at column `start`, continuation lines are indented `hang`, and a
    line never passes column `width`.
    """
    values = [str(m) for m in members]
    bad = [v for v in values if not _SAFE.match(v)]
    if bad:
        raise ValueError(f"not a plain lower-case identifier, cannot go into SQL: {bad}")
    if width is None:
        return ", ".join(f"'{v}'" for v in values)
    lines: list[str] = []
    cur = ""
    col = start
    for i, v in enumerate(values):
        token = f"'{v}'" + ("," if i < len(values) - 1 else "")
        if cur and col + 1 + len(token) > width:
            lines.append(cur)
            cur = token
            col = hang + len(token)
        elif cur:
            cur += " " + token
            col += 1 + len(token)
        else:
            cur = token
            col = start + len(token)
    lines.append(cur)
    return ("\n" + " " * hang).join(lines)


def marks(group: Sequence[object]) -> str:
    """`?, ?, ?` with one mark per member of `group`."""
    return ", ".join("?" for _ in group)
