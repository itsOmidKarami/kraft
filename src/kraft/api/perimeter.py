"""SPA navigation shortcut, local-client check, and the auth/perimeter
middleware. Applied to `app` by `kraft.api.__init__` (an `app.middleware`
decorator needs `app`, which does not exist until the package `__init__`
builds it) -- registration order there must stay `_spa_navigation`,
`_authenticate`, `_perimeter` (rule 9: Starlette runs the last-declared
middleware first, so `_perimeter` must be declared last)."""

from __future__ import annotations

import hmac
import ipaddress
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from kraft import auth as auth_mod
from kraft import config as config_mod


def _is_api_path(path: str) -> bool:
    """True for `/api` itself or anything under it — one predicate for the
    boundary every middleware and 404 path below has to agree on. `path ==
    "/api"` matters even though nothing is mounted at the router root today:
    `.startswith("/api/")` alone lets the bare prefix slip through as if it
    were an ordinary SPA path."""
    return path == "/api" or path.startswith("/api/")


async def _spa_navigation(request: Request, call_next):
    # A browser deep-link / refresh on a client-side route (e.g. /work-items/<id>)
    # would otherwise reach the same catch-all any GET falls through to anyway
    # — this is a fast path, not the only path. Any top-level navigation
    # carries Sec-Fetch-Dest: document; hand those the SPA shell directly and
    # skip routing. Static assets are dest=script/style, XHR is dest=empty, so
    # only real navigations are caught. Excluded for /api/: that prefix is
    # unambiguously JSON, so a forged header there must not stand in for a
    # real 401/404/200.
    dist = getattr(request.app.state, "frontend_dist", None)
    if (
        dist is not None
        and request.method == "GET"
        and request.headers.get("sec-fetch-dest") == "document"
        and not _is_api_path(request.url.path)
    ):
        # The browser caches by URL, so without no-store a refresh could answer
        # from a stale cached shell. `vary` says the same thing to caches that
        # honour it.
        return FileResponse(
            dist / "index.html",
            headers={"cache-control": "no-store", "vary": "sec-fetch-dest"},
        )
    return await call_next(request)


#: Paths that must work before a session exists.
#: `/api/health` is deliberately open — a monitor should not need a session,
#: and the login screen reads the bind address from it.
_PUBLIC_PATHS = {"/api/login", "/api/health"}


def _is_static_asset(app: FastAPI, path: str) -> bool:
    """True for a file that ships with the SPA bundle.

    The login page cannot render if its own JS and CSS come back 401, so the
    bundle is served before a session exists. These are build artifacts, not
    data — nothing about the running system leaks through them.
    """
    dist = getattr(app.state, "frontend_dist", None)
    if dist is None or not path or path == "/":
        return False
    candidate = (dist / path.lstrip("/")).resolve()
    return dist.resolve() in candidate.parents and candidate.is_file()


#: Hostnames that can only mean this machine. `urlsplit().hostname` strips the
#: brackets off an IPv6 literal, so "::1" covers "[::1]" as well.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _client_is_local(request: Request | WebSocket) -> bool:
    """True when the peer address is a loopback IP.

    Fails closed: an absent peer, or one that is not an IP at all (starlette's
    TestClient reports the literal "testclient"), is not local.

    uvicorn resolves the peer through `proxy_headers`, on by default with
    `forwarded_allow_ips="127.0.0.1"` — so a reverse proxy on this box surfaces
    the real client here, and a forged `X-Forwarded-For` from a remote peer is
    ignored because that peer is not trusted. Both directions only make this
    stricter than reading the socket would be.
    """
    client = request.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


def _requires_auth(app: FastAPI, request: Request | WebSocket) -> bool:
    """Auth is off on localhost and on for anything else (design 5e).

    Three details this depends on:

    * it is keyed to the address the process actually bound at startup, not the
      one saved in `access.yaml` — a bind change "takes effect on restart", so
      saving one must not lock the operator out of a server still on loopback;
    * the short-circuit needs the *peer* to be loopback too. `bound_host` is a
      claim, and a launch that widened the bind without going through
      `cli.admin._bind` (`uvicorn kraft.api:app --host 0.0.0.0`) does not update it —
      so a remote caller reaching a server that believes it is on loopback still
      has to log in;
    * with no password set there is nothing to authenticate *against*, so the
      gate stays open rather than bricking the API into a state where even
      setting the first password is refused. `__main__` will not start a
      non-loopback bind in that state, and the Access screen refuses to save one.
      `_perimeter` is what stops that open gate from being reachable remotely.
    """
    local_bind = getattr(app.state, "bound_host", "127.0.0.1") in config_mod.LOOPBACK
    if local_bind and _client_is_local(request):
        return False
    return bool((getattr(app.state, "access", None) or {}).get("password_hash"))


