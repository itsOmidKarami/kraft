"""GitLab over its REST API with an account's token, never through `glab`.

`GlabCli` already sends every request as a `projects/:id/...` path through the
`Transport` protocol, so this is `GlabCli` with a per-repo transport: it fills `:id` from
the repo's push URL, reads the token through the per-account cache, re-reads it once on a
401, and names the account and token source on the errors it raises. Every request shape
stays in `glab.py`."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

import httpx

from kraft.adapters.forge import credentials
from kraft.adapters.forge.glab import GlabCli
from kraft.adapters.forge.http import HttpTransport
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.transport import ApiResponse, Field, Transport
from kraft.forges import ForgeAccount, repo_project

_ID = re.compile(r"(?<=projects/):id(?=/|$)")


class GitLabApi(GlabCli):
    """`client: api` on a GitLab account. One instance serves one forge node or one route
    call, so what it caches (the project, a token's transport) is never long-lived."""

    def __init__(
        self, name: str, account: ForgeAccount, *, http: httpx.AsyncBaseTransport | None = None
    ):
        super().__init__()
        self.name, self.account, self._http = name, account, http
        self._projects: dict[Path, str] = {}
        self._by_token: dict[str, HttpTransport] = {}  # each one reads git's CA setting once

    def _transport(self, repo: Path) -> Transport:
        return _AccountTransport(self, repo)

    def _http_for(self, token: str) -> HttpTransport:
        if token not in self._by_token:
            self._by_token[token] = HttpTransport(
                self.name, self.account, token, transport=self._http
            )
        return self._by_token[token]

    async def _project(self, repo: Path) -> str:
        if repo not in self._projects:
            try:
                found = await asyncio.to_thread(repo_project, repo, self.account)
            except ValueError as exc:  # worded without the URL
                raise ForgeError(f"forge account {self.name}: {exc}") from None
            if not found:
                raise ForgeError(
                    f"forge account {self.name}: {repo.name} has no origin URL Kraft "
                    "can read a GitLab project from"
                )
            self._projects[repo] = found
        return self._projects[repo]


class _AccountTransport:
    def __init__(self, api: GitLabApi, repo: Path):
        self.api, self.repo = api, repo

    async def _token(self, *, fresh: bool = False) -> str:
        try:
            return await credentials.token_for(self.api.name, self.api.account, fresh=fresh)
        except credentials.NoToken as exc:
            raise ForgeError(f"forge account {self.api.name}: {exc}", reason="auth") from None

    def _stamp(self, exc: ForgeError) -> None:
        exc.account, exc.source = self.api.name, self.api.account.source().label()

    async def _send(self, call) -> object:
        """`call(http)` with the account's token; a 401 re-reads the token once and retries
        when it changed. An `env` source changes only with a restart, so it never re-reads."""
        try:
            token = await self._token()
            try:
                return await call(self.api._http_for(token))
            except ForgeError as exc:
                if exc.status != 401 or self.api.account.source().from_ == "env":
                    raise
                fresh = await self._token(fresh=True)
                if fresh == token:
                    raise
            return await call(self.api._http_for(fresh))
        except ForgeError as exc:
            self._stamp(exc)
            raise

    async def rest(
        self,
        method: str,
        path: str,
        *,
        fields: Mapping[str, Field] | None = None,
        tail: bool = False,
        download: bool = False,
    ) -> ApiResponse:
        if _ID.search(path):
            path = _ID.sub(quote(await self.api._project(self.repo), safe=""), path)
        return await self._send(
            lambda http: http.rest(method, path, fields=fields, tail=tail, download=download)
        )

    async def graphql(self, query: str, variables: Mapping[str, Field] | None = None) -> dict:
        return await self._send(lambda http: http.graphql(query, variables))
