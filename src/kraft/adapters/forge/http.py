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
from urllib.parse import urljoin, urlsplit

import certifi
import httpx

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
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
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


def ssl_context(account: ForgeAccount) -> ssl.SSLContext:
    """ca_file, else git's `http.sslCAInfo` for the API address, else the system's store
    (plus certifi's when that holds no CA). Always verifying, never a way to turn it off."""
    if account.ca_file:
        try:
            if not Path(account.ca_file).is_file():
                raise FileNotFoundError(account.ca_file)
            return ssl.create_default_context(cafile=account.ca_file)
        except (OSError, ssl.SSLError) as exc:
            raise ForgeError(
                f"ca_file {account.ca_file} cannot be read as a CA bundle",
                reason="tls",
            ) from exc
    try:
        done = credentials.neutral_run(
            ["git", "config", "--get-urlmatch", "http.sslCAInfo", account.api()],
            timeout=credentials.CREDENTIAL_TIMEOUT_S,
        )
        path = done.stdout.strip() if done.returncode == 0 else ""
        if path:
            return ssl.create_default_context(cafile=path)
    except Exception:  # noqa: BLE001 - a git that cannot answer means the system store
        pass
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"] == 0:
        ctx.load_verify_locations(certifi.where())  # the same context: a capath keeps its CAs
    return ctx


def _is_tls(exc: BaseException) -> bool:
    chain = []
    while exc is not None and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return any(isinstance(e, ssl.SSLError) for e in chain) or "CERTIFICATE_VERIFY_FAILED" in str(
        chain[0]
    )


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
        self.name, self.account, self._token = name, account, token
        self.base = account.web_url if base == "web" else account.api()
        self.timeout = git.CLI_TIMEOUT_S if timeout is None else timeout
        self._transport = transport
        self._ctx: ssl.SSLContext | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}", "User-Agent": _user_agent()}
        if self.account.forge == "github":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return headers

    async def _send(
        self, label: str, method: str, url: str, *, params=None, body=None
    ) -> httpx.Response:
        here = origin(url)
        if self._ctx is None:
            self._ctx = await asyncio.to_thread(ssl_context, self.account)
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                verify=self._ctx,
                timeout=self.timeout,
                trust_env=True,
                transport=self._transport,
            ) as client:
                for hop in range(MAX_REDIRECTS + 1):
                    resp = await client.request(
                        method, url, params=params, json=body, headers=self._headers()
                    )
                    if resp.status_code not in _REDIRECTS or "location" not in resp.headers:
                        return resp
                    url = urljoin(str(resp.url), resp.headers["location"])
                    target = urlsplit(url)
                    why = (
                        "it is not a GET"
                        if method != "GET"
                        else "another origin"
                        if origin(url) != here
                        else "too many redirects"
                        if hop == MAX_REDIRECTS
                        else ""
                    )
                    if why:
                        raise ForgeError(
                            f"{label}: redirected to {target.scheme}://{target.netloc}, "
                            f"not followed ({why})",
                            reason="unreachable",
                        )
                    params = None  # the Location carries its own query
        except httpx.HTTPError as exc:
            shown = f"{here[0]}://{here[1]}:{here[2]}"
            if _is_tls(exc):
                raise ForgeError(
                    f"TLS verification failed for {shown}; set ca_file on forge account "
                    f"{self.name} to the CA that signs it",
                    reason="tls",
                ) from None
            raise ForgeError(
                f"{label}: cannot reach {shown} ({type(exc).__name__})", reason="unreachable"
            ) from None
        raise AssertionError("unreachable")  # pragma: no cover

    async def _call(self, label: str, method: str, url: str, **kw) -> ApiResponse:
        for attempt in (0, 1):
            raw = await self._send(label, method, url, **kw)
            resp = ApiResponse(
                raw.status_code, {k.lower(): v for k, v in raw.headers.items()}, raw.text
            )
            if resp.status < 400:
                return resp
            try:
                message = str(resp.json().get("message", ""))
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
        self, method: str, path: str, *, fields: Mapping[str, Field] | None = None
    ) -> ApiResponse:
        label = f"{method} {path}"
        if _NOT_A_PATH.match(path):
            raise ForgeError(f"{label}: a path, not an address, goes under the account's API")
        url = f"{self.base.rstrip('/')}/{path.lstrip('/')}"
        if method != "GET":
            return await self._call(label, method, url, body=dict(fields or {}))
        params = [
            (f"{k}[]" if isinstance(v, list) else k, x)
            for k, v in (fields or {}).items()
            for x in (v if isinstance(v, list) else [v])
        ]
        return await self._call(label, method, url, params=params)

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
