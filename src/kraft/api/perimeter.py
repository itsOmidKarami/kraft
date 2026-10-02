"""SPA navigation shortcut, local-client check, and the auth/perimeter
middleware. Applied to `app` by `kraft.api.__init__` (an `app.middleware`
decorator needs `app`, which does not exist until the package `__init__`
builds it) -- registration order there must stay `_spa_navigation`,
`_authenticate`, `_perimeter`, `_frame_guard` (rule 9: Starlette runs the
last-declared middleware first, so `_perimeter` must come after the other two,
and `_frame_guard` after it so a refusal carries its headers too)."""

from __future__ import annotations

import hmac
import html
import ipaddress
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from kraft import auth as auth_mod
from kraft import config as config_mod
from kraft import update as update_mod
from kraft.api import apidocs


def _is_api_path(path: str) -> bool:
    """True for `/api` itself or anything under it — one predicate for the
    boundary every middleware and 404 path below has to agree on. `path ==
    "/api"` matters even though nothing is mounted at the router root today:
    `.startswith("/api/")` alone lets the bare prefix slip through as if it
    were an ordinary SPA path."""
    return path == "/api" or path.startswith("/api/")


def _is_fastapi_docs_path(app: FastAPI, path: str) -> bool:
    """FastAPI's own pages: Swagger UI, ReDoc and the schema. Their JS and CSS
    come from a CDN, so these paths are all a browser fetches from us for
    them."""
    own = {apidocs.SWAGGER_PATH, apidocs.REDOC_PATH, app.openapi_url}
    return path in own - {None}


async def _spa_navigation(request: Request, call_next):
    # A browser deep-link / refresh on a client-side route (e.g. /work-items/<id>)
    # would otherwise reach the same catch-all any GET falls through to anyway
    # — this is a fast path, not the only path. Any top-level navigation
    # carries Sec-Fetch-Dest: document; hand those the SPA shell directly and
    # skip routing. Static assets are dest=script/style, XHR is dest=empty, so
    # only real navigations are caught. Excluded for /api/: that prefix is
    # unambiguously JSON, so a forged header there must not stand in for a
    # real 401/404/200. Nor for FastAPI's /docs and /redoc, which a browser
    # would otherwise never see.
    dist = getattr(request.app.state, "frontend_dist", None)
    if (
        dist is not None
        and request.method == "GET"
        and request.headers.get("sec-fetch-dest") == "document"
        and not _is_api_path(request.url.path)
        and not _is_fastapi_docs_path(request.app, request.url.path)
    ):
        # The browser caches by URL, so without no-store a refresh could answer
        # from a stale cached shell. `vary` says the same thing to caches that
        # honour it.
        return spa_shell(request, dist, {"vary": "sec-fetch-dest"})
    return await call_next(request)


def spa_shell(request: Request, dist, headers: dict[str, str] | None = None):
    """The SPA's `index.html`, or a page saying to restart when the bundle on
    disk is no longer this server's.

    `kraft admin update` replaces the installed package, `_bundled/web`
    included, under a server that keeps running until it is restarted. The new
    interface against the old API misreads it (a 1.4 server's board, drawn by
    1.5's interface, put every item under RUNNING), so the shell is refused. A
    hashed asset is still served as is: its name is its content, and a tab
    already open on this server's own interface keeps working."""
    headers = {"cache-control": "no-store", **(headers or {})}
    running = getattr(request.app.state, "version", None)
    installed = update_mod.installed()
    if running and installed != running:
        return HTMLResponse(_restart_page(running, installed), status_code=503, headers=headers)
    return FileResponse(dist / "index.html", headers=headers)


def _restart_page(running: str, installed: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Kraft: restart to finish the update</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:3rem auto;"
        "padding:0 16px;color:#1d1d1f;background:#fff}"
        "@media (prefers-color-scheme:dark){body{color:#e8e8ea;background:#161618}}"
        "code{font-size:.95em}</style></head><body>"
        "<h1>Restart Kraft to finish the update</h1>"
        f"<p>Kraft {html.escape(installed)} is installed, but this server is still running "
        f"{html.escape(running)}. Its web interface is the new one, which does not match the "
        "running server, so it is not shown.</p>"
        "<p>Restart the server, then reload this page:</p>"
        "<pre><code>kraft admin restart</code></pre></body></html>"
    )


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