async def _authenticate(request: Request, call_next):
    app = request.app
    if (
        not _requires_auth(app, request)
        or request.url.path in _PUBLIC_PATHS
        or _is_static_asset(app, request.url.path)
        or (request.method == "GET" and not _is_api_path(request.url.path))
    ):
        return await call_next(request)
    # After the SPA-shell branch on purpose: that branch answers any non-/api
    # GET, so a bearer check ahead of it would leave that path reachable, and
    # one inside it would hand an MCP client HTML not JSON.
    bearer = request.headers.get("authorization", "")
    expected = getattr(app.state, "mcp_token", None)
    if expected and bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], expected):
        return await call_next(request)
    token = request.cookies.get(auth_mod.COOKIE)
    if not token or not await app.state.db.write(
        lambda c, token=token: auth_mod.touch_session(c, token)
    ):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


async def _perimeter(request: Request, call_next):
    """Who may talk to this server at all, before any question of a session.

    Declared *after* `_authenticate` on purpose. Starlette inserts each added
    middleware at the front of the stack, so the last one declared is the
    outermost and runs first — this has to be entered before the auth gate and
    before the SPA-shell branch, or a refused request gets answered by them.
    """
    st = request.app.state
    peer = request.client.host if request.client else "an unknown peer"
    has_password = bool((getattr(st, "access", None) or {}).get("password_hash"))

    # 1. The bind in access.yaml is a claim; the peer address is the fact. A
    #    launch that widened the bind without going through `cli.admin._bind` would
    #    otherwise serve the whole API to the LAN with the auth gate wide open,
    #    because `_requires_auth` has no password to demand.
    if not _client_is_local(request) and not has_password:
        return JSONResponse(
            {"detail": f"this server is configured for a loopback bind; refusing {peer}"},
            status_code=403,
        )

    # 2. DNS rebinding: a page on evil.com whose name flips to 127.0.0.1 is
    #    same-origin with a local Kraft and can read every response, ids
    #    included, then drive any route. A server that bound loopback may only
    #    be addressed by a loopback name; a server bound off loopback may only
    #    be addressed by a name the operator put in `allowed_hosts` (Kraft-cdy)
    #    -- there is no bound address to compare against there, so without an
    #    allowlist this fails closed rather than skipping the check.
    #
    #    Keyed on `sec-fetch-site` because only a browser can be rebound, and
    #    every other client — the CLI, MCP, httpx, curl — would otherwise need a
    #    Host allowlist for no gain. Browsers older than the Fetch Metadata
    #    rollout (pre-2020) do not send it and are not covered.
    if request.headers.get("sec-fetch-site"):
        hostname = urlsplit(f"//{request.headers.get('host', '')}").hostname
        bound_host = getattr(st, "bound_host", "127.0.0.1")
        if bound_host in config_mod.LOOPBACK:
            allowed = _LOCAL_HOSTS
        else:
            allowed = set((getattr(st, "access", None) or {}).get("allowed_hosts") or [])
        if hostname not in allowed:
            return JSONResponse(
                {"detail": f"unexpected Host for a server bound to {bound_host}"},
                status_code=403,
            )

    # 3. Cross-site write. `_origin_ok` plus one clause, so that a LAN instance
    #    serving its own SPA (Origin and Host both 192.168.1.5:8765) is not
    #    locked out of its own board. Reads are left alone: this is about the
    #    mutating routes that take no JSON body and so need no preflight.
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD")
        and origin
        and not _origin_ok(origin)
        and urlsplit(origin).netloc != request.headers.get("host")
    ):
        return JSONResponse({"detail": "cross-site request refused"}, status_code=403)

    return await call_next(request)


def _origin_ok(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser client
    return urlsplit(origin).hostname in _LOCAL_HOSTS
