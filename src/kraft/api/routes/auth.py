from __future__ import annotations

import math
import os
import time
from dataclasses import asdict

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kraft import auth as auth_mod
from kraft import storage
from kraft import update as update_mod
from kraft.api import api_router, deps
from kraft.plugins import update as plugin_update
from kraft.worker import reattach as reattach_mod

#: Failed logins one address may make within `LOGIN_WINDOW_S` before it is
#: refused with 429 until `LOGIN_WINDOW_S` has passed since the last of them.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_S = 15 * 60


class Login(BaseModel):
    password: str
    stay_signed_in: bool = True


@api_router.post("/login")
async def login(body: Login, request: Request):
    st = request.app.state
    # Keyed on the same peer `_perimeter` judges: uvicorn trusts
    # `X-Forwarded-For` only from 127.0.0.1, so a remote caller cannot pick
    # its own key. Everything behind one proxy that forwards no address
    # shares a count.
    # ponytail: in memory, one list per address that ever failed; lost on
    # restart and never swept. A sweep if addresses ever pile up.
    addr = request.client.host if request.client else ""
    now = time.monotonic()
    fails = st.login_failures.get(addr, [])
    if len(fails) >= LOGIN_MAX_FAILURES and now - fails[-1] < LOGIN_WINDOW_S:
        retry_after = math.ceil(LOGIN_WINDOW_S - (now - fails[-1]))
        raise HTTPException(
            429, "Too many failed logins. Try again later.", {"Retry-After": str(retry_after)}
        )
    if not auth_mod.verify_password(body.password, st.access["password_hash"]):
        st.login_failures[addr] = [t for t in fails if now - t < LOGIN_WINDOW_S] + [now]
        raise HTTPException(401, "Wrong password.")
    st.login_failures.pop(addr, None)
    token = auth_mod.new_token()
    await st.db.write(
        lambda c: auth_mod.create_session(
            c,
            token,
            label=request.headers.get("user-agent", "unknown"),
            ip=request.client.host if request.client else "",
            expiry_days=int(st.access["session_expiry_days"]),
        )
    )
    response = JSONResponse({"ok": True})
    response.set_cookie(
        auth_mod.COOKIE,
        token,
        httponly=True,
        samesite="lax",
        # Over the tunnel's https URL the scheme reaching us may be http, with
        # the tunnel's `X-Forwarded-Proto` saying otherwise. Believing a forged
        # one only adds `Secure`, which costs a plain-HTTP caller its cookie.
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
        # `stay_signed_in=False` drops the cookie's Max-Age, making it a
        # session cookie the browser clears on close. The DB-side session
        # (and its real expiry_days) is unchanged either way -- unchecking
        # the switch changes how long the browser remembers you, not how
        # long the session itself is valid for revocation/listing purposes.
        max_age=int(st.access["session_expiry_days"]) * 86400 if body.stay_signed_in else None,
        path="/",
    )
    return response


@api_router.post("/logout", status_code=204)
async def logout(request: Request):
    token = request.cookies.get(auth_mod.COOKIE)
    if token:
        await request.app.state.db.write(
            lambda c: auth_mod.revoke_session(c, auth_mod.token_id(token))
        )
    response = Response(status_code=204)
    response.delete_cookie(auth_mod.COOKIE, path="/")
    return response


@api_router.get("/sessions")
async def list_sessions(request: Request):
    token = request.cookies.get(auth_mod.COOKIE)
    return {"sessions": request.app.state.db.read(lambda c: auth_mod.list_sessions(c, token))}


@api_router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: str, request: Request):
    revoked = await request.app.state.db.write(lambda c: auth_mod.revoke_session(c, session_id))
    if not revoked:
        raise HTTPException(404, "unknown session")


@api_router.get("/health")
async def health(request: Request):
    st = request.app.state
    invalid_policy = st.invalid_policy
    # A library that did not parse makes every chain unselectable, and a chain
    # that does not resolve makes that one unselectable -- a degraded instance
    # for the same reason a bad `policy.yaml` is, reported under
    # `invalid_templates` so the SPA's health badge, `kraft admin health` and
    # `admin doctor` all show it (Kraft-n1zp9). The other chains still run.
    invalid = deps.invalid_templates(st)
    summary = asdict(st.reattach_summary)
    summary["unknown"] = st.db.read(lambda c: reattach_mod.still_orphaned(c, summary["unknown"]))
    # An `intake.yaml` that does not load leaves auto-intake off and its
    # schedules unfired, which nothing else would say (R12E-03).
    invalid_intake = getattr(st, "invalid_intake", None)
    storage_health = storage.health(st)
    held = (storage_health or {}).get("state") == "held"
    return {
        "status": "degraded" if (invalid or invalid_policy or invalid_intake or held) else "ok",
        "invalid_templates": invalid,
        "invalid_policy": invalid_policy,
        "invalid_intake": invalid_intake,
        # Each auto-updating plugin's last outcome. A held or failed one is
        # not degraded: the locked version still serves.
        "plugin_updates": plugin_update.read_status(st.run_dirs.plugins),
        # public: totals only. None without `storage.worktrees.limit`. `held`
        # degrades: starts that need a new worktree are waiting on disk space.
        "storage": storage_health,
        # True when the server started on that file, so auto-intake and its
        # schedules are off (a trigger left in policy.yaml still fires); False
        # when a reload refused it and the running ones are kept.
        "intake_off": bool(getattr(st, "intake_off", False)),
        # `unknown` as it stands now, not as startup found it.
        "reattach_summary": summary,
        "index": st.indexer.health(),
        # public: the login screen says which address it is asking a password for.
        # What the server is listening on, not access.yaml: `--port` or
        # KRAFT_PORT can differ, and these fields identify the instance.
        "bind": st.bound_host,
        "port": st.bound_port,
        # public: which instance this is -- a monitor, or a human's CLI, needs
        # this to tell a real answer from an impostor on the same port
        # (Kraft-kquf: "status ok, documents 2" from somebody's e2e fixture).
        "run_dir": str(st.run_dirs.base),
        "pid": os.getpid(),
        # public: how long this process has been up; the About page draws it next to the pid.
        "uptime_s": int(time.monotonic() - st.started_at),
        # public: the login screen's "stay signed in · N days" needs this
        # before a session exists to ask `/access` for it.
        "session_expiry_days": st.access["session_expiry_days"],
        # public: the sidebar footer names its own build (UI v2 · 01). The
        # version this process is running, not what is on disk now:
        # `kraft admin update` without `--restart` leaves the old server up.
        "version": getattr(st, "version", None) or update_mod.installed(),
        # public: what a restart would run. Differs from `version` between an
        # update and the restart that finishes it; the SPA and `admin doctor`
        # both say so.
        "installed": update_mod.installed(),
    }
