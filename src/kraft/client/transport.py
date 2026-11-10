"""The wire: base URL, the httpx client, and the request/response envelope
every other client module goes through.

Other submodules call these through module attribute access
(`from kraft.client import transport; transport.get(...)`) rather than
`from kraft.client.transport import get`, so a test that monkeypatches
`kraft.client.transport.http` (or any other name here) reaches every caller
regardless of which submodule it lives in.
"""

from __future__ import annotations

import enum
import os
import re
import ssl
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from kraft import auth, config
from kraft.paths import config_dir, default_run_dir

if TYPE_CHECKING:
    from kraft.client import remote as remote_mod

#: Set by the console for `--server`; None everywhere else, so a shell `kraft`
#: verb is never remote. The one switch behind `base_url`, `http`,
#: `reads.stream_events` and `reads.health`. Underscored so `from
#: kraft.client.transport import *` does not copy it into `kraft.client`, where a
#: public `remote` would shadow the `kraft.client.remote` submodule; read it
#: through `target()` / `is_remote()`.
_target: remote_mod.Target | None = None


def set_remote(target: remote_mod.Target | None) -> None:
    global _target
    _target = target


def target() -> remote_mod.Target | None:
    return _target


def is_remote() -> bool:
    return _target is not None


def session_headers(target: remote_mod.Target) -> dict[str, str]:
    """The only credential a remote client sends, to the origin it was issued by."""
    return {"Cookie": f"{auth.COOKIE}={target.session}"}


TLS_SENTENCE = (
    "the server's certificate is not trusted; set SSL_CERT_FILE to the file that holds "
    "the certificate authority that signed it"
)


class Failure(enum.StrEnum):
    """What kind of failure a `ClientError` is, so a caller that must tell
    them apart (the console) never parses the sentence."""

    UNREACHABLE = "unreachable"  # no server answered
    # No answer in time, or the connection dropped mid-request: the server may
    # still finish what it was asked.
    INTERRUPTED = "interrupted"
    OTHER_INSTANCE = "other_instance"  # a server, but not this KRAFT_HOME's
    UNAUTHORIZED = "unauthorized"  # 401
    NOT_FOUND = "not_found"  # 404
    REFUSED = "refused"  # any other 4xx, and the client's own checks
    SERVER_ERROR = "server_error"  # 5xx
    BAD_RESPONSE = "bad_response"  # an answer that is not JSON


