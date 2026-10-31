"""Builders for the API-account backend tests: an account, a checkout with an origin, and a
scripted HTTP handler. `test_gitlab_api.py` and `test_github_api.py` share them."""

from __future__ import annotations

import httpx

from kraft.forges import ForgeAccount, TokenSource
from support.harness import git, make_repo


def account(forge: str, web: str, **token) -> ForgeAccount:
    """An `api` account; the token comes from `$CORP_GL` / `$CORP_GH` unless `token` says."""
    env = "CORP_GL" if forge == "gitlab" else "CORP_GH"
    return ForgeAccount(
        forge=forge,
        client="api",
        web_url=web,
        token=TokenSource(**(token or {"from": "env", "env": env})),
    )


def checkout(tmp_path, remote=None):
    path = make_repo(tmp_path)
    if remote:
        git(path, "remote", "add", "origin", remote)
    return path


def answers(*bodies):
    """A handler answering each request with the next status or (status, body)."""
    queue = list(bodies)

    def handler(request):
        got = queue.pop(0) if len(queue) > 1 else queue[0]
        status, body = got if isinstance(got, tuple) else (got, {"message": "no"})
        return httpx.Response(status, json=body)

    return handler
