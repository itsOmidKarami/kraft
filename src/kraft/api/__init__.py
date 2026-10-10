"""The FastAPI app. Split out of what used to be a single `kraft/api.py` --
see the sibling modules for the actual routes:

- `deps` -- helpers shared by several route modules
- `startup` -- `lifespan()` and its background tasks
- `perimeter` -- SPA navigation shortcut, local-client check, auth middleware
- `apidocs` -- `/docs` and `/redoc`, with their CDN scripts pinned
- `routes/*` -- one module per resource, each decorating `api_router` below

Nothing outside this package imports anything from here except `app` --
`kraft.intake`, `kraft.waits`, and `kraft.rate_limit_retry` import
`kraft.api.deps` directly instead (see that module's docstring).
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from kraft import config as config_mod
from kraft import update
from kraft.api import apidocs, perimeter
from kraft.api.startup import lifespan

# The title and version are what `/docs`, `/redoc` and `/openapi.json` show,
# all three reachable without a login: FastAPI's own defaults read "FastAPI
# 0.1.0". Every route's docstring is published there as its description, so
# a reference only the maintainer can follow goes in a comment above the
# route instead (tests/api/test_openapi.py).
#
# FastAPI's own `/docs` and `/redoc` are off: `apidocs` serves the same pages
# with their scripts pinned to one release and checked by hash.
app = FastAPI(
    title="Kraft",
    version=update.installed(),
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    swagger_ui_oauth2_redirect_url=None,
)

#: Every JSON endpoint lives under here, so it can never share a path with an
#: SPA client-side route (e.g. GET /work-items/<id> the page vs. the same path
#: as a JSON handler) — that collision used to make a browser render raw JSON
#: on some deep links instead of the app shell.
api_router = APIRouter(prefix="/api")

#: The `type` of a body validator's error whose message is the whole answer:
#: the 422's `detail` is that one sentence, not pydantic's list, which
#: echoes the request body back (`_validation_error`).
SENTENCE_ERROR = "kraft_sentence"

# Registration order matters: Starlette runs the *last*-declared middleware
# first, so `_perimeter` (who may talk to this server at all) has to be
# declared after `_authenticate` (session/bearer), after `_spa_navigation`
# (the SPA-shell fast path) -- see `perimeter._perimeter`'s own docstring.
# `_frame_guard` only adds response headers, and goes last so that even a
# refusal from `_perimeter` carries them.
app.middleware("http")(perimeter._spa_navigation)
app.middleware("http")(perimeter._authenticate)
app.middleware("http")(perimeter._perimeter)
app.middleware("http")(perimeter._frame_guard)

# Each of these decorates `api_router` (imported above) with its own routes.
from kraft.api.routes import (  # noqa: E402,F401
    admin,
    artifacts,
    auth,
    board,
    check,
    drafts,
    gates,
    harnesses,
    lifecycle,
    repos,
    review,
    search,
    sessions,
    settings,
    storage,
    work_items,
)

app.include_router(api_router)
app.get(apidocs.SWAGGER_PATH, include_in_schema=False)(apidocs.swagger)
app.get(apidocs.REDOC_PATH, include_in_schema=False)(apidocs.redoc)


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
    if (
        dist.resolve() in candidate.parents
        and candidate.is_file()
        and candidate != (dist / "index.html").resolve()
    ):
        return FileResponse(candidate)
    return perimeter.spa_shell(request, dist)


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


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422, less pydantic's "Value error, " ahead of a sentence
    a model's validator wrote: `start_line must be >= 1 and <= end_line` is
    Kraft's, the prefix pydantic's. The CLI and MCP stripped it on their side;
    a raw HTTP caller got it as sent (R11F-06). The shape is FastAPI's,
    except for a `SENTENCE_ERROR`, whose message is the `detail`."""
    for e in exc.errors():
        if isinstance(e, dict) and e.get("type") == SENTENCE_ERROR:
            return JSONResponse({"detail": e["msg"]}, status_code=422)
    errors = [
        {**e, "msg": e["msg"].removeprefix("Value error, ")}
        if isinstance(e, dict) and isinstance(e.get("msg"), str)
        else e
        for e in exc.errors()
    ]
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


@app.exception_handler(404)
async def _spa_deep_link(request: Request, exc: HTTPException):
    # Every non-/api GET is answered directly by the spa() catch-all above —
    # it returns a file or index.html unconditionally and never raises — so
    # the only 404s that reach here are genuinely /api/ ones (a bad path, or a
    # handler's own `raise HTTPException(404, ...)`). Always JSON: nothing
    # under /api/ is ever the shell, whatever Accept header asks for it.
    return JSONResponse({"detail": exc.detail}, status_code=404)
