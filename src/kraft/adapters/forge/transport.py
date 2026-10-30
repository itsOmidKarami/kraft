"""One seam under the forge backends: send a REST or GraphQL request, get an
`ApiResponse` or a `ForgeError` that carries the HTTP `status` and a `reason`.

`CliTransport` does it with `gh api -i` / `glab api -i`, which print the status
line and headers before the body, so the status, `Retry-After` and
`X-GitHub-SSO` reach the same mapping (`models.reason_for`) any transport uses.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from kraft.adapters.forge import git
from kraft.adapters.forge.models import ForgeError, Reason, reason_for
from kraft.config import _REMOTE, git_read

#: Values `gh api` expands itself, so they go as `-F` and nothing else does.
_PLACEHOLDERS = frozenset({"{owner}", "{repo}", "{branch}"})
_CLIP = 200
_STATUS_LINE = re.compile(r"^HTTP/\S+\s+(\d{3})")

Field = str | int | bool


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as exc:
            raise ForgeError(
                f"HTTP {self.status}: not JSON: {_clip(self.text)}",
                status=self.status,
                reason="unreachable",
            ) from exc


class Transport(Protocol):
    async def rest(
        self, method: str, path: str, *, fields: Mapping[str, Field] | None = None
    ) -> ApiResponse: ...

    async def graphql(self, query: str, variables: Mapping[str, Field] | None = None) -> dict: ...


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _CLIP else f"{text[:_CLIP]}…"


def parse_included(stdout: str) -> ApiResponse | None:
    """The last response in `api -i` output (a `100 Continue` precedes the real
    one), or None when stdout holds no status line."""
    blocks = stdout.replace("\r\n", "\n").split("\n\n")
    start = next((i for i in range(len(blocks) - 1, -1, -1) if _STATUS_LINE.match(blocks[i])), None)
    if start is None:
        return None
    head, *rest = blocks[start].split("\n")
    headers = {
        k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in rest) if _
    }
    body = "\n\n".join(blocks[start + 1 :])
    return ApiResponse(int(_STATUS_LINE.match(head)[1]), headers, body)  # type: ignore[index]


def graphql_reason(errors: list[dict]) -> Reason | None:
    """GraphQL answers 200 with `errors[]`; the first error that maps decides."""
    for err in errors:
        kind, msg = err.get("type"), str(err.get("message", "")).lower()
        if kind == "NOT_FOUND":
            return "not_found"
        if kind == "FORBIDDEN":
            return "forbidden"
        if "expectedheadoid" in msg or (
            "head" in msg and ("out of date" in msg or "modified" in msg)
        ):
            return "head_moved"
    return None


def origin_host(repo: Path) -> str | None:
    """The host of the repo's `origin`, None without one."""
    remote = git_read(repo, "remote", "get-url", "origin", expected_failure=True)
    match = next((m for rx in _REMOTE if remote and (m := rx.match(remote.strip()))), None)
    return match["host"].lower() if match else None


def _flags(fields: Mapping[str, Field] | None) -> list[str]:
    flags: list[str] = []
    for key, value in (fields or {}).items():
        typed = not isinstance(value, str) or value in _PLACEHOLDERS
        if isinstance(value, bool):
            value = "true" if value else "false"
        flags += ["-F" if typed else "-f", f"{key}={value}"]
    return flags


def _sso_url(header: str) -> str:
    match = re.search(r"url=(\S+)", header)
    return match[1] if match else ""


class CliTransport:
    def __init__(self, cli: Literal["gh", "glab"], repo: Path):
        self.cli, self.repo = cli, repo

    async def _call(
        self, label: str, args: list[str], *, graphql: bool = False
    ) -> tuple[ApiResponse, int]:
        """Run `<cli> api … -i`; the response and exit code, or the `ForgeError` it
        earned. A GraphQL 200 is returned whatever the exit code: its `errors[]`
        hold the reason."""
        done = await git.run_cli(self.repo, [self.cli, "api", *args])
        resp = parse_included(done.stdout)
        if resp is None:
            if done.returncode == 0:
                raise ForgeError(f"{label}: {self.cli} printed no HTTP response")
            # No request was made (not signed in, no network): no status, no reason,
            # so `failure_cause`'s text markers still decide.
            raise ForgeError(f"{label}: {_clip(done.stderr.strip() or done.stdout)}")
        if resp.status >= 400 or (done.returncode != 0 and not graphql):
            try:
                message = str(resp.json().get("message", ""))
            except (ForgeError, AttributeError):
                message = ""
            retry = resp.headers.get("retry-after", "")
            raise ForgeError(
                f"{label}: {message or _clip(resp.text or done.stderr)} (HTTP {resp.status})",
                status=resp.status,
                reason=reason_for(resp.status, resp.headers),
                retry_after=float(retry) if retry.replace(".", "", 1).isdigit() else None,
                sso_url=_sso_url(resp.headers.get("x-github-sso", "")),
            )
        return resp, done.returncode

    def _host(self) -> list[str]:
        host = origin_host(self.repo) if self.cli == "gh" else None
        return ["--hostname", host] if host and host != "github.com" else []

    async def rest(
        self, method: str, path: str, *, fields: Mapping[str, Field] | None = None
    ) -> ApiResponse:
        args = [path, "-X", method, "-i", *_flags(fields), *self._host()]
        return (await self._call(f"{method} {path}", args))[0]

    async def graphql(self, query: str, variables: Mapping[str, Field] | None = None) -> dict:
        args = ["graphql", "-i", "-f", f"query={query}", *_flags(variables), *self._host()]
        resp, rc = await self._call("graphql", args, graphql=True)
        body = resp.json()
        if errors := body.get("errors"):
            message = "; ".join(str(e.get("message", e)) for e in errors)
            raise ForgeError(
                f"graphql: {_clip(message)}", status=resp.status, reason=graphql_reason(errors)
            )
        if rc != 0:
            raise ForgeError(f"graphql: {self.cli} exited {rc}", status=resp.status)
        return body.get("data") or {}
