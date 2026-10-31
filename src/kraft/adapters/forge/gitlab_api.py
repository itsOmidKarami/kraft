"""GitLab over its REST API with an account's token, never through `glab`.

`GlabCli` already sends every request as a `projects/:id/...` path through the
`Transport` protocol, so this is `GlabCli` on the shared account transport
(`api_account`): it fills `:id` from the repo's push URL. Every request shape stays in
`glab.py`."""

from __future__ import annotations

import re
from urllib.parse import quote

from kraft.adapters.forge.api_account import ApiAccount
from kraft.adapters.forge.glab import GlabCli

_ID = re.compile(r"(?<=projects/):id(?=/|$)")


class GitLabApi(ApiAccount, GlabCli):
    """`client: api` on a GitLab account."""

    _PLACEHOLDER = _ID
    _PROJECT_WORD = "a GitLab project"

    def _fill(self, project: str, path: str) -> str:
        return _ID.sub(quote(project, safe=""), path)
