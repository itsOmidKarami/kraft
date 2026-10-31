"""`GitHubApi`: GhCli's requests over HTTP with an account's token. Nothing dials a host."""

from __future__ import annotations

import json

import httpx
import pytest
from support.api_accounts import account as accounts
from support.api_accounts import answers, checkout

from kraft.adapters.forge import credentials
from kraft.adapters.forge.github_api import GitHubApi
from kraft.adapters.forge.github_queries import owner_vars
from kraft.adapters.forge.models import ForgeError
from kraft.forges import ForgeAccount

TOK = "ghp_0123456789"
NEW = "ghp_9876543210"
CORP = "https://ghe.corp.example"
SSO = "https://github.com/orgs/acme/sso?authorization_request=abc"


def account(web="https://github.com", **token) -> ForgeAccount:
    return accounts("github", web, **token)


@pytest.fixture
def seen(mock_http, monkeypatch):
    """Requests the backend sent, with CORP_GH taken from the 'server environment'."""
    monkeypatch.setattr(credentials, "_TAKEN", {"CORP_GH": TOK})
    return mock_http(handler=answers((200, {})))


def rest(path, method="GET", **fields):
    return lambda t: t.rest(method, path, fields=fields or None)


@pytest.mark.parametrize(
    "web, call, method, host, raw, sent",
    [
        pytest.param(
            "https://github.com",
            rest("repos/{owner}/{repo}/pulls"),
            "GET",
            "api.github.com",
            "/repos/o/r/pulls",
            None,
            id="rest-path",
        ),
        pytest.param(
            CORP,
            rest("repos/{owner}/{repo}/pulls"),
            "GET",
            "ghe.corp.example",
            "/api/v3/repos/o/r/pulls",
            None,
            id="ghes",
        ),
        pytest.param(
            "https://github.com",
            lambda t: t.graphql("query", owner_vars(b="x")),
            "POST",
            "api.github.com",
            "/graphql",
            {"variables": {"owner": "o", "name": "r", "b": "x"}},
            id="graphql-variables",
        ),
        pytest.param(
            CORP,
            lambda t: t.graphql("query", owner_vars()),
            "POST",
            "ghe.corp.example",
            "/api/graphql",
            {"variables": {"owner": "o", "name": "r"}},
            id="ghes-graphql",
        ),
        pytest.param(
            "https://acme.ghe.com",
            rest("repos/{owner}/{repo}"),
            "GET",
            "api.acme.ghe.com",
            "/repos/o/r",
            None,
            id="ghe-cloud",
        ),
        pytest.param(
            "https://github.com",
            rest("repos/{owner}/{repo}/issues/1", "PATCH", body="see {owner}/{repo}"),
            "PATCH",
            "api.github.com",
            "/repos/o/r/issues/1",
            {"body": "see {owner}/{repo}"},
            id="body-keeps-literal-placeholder-text",
        ),
        pytest.param(
            "https://github.com",
            rest("repos/{owner}/{repo}/pulls", "POST", head="{owner}", base="{repo}"),
            "POST",
            "api.github.com",
            "/repos/o/r/pulls",
            {"head": "o", "base": "r"},
            id="field-whole-value-filled",
        ),
        pytest.param(
            "https://github.com",
            rest("repos/{owner}/{repo}/issues/1/labels", "POST", **{"labels": ["{owner}"]}),
            "POST",
            "api.github.com",
            "/repos/o/r/issues/1/labels",
            {"labels": ["{owner}"]},
            id="list-item-not-filled",
        ),
    ],
)
async def test_the_repository_fills_owner_and_repo_and_nothing_else(
    tmp_path, seen, web, call, method, host, raw, sent
):
    repo = checkout(tmp_path, f"{web}/o/r.git")
    await call(GitHubApi("corp-gh", account(web), http=seen.transport)._transport(repo))
    (req,) = seen.requests
    assert (req.method, req.url.host) == (method, host)
    assert req.url.raw_path.decode().split("?")[0] == raw
    body = json.loads(req.content) if req.content else {}
    assert not sent or all(body.get(k) == v for k, v in sent.items())


async def test_a_call_with_no_placeholder_reads_no_project(tmp_path, seen):
    api = GitHubApi("corp-gh", account(), http=seen.transport)
    await api._transport(checkout(tmp_path)).rest("GET", "rate_limit")
    assert len(seen.requests) == 1