#: Hostnames that can only mean this machine. `config.host_name` keeps an IPv6
#: literal's brackets and `urlsplit().hostname` (an Origin) strips them, so
#: both spellings of ::1 are here.
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
    presented = bearer[7:] if bearer.startswith("Bearer ") else ""
    expected = getattr(app.state, "mcp_token", None)
    if presented and expected and hmac.compare_digest(presented, expected):
        return await call_next(request)
    # The trigger token files work and nothing else; anywhere else it falls
    # through to the cookie check like no credential at all.
    trigger = getattr(app.state, "trigger_token", None)
    if (
        presented
        and trigger
        and request.method == "POST"
        and request.url.path == "/api/triggers"
        and hmac.compare_digest(presented, trigger)
    ):
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
    refused = _refusal(request, check_origin=request.method not in ("GET", "HEAD"))
    if refused is None:
        return await call_next(request)
    bound_host = getattr(request.app.state, "bound_host", "127.0.0.1")
    if refused == _unexpected_host(bound_host) and _is_page_navigation(request):
        return HTMLResponse(_unexpected_host_page(request, bound_host), status_code=403)
    return JSONResponse({"detail": refused}, status_code=403)


def _unexpected_host(bound_host: str) -> str:
    return f"unexpected Host for a server bound to {bound_host}"


def _is_page_navigation(request: Request) -> bool:
    """A person opening a page in a browser, not a script or the SPA's own
    calls: those get the JSON refusal every API client reads."""
    return (
        request.method in ("GET", "HEAD")
        and not _is_api_path(request.url.path)
        and "text/html" in request.headers.get("accept", "")
    )


def _raw_host(request: Request) -> str:
    """The `Host` header as the browser sent it, UTF-8 with replacement."""
    for key, value in request.scope.get("headers", []):
        if key == b"host":
            return value.decode("utf-8", "replace")
    return ""


def _unexpected_host_page(request: Request, bound_host: str) -> str:
    """What a browser shows for a name this server does not answer to: the
    name it got and how to allow it, not a bare JSON line. A 1.4 server never
    checked the Host of a plain-http browser, so after an upgrade this is the
    first a LAN user hears of `allowed_hosts`. The Host is the requester's
    own text, so it is escaped, after decoding it as UTF-8: Starlette's
    latin-1 turns a non-ASCII name into mojibake.

    The link back is the bound address, or 127.0.0.1 for a wildcard bind,
    which listens there too."""
    raw = _raw_host(request)
    name = html.escape(config_mod.host_name(raw) or raw or "(none)")
    port = getattr(request.app.state, "bound_port", None) or request.url.port or 8765
    here = "127.0.0.1" if bound_host in ("0.0.0.0", "::") else bound_host
    local = html.escape(f"http://{config_mod.url_host(here)}:{port}/")
    if bound_host in config_mod.LOOPBACK:
        how = (
            f"<p>It is bound to {html.escape(bound_host)}, so it answers only at "
            f"<code>localhost</code> or <code>127.0.0.1</code>. On this machine, open "
            f'<a href="{local}">{local}</a>. To reach it from another device, bind it to '
            "the network on Settings &rsaquo; Access first.</p>"
        )
    else:
        how = (
            "<p>On a network bind, Kraft answers a browser only at a name listed in "
            "<code>allowed_hosts</code>. To allow this one, open Kraft on the machine it "
            f'runs on, at <a href="{local}">{local}</a>, and add <code>{name}</code> on '
            "Settings &rsaquo; Access, where it applies at once. Or add it to "
            "<code>allowed_hosts</code> in <code>access.yaml</code> and restart Kraft. "
            "Enter the exact name: a wildcard is refused.</p>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Kraft: address not allowed</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:3rem auto;"
        "padding:0 16px;color:#1d1d1f;background:#fff}"
        "@media (prefers-color-scheme:dark){body{color:#e8e8ea;background:#161618}"
        "a{color:#8ab4ff}}code{font-size:.95em}</style></head><body>"
        f"<h1>Kraft does not answer to <code>{name}</code></h1>{how}</body></html>"
    )


#: No other site may put Kraft in a frame. A loopback board has no login, so
#: a page that framed it invisibly could turn a click on its own content into
#: Resume or Approve (clickjacking). `X-Frame-Options` is the older spelling
#: of the same rule, for a browser that ignores `frame-ancestors`.
_FRAME_HEADERS = {
    "content-security-policy": "frame-ancestors 'self'",
    "x-frame-options": "SAMEORIGIN",
}


