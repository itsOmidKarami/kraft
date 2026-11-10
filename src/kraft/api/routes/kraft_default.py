"""`GET /kraft-default`: the lines behind the board's drift banner. Authenticated;
`/health` carries only their signature and count."""

from __future__ import annotations

from fastapi import Request

from kraft.api import api_router


@api_router.get("/kraft-default")
async def kraft_default(request: Request):
    """The lines behind the board's drift banner: where this config differs from what the
    installed Kraft ships."""
    found = getattr(request.app.state, "kraft_default", None) or {}
    return {"lines": found.get("lines", []), "signature": found.get("signature")}
