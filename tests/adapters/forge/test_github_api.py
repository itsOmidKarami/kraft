"""`GitHubApi`: GhCli's requests over HTTP with an account's token. Nothing dials a host."""

from __future__ import annotations

import json
import os
import re
import secrets
from types import SimpleNamespace

import httpx
import pytest
from support.api_accounts import LEAK_PLACES, answers, checkout, logged_push_repo
from support.api_accounts import account as accounts

from kraft.adapters.forge import credentials
from kraft.adapters.forge.github_api import GitHubApi
from kraft.adapters.forge.github_queries import owner_vars
from kraft.adapters.forge.models import ForgeError
from kraft.forges import ForgeAccount, TokenSource

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
            "https://github.com/../x.git", "can read a GitHub repository from", id="dotdot-owner"
        ),
        pytest.param(
            "https://github.com/o/...git", "can read a GitHub repository from", id="dot-name"
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
        # Most pull request reads are GraphQL, which answers SSO with HTTP 200.
        pytest.param(
            lambda t: t.graphql("query", owner_vars()),
            [(200, {"errors": [{"type": "FORBIDDEN", "message": "SAML enforcement"}]})],
            200,
            "forbidden",
            SSO,
            id="graphql-sso-at-200-carries-the-link",
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


@pytest.fixture
def pushed(tmp_path, monkeypatch, mock_http):
    """`GitHubApi` opening a pull request, reading labels (a 401 that quotes the Bearer it was
    sent), then a job log (302 to blob storage), with a real `git push` (`logged_push_repo`)."""
    tok = "ghp_" + secrets.token_hex(18)
    got = logged_push_repo(
        tmp_path, monkeypatch, env="CORP_GH", tok=tok, remote="https://github.com/o/r.git"
    )

    def handler(request):
        if request.url.host == "blob.example":
            return httpx.Response(200, text="log")
        if request.url.path.endswith("/labels"):
            return httpx.Response(401, json={"message": f"bad {request.headers['authorization']}"})
        if request.url.path.endswith("/logs"):
            return httpx.Response(302, headers={"location": "https://blob.example/l?sig=s"})
        return httpx.Response(200, json={"number": 7, "html_url": "w"})

    m = mock_http(handler=handler)
    api = GitHubApi("corp-gh", account(), http=m.transport)

    async def run() -> SimpleNamespace:
        await api.open_mr(repo=got.repo, branch="kraft/abc", base="main", title="t", body="b")
        with pytest.raises(ForgeError) as err:
            await api.labels(repo=got.repo)
        assert await api.job_log(repo=got.repo, job_id="1") == "log"
        return SimpleNamespace(
            tok=tok,
            git=got.log.read_text(),
            config=(got.repo / ".git" / "config").read_text(),
            err=f"{err.value} {err.value!r} {err.value.args}",
            requests=m.requests,
            blob="\n".join(
                f"{k}: {v}"
                for r in m.requests
                if r.url.host == "blob.example"
                for k, v in r.headers.items()
            ),
        )

    return run


@pytest.mark.parametrize(
    "place", [*LEAK_PLACES, pytest.param(lambda r: r.blob, id="blob-redirect")]
)
async def test_a_github_api_token_reaches_only_its_origin(pushed, place):
    got = await pushed()
    assert {r.url.host for r in got.requests} == {"api.github.com", "blob.example"}
    assert all(
        ("authorization" in r.headers) == (r.url.host == "api.github.com") for r in got.requests
    ), "a Bearer went to the wrong host, or none to the API"
    assert any(r.headers.get("authorization") == f"Bearer {got.tok}" for r in got.requests)
    assert re.search(r"^ARGV .*\bpush\b", got.git, re.M), "git never pushed"
    assert got.blob, "the blob request never happened"
    assert got.tok not in place(got)


@pytest.mark.e2e("git")
@pytest.mark.skipif(
    not (os.environ.get("E2E_GITHUB_TOKEN") and os.environ.get("E2E_GITHUB_REPO")),
    reason="e2e: needs E2E_GITHUB_TOKEN (the workflow's read-only github.token) and "
    "E2E_GITHUB_REPO (owner/name); the e2e-cli job in .github/workflows/test.yml sets both",
)
async def test_github_api_reads_this_repo(tmp_path, monkeypatch):
    """Read-only, against this repository, with the workflow's `github.token`. It does not
    call `probe_account`: an installation token gets 403 from `GET /user`."""
    slug = os.environ["E2E_GITHUB_REPO"]
    monkeypatch.setattr(credentials, "_TAKEN", {})
    monkeypatch.setenv("E2E_GITHUB_TOKEN", os.environ["E2E_GITHUB_TOKEN"])  # restored at exit
    credentials.take_env(["E2E_GITHUB_TOKEN"])
    acct = ForgeAccount(
        forge="github",
        client="api",
        web_url="https://github.com",
        token=TokenSource(**{"from": "env", "env": "E2E_GITHUB_TOKEN", "kind": "unknown"}),
    )
    repo = checkout(tmp_path, f"https://github.com/{slug}.git")
    api = GitHubApi("e2e-gh", acct)

    assert "release::minor" in [label.name for label in await api.labels(repo=repo)]
    merged = await api.merged_mrs(repo=repo, base="main", limit=5)
    assert merged
    # By number: a later pull request can reuse the head branch, so no `find_mr` on it.
    pull = await api._api(repo, f"repos/{{owner}}/{{repo}}/pulls/{merged[0].number}")
    assert {"merged_at", "title", "labels", "head", "base"} <= pull.keys()
    assert pull["merged_at"] and pull["base"]["ref"] == "main" and pull["head"]["ref"]

    # A skipped job has no log and answers 404, so the log is a succeeded job's.
    done = [
        p
        for p in await api.pipelines(repo=repo, branch="main", base="main")
        if p.status == "success"
    ]
    assert done
    jobs = [j for p in done[:5] for j in await api.jobs(repo=repo, pipeline_id=p.id)]
    assert jobs
    ok = next(j for j in jobs if j.status == "success")
    assert re.search(r"^\ufeff?\d{4}-\d\d-\d\dT", await api.job_log(repo=repo, job_id=ok.id), re.M)

    junk = "ghp_" + secrets.token_hex(18)
    monkeypatch.setenv("E2E_GITHUB_JUNK", junk)
    credentials.take_env(["E2E_GITHUB_JUNK"])
    bad = ForgeAccount(
        forge="github",
        client="api",
        web_url="https://github.com",
        token=TokenSource(**{"from": "env", "env": "E2E_GITHUB_JUNK", "kind": "unknown"}),
    )
    with pytest.raises(ForgeError) as err:
        await GitHubApi("e2e-junk", bad).labels(repo=repo)
    assert err.value.reason == "auth" and err.value.account == "e2e-junk"
    assert junk not in str(err.value)


async def test_a_blob_401_neither_re_reads_the_token_nor_repeats_the_call(
    tmp_path, mock_http, monkeypatch
):
    path = tmp_path / "outside" / "token"
    path.parent.mkdir()
    path.write_text(TOK + "\n")
    path.chmod(0o600)

    async def context():
        return credentials.ReadContext(repos=[], worktrees=tmp_path / "wt", expose=[])

    credentials.set_token_context(context)
    acct = account(from_="file", file=str(path))

    def handler(req):
        if req.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://blob.example/l?sig=S"})
        return httpx.Response(401)

    m = mock_http(handler=handler)
    api = GitHubApi("corp-gh", acct, http=m.transport)
    await credentials.token_for("corp-gh", acct)
    path.write_text(NEW + "\n")  # a re-read would find a changed token, and retry
    reads = []
    real = credentials.read_token

    async def spy(*a, **kw):
        reads.append(1)
        return await real(*a, **kw)

    monkeypatch.setattr(credentials, "read_token", spy)
    repo = checkout(tmp_path, "https://github.com/o/r.git")
    with pytest.raises(ForgeError) as err:
        await api._transport(repo).rest("GET", "repos/o/r/actions/jobs/1/logs", download=True)
    assert err.value.reason == "unreachable" and reads == []
    assert len(m.requests) == 2
