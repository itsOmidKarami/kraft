"""`GitLabApi`: GlabCli's requests over HTTP with an account's token. Nothing dials a host."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
from types import SimpleNamespace

import httpx
import pytest
from support.api_accounts import account as accounts
from support.api_accounts import answers, checkout
from support.harness import git

from kraft.adapters.forge import credentials
from kraft.adapters.forge.credentials import ReadContext, set_token_context
from kraft.adapters.forge.gitlab_api import GitLabApi
from kraft.adapters.forge.models import MR, ForgeError
from kraft.forges import ForgeAccount, TokenSource

TOK = "tok-0123456789"
NEW = "tok-9876543210"
WEB = "https://code.corp.example"


def account(web=WEB, **token) -> ForgeAccount:
    return accounts("gitlab", web, **token)


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


@pytest.fixture
def pushed(tmp_path, monkeypatch, mock_http):
    """`GitLabApi` opening and labelling a merge request, then reading labels, with a real
    `git push` to a local bare repo (`insteadOf` the GitLab URL) through a `git` that logs
    its argv and environment. The token enters as in production: set, then `take_env`."""
    tok = "glpat-" + secrets.token_hex(16)
    monkeypatch.setattr(credentials, "_TAKEN", {})
    monkeypatch.setenv("CORP_GL", tok)
    credentials.take_env(["CORP_GL"])
    log = tmp_path / "git.log"
    wrapper = tmp_path / "wrap" / "git"
    wrapper.parent.mkdir()
    wrapper.write_text(
        f'#!/bin/sh\n{{ printf "ARGV %s\\n" "$*"; env; }} >> {log}\n'
        f'exec {shutil.which("git")} "$@"\n'
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{wrapper.parent}:{os.environ['PATH']}")

    remote = f"{WEB}/team/app.git"
    repo = checkout(tmp_path, remote)
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "config", f"url.{bare}.insteadOf", remote)
    git(repo, "checkout", "-q", "-b", "kraft/abc")

    def handler(request):
        if request.url.path.endswith("/labels"):  # a 401 that quotes the token it was sent
            sent = request.headers["authorization"]
            return httpx.Response(401, json={"message": f"bad {sent}"})
        return httpx.Response(200, json={"iid": 7, "web_url": "w", "labels": []})

    m = mock_http(handler=handler)
    api = GitLabApi("corp-gl", account(), http=m.transport)

    async def run() -> SimpleNamespace:
        await api.open_mr(repo=repo, branch="kraft/abc", base="main", title="t", body="b")
        await api.set_labels(repo=repo, branch="kraft/abc", mr=MR(7, "w"), labels=("x",))
        with pytest.raises(ForgeError) as err:
            await api.labels(repo=repo)
        return SimpleNamespace(
            tok=tok,
            git=log.read_text(),
            config=(repo / ".git" / "config").read_text(),
            err=f"{err.value} {err.value!r} {err.value.args}",
            requests=m.requests,
        )

    return run


@pytest.mark.parametrize(
    "place",
    [
        pytest.param(
            lambda r: "\n".join(x for x in r.git.splitlines() if x.startswith("ARGV")), id="argv"
        ),
        pytest.param(lambda r: r.git, id="git-env"),  # every line a hook would inherit, argv too
        pytest.param(lambda r: r.config, id="git-config"),
        pytest.param(lambda r: r.err, id="401-text"),
    ],
)
async def test_a_gitlab_api_token_reaches_only_its_origin(pushed, place):
    got = await pushed()
    assert all(r.url.host == "code.corp.example" for r in got.requests)
    assert any(r.headers["authorization"] == f"Bearer {got.tok}" for r in got.requests)
    assert re.search(r"^ARGV .*\bpush\b", got.git, re.M), "git never pushed"
    assert got.tok not in place(got)


@pytest.mark.e2e("git")
@pytest.mark.real_probe
@pytest.mark.skipif(
    not (os.environ.get("E2E_GITLAB_TOKEN") and os.environ.get("E2E_GITLAB_PROJECT")),
    reason="e2e: needs E2E_GITLAB_TOKEN (a read_api token secret) and E2E_GITLAB_PROJECT "
    "(namespace/project on gitlab.com); the setup is in this test's docstring",
)
async def test_gitlab_api_reads_a_real_gitlab_com_project(tmp_path, monkeypatch):
    """Read-only, against a project holding an open draft merge request on `kraft-e2e/fixture`,
    a label `kraft-e2e`, and a pipeline whose `e2e` job prints `kraft-e2e`.

    To turn it on, create:
    1. A gitlab.com project, e.g. `<namespace>/kraft-e2e` (private is fine), with a
       `.gitlab-ci.yml` whose one job `e2e` runs `echo kraft-e2e` (shared runners need a
       verified account); a branch `kraft-e2e/fixture` with an open draft merge request into
       `main` whose pipeline has run once; one merged merge request into `main`; and a project
       label `kraft-e2e`.
    2. A token with scope `read_api` only: a project access token (Reporter) where the tier
       offers one, else a personal token from a dedicated account that is a Reporter on that
       project alone. Set the longest expiry allowed.
    3. In the GitHub repo, the secret `E2E_GITLAB_TOKEN` (the token) and the variable
       `E2E_GITLAB_PROJECT` (`<namespace>/kraft-e2e`).
    4. In the e2e job's `env:` of `.github/workflows/test.yml`, `E2E_GITLAB_TOKEN: ${{
       secrets.E2E_GITLAB_TOKEN }}` and `E2E_GITLAB_PROJECT: ${{ vars.E2E_GITLAB_PROJECT }}`.
       Pull requests from forks get no secrets, so the test skips there.
    """
    from kraft.adapters.forge.probe import probe_account
    from kraft.config import RepoEntry

    project = os.environ["E2E_GITLAB_PROJECT"]
    acct = ForgeAccount(
        forge="gitlab",
        client="api",
        web_url="https://gitlab.com",
        token=TokenSource(**{"from": "env", "env": "E2E_GITLAB_TOKEN", "kind": "pat"}),
    )
    credentials.take_env(["E2E_GITLAB_TOKEN"])
    repo = checkout(tmp_path, f"https://gitlab.com/{project}.git")
    api = GitLabApi("e2e-gl", acct)

    probe = await probe_account(
        "e2e-gl", acct, repos=[RepoEntry(path=str(repo))], worktrees=tmp_path / "wt"
    )
    assert probe.state == "ok"
    assert probe.projects[str(repo)]["visible"] is True
    assert await api._version(repo) is not None
    assert "kraft-e2e" in [label.name for label in await api.labels(repo=repo)]
    found = await api.find_mr(repo=repo, branch="kraft-e2e/fixture")
    assert found and found.state == "open"
    details = await api.mr_details(repo=repo, branch="kraft-e2e/fixture")
    assert details and details.draft
    listed = await api.pipelines(repo=repo, branch="kraft-e2e/fixture", base="main")
    assert listed
    jobs = {j.name: j for j in await api.jobs(repo=repo, pipeline_id=listed[0].id)}
    assert "kraft-e2e" in await api.job_log(repo=repo, job_id=jobs["e2e"].id)
    assert await api.merged_mrs(repo=repo, base="main", limit=5)

    junk = "glpat-" + secrets.token_hex(16)
    monkeypatch.setenv("E2E_GITLAB_JUNK", junk)
    credentials.take_env(["E2E_GITLAB_JUNK"])
    bad = ForgeAccount(
        forge="gitlab",
        client="api",
        web_url="https://gitlab.com",
        token=TokenSource(**{"from": "env", "env": "E2E_GITLAB_JUNK", "kind": "pat"}),
    )
    with pytest.raises(ForgeError) as err:
        await GitLabApi("e2e-junk", bad).labels(repo=repo)
    assert err.value.reason == "auth" and err.value.account == "e2e-junk"
    assert junk not in str(err.value)
