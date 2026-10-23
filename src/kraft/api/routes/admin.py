from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kraft import apply as apply_mod
from kraft import update, when_free
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
    # A person asking again: a restart that failed before is tried afresh.
    when_free.clear_failure(st)
    try:
        getattr(st, "restart_runner", apply_mod.spawn_restart)(st)
    except (OSError, SystemExit) as exc:
        raise HTTPException(500, f"could not start the restart: {exc}") from exc
    return {"restarting": True}


@api_router.post("/update/confirm")
async def post_update_confirm(request: Request):
    """The restart helper's word that this server came up as the process an
    update expected: it ends probation, and the walks and pollers it held back
    start. 409 when no update waits to be confirmed."""
    from kraft.api import startup

    if not await startup.end_probation(request.app):
        raise HTTPException(409, "no update is waiting to be confirmed")
    return {"confirmed": True}


async def _update_state(request: Request, channel: str | None, *, force: bool) -> dict:
    from kraft.cli import admin as admin_cli

    st = request.app.state
    cfg = when_free.settings(st)
    # An explicit `channel` is for this read only; else the stored one, else the
    # installed version's own, as it was before the setting existed.
    channel = channel or when_free.effective_channel(cfg)
    if channel not in update.CHANNELS:
        raise HTTPException(
            400, f"unknown channel {channel!r}: one of {', '.join(update.CHANNELS)}"
        )
    # KRAFT_NO_UPDATE_CHECK turns off the check Kraft makes on its own, and the
    # sidebar's read on every page load is that. An explicit check (`force`)
    # is somebody asking, so it still goes out.
    check_disabled = bool(os.environ.get("KRAFT_NO_UPDATE_CHECK"))
    cache_only = not force and check_disabled
    release = await asyncio.to_thread(
        lambda: update.latest(force=force, channel=channel, cache_only=cache_only)
    )
    installed = update.installed()
    parts = update._parts(installed)
    in_major = None
    if parts:
        in_major = await asyncio.to_thread(
            lambda: update.latest_in_major(
                parts[0], channel=channel, force=False, cache_only=cache_only
            )
        )
    checked = update.last_checked(channel)
    pending = when_free.pending(st)
    return {
        "installed": installed,
        "latest": release.tag if release else None,
        # The newest within the installed major: a newer major shows beside it, and
        # only a person's mark takes that one.
        "latest_in_major": in_major.tag if in_major else None,
        "channel": channel,
        # About's footer names the interpreter; here, behind the login, not on /health.
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        # An unreachable feed is not "up to date": unknown is null, not false.
        "behind": update.is_behind(release) if release else None,
        "checked_at": datetime.fromtimestamp(checked, UTC).isoformat() if checked else None,
        "check_disabled": check_disabled,
        "install_kind": update.install_kind(),
        "dev_install": update.is_dev_install(),
        "restart_kind": admin_cli.restart_kind(st.run_dirs.base),
        "marked": when_free.marked(st),
        "pending": {"release": pending.release, "restart": pending.restart},
        "blocked": pending.blocked,
        "outcome": when_free.outcome(st),
    }


@api_router.get("/update")
async def get_update(request: Request, channel: str | None = None):
    return await _update_state(request, channel, force=False)


@api_router.post("/update/check")
async def post_update_check(request: Request, channel: str | None = None):
    return await _update_state(request, channel, force=True)


class MarkBody(BaseModel):
    tag: str
    #: Where to look the release up, for this call only.
    channel: str | None = None


@api_router.post("/update/mark")
async def post_update_mark(body: MarkBody, request: Request):
    """Install a release when Kraft is free. A person asking, so it works whatever
    `auto_update` says, and a major version is taken too."""
    st = request.app.state
    cfg = when_free.settings(st)
    channel = body.channel or when_free.effective_channel(cfg)
    if channel not in update.CHANNELS:
        raise HTTPException(
            400, f"unknown channel {channel!r}: one of {', '.join(update.CHANNELS)}"
        )
    tag = body.tag if body.tag.startswith("v") else f"v{body.tag}"
    if update.is_dev_install():
        raise HTTPException(
            409,
            "this is a dev install, installed from a checkout: it has no release to install. "
            "Install it by hand; its restart waits for a free moment.",
        )
    if (kind := update.install_kind()) != "uv":
        raise HTTPException(409, update.cannot_install_reason(kind))
    release = await asyncio.to_thread(update.release_for, tag, channel=channel)
    if release is None:
        raise HTTPException(404, f"no release {tag} on the {channel} channel")
    if not update.is_behind(release):
        raise HTTPException(
            409, f"{tag} is not newer than the installed {update.installed()}; nothing to install"
        )
    when_free.mark(st, release.tag, "you", release.wheel_url)
    return await _update_state(request, None, force=False)


@api_router.delete("/update/mark")
async def delete_update_mark(request: Request):
    when_free.unmark(request.app.state)
    return await _update_state(request, None, force=False)


@api_router.post("/update/restart-when-free")
async def post_update_restart_when_free(request: Request):
    """One restart at the next free moment, whatever `restart_when_free` says."""
    when_free.ask_restart(request.app.state)
    return await _update_state(request, None, force=False)
