"""The ids Kraft mints: a work item's 8 characters, a session's 6, both from
lowercase Crockford base32 (no i, l, o or u, which read as 1, 1, 0 and v).

Drawn with `secrets`, not `random`: processes forked from one parent share
`random`'s state and would draw the same ids.

A session is unique only within its work item, so outside it a session has
two spellings: `session_ref` (`<wid>/<sid>`) for what a person or a header
reads, and `qualified` for a name that cannot hold a `/`."""

from __future__ import annotations

import secrets
from collections.abc import Callable

ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
WORK_ITEM_LENGTH = 8
SESSION_LENGTH = 6
#: Every id minted before these: `uuid.uuid4().hex`.
LEGACY_LENGTH = 32


class Exhausted(RuntimeError):
    """Every candidate was taken: not bad luck at these lengths, a broken
    `taken` or generator, so said loudly rather than looped on."""


def mint(length: int, taken: Callable[[str], bool], *, tries: int = 10) -> str:
    """The first of up to `tries` drawn candidates that `taken` does not
    reject. `taken` is asked when the id is drawn; the row that holds it is
    written later, and its primary key refuses a draw that raced another."""
    for _ in range(tries):
        candidate = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if not taken(candidate):
            return candidate
    raise Exhausted(f"{tries} candidate ids in a row were taken: refusing to guess further")


def session_ref(work_item_id: str, session_id: str) -> str:
    """A session named outside its item: in `KRAFT_SESSION_ID`, the
    `X-Kraft-Session-Id` header, messages and logs. Never stored, and never
    one URL path segment: split it first."""
    return f"{work_item_id}/{session_id}"


def parse_session_ref(value: str) -> tuple[str | None, str]:
    """`(work item id, session id)`. A value with no `/` is a session from
    before per-item ids, unique on its own, so its item is None."""
    wid, sep, sid = value.rpartition("/")
    return (wid, sid) if sep else (None, value)


def qualified(work_item_id: str, session_id: str) -> str:
    """What a machine-wide name for a session is built from: a container, a
    volume, a certificate's CN and folder, a socket folder, an in-memory key.
    A session minted before per-item ids keeps its bare id, so a session that
    ran across the upgrade is found by the names it started with."""
    if len(session_id) == LEGACY_LENGTH:
        return session_id
    return f"{work_item_id}-{session_id}"
