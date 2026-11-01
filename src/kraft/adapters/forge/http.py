"""`HttpTransport`: the forge REST and GraphQL API over HTTPS with a Bearer token.

The token goes to one origin: a redirect is followed only for a GET inside it,
and TLS verification has no off switch (a corporate CA is `ca_file`)."""

from __future__ import annotations

import asyncio
import re
import ssl
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

import certifi
import httpx

from kraft import redact
from kraft.adapters.forge import credentials, git
from kraft.adapters.forge.models import ForgeError, reason_for
from kraft.adapters.forge.transport import (
    ApiResponse,
    Field,
    _clip,
    _retry_after,
    _sso_url,
    graphql_reason,
)
from kraft.forges import ForgeAccount, origin

MAX_REDIRECTS = 5
MAX_BODY = 32 << 20  # a forge answer bigger than this is not one Kraft can use
TRACE_MAX = 8 << 20  # a job log keeps its last this much
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_LOG_PATH = re.compile(r"repos/[^/]+/[^/]+/actions/jobs/\d+/logs")
_NOT_A_PATH = re.compile(r"^(//|[A-Za-z][A-Za-z0-9+.-]*:)")


def _user_agent() -> str:
    try:
        return f"kraft/{_pkg_version('kraft')}"
    except PackageNotFoundError:
        return "kraft/unknown"


def graphql_url(account: ForgeAccount) -> str:
    if account.forge == "gitlab":
        return f"{account.web_url.rstrip('/')}/api/graphql"
    api = account.api().rstrip("/")
    return f"{api.removesuffix('/v3')}/graphql" if api.endswith("/api/v3") else f"{api}/graphql"


def _load_ca(path: str, named: str) -> ssl.SSLContext:
    """A CA the person configured, or a refusal naming it: never the system store instead."""
    try:
        if not Path(path).is_file():
            raise FileNotFoundError(path)
        return ssl.create_default_context(cafile=path)
    except (OSError, ssl.SSLError) as exc:
        raise ForgeError(f"{named} {path} cannot be read as a CA bundle", reason="tls") from exc


def ssl_context(account: ForgeAccount, *, plus_system: bool = False) -> ssl.SSLContext:
    """ca_file, else git's `http.sslCAInfo` for the API address, else the system's store
    (plus certifi's when that holds no CA). Always verifying, never a way to turn it off.
    `plus_system` trusts the system's store as well as that CA (the log download's blob host)."""
    found = None
    if account.ca_file:
        found = (account.ca_file, "ca_file")
    else:
        try:
            done = credentials.neutral_run(
                ["git", "config", "--get-urlmatch", "http.sslCAInfo", account.api()],
                timeout=credentials.CREDENTIAL_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 - a git that cannot answer means the system store
            done = None
        if done is not None and done.returncode == 0 and (path := done.stdout.strip()):
            found = (path, "git's http.sslCAInfo")
    if found and not plus_system:
        return _load_ca(*found)
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] == 0:
        ctx.load_verify_locations(certifi.where())  # the same context: a capath keeps its CAs
    if found:
        _load_ca(*found)  # a CA that cannot be read is refused, not skipped
        ctx.load_verify_locations(found[0])
    return ctx


def _is_tls(exc: BaseException) -> bool:
    chain = []
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return any(isinstance(e, ssl.SSLError) for e in chain) or "CERTIFICATE_VERIFY_FAILED" in str(
        chain[0]
    )


async def _read(label: str, resp: httpx.Response, tail: bool = False) -> ApiResponse:
    data = bytearray()
    async for chunk in resp.aiter_bytes():
        data += chunk
        if tail and len(data) > 2 * TRACE_MAX:  # amortised: trim at twice the cap, not per chunk
            del data[:-TRACE_MAX]
        elif not tail and len(data) > MAX_BODY:
            raise ForgeError(f"{label}: answered more than {MAX_BODY} bytes", reason="unreachable")
    text = (data[-TRACE_MAX:] if tail else data).decode(resp.charset_encoding or "utf-8", "replace")
    return ApiResponse(resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, text)


