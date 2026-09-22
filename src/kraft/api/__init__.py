"""The FastAPI app. Split out of what used to be a single `kraft/api.py` --
see the sibling modules for the actual routes:

- `deps` -- helpers shared by several route modules
- `startup` -- `lifespan()` and its background tasks
- `perimeter` -- SPA navigation shortcut, local-client check, auth middleware
- `routes/*` -- one module per resource, each decorating `api_router` below

Nothing outside this package imports anything from here except `app` --
`kraft.intake`, `kraft.waits`, and `kraft.rate_limit_retry` import
`kraft.api.deps` directly instead (see that module's docstring).
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from kraft import config as config_mod
from kraft.api import perimeter
from kraft.api.startup import lifespan

app = FastAPI(lifespan=lifespan)

#: Every JSON endpoint lives under here, so it can never share a path with an
#: SPA client-side route (e.g. GET /work-items/<id> the page vs. the same path
#: as a JSON handler) — that collision used to make a browser render raw JSON
#: on some deep links instead of the app shell.
api_router = APIRouter(prefix="/api")

# Registration order matters: Starlette runs the *last*-declared middleware
# first, so `_perimeter` (who may talk to this server at all) has to be
# declared last, after `_authenticate` (session/bearer), after
# `_spa_navigation` (the SPA-shell fast path) -- see `perimeter._perimeter`'s
# own docstring.
app.middleware("http")(perimeter._spa_navigation)
app.middleware("http")(perimeter._authenticate)
app.middleware("http")(perimeter._perimeter)

# Each of these decorates `api_router` (imported above) with its own routes.
from kraft.api.routes import (  # noqa: E402,F401
    artifacts,
    auth,
    board,
    gates,
    harnesses,
    lifecycle,
    repos,
    search,
    sessions,
    settings,
    work_items,
)

app.include_router(api_router)


@app.get("/{path:path}")
async def spa(path: str, request: Request):
    if perimeter._is_api_path(f"/{path}"):
        # A real API prefix with no matching route is a bad request, not a
        # missing page — this must not fall through to the SPA shell.
        raise HTTPException(404, "not found")
    dist = request.app.state.frontend_dist
    if dist is None:
        raise HTTPException(404, "not found")
    candidate = (dist / path).resolve()
    if dist.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(dist / "index.html")


@app.exception_handler(config_mod.ConfigError)
async def _bad_config_file(request: Request, exc: config_mod.ConfigError) -> JSONResponse:
    """A legible 422 for any `load_repos`/`Intake.load`/etc. caller that does
    not catch `ConfigError` itself. `templates/` is a plain directory an
    operator can hand-edit, and the message already names the file and what is
    wrong with it — a global backstop is the fix, not a guard at each call
    site, because the next caller of `load_repos` would just inherit the same
    trap a per-route `try/except` does nothing to close.

    A route that already catches `ConfigError` and raises its own 4xx
    (`_validate_repos`, `probe_repo`, `_launch`) never reaches this handler —
    only an *uncaught* `ConfigError` does."""
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(404)
async def _spa_deep_link(request: Request, exc: HTTPException):
    # Every non-/api GET is answered directly by the spa() catch-all above —
    # it returns a file or index.html unconditionally and never raises — so
    # the only 404s that reach here are genuinely /api/ ones (a bad path, or a
    # handler's own `raise HTTPException(404, ...)`). Always JSON: nothing
    # under /api/ is ever the shell, whatever Accept header asks for it.
    return JSONResponse({"detail": exc.detail}, status_code=404)