class ClientError(ValueError):
    """A call Kraft refused or could not make. `str()` is the one sentence both
    front doors show; `kind` and `status` say what it was. A `ValueError`, so
    every `except ValueError` in the CLI and MCP keeps working."""

    def __init__(self, message: str, *, kind: Failure, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


def status_error(status: int, detail: str) -> ClientError:
    """`kraft <status>: <detail>`, the sentence every refused call reads as."""
    if status == 401:
        kind = Failure.UNAUTHORIZED
    elif status == 404:
        kind = Failure.NOT_FOUND
    elif status >= 500:
        kind = Failure.SERVER_ERROR
    else:
        kind = Failure.REFUSED
    return ClientError(f"kraft {status}: {detail}", kind=kind, status=status)


def tls_error(exc: BaseException | None) -> ClientError | None:
    """The sentence for a certificate failure anywhere in `exc`'s cause chain."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLCertVerificationError):
            return ClientError(TLS_SENTENCE, kind=Failure.REFUSED)
        exc = exc.__cause__ or exc.__context__
    return None


def _unreachable() -> ClientError:
    if (remote := target()) is not None:
        return ClientError(f"no Kraft server at {remote.base}", kind=Failure.UNREACHABLE)
    return ClientError(
        f"no Kraft server at {base_url()} — start one with `kraft`", kind=Failure.UNREACHABLE
    )


def base_url() -> str:
    """Where the local server is listening.

    Mirrors `cli.admin._bind` on host and port but not on its refusal to bind a LAN
    address without a password: that is a rule about *serving*, and this is a
    client. A wildcard bind is rewritten to loopback — `0.0.0.0` is an address to
    listen on, never one to connect to.
    """
    if (remote := target()) is not None:
        return remote.base
    templates_dir = config_dir()
    access = config.Access.load(templates_dir / "access.yaml")
    host = os.environ.get("KRAFT_HOST") or access.bind
    port = int(os.environ.get("KRAFT_PORT") or access.port)
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{config.url_host(host)}:{port}"


#: Variables a named harness sets in the commands its agent runs: (variable, harness, source).
#: Add a harness only with an official source that it sets the variable and only there;
#: AGENT, AI_AGENT, CURSOR_AGENT, GEMINI_CLI and CODEX_SANDBOX were left out for want of one.
AGENT_SHELL_VARIABLES = (
    (
        "CLAUDE_CODE_CHILD_SESSION",
        "Claude Code",
        "code.claude.com/docs/en/env-vars: set to 1 in subprocesses Claude Code spawns through "
        "the Bash, PowerShell and Monitor tools, hook commands and status line commands, and "
        "only by Claude Code itself, not by the IDE extensions. Needs Claude Code v2.1.172 or "
        "later; an older one goes undetected and is recorded as a person, as before. Not "
        "CLAUDECODE: the same page says the IDE extensions also set it in their integrated "
        "terminals, where a person types, so it would record that person as an agent.",
    ),
)


def agent_shell() -> bool:
    """True when this process runs under a coding agent's shell (see `AGENT_SHELL_VARIABLES`)."""
    return any(name in os.environ for name, _harness, _source in AGENT_SHELL_VARIABLES)


def http() -> httpx.AsyncClient:
    """A configured client. Module-level so tests can point it at the ASGI app."""
    if (remote := target()) is not None:
        # The session cookie only: the mcp-token on this machine belongs to this
        # machine's server. No redirects, so the cookie never reaches another host.
        return httpx.AsyncClient(
            base_url=remote.base,
            headers=session_headers(remote),
            timeout=30,
            follow_redirects=False,
        )
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
    # An explicit KRAFT_CLIENT wins (honour-system escape hatch, documented only in the CLI
    # docs page, never in an error); else an agent's shell is tagged; else nothing is sent.
    client = os.environ.get("KRAFT_CLIENT") or ("agent-shell" if agent_shell() else None)
    if client:
        # Which front door this call came in by, so a route can tell an agent's
        # decision from a person's (Kraft-s7c04.43). The bearer token above
        # cannot: it is attached to every call this module makes, so a human
        # typing `kraft item approve` carries the same credential an MCP client
        # does. The MCP server sets `KRAFT_CLIENT` in `mcp.serve_stdio`; a call
        # from a coding agent's shell is tagged `agent-shell` by `agent_shell`.
        headers["X-Kraft-Client"] = client
    return httpx.AsyncClient(base_url=base_url(), headers=headers, timeout=30)


def _detail(response: httpx.Response) -> str:
    try:
        return detail_of(response.json())
    except ValueError:
        return response.text


def detail_of(body: object) -> str:
    """The reason an API answer gives, as one line. Kraft's own refusals carry
    a sentence in `detail`; a body that does not fit a route's model gets
    FastAPI's 422, whose `detail` is a list of pydantic errors, which would
    otherwise reach a person or an agent as a Python repr. Each reads
    `field: message`, joined with `; `."""
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list) and detail:
        return "; ".join(_invalid_field(error) for error in detail)
    return str(detail)


def _invalid_field(error: object) -> str:
    if not isinstance(error, dict) or "msg" not in error:
        return str(error)
    where = [str(p) for p in error.get("loc") or ()]
    # A leading `body`, `query` or `path` says where FastAPI looked, not what
    # was wrong: a field may itself be named `body`.
    if where and where[0] in ("body", "query", "path"):
        where = where[1:]
    # A model's own check raises `ValueError`, which pydantic reports as
    # "Value error, <what it said>": the type is pydantic's, the sentence ours.
    msg = str(error["msg"]).removeprefix("Value error, ")
    return f"{'.'.join(where)}: {msg}" if where else msg


# The lead some raised messages carry themselves: `kraft 404: ...` from the
# envelope below, `kraft: no gate is pending ...` from a few client checks.
_OWN_LEAD = re.compile(r"^kraft(?:: | (?=\d{3}: ))")


