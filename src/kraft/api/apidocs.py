"""`/docs` (Swagger UI) and `/redoc` (ReDoc), FastAPI's own pages with their
scripts pinned.

FastAPI's defaults load `swagger-ui-dist@5` and `redoc@2` from jsdelivr: a
major version, whatever its newest release is that day, with no integrity
check. The pages need no login and run as Kraft's own origin, so a bad
release of either would hold the whole API, which on a loopback bind needs no
login either. Here each asset is one exact release, and the browser refuses
it unless it hashes to what is written below.

To move to a newer release, change its version and recompute every hash for
it, from the file inside the package's npm tarball (jsdelivr serves those
bytes unchanged):

    openssl dgst -sha384 -binary package/swagger-ui-bundle.js | base64
"""

from __future__ import annotations

from fastapi import Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

SWAGGER_PATH = "/docs"
REDOC_PATH = "/redoc"

_SWAGGER = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.15"
_SWAGGER_JS = f"{_SWAGGER}/swagger-ui-bundle.js"
_SWAGGER_CSS = f"{_SWAGGER}/swagger-ui.css"
_REDOC_JS = "https://cdn.jsdelivr.net/npm/redoc@2.5.4/bundles/redoc.standalone.js"

#: Subresource Integrity: the sha384 of each file above.
INTEGRITY = {
    _SWAGGER_JS: "sha384-m7zaGj7MPzU+G4lz2eyy73GxK9bbRDr9bB2CSdj8wodg2wu/Wnt6wsoLP3JD+RS9",
    _SWAGGER_CSS: "sha384-fgyWYkUAamzuI8mJFu/xpRP0JWCJRwkwUwsYDoOYVHUJ8NQE5cENn8ib3ppwFFSX",
    _REDOC_JS: "sha384-w447zOpYfw/1Tv/5AK9NfHTlQIqE3RVR6KY62jCyy9zNDgO64cMwGGP1Fj0zJVf5",
}


def _with_integrity(page: HTMLResponse) -> HTMLResponse:
    """`page` with an `integrity` on every pinned asset it loads. FastAPI's
    page builders take a URL but no attributes for it, so they are added to
    the tag the URL sits in. `crossorigin` is what lets the browser check a
    hash on a file from another origin at all."""
    html = page.body.decode()
    for url, digest in INTEGRITY.items():
        html = html.replace(f'"{url}"', f'"{url}" integrity="{digest}" crossorigin="anonymous"')
    return HTMLResponse(html)


async def swagger(request: Request) -> HTMLResponse:
    app = request.app
    return _with_integrity(
        get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - Swagger UI",
            swagger_js_url=_SWAGGER_JS,
            swagger_css_url=_SWAGGER_CSS,
        )
    )


async def redoc(request: Request) -> HTMLResponse:
    # No Google Fonts: a stylesheet from a third party that nothing pins.
    app = request.app
    return _with_integrity(
        get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - ReDoc",
            redoc_js_url=_REDOC_JS,
            with_google_fonts=False,
        )
    )
