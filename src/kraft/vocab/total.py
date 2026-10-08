"""A table that must say something about every member of its enum."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType


def total[E: Enum, R](enum: type[E], rows: Mapping[E, R], *, name: str) -> Mapping[E, R]:
    """`rows` keyed by every member of `enum`, in declaration order, read-only.

    The import-time guard of the whole package: adding a member to an enum
    without a row here raises, naming the member, so the author has to decide
    what the new value means before anything else runs.
    """
    members = list(enum)
    problems = [f"{enum.__name__}.{m.name} has no row in {name}" for m in members if m not in rows]
    problems += [
        f"{name} has a row for {k!r}, which is not a member of {enum.__name__}"
        for k in rows
        if k not in members
    ]
    if problems:
        raise ValueError("; ".join(problems))
    return MappingProxyType({m: rows[m] for m in members})
