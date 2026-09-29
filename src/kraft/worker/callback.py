"""Who a worker's callback acts as, and what it may reach (sandbox part 2,
P5; spec §5). Transport-neutral: the egress proxy's channel enforces this
today, and a remote worker's socket is to enforce the same list (the shape
agreed with the remote-workers epic, Kraft-5c1tm).

Identity is the channel's, never a token or a header the worker sent: a
session's unix socket, or the subject of its relay's client certificate.
The allowlist holds a session to its own work item: its progress, retry,
replies on its own threads, its own permission asks, and reads of its own
item.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kraft.worker.egress import EgressSession


@dataclass(frozen=True)
class SessionScope:
    session_id: str
    work_item_id: str


def scope_for_channel(session: EgressSession) -> SessionScope:
    """The scope of every request arriving on `session`'s channel."""
    return SessionScope(session.session_id, session.work_item_id)


def address(kind: str) -> str:
    """Where a worker run by backend `kind` reaches the worker API: for
    docker (and later kit), host `kraft` through its egress proxy."""
    if kind in ("docker", "kit"):
        return "http://kraft"
    # A remote worker's is its worker daemon's loopback forwarder, not built.
    raise ValueError(f"no worker API address for a {kind!r} worker")


# One path segment. What it may be is then decided by equality with an id
# the server made (the scope's, or a thread's own), never by its shape.
_P = r"(?P<p>[^/]+)"

#: (method, path, what its parameter must be). "item": the session's own
#: work item; "session": the session itself; "thread": a review thread of
#: its own item; "channel": no parameter, the channel's session is the
#: scope (the MCP endpoint: its tools must act as that session alone).
ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (method, re.compile(path), kind)
    for method, path, kind in (
        ("GET", rf"/api/work-items/{_P}", "item"),
        ("GET", rf"/api/work-items/{_P}/threads", "item"),
        ("GET", rf"/api/work-items/{_P}/diff", "item"),
        ("GET", rf"/api/work-items/{_P}/compare", "item"),
        ("POST", rf"/api/work-items/{_P}/progress", "item"),
        # The escalation self-retry keeps its server-side carve-out.
        ("POST", rf"/api/work-items/{_P}/retry", "item"),
        ("POST", rf"/api/threads/{_P}/replies", "thread"),
        ("POST", rf"/api/worker-sessions/{_P}/permission", "session"),
        ("POST", rf"/api/worker-sessions/{_P}/permission-hook", "session"),
        # Streamable HTTP: POST a message, GET the event stream, DELETE to end.
        ("POST", r"/mcp", "channel"),
        ("GET", r"/mcp", "channel"),
        ("DELETE", r"/mcp", "channel"),
    )
)


def allowed(
    scope: SessionScope, method: str, path: str, *, thread_owner: Callable[[str], str | None]
) -> bool:
    """Whether `method path` (no query) is on the list for `scope`. A path
    shaped like a route but naming another item, session or item's thread
    is not. `thread_owner` answers a thread's work item id, None for no
    such thread."""
    for want, pattern, kind in ROUTES:
        found = pattern.fullmatch(path)
        if method != want or found is None:
            continue
        if kind == "channel":
            return True
        value = found["p"]
        if kind == "item":
            return value == scope.work_item_id
        if kind == "session":
            return value == scope.session_id
        return thread_owner(value) == scope.work_item_id
    return False