class HttpTransport:
    def __init__(
        self,
        name: str,
        account: ForgeAccount,
        token: str,
        *,
        base: Literal["api", "web"] = "api",
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if base == "web" and account.forge != "gitlab":
            raise ValueError("the web base is for GitLab only")
        # Whoever built this transport, the token never reaches text Kraft writes.
        redact.register(token)
        redact.register(credentials.basic_form(token, account.source().kind))
        self.name, self.account, self._token = name, account, token
        self.base = account.web_url if base == "web" else account.api()
        self.timeout = git.CLI_TIMEOUT_S if timeout is None else timeout
        self._transport = transport
        self._ctx: ssl.SSLContext | None = None
        self._blob_ctx: ssl.SSLContext | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": _user_agent()}
        if self.account.forge == "github":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return headers

    async def _send(
        self,
        label: str,
        method: str,
        url: str,
        *,
        params=None,
        body=None,
        tail: bool = False,
        download: bool = False,
    ) -> ApiResponse:
        here = origin(url)
        shown = f"{here[0]}://{here[1]}:{here[2]}"
        if self._ctx is None:
            self._ctx = await asyncio.to_thread(ssl_context, self.account)
        blob = None
        # The whole exchange, redirects and a log download included: httpx's own is per phase.
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            async with (
                asyncio.timeout_at(deadline),
                httpx.AsyncClient(
                    follow_redirects=False,
                    verify=self._ctx,
                    timeout=self.timeout,
                    trust_env=True,
                    transport=self._transport,
                ) as client,
            ):
                for hop in range(MAX_REDIRECTS + 1):
                    request = client.build_request(
                        method, url, params=params, json=body, headers=self._headers()
                    )
                    resp = await client.send(request, stream=True)
                    try:
                        if resp.status_code not in _REDIRECTS or "location" not in resp.headers:
                            return await _read(label, resp, tail)
                        url = urljoin(str(resp.url), resp.headers["location"])
                    finally:
                        await resp.aclose()
                    to = origin(url)
                    if download and to != here and to[0] == "https":
                        blob = url  # the one hop that leaves the origin, and carries no token
                        break
                    why = (
                        "it is not a GET"
                        if method != "GET"
                        else "another origin"
                        if to != here
                        else "too many redirects"
                        if hop == MAX_REDIRECTS
                        else ""
                    )
                    if why:  # host and port only: a Location can carry userinfo
                        raise ForgeError(
                            f"{label}: redirected to {to[0]}://{to[1]}:{to[2]}, "
                            f"not followed ({why})",
                            reason="unreachable",
                        )
                    params = None  # the Location carries its own query
        except TimeoutError:
            raise ForgeError(
                f"{label}: {shown} did not answer in {self.timeout:g}s", reason="unreachable"
            ) from None
        except httpx.HTTPError as exc:
            if _is_tls(exc):
                raise ForgeError(
                    f"TLS verification failed for {shown}; set ca_file on forge account "
                    f"{self.name} to the CA that signs it",
                    reason="tls",
                ) from None
            raise ForgeError(
                f"{label}: cannot reach {shown} ({type(exc).__name__})", reason="unreachable"
            ) from None
        if blob is None:
            raise AssertionError("unreachable")  # pragma: no cover
        return await self._download(label, blob, tail, deadline)

    async def _download(self, label: str, url: str, tail: bool, deadline: float) -> ApiResponse:
        """GitHub's pre-signed log URL, in a client of its own: no token, no GitHub headers,
        no cookies, no redirect. Every failure names the blob origin and never the URL (its
        query is the signature), and is `unreachable`: an expired signature is not a token stop."""
        kind, host, port = origin(url)
        shown = f"{kind}://{host}:{port}"
        if self._blob_ctx is None:
            self._blob_ctx = await asyncio.to_thread(ssl_context, self.account, plus_system=True)
        try:
            async with (
                asyncio.timeout_at(deadline),
                httpx.AsyncClient(
                    follow_redirects=False,
                    verify=self._blob_ctx,
                    timeout=self.timeout,
                    trust_env=True,
                    auth=httpx.Auth(),  # a netrc entry must not put an Authorization here
                    transport=self._transport,
                ) as client,
            ):
                request = client.build_request("GET", url, headers={"User-Agent": _user_agent()})
                resp = await client.send(request, stream=True)
                try:
                    if resp.status_code in _REDIRECTS:
                        raise ForgeError(
                            f"{label}: the log download from {shown} redirected, "
                            "not followed (a download redirects once)",
                            reason="unreachable",
                        )
                    if resp.status_code >= 400:
                        raise ForgeError(
                            f"{label}: the log download from {shown} answered "
                            f"HTTP {resp.status_code}",
                            status=resp.status_code,
                            reason="unreachable",
                        )
                    return await _read(label, resp, tail)
                finally:
                    await resp.aclose()
        except TimeoutError:
            raise ForgeError(
                f"{label}: the log download from {shown} did not answer within the time left",
                reason="unreachable",
            ) from None
        except httpx.HTTPError as exc:
            why = (
                "failed TLS verification"
                if _is_tls(exc)
                else f"cannot be reached ({type(exc).__name__})"
            )
            raise ForgeError(
                f"{label}: the log download from {shown} {why}", reason="unreachable"
            ) from None

    async def _call(self, label: str, method: str, url: str, **kw) -> ApiResponse:
        for attempt in (0, 1):
            resp = await self._send(label, method, url, **kw)
            if resp.status < 400:
                return resp
            try:
                message = _clip(str(resp.json().get("message", "")))
            except (ForgeError, AttributeError):
                message = ""
            reason = reason_for(resp.status, resp.headers, message)
            wait = _retry_after(resp.headers)
            if (
                reason == "rate_limited"
                and wait is not None
                and wait <= self.timeout
                and not attempt
            ):
                await asyncio.sleep(wait)
                continue
            raise ForgeError(
                f"{label}: {message or _clip(resp.text)} (HTTP {resp.status})",
                status=resp.status,
                reason=reason,
                retry_after=wait,
                sso_url=_sso_url(resp.headers.get("x-github-sso", "")),
            )
        raise AssertionError("unreachable")  # pragma: no cover

    async def rest(
        self,
        method: str,
        path: str,
        *,
        fields: Mapping[str, Field] | None = None,
        tail: bool = False,
        download: bool = False,
    ) -> ApiResponse:
        label = f"{method} {path}"
        if "?" in path or "#" in path:  # httpx drops a URL's query when params are given
            bare = re.split("[?#]", path)[0]
            raise ForgeError(f"{method} {bare}: a query goes in fields, not in the path")
        if _NOT_A_PATH.match(path):
            raise ForgeError(f"{label}: a path, not an address, goes under the account's API")
        if download and method != "GET":
            raise ForgeError(f"{label}: only a GET downloads")
        if download and not (self.account.forge == "github" and _LOG_PATH.fullmatch(path)):
            raise ForgeError(f"{label}: only a GitHub job log downloads from another origin")
        url = f"{self.base.rstrip('/')}/{path.lstrip('/')}"
        if method != "GET":
            return await self._call(label, method, url, body=dict(fields or {}))
        params = [
            (f"{k}[]" if isinstance(v, list) else k, x)
            for k, v in (fields or {}).items()
            for x in (v if isinstance(v, list) else [v])
        ]
        return await self._call(label, method, url, params=params, tail=tail, download=download)

    async def graphql(self, query: str, variables: Mapping[str, Field] | None = None) -> dict:
        resp = await self._call(
            "graphql",
            "POST",
            graphql_url(self.account),
            body={"query": query, "variables": dict(variables or {})},
        )
        body = resp.json()
        if not isinstance(body, dict):
            raise ForgeError(f"graphql: answered without an object: {_clip(resp.text)}")
        if errors := body.get("errors"):
            message = "; ".join(str(e.get("message", e)) for e in errors)
            raise ForgeError(
                f"graphql: {_clip(message)}",
                status=resp.status,
                reason=graphql_reason(errors),
                retry_after=_retry_after(resp.headers),
                sso_url=_sso_url(resp.headers.get("x-github-sso", "")),
            )
        return body.get("data") or {}
