"""Builders for the API-account backend tests: an account, a checkout with an origin, a
scripted HTTP handler, and the repo and places of the token-leak pins. `test_gitlab_api.py`
and `test_github_api.py` share them."""

from __future__ import annotations

import os
import shutil
import stat
from types import SimpleNamespace

import httpx
import pytest

from kraft.adapters.forge import credentials
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


def logged_push_repo(tmp_path, monkeypatch, *, env: str, tok: str, remote: str) -> SimpleNamespace:
    """A checkout on `kraft/abc` whose `remote` is `insteadOf` a local bare repo, so a real
    `git push` works, through a `git` that logs its argv and environment to `.log`. The token
    enters as in production: set in `env`, then `take_env`."""
    monkeypatch.setattr(credentials, "_TAKEN", {})
    monkeypatch.setenv(env, tok)
    credentials.take_env([env])
    log = tmp_path / "git.log"
    wrapper = tmp_path / "wrap" / "git"
    wrapper.parent.mkdir()
    wrapper.write_text(
        f'#!/bin/sh\n{{ printf "ARGV %s\\n" "$*"; env; }} >> {log}\n'
        f'exec {shutil.which("git")} "$@"\n'
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{wrapper.parent}:{os.environ['PATH']}")

    repo = checkout(tmp_path, remote)
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "config", f"url.{bare}.insteadOf", remote)
    git(repo, "checkout", "-q", "-b", "kraft/abc")
    return SimpleNamespace(repo=repo, log=log)


#: Where a token must not appear, each read from a result with `git` (the log: every line a
#: hook would inherit, argv too), `config` (the checkout's .git/config) and `err` (an error).
LEAK_PLACES = [
    pytest.param(
        lambda r: "\n".join(x for x in r.git.splitlines() if x.startswith("ARGV")), id="argv"
    ),
    pytest.param(lambda r: r.git, id="git-env"),
    pytest.param(lambda r: r.config, id="git-config"),
    pytest.param(lambda r: r.err, id="401-text"),
]