def reason_of(message: str) -> str:
    """`message` without the `kraft` lead it may carry: `404: unknown session`
    for `kraft: 404: unknown session` or `kraft 404: unknown session`."""
    return _OWN_LEAD.sub("", message, count=1)


def refusal(exc: BaseException) -> str:
    """The one line both front doors show for a refused call: `kraft: `, then
    the reason. `kraft <verb>` prints it on stderr and `kraft admin mcp` hands
    it to the agent, so the two read the same. A message that already led with
    `kraft` would otherwise read `kraft: kraft 404: ...`."""
    return f"kraft: {reason_of(str(exc))}"


def segment(value: object) -> str:
    """`value` as one URL path segment. Every id, thread id or doc id a
    caller passes goes through here before it enters a path: unencoded, an
    MCP `get_work_item("../../etc/passwd")` became `GET /etc/passwd`, and
    `x/../<other>/pause` paused another item. `quote` leaves `.` alone, and a
    bare `.` or `..` is a dot segment httpx would still resolve away."""
    text = quote(str(value), safe="")
    return text.replace(".", "%2E") if text in (".", "..") else text


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
        raise (tls_error(exc) or _unreachable()) from exc
    except httpx.TimeoutException as exc:
        # The server may still finish what it was asked: say so, rather than
        # hand an agent a traceback, or a person a reason to ask twice.
        raise ClientError(
            f"Kraft did not answer {method} {path} in time; it may still finish it",
            kind=Failure.INTERRUPTED,
        ) from exc
    except httpx.TransportError as exc:
        # The request may have reached the server before the connection went:
        # the same reading a timeout gets, for the same reason.
        raise ClientError(
            f"lost the connection to Kraft during {method} {path}; it may still finish it",
            kind=Failure.INTERRUPTED,
        ) from exc


async def _get(path: str, **params) -> dict | list:
    response = await _send("GET", path, params={k: v for k, v in params.items() if v is not None})
    if response.status_code >= 400:
        # An agent reads this string. "404: work item not found" is actionable;
        # an httpx traceback is not.
        raise status_error(response.status_code, _detail(response))
    return _json(response)


def _json(response: httpx.Response) -> dict | list:
    """The answer's body. A proxy's HTML page or a cut-off body is a sentence,
    not a JSONDecodeError out of the client."""
    try:
        return response.json()
    except ValueError as exc:
        raise ClientError(
            f"kraft {response.status_code}: the answer was not JSON",
            kind=Failure.BAD_RESPONSE,
            status=response.status_code,
        ) from exc


async def _delete(url: str, **params) -> dict | list | None:
    """DELETE /repos answers 204 with no body; a route that answers with one
    (DELETE /workers/{name}) has it returned. The error string is `_get`'s,
    because a 404 an agent reads must be one sentence either way."""
    response = await _send("DELETE", url, params=params)
    if response.status_code >= 400:
        raise status_error(response.status_code, _detail(response))
    return _json(response) if response.content else None


async def _patch(path: str, payload: dict) -> dict:
    """PATCH's own status/body handling, alongside `_post`'s POST version --
    `update_work_item` answers 404/409/422 depending on which field went
    wrong, and every caller here turns that into the same one-sentence error
    `_get`/`_act` already give."""
    response = await _send("PATCH", path, json=payload)
    if response.status_code < 400:
        return _json(response)
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text}
    raise status_error(response.status_code, detail_of(body))


async def _post(path: str, payload: dict | None = None, **kwargs) -> tuple[int, dict]:
    """Status alongside the body: some callers treat a 4xx as a normal outcome
    (a 409 from POST /repos means the repo is already connected). `kwargs`
    reach httpx's request (a caller's own `timeout`)."""
    response = await _send("POST", path, json=payload or {}, **kwargs)
    try:
        body = _json(response) if response.status_code < 400 else response.json()
    except ClientError:
        raise
    except ValueError:
        body = {"detail": response.text}
    return response.status_code, body


async def _act(path: str, payload: dict | None = None, **kwargs) -> dict:
    status, body = await _post(path, payload, **kwargs)
    if status >= 400:
        raise status_error(status, detail_of(body))
    return body
