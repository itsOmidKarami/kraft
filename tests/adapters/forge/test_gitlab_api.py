"""`GitLabApi`: GlabCli's requests over HTTP with an account's token. Nothing dials a host."""

from __future__ import annotations

import httpx
import pytest
from support.harness import git, make_repo

from kraft.adapters.forge import credentials
from kraft.adapters.forge.credentials import ReadContext, set_token_context
from kraft.adapters.forge.gitlab_api import GitLabApi
from kraft.adapters.forge.models import ForgeError
from kraft.forges import ForgeAccount, TokenSource

TOK = "tok-0123456789"
NEW = "tok-9876543210"
WEB = "https://code.corp.example"


def account(web=WEB, **token) -> ForgeAccount:
    return ForgeAccount(
        forge="gitlab",
        client="api",
        web_url=web,
        token=TokenSource(**(token or {"from": "env", "env": "CORP_GL"})),
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


@pytest.fixture
def seen(mock_http, monkeypatch):
    """Requests the backend sent, with CORP_GL taken from the 'server environment'."""
    monkeypatch.setattr(credentials, "_TAKEN", {"CORP_GL": TOK})
    return mock_http(handler=answers((200, [])))


@pytest.mark.parametrize(
    "remote, acct, path, raw",
    [
        pytest.param(
            f"{WEB}/team/app.git",
            account(),
            "projects/:id/merge_requests",
            "/api/v4/projects/team%2Fapp/merge_requests",
            id="simple",
        ),
        pytest.param(
            f"{WEB}/gitlab/team/sub/app.git",
            account(f"{WEB}/gitlab"),
            "projects/:id/merge_requests",
            "/gitlab/api/v4/projects/team%2Fsub%2Fapp/merge_requests",
            id="subgroup-under-a-prefix",
        ),
        pytest.param(
            f"{WEB}/team/app.git",
            account(),
            "projects/42/repository/commits/x",
            "/api/v4/projects/42/repository/commits/x",
            id="numeric-project-id-untouched",
        ),
        pytest.param(
            "ssh://git@code.corp.example:2222/gitlab/team/app.git",
            account(f"{WEB}/gitlab"),
            "projects/:id",
            "/gitlab/api/v4/projects/gitlab%2Fteam%2Fapp",
            id="ssh-under-prefix-not-stripped",
        ),
    ],
)
async def test_the_project_fills_id_and_nothing_else(tmp_path, seen, remote, acct, path, raw):
    repo = checkout(tmp_path, remote)
    await GitLabApi("corp-gl", acct, http=seen.transport)._transport(repo).rest("GET", path)
    (req,) = seen.requests
    assert req.url.raw_path.decode().split("?")[0] == raw


@pytest.mark.parametrize(
    "remote, says",
    [
        pytest.param(None, "has no origin URL", id="no-project"),
        pytest.param(
            "https://oauth2:glpat-SECRET0000@other.example/team/app.git",
            "different host",
            id="other-host-never-quotes-the-url",
        ),
    ],
)
async def test_a_project_it_cannot_name_stops_without_quoting_the_remote(
    tmp_path, seen, remote, says
):
    api = GitLabApi("corp-gl", account(), http=seen.transport)
    with pytest.raises(ForgeError) as err:
        await api._transport(checkout(tmp_path, remote)).rest("GET", "projects/:id/pipelines")
    assert says in str(err.value) and "forge account corp-gl" in str(err.value)
    assert "SECRET0000" not in str(err.value) and "other.example" not in str(err.value)
    assert not seen.requests


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "outside" / "token"
    path.parent.mkdir()
    path.write_text(TOK + "\n")
    path.chmod(0o600)

    async def context():
        return ReadContext(repos=[], worktrees=tmp_path / "wt", expose=[])

    set_token_context(context)
    return path


@pytest.mark.parametrize(
    "source, changed, replies, sent, reads, raises",
    [
        pytest.param(
            "file", True, [401, (200, [])], [TOK, NEW], 1, False, id="file-changed-retried"
        ),
        pytest.param("file", False, [401], [TOK], 1, True, id="file-same-raises"),
        pytest.param("env", False, [401], [TOK], 0, True, id="env-never-re-reads"),
        pytest.param("file", True, [401, 401], [TOK, NEW], 1, True, id="retry-401-raises"),
    ],
)
async def test_a_401_re_reads_the_token_once(
    tmp_path, token_file, mock_http, monkeypatch, source, changed, replies, sent, reads, raises
):
    monkeypatch.setattr(credentials, "_TAKEN", {"CORP_GL": TOK})
    acct = account(from_="file", file=str(token_file)) if source == "file" else account()
    m = mock_http(handler=answers(*replies))
    api = GitLabApi("corp-gl", acct, http=m.transport)
    repo = checkout(tmp_path, f"{WEB}/team/app.git")
    await credentials.token_for("corp-gl", acct)  # warm: the token the first request uses
    if changed:
        token_file.write_text(NEW + "\n")
    spent = []
    real = credentials.read_token

    async def spy(*a, **kw):
        spent.append(1)
        return await real(*a, **kw)

    monkeypatch.setattr(credentials, "read_token", spy)
    monkeypatch.setattr(credentials, "env_token", lambda n: spent.append(1) or TOK)

    call = api._transport(repo).rest("GET", "projects/:id/pipelines")
    if raises:
        with pytest.raises(ForgeError) as err:
            await call
        assert err.value.status == 401 and err.value.reason == "auth"
    else:
        await call
    assert [r.headers["authorization"] for r in m.requests] == [f"Bearer {t}" for t in sent]
    assert len(spent) == reads


@pytest.mark.parametrize(
    "acct, replies, status, reason, source",
    [
        pytest.param(
            account(from_="file", file="/secrets/tok"),
            [404],
            404,
            None,
            "file /secrets/tok",
            id="http-404",
        ),
        pytest.param(
            account(from_="command", command="vault read LEAKMARK"),
            [(403, {"message": "no"})],
            403,
            "forbidden",
            "command",
            id="command-source-label",
        ),
        pytest.param(account(), [], None, "auth", "env CORP_GL", id="no-token"),
    ],
)
async def test_every_error_names_its_account_and_source(
    tmp_path, mock_http, monkeypatch, acct, replies, status, reason, source
):
    # The file and command sources are cached as read; CORP_GL is not set for no-token.
    seed = {} if not replies else {(("corp-gl", acct.model_dump_json())): TOK}
    monkeypatch.setattr(credentials, "_TOKENS", seed)
    m = mock_http(handler=answers(*(replies or [200])))
    api = GitLabApi("corp-gl", acct, http=m.transport)
    with pytest.raises(ForgeError) as err:
        await api._transport(checkout(tmp_path, f"{WEB}/team/app.git")).rest(
            "GET", "projects/:id/pipelines"
        )
    exc = err.value
    assert (exc.account, exc.source, exc.status) == ("corp-gl", source, status)
    if reason:
        assert exc.reason == reason
    assert "LEAKMARK" not in exc.source