@pytest.mark.parametrize(
    "remote, says",
    [
        pytest.param(None, "has no origin URL Kraft can read a GitHub repository from", id="none"),
        pytest.param(
            "https://github.com/a/b/c.git", "can read a GitHub repository from", id="3-segments"
        ),
        pytest.param(
            "https://oauth2:ghp_SECRET0000@evil.example/o/r.git",
            "different host",
            id="other-host-never-quotes-the-url",
        ),
    ],
)
async def test_a_repository_it_cannot_name_stops_without_quoting_the_remote(
    tmp_path, seen, remote, says
):
    api = GitHubApi("corp-gh", account(), http=seen.transport)
    with pytest.raises(ForgeError) as err:
        await api._transport(checkout(tmp_path, remote)).rest("GET", "repos/{owner}/{repo}")
    assert says in str(err.value) and "forge account corp-gh" in str(err.value)
    for leak in ("SECRET0000", "evil.example", "github.com/a/b"):
        assert leak not in str(err.value)
    assert not seen.requests


@pytest.mark.parametrize(
    "remote, pushurl, raw",
    [
        pytest.param("git@github-work:o/r.git", None, "/repos/o/r/x", id="ssh-alias"),
        pytest.param(
            "https://github.com/o/r.git",
            "https://github.com/fork/r.git",
            "/repos/fork/r/x",
            id="pushurl-names-the-repository",
        ),
    ],
)
async def test_the_repository_comes_from_the_push_url(tmp_path, seen, remote, pushurl, raw):
    from support.harness import git

    repo = checkout(tmp_path, remote)
    if pushurl:
        git(repo, "config", "remote.origin.pushurl", pushurl)
    api = GitHubApi("corp-gh", account(), http=seen.transport)
    await api._transport(repo).rest("GET", "repos/{owner}/{repo}/x")
    assert seen.requests[0].url.raw_path.decode() == raw


async def test_a_401_re_reads_the_token_once(tmp_path, mock_http, monkeypatch):
    """The 401 logic is shared with GitLab (`test_gitlab_api.py` has the rows); this is the
    GraphQL door: a 401 then a 200, the second request carrying the new Bearer."""
    path = tmp_path / "outside" / "token"
    path.parent.mkdir()
    path.write_text(TOK + "\n")
    path.chmod(0o600)

    async def context():
        return credentials.ReadContext(repos=[], worktrees=tmp_path / "wt", expose=[])

    credentials.set_token_context(context)
    acct = account(from_="file", file=str(path))
    m = mock_http(handler=answers(401, (200, {"data": {}})))
    api = GitHubApi("corp-gh", acct, http=m.transport)
    await credentials.token_for("corp-gh", acct)
    path.write_text(NEW + "\n")
    repo = checkout(tmp_path, "https://github.com/o/r.git")
    await api._transport(repo).graphql("query", owner_vars())
    assert [r.headers["authorization"] for r in m.requests] == [f"Bearer {TOK}", f"Bearer {NEW}"]


@pytest.mark.parametrize(
    "call, replies, status, reason, sso",
    [
        pytest.param(rest("repos/{owner}/{repo}"), [404], 404, None, "", id="http-404"),
        pytest.param(
            lambda t: t.graphql("query", owner_vars()),
            [(200, {"errors": [{"type": "NOT_FOUND", "message": "gone"}]})],
            200,
            "not_found",
            "",
            id="graphql-errors-at-200",
        ),
        pytest.param(
            rest("repos/{owner}/{repo}"),
            [(403, {"message": "Resource protected by organization SAML enforcement."})],
            403,
            "forbidden",
            SSO,
            id="sso-403-carries-the-link",
        ),
    ],
)
async def test_every_error_names_its_account_and_source(
    tmp_path, mock_http, monkeypatch, call, replies, status, reason, sso
):
    monkeypatch.setattr(credentials, "_TAKEN", {"CORP_GH": TOK})
    headers = {"x-github-sso": f"required; url={SSO}"} if sso else None
    status_, body = replies[0] if isinstance(replies[0], tuple) else (replies[0], {"message": "no"})
    m = mock_http(handler=lambda r: httpx.Response(status_, json=body, headers=headers))
    api = GitHubApi("corp-gh", account(), http=m.transport)
    with pytest.raises(ForgeError) as err:
        await call(api._transport(checkout(tmp_path, "https://github.com/o/r.git")))
    exc = err.value
    assert (exc.account, exc.source, exc.status) == ("corp-gh", "env CORP_GH", status)
    assert not reason or exc.reason == reason
    assert exc.sso_url == sso
