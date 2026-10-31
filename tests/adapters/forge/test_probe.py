"""The server-side token probe, against `httpx.MockTransport` only."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date

import httpx
import pytest
from support import forge_probe as canned
from support.harness import git, make_repo

from kraft import config, redact
from kraft.adapters.forge import credentials, probe
from kraft.forges import ForgeAccount, TokenSource

TOK = "tok-1234567890"
GH, GL = "https://api.github.com", "https://code.corp.example/gitlab"
GL_USER = {"username": "ada"}


def acct(forge="github", web="https://github.com", **token) -> ForgeAccount:
    return ForgeAccount(
        forge=forge,
        client="api",
        web_url=web,
        token=TokenSource(**{"from": "env", "env": "CORP_TOKEN", **token}),
    )


def gitlab(**token) -> ForgeAccount:
    return acct("gitlab", GL, **token)


@pytest.fixture
def run(monkeypatch, tmp_path):
    async def go(m, account, *, repos=(), token=TOK, no_token=None):
        async def read_token(*a, **kw):
            if no_token:
                raise credentials.NoToken(no_token)
            redact.register(token)  # as the real read_token does
            return token

        monkeypatch.setattr(credentials, "read_token", read_token)
        return await probe.probe_account(
            "corp", account, repos=repos, worktrees=tmp_path, transport=m.transport
        )

    return go


@pytest.mark.real_probe
@pytest.mark.parametrize(
    "account, routes, want",
    [
        pytest.param(
            acct(),
            {
                ("GET", f"{GH}/user"): (
                    200,
                    {"login": "ada"},
                    {
                        "x-oauth-scopes": "repo, read:org",
                        "github-authentication-token-expiration": "2027-01-15 00:00:00 UTC",
                    },
                )
            },
            ("ada", ("repo", "read:org"), "2027-01-15"),
            id="github-classic",
        ),
        pytest.param(
            acct(),
            {("GET", f"{GH}/user"): (200, {"login": "ada"})},
            ("ada", None, None),
            id="github-fine-grained",
        ),
        pytest.param(
            gitlab(kind="pat"),
            {
                ("GET", f"{GL}/api/v4/user"): (200, GL_USER),
                ("GET", f"{GL}/api/v4/personal_access_tokens/self"): (
                    200,
                    {"scopes": ["api", "read_user"], "expires_at": "2027-02-01"},
                ),
            },
            ("ada", ("api", "read_user"), "2027-02-01"),
            id="gitlab-pat",
        ),
        pytest.param(
            gitlab(kind="oauth"),
            {
                ("GET", f"{GL}/api/v4/user"): (200, GL_USER),
                ("GET", f"{GL}/oauth/token/info"): (200, {"scope": ["api"], "expires_in": None}),
            },
            ("ada", ("api",), None),
            id="gitlab-oauth",
        ),
        pytest.param(
            gitlab(),
            {
                ("GET", f"{GL}/api/v4/user"): (200, GL_USER),
                ("GET", f"{GL}/api/v4/personal_access_tokens/self"): 401,
                ("GET", f"{GL}/oauth/token/info"): (200, {"scope": ["read_api"]}),
            },
            ("ada", ("read_api",), None),
            id="gitlab-unknown-falls-back-to-oauth",
        ),
        pytest.param(
            acct(),
            {
                ("GET", f"{GH}/user"): (
                    200,
                    {"login": "ada"},
                    {"github-authentication-token-expiration": "soon"},
                )
            },
            ("ada", None, None),
            id="expiry-not-a-date-is-unknown",
        ),
        pytest.param(
            gitlab(kind="pat"),
            {
                ("GET", f"{GL}/api/v4/user"): (200, GL_USER),
                ("GET", f"{GL}/api/v4/personal_access_tokens/self"): (200, ["not", "an", "object"]),
            },
            ("ada", None, None),
            id="token-info-not-an-object",
        ),
    ],
)
async def test_the_probe_reports_who_the_token_signs_in_as(mock_http, run, account, routes, want):
    m = mock_http(routes)
    got = await run(m, account)
    assert (got.state, got.login, got.scopes, got.expires_on) == ("ok", *want)


@pytest.mark.real_probe
@pytest.mark.parametrize(
    "answer, state, text",
    [
        pytest.param(None, "no_token", "nothing saved", id="no-token"),
        pytest.param(
            (401, {"message": "Bad credentials"}), "refused", "HTTP 401", id="refused-401"
        ),
        pytest.param(
            (403, {"message": "SSO"}, {"x-github-sso": "required; url=https://sso.example/x"}),
            "forbidden",
            "HTTP 403",
            id="forbidden-sso",
        ),
        pytest.param(
            httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed"),
            "tls",
            "ca_file",
            id="tls",
        ),
        pytest.param(httpx.ConnectError("down"), "unreachable", "cannot reach", id="unreachable"),
        pytest.param(
            (200, ["not", "an", "object"]),
            "unreachable",
            "without an object",
            id="user-not-an-object",
        ),
    ],
)
async def test_the_probe_names_why_a_token_does_not_work(mock_http, run, answer, state, text):
    m = mock_http({("GET", f"{GH}/user"): answer or 200})
    got = await run(m, acct(), no_token="nothing saved" if answer is None else None)
    assert got.state == state and text in got.detail
    assert bool(got.sso_url) is (state == "forbidden")
    assert got.sso_url in got.text()  # what `kraft admin forge test` prints


@pytest.mark.real_probe
@pytest.mark.parametrize(
    "status, visible", [(200, True), (404, False)], ids=["visible", "not-found"]
)
async def test_the_probe_says_whether_each_repo_is_visible(
    mock_http, run, tmp_path, status, visible
):
    dest = make_repo(tmp_path)
    git(dest, "remote", "add", "origin", "https://code.corp.example/gitlab/team/app.git")
    entry = config.RepoEntry(path=str(dest), forge="gitlab", forge_account="corp")
    m = mock_http(
        {
            ("GET", f"{GL}/api/v4/user"): (200, GL_USER),
            ("GET", f"{GL}/api/v4/personal_access_tokens/self"): (200, {"scopes": []}),
            ("GET", f"{GL}/api/v4/projects/team%2Fapp"): (status, {"message": "404 Not found"}),
        }
    )
    got = await run(m, gitlab(kind="pat"), repos=[entry])
    seen = got.projects[str(dest)]
    assert seen["visible"] is visible and seen["project"] == "team/app"
    assert visible or ("HTTP 404" in seen["detail"] and "team/app" in seen["detail"])


@pytest.mark.real_probe
@pytest.mark.parametrize(
    "account, token, want",
    [
        pytest.param(acct(kind="oauth"), "ghp_1234567890", "starts with ghp_", id="ghp-says-oauth"),
        pytest.param(
            acct(env="GITLAB_TOKEN"), TOK, "GITLAB_TOKEN is read by glab/gh", id="env-gitlab-token"
        ),
    ],
)
async def test_the_probe_warns_on_a_prefix_or_a_well_known_env_name(
    mock_http, run, account, token, want
):
    m = mock_http({("GET", f"{GH}/user"): (200, {"login": "ada"})})
    got = await run(m, account, token=token)
    assert any(want in w for w in got.warnings)


@pytest.mark.real_probe
async def test_the_probe_never_puts_a_token_in_its_result(mock_http, run):
    m = mock_http({("GET", f"{GH}/user"): (401, {"message": f"bad token {TOK}"})})
    got = await run(m, acct())
    assert got.state == "refused" and TOK not in json.dumps(got.to_json())


@pytest.mark.real_probe
async def test_a_login_that_echoes_the_token_is_redacted(mock_http, run):
    m = mock_http({("GET", f"{GH}/user"): (200, {"login": f"ada {TOK}"})})
    got = await run(m, acct())
    assert got.state == "ok" and TOK not in json.dumps(got.to_json())


async def _hangs(*a, **kw):
    await asyncio.sleep(10)


@pytest.mark.real_probe
@pytest.mark.parametrize(
    "token, slow, says",
    [
        pytest.param(
            "tök-1234567890", False, "probe failed (UnicodeEncodeError)", id="non-ascii-token"
        ),
        pytest.param(TOK, True, "did not finish in 0.05s", id="too-slow"),
    ],
)
async def test_a_probe_that_raises_or_hangs_is_its_own_unreachable_result(
    mock_http, run, monkeypatch, token, slow, says
):
    m = mock_http({("GET", f"{GH}/user"): (200, {"login": "ada"})})
    if slow:
        monkeypatch.setattr(probe, "PROBE_TIMEOUT_S", 0.05)
        monkeypatch.setattr(probe, "_probe_account", _hangs)
    got = await run(m, acct(), token=token)
    assert got.state == "unreachable" and says in got.detail, got


@pytest.mark.parametrize(
    "state, days, degraded",
    [
        pytest.param("ok", 400, False, id="ok-far"),
        pytest.param("ok", 7, True, id="expires-in-7-days"),
        pytest.param("ok", 8, False, id="expires-in-8-days"),
        pytest.param("refused", None, True, id="refused"),
        pytest.param("unreachable", None, False, id="unreachable-not-degraded"),
    ],
)
def test_health_rows_degrade_on_a_bad_state_or_a_near_expiry(state, days, degraded):
    today = date(2026, 10, 31)
    expires = date.fromordinal(today.toordinal() + days).isoformat() if days else None
    found = probe.Probe("corp", "t", state, "d", expires_on=expires)
    rows, bad = probe.health_rows({"corp": found}, today)
    assert rows == {"corp": {"state": state, "expires_on": expires}}
    assert bad == (["corp"] if degraded else [])


# The server: when it probes. The canned probe (support.forge_probe) counts the passes.


@pytest.fixture
def templates_dir(templates_dir):
    (templates_dir / "forges.yaml").write_text(
        "accounts:\n  corp:\n    forge: gitlab\n    client: api\n"
        "    web_url: https://code.corp.example/gitlab\n"
        "    token: {from: env, env: CORP_GL, kind: pat}\n"
    )
    return templates_dir


def until(n):
    for _ in range(100):
        if len(canned.CALLS) >= n:
            break
        time.sleep(0.05)
    return len(canned.CALLS)


def test_probes_run_at_start_reload_test_and_timer_never_per_health_poll(client, templates_dir):
    assert until(1) == 1
    for _ in range(3):
        client.get("/api/health")
    (templates_dir / "forges.yaml").write_text(
        (templates_dir / "forges.yaml").read_text().replace("pat}", "oauth}")
    )
    client.post("/api/apply/reload")
    assert until(2) == 2
    for _ in range(3):
        client.get("/api/health")
    time.sleep(0.2)
    assert len(canned.CALLS) == 2
    assert client.post("/api/forges/corp/test").json()["ok"] is True
    assert len(canned.CALLS) == 3
    assert client.app.state.forge_probes["corp"].state == "ok"


@pytest.fixture
def fast_timer(monkeypatch):
    monkeypatch.setattr(probe, "PROBE_INTERVAL_S", 0.05)


def test_the_timer_probes_again_with_nothing_asking(fast_timer, client):
    assert until(3) >= 3


@pytest.mark.parametrize("how", ["reload", "delete"])
def test_a_removed_account_leaves_health(client, templates_dir, how):
    # The first pass stored its result, so it cannot overwrite the one planted below.
    for _ in range(100):
        if "corp" in client.app.state.forge_probes:
            break
        time.sleep(0.05)
    client.app.state.forge_probes["corp"] = probe.Probe("corp", "t", "refused", "HTTP 401")
    assert client.get("/api/health").json()["status"] == "degraded"
    if how == "delete":
        assert client.delete("/api/forges/corp").status_code == 204
    else:
        (templates_dir / "forges.yaml").write_text("accounts: {}\n")
        client.post("/api/apply/reload")
    health = client.get("/api/health").json()
    assert health["forge_tokens"] == {} and health["status"] == "ok", health


def test_a_test_of_an_unreloaded_edit_never_reaches_health(client, templates_dir):
    for _ in range(100):
        if "corp" in client.app.state.forge_probes:
            break
        time.sleep(0.05)
    client.app.state.forge_probes["corp"] = probe.Probe("corp", "t", "refused", "HTTP 401")
    forges = templates_dir / "forges.yaml"
    forges.write_text(forges.read_text().replace("pat}", "oauth}"))  # on disk, not reloaded
    assert client.post("/api/forges/corp/test").json()["ok"] is True
    assert client.app.state.forge_probes["corp"].state == "refused"


async def test_a_file_token_is_not_read_while_repos_yaml_does_not_load(tmp_path):
    from types import SimpleNamespace

    from kraft.forges import ForgesFile

    (tmp_path / "repos.yaml").write_text("repos: [\n")
    account = ForgeAccount(
        forge="gitlab",
        client="api",
        web_url=GL,
        token=TokenSource(**{"from": "file", "file": "/s/tok"}),
    )
    st = SimpleNamespace(
        forges=ForgesFile(accounts={"corp": account}),
        templates_dir=tmp_path,
        instance_policy=SimpleNamespace(sandbox=None),
        run_dirs=SimpleNamespace(worktrees=tmp_path / "wt"),
        forge_probes={},
    )
    got = await probe.refresh(st)
    assert got["corp"].state == "no_token" and "repos.yaml" in got["corp"].detail
    assert canned.CALLS == []
