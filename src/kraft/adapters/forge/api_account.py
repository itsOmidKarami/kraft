"""An API account's per-repo transport, shared by the backends that use one.

A CLI backend (`GlabCli`, `GhCli`) already sends every request through the `Transport`
protocol with a placeholder for the repo (`:id`, `{owner}`/`{repo}`). `ApiAccount` swaps the
CLI transport for one that fills that placeholder from the repo's push URL, reads the
account's token through the per-account cache, re-reads it once on a 401, and names the
account and token source on the errors it raises. A backend supplies `_PLACEHOLDER`,
`_PROJECT_WORD` and `_fill`, and may fill whole field values with `_VALUE_PLACEHOLDER` and
`_fill_value`."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path

import httpx

from kraft.adapters.forge import credentials
from kraft.adapters.forge.http import HttpTransport
from kraft.adapters.forge.models import ForgeError
from kraft.adapters.forge.transport import ApiResponse, Field, Transport
from kraft.forges import ForgeAccount, repo_project


class ApiAccount:
    """Mixin ahead of a CLI backend. One instance serves one forge node or one route call,
    so what it caches (the project, a token's transport) is never long-lived."""

    _PLACEHOLDER: re.Pattern  # in a path, anywhere it matches
    _PROJECT_WORD: str  # "a GitLab project": what the repo's origin must name
    #: A field or variable whose WHOLE value matches is filled by `_fill_value` (what `gh api
    #: -F` does). None: no value is ever filled. A list item (`key[]=item`) is never filled.
    _VALUE_PLACEHOLDER: re.Pattern | None = None

    def __init__(
        self, name: str, account: ForgeAccount, *, http: httpx.AsyncBaseTransport | None = None
    ):
        super().__init__()
        self.name, self.account, self._http = name, account, http
        self._projects: dict[Path, str] = {}
        self._by_token: dict[str, HttpTransport] = {}  # each one reads git's CA setting once

    def _fill(self, project: str, path: str) -> str:
        raise NotImplementedError

    def _fill_value(self, project: str, value: str) -> str:
        raise NotImplementedError

    def _transport(self, repo: Path) -> Transport:
        return AccountTransport(self, repo)

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
                    f"can read {self._PROJECT_WORD} from"
                )
            self._projects[repo] = found
        return self._projects[repo]


class AccountTransport:
    def __init__(self, api: ApiAccount, repo: Path):
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

    async def _filled(
        self, path: str | None, fields: Mapping[str, Field] | None
    ) -> tuple[str | None, Mapping[str, Field] | None]:
        """Fill the placeholders, reading the project only when there is one to fill."""
        api, want = self.api, self.api._VALUE_PLACEHOLDER
        valued = [
            k
            for k, v in (fields or {}).items()
            if want and isinstance(v, str) and want.fullmatch(v)
        ]
        if not valued and not (path and api._PLACEHOLDER.search(path)):
            return path, fields
        project = await api._project(self.repo)
        if path:
            path = api._fill(project, path)
        # A body or branch that is exactly `{owner}` is rewritten, on this transport as on
        # `gh api`'s: both read a whole-value placeholder as one.
        return path, {**(fields or {}), **{k: api._fill_value(project, fields[k]) for k in valued}}

    async def rest(
        self,
        method: str,
        path: str,
        *,
        fields: Mapping[str, Field] | None = None,
        tail: bool = False,
        download: bool = False,
    ) -> ApiResponse:
        path, fields = await self._filled(path, fields)
        return await self._send(
            lambda http: http.rest(method, path, fields=fields, tail=tail, download=download)
        )

    async def graphql(self, query: str, variables: Mapping[str, Field] | None = None) -> dict:
        _, variables = await self._filled(None, variables)
        return await self._send(lambda http: http.graphql(query, variables))
