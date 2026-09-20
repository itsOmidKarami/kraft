from __future__ import annotations

import os
from dataclasses import asdict

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kraft import auth as auth_mod
from kraft import update as update_mod
from kraft.api import api_router


class Login(BaseModel):
    password: str
    stay_signed_in: bool = True


@api_router.post("/login")
async def login(body: Login, request: Request):
    st = request.app.state
    if not auth_mod.verify_password(body.password, st.access["password_hash"]):
        raise HTTPException(401, "Wrong password.")
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
    invalid = st.templates.invalid
    invalid_policy = st.invalid_policy
    # A library that did not parse makes every chain unselectable, so it is a
    # degraded instance for the same reason a bad `policy.yaml` is -- reported
    # under `invalid_templates` (keyed by the file, beside the legacy per-chain
    # entries) rather than as a new field, so the SPA's health badge, `kraft
    # admin health` and `admin doctor` all show it without changing.
    library_errors = getattr(st, "invalid_library", None) or []
    if library_errors:
        invalid = {**invalid, "library": "; ".join(library_errors)}
    return {
        "status": "degraded" if (invalid or invalid_policy) else "ok",
        "invalid_templates": invalid,
        "invalid_policy": invalid_policy,
        "reattach_summary": asdict(st.reattach_summary),
        "index": st.indexer.health(),
        # public: the login screen says which address it is asking a password for
        "bind": st.access["bind"],
        "port": st.access["port"],
        # public: which instance this is -- a monitor, or a human's CLI, needs
        # this to tell a real answer from an impostor on the same port
        # (Kraft-kquf: "status ok, documents 2" from somebody's e2e fixture).
        "run_dir": str(st.run_dirs.base),
        "pid": os.getpid(),
        # public: the login screen's "stay signed in · N days" needs this
        # before a session exists to ask `/access` for it.
        "session_expiry_days": st.access["session_expiry_days"],
        # public: the sidebar footer names its own build (UI v2 · 01)
        "version": update_mod.installed(),
    }
