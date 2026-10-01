from __future__ import annotations

from fastapi import HTTPException, Request

from kraft import apply as apply_mod
from kraft.api import api_router


@api_router.get("/apply")
async def get_apply(request: Request):
    """What is saved but not running: `restart` and `reload` items."""
    return apply_mod.pending(request.app.state)


@api_router.post("/apply/reload")
async def post_apply_reload(request: Request):
    await apply_mod.reload(request.app)
    return apply_mod.pending(request.app.state)


@api_router.post("/apply/restart", status_code=202)
async def post_apply_restart(request: Request):
    """Only for a server a service manager started; one started from a terminal
    is that terminal's to restart. The runner is `app.state.restart_runner`."""
    st = request.app.state
    if not apply_mod.managed():
        raise HTTPException(409, "started from a terminal: restart it there")
    try:
        getattr(st, "restart_runner", apply_mod.spawn_restart)(st)
    except (OSError, SystemExit) as exc:
        raise HTTPException(500, f"could not start the restart: {exc}") from exc
    return {"restarting": True}
