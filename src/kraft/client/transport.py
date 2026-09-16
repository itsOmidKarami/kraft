"""The wire: base URL, the httpx client, and the request/response envelope
every other client module goes through.

Other submodules call these through module attribute access
(`from kraft.client import transport; transport.get(...)`) rather than
`from kraft.client.transport import get`, so a test that monkeypatches
`kraft.client.transport.http` (or any other name here) reaches every caller
regardless of which submodule it lives in.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from kraft import auth, config
from kraft.paths import default_run_dir, default_templates_dir


def base_url() -> str:
    """Where the local server is listening.

    Mirrors `cli.admin._bind` on host and port but not on its refusal to bind a LAN
    address without a password: that is a rule about *serving*, and this is a
    client. A wildcard bind is rewritten to loopback — `0.0.0.0` is an address to
    listen on, never one to connect to.
    """
    templates_dir = Path(os.environ.get("KRAFT_TEMPLATES_DIR") or default_templates_dir())
    access = config.load_access(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access["bind"]
    port = int(os.environ.get("KRAFT_PORT") or access["port"])
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def http() -> httpx.AsyncClient:
    """A configured client. Module-level so tests can point it at the ASGI app."""
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    headers = {}
    token = auth.read_mcp_token(run_dir)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session_id = os.environ.get("KRAFT_SESSION_ID")
    if session_id:
        # Lets a route exempt the caller's own session from a check that
        # would otherwise block it on itself -- e.g. `retry`'s
        # `escalation_running` guard against the very escalation turn
        # calling it (`kraft.executor.gates.auto_escalate_stuck`).
        headers["X-Kraft-Session-Id"] = session_id
    client = os.environ.get("KRAFT_CLIENT")
    if client:
        # Which front door this call came in by, so a route can tell an agent's
        # decision from a person's (Kraft-s7c04.43). The bearer token above
        # cannot: it is attached to every call this module makes, so a human
        # typing `kraft item approve` carries the same credential an MCP client
        # does. Only the MCP server sets this, in `mcp.serve_stdio`.
        headers["X-Kraft-Client"] = client
    return httpx.AsyncClient(base_url=base_url(), headers=headers, timeout=30)


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text))
    except ValueError:
        return response.text


def _api(path: str) -> str:
    """Every JSON route lives under /api/ (Kraft-psuq). `_send` is the one
    request path most callers take, but the two streaming endpoints below
    build their own request outside it and need the same prefix."""
    return f"/api{path}"


async def _send(method: str, path: str, **kwargs) -> httpx.Response:
    """Everything that isn't a stream goes through here, so a dead server
    reads the same at both front doors — an agent calling through MCP gets
    this sentence too, not a traceback it will try to reason about."""
    try:
        async with http() as session:
            return await session.request(method, _api(path), **kwargs)
    except httpx.ConnectError as exc:
        raise ValueError(f"no Kraft server at {base_url()} — start one with `kraft`") from exc


async def _get(path: str, **params) -> dict | list:
    response = await _send("GET", path, params={k: v for k, v in params.items() if v is not None})
    if response.status_code >= 400:
        # An agent reads this string. "404: work item not found" is actionable;
        # an httpx traceback is not.
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
    return response.json()


async def _delete(url: str, **params) -> None:
    """No body on the way back: DELETE /repos answers 204. The error string is
    `_get`'s, because a 404 an agent reads must be one sentence either way."""
    response = await _send("DELETE", url, params=params)
    if response.status_code >= 400:
        raise ValueError(f"kraft {response.status_code}: {_detail(response)}")


async def _patch(path: str, payload: dict) -> dict:
    """PATCH's own status/body handling, alongside `_post`'s POST version --
    `update_work_item` answers 404/409/422 depending on which field went
    wrong, and every caller here turns that into the same one-sentence error
    `_get`/`_act` already give."""
    response = await _send("PATCH", path, json=payload)
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    if response.status_code >= 400:
        raise ValueError(f"kraft {response.status_code}: {body.get('detail', body)}")
    return body


async def _post(path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Status alongside the body: some callers treat a 4xx as a normal outcome
    (a 409 from POST /repos means the repo is already connected)."""
    response = await _send("POST", path, json=payload or {})
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    return response.status_code, body


async def _act(path: str, payload: dict | None = None) -> dict:
    status, body = await _post(path, payload)
    if status >= 400:
        raise ValueError(f"kraft {status}: {body.get('detail', body)}")
    return body
