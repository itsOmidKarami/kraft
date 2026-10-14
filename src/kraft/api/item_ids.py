"""A work item named by the start of its id, made whole before routing.

Imports only `store` and FastAPI: `kraft.api.__init__` registers the
middleware before the route modules load, and `api.deps` pulls in the
executor."""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse

from kraft import store

#: The one place a work item id arrives in a path, and the literal routes
#: that share its prefix.
_ITEM_PATH = re.compile(r"^/api/work-items/([^/]+)")
_LITERAL_SEGMENTS = frozenset({"bulk"})


def ambiguous(given: str, ids: list[str]) -> str:
    """Why `given` names no one work item: it is the start of every one of `ids`."""
    return f"{given!r} is the start of {len(ids)} work items: {', '.join(ids)}"


async def whole_work_item_id(request: Request, call_next):
    """Rewrite a unique start of a work item id in the path to the whole id,
    before routing. A handler goes on using its `wid` argument after its
    lookup (spend, drafts, viewed marks, the worktree path), and
    `deps.forbid_self_action` compares it with the caller's own item before
    any lookup, so resolving inside `deps._work_item_row` alone would act on
    the prefix.

    Several matches answer 409 here. No match passes through untouched, so
    the handler's own 404 is unchanged. Registered innermost
    (`kraft.api.__init__`), after the perimeter and authentication admitted
    the caller, so a refused caller learns nothing about which ids exist."""
    m = _ITEM_PATH.match(request.scope["path"])
    db = getattr(request.app.state, "db", None)
    if m is None or m.group(1) in _LITERAL_SEGMENTS or db is None:
        return await call_next(request)
    given = m.group(1)
    ids = db.read(lambda c: store.match_work_item_ids(c, given))
    if len(ids) > 1:
        return JSONResponse({"detail": ambiguous(given, ids)}, status_code=409)
    if len(ids) == 1 and ids[0] != given:
        whole, path = ids[0], request.scope["path"]
        request.scope["path"] = path[: m.start(1)] + whole + path[m.end(1) :]
        raw = request.scope.get("raw_path")
        if raw:
            # Best effort: Starlette routes and builds `request.url` from
            # `scope["path"]`, and nothing in Kraft reads `raw_path`; this only
            # keeps the two alike for whatever might. A client that
            # percent-encoded a character of the segment sent other bytes, and
            # its `raw_path` is left as sent.
            head = b"/api/work-items/"
            request.scope["raw_path"] = raw.replace(head + given.encode(), head + whole.encode(), 1)
    return await call_next(request)