async def _frame_guard(request: Request, call_next):
    """Every response, the SPA shell and the API alike, says only Kraft's own
    pages may frame it. Declared last, so it is the outermost middleware and
    a 401 or 403 from the ones inside carries the headers as well. A page
    with a policy of its own (`apidocs`) gets the directive added to it."""
    response = await call_next(request)
    for name, value in _FRAME_HEADERS.items():
        own = response.headers.get(name)
        if own is None:
            response.headers[name] = value
        elif name == "content-security-policy" and "frame-ancestors" not in own:
            response.headers[name] = f"{own}; {value}"
    return response


def _refusal(request: Request | WebSocket, *, check_origin: bool) -> str | None:
    """Why `_perimeter` refuses this request, or None to let it through.

    HTTP middleware does not run for websockets, so `/ws/events` asks this
    too, with `check_origin` always on: a browser opens a websocket to any
    site with no same-origin check, so its Origin is the only sign of which
    page asked. One set of rules for both, or the board's own live stream
    is refused on a LAN bind its HTTP calls pass.
    """
    st = request.app.state
    peer = request.client.host if request.client else "an unknown peer"
    has_password = bool((getattr(st, "access", None) or {}).get("password_hash"))

    # 1. The bind in access.yaml is a claim; the peer address is the fact. A
    #    launch that widened the bind without going through `cli.admin._bind` would
    #    otherwise serve the whole API to the LAN with the auth gate wide open,
    #    because `_requires_auth` has no password to demand.
    if not _client_is_local(request) and not has_password:
        return f"this server is configured for a loopback bind; refusing {peer}"

    # 2. DNS rebinding: a page on evil.com whose name flips to 127.0.0.1 is
    #    same-origin with a local Kraft and can read every response, ids
    #    included, then drive any route. A server that bound loopback may only
    #    be addressed by a loopback name; a server bound off loopback may only
    #    be addressed by a name the operator put in `allowed_hosts` (Kraft-cdy)
    #    -- there is no bound address to compare against there, so without an
    #    allowlist this fails closed rather than skipping the check.
    #
    #    Only a browser can be rebound, but a browser cannot be told apart by
    #    `sec-fetch-site` alone: Chromium sends no Sec-Fetch-* headers to a
    #    plain-http origin on any name but localhost, which is exactly the
    #    origin a rebinding page has. Nor by Origin or Referer: a same-origin
    #    GET carries no Origin, and the page picks its own referrer policy.
    #    So on a loopback bind the Host is checked on every request. Every
    #    client of its own -- the CLI, MCP, the VS Code extension, the dev
    #    proxy, a worker's in-process calls -- already dials 127.0.0.1 or
    #    localhost. Off loopback, auth is on, and a rebound page has no
    #    session cookie for its own name (and its login POST carries an
    #    Origin), so the allowlist is held to requests that look like a
    #    browser's, and the CLI, MCP and curl need no entry in it. A loopback
    #    name is always allowed there too: a browser sends one only when it is
    #    on this machine, so the board still opens at 127.0.0.1 on a LAN bind
    #    without listing it, and a rebound page carries its own name instead.
    hostname = config_mod.host_name(request.headers.get("host", ""))
    bound_host = getattr(st, "bound_host", "127.0.0.1")
    if bound_host in config_mod.LOOPBACK:
        refused = hostname not in _LOCAL_HOSTS
    else:
        allowed = set((getattr(st, "access", None) or {}).get("allowed_hosts") or [])
        refused = _from_a_browser(request) and hostname not in allowed | _LOCAL_HOSTS
    if refused:
        return _unexpected_host(bound_host)

    # 3. Cross-site write. `_origin_ok` plus one clause, so that a LAN instance
    #    serving its own SPA (Origin and Host both 192.168.1.5:8765) is not
    #    locked out of its own board. Reads are left alone: this is about the
    #    mutating routes that take no JSON body and so need no preflight.
    origin = request.headers.get("origin")
    if (
        check_origin
        and origin
        and not _origin_ok(origin)
        and urlsplit(origin).netloc != request.headers.get("host")
    ):
        return "cross-site request refused"

    return None


def _from_a_browser(request: Request | WebSocket) -> bool:
    """Whether a request carries anything a browser adds on its own: Fetch
    metadata, an Origin, a Referer, or the Accept of a page navigation. The
    CLI, MCP and curl send none of these."""
    headers = request.headers
    return bool(
        headers.get("sec-fetch-site")
        or headers.get("origin")
        or headers.get("referer")
        or "text/html" in headers.get("accept", "")
    )


def _origin_ok(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser client
    return urlsplit(origin).hostname in _LOCAL_HOSTS
