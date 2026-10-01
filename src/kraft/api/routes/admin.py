from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import HTTPException, Request

from kraft import apply as apply_mod
from kraft import update
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


async def _update_state(channel: str | None, *, force: bool) -> dict:
    channel = channel or update.channel_of(update.installed())
    if channel not in update.CHANNELS:
        raise HTTPException(
            400, f"unknown channel {channel!r}: one of {', '.join(update.CHANNELS)}"
        )
    release = await asyncio.to_thread(lambda: update.latest(force=force, channel=channel))
    checked = update.last_checked(channel)
    return {
        "installed": update.installed(),
        "latest": release.tag if release else None,
        "channel": channel,
        # An unreachable feed is not "up to date": unknown is null, not false.
        "behind": update.is_behind(release) if release else None,
        "checked_at": datetime.fromtimestamp(checked, UTC).isoformat() if checked else None,
    }


@api_router.get("/update")
async def get_update(channel: str | None = None):
    return await _update_state(channel, force=False)


@api_router.post("/update/check")
async def post_update_check(channel: str | None = None):
    return await _update_state(channel, force=True)
