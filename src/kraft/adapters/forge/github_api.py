"""GitHub over its REST and GraphQL APIs with an account's token, never through `gh`.

`GhCli` already sends every request as a `repos/{owner}/{repo}/...` path, or a GraphQL
document with `{"owner": "{owner}", "name": "{repo}"}` variables, which `gh` expands. So
this is `GhCli` on the shared account transport (`api_account`), filling the same
placeholders from the repo's push URL. Every request shape stays in `gh.py`."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from kraft.adapters.forge.api_account import ApiAccount
from kraft.adapters.forge.gh import GhCli
from kraft.adapters.forge.models import ForgeError
from kraft.forges import github_owner_name

_PLACEHOLDER = re.compile(r"\{owner\}|\{repo\}")


class GitHubApi(ApiAccount, GhCli):
    """`client: api` on a GitHub account (github.com, GHES or `SUBDOMAIN.ghe.com`)."""

    _PLACEHOLDER = _PLACEHOLDER
    _VALUE_PLACEHOLDER = _PLACEHOLDER  # as `gh api -F`: the whole value, nothing in a string
    _PROJECT_WORD = "a GitHub repository"

    async def _project(self, repo: Path) -> str:
        project = await super()._project(repo)
        try:
            github_owner_name(project, repo.name)
        except ValueError as exc:
            raise ForgeError(f"forge account {self.name}: {exc}") from None
        return project

    def _segment(self, project: str, placeholder: str) -> str:
        owner, name = project.split("/")
        return owner if placeholder == "{owner}" else name

    def _fill(self, project: str, path: str) -> str:
        return _PLACEHOLDER.sub(lambda m: quote(self._segment(project, m[0]), safe=""), path)

    def _fill_value(self, project: str, value: str) -> str:
        return self._segment(project, value)  # JSON, not a path: the raw segment
