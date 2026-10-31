"""HttpTransport against `httpx.MockTransport` only: nothing here dials a host."""

from __future__ import annotations

import asyncio
import ssl
import subprocess

import certifi
import httpx
import pytest

from kraft import redact
from kraft.adapters.forge import credentials
from kraft.adapters.forge.http import HttpTransport, graphql_url
from kraft.adapters.forge.models import ForgeError
from kraft.forges import ForgeAccount

TOK = "tok-1234567890"
GH = "https://api.github.com"


def acct(forge="github", web="https://github.com", **kw) -> ForgeAccount:
    return ForgeAccount(forge=forge, client="api", web_url=web, **kw)


def tr(m, account=None, **kw) -> HttpTransport:
    return HttpTransport("corp", account or acct(), TOK, transport=m.transport, **kw)


def redirect(to, status=302):
    return (status, None, {"Location": to})


@pytest.mark.parametrize(
    "account, url",
    [
        pytest.param(acct(), f"{GH}/user", id="github-com"),
        pytest.param(
            acct("gitlab", "https://code.corp.example/gitlab"),
            "https://code.corp.example/gitlab/api/v4/user",
            id="gitlab-prefix",
        ),
        pytest.param(
            acct(web="https://ghe.corp.example:8443"),
            "https://ghe.corp.example:8443/api/v3/user",
            id="port-kept",
        ),
    ],
)
async def test_a_request_carries_bearer_to_the_accounts_api_origin(mock_http, account, url):
    m = mock_http({("GET", url): (200, {})})
    await tr(m, account).rest("GET", "user")
    (req,) = m.requests
    assert req.headers["authorization"] == f"Bearer {TOK}"
    assert req.headers["user-agent"].startswith("kraft/")
    assert (req.headers["accept"] == "application/vnd.github+json") == (account.forge == "github")


@pytest.mark.parametrize(
    "forge, web, ok",
    [("github", "https://github.com", False), ("gitlab", "https://code.corp.example/gl", True)],
)
async def test_the_web_base_is_gitlabs_alone(mock_http, forge, web, ok):
    account = acct(forge, web)
    if not ok:
        with pytest.raises(ValueError):
            tr(mock_http(), account, base="web")
        return
    m = mock_http({("GET", f"{web}/oauth/token/info"): (200, {})})
    await tr(m, account, base="web").rest("GET", "oauth/token/info")
    assert len(m.requests) == 1


@pytest.mark.parametrize(
    "method, first, sent, named",
    [
        pytest.param("GET", redirect(f"{GH}/b"), 2, None, id="same-origin-followed"),
        pytest.param(
            "GET",
            redirect("https://evil.example/b"),
            1,
            "https://evil.example",
            id="other-host-refused",
        ),
        pytest.param(
            "GET",
            redirect(f"{GH}:8443/b"),
            1,
            "https://api.github.com:8443",
            id="other-port-refused",
        ),
        pytest.param(
            "GET",
            redirect("http://api.github.com/b"),
            1,
            "http://api.github.com",
            id="http-downgrade-refused",
        ),
        pytest.param(
            "POST", redirect(f"{GH}/b", 307), 1, "https://api.github.com", id="on-a-post-refused"
        ),
        pytest.param("GET", "chain", 6, "https://api.github.com", id="more-than-five-refused"),
    ],
)
async def test_a_redirect_is_followed_only_within_the_origin(mock_http, method, first, sent, named):
    def handler(req):
        if first == "chain":
            n = int(req.url.path.removeprefix("/a") or 0)
            return httpx.Response(302, headers={"Location": f"{GH}/a{n + 1}"})
        if req.url.path == "/a":
            return httpx.Response(first[0], headers=first[2])
        return httpx.Response(200, json={})

    m = mock_http(handler=handler)
    if named is None:
        assert (await tr(m).rest("GET", "a")).status == 200
    else:
        with pytest.raises(ForgeError, match="not followed") as err:
            await tr(m).rest(method, "a")
        assert named in str(err.value) and err.value.reason == "unreachable"
    assert len(m.requests) == sent
    assert {r.headers["authorization"] for r in m.requests} == {f"Bearer {TOK}"}


@pytest.mark.parametrize(
    "path",
    ["https://evil.example/x", "//evil.example/x"],
    ids=["absolute-url", "protocol-relative"],
)
async def test_an_absolute_or_protocol_relative_path_is_refused(mock_http, path):
    m = mock_http()
    with pytest.raises(ForgeError):
        await tr(m).rest("GET", path)
    assert m.requests == []


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_fields_go_as_query_on_get_and_json_otherwise(mock_http, method):
    m = mock_http({(method, f"{GH}/x"): (200, {})})
    fields = {"n": 3, "on": True, "tags": ["a", "b"]}
    await tr(m).rest(method, "x", fields=fields)
    (req,) = m.requests
    if method == "GET":
        assert str(req.url.query, "ascii") == "n=3&on=true&tags%5B%5D=a&tags%5B%5D=b"
    else:
        assert req.read() == b'{"n":3,"on":true,"tags":["a","b"]}' and not req.url.query


async def test_a_netrc_for_the_host_never_replaces_the_bearer(mock_http, tmp_path, monkeypatch):
    rc = tmp_path / ".netrc"
    rc.write_text("machine api.github.com login me password other-secret\n")
    rc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(rc))
    monkeypatch.setenv("HOME", str(tmp_path))
    m = mock_http({("GET", f"{GH}/user"): (200, {})})
    await tr(m).rest("GET", "user")
    assert m.requests[0].headers["authorization"] == f"Bearer {TOK}"


@pytest.mark.parametrize(
    "answers, slept, sent, err",
    [
        pytest.param(
            [(429, {}, {"Retry-After": "2"}), 200], [2.0], 2, None, id="short-wait-retried"
        ),
        pytest.param(
            [(429, {}, {"Retry-After": "9999"})], [], 1, 9999, id="longer-than-timeout-raises"
        ),
        pytest.param([(429, {}, {"Retry-After": "1"})] * 2, [1.0], 2, 1, id="second-limit-raises"),
    ],
)
async def test_retry_after_within_the_timeout_is_slept_and_retried_once(
    mock_http, monkeypatch, answers, slept, sent, err
):
    waits = []

    async def sleep(s):
        waits.append(s)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    m = mock_http({("GET", f"{GH}/x"): answers})
    if err is None:
        assert (await tr(m).rest("GET", "x")).status == 200
    else:
        with pytest.raises(ForgeError) as e:
            await tr(m).rest("GET", "x")
        assert (e.value.reason, e.value.retry_after) == ("rate_limited", err)
    assert (waits, len(m.requests)) == (slept, sent)


@pytest.mark.parametrize(
    "answer, reason, status",
    [
        pytest.param((401, {"message": "Bad credentials"}), "auth", 401, id="401-auth"),
        pytest.param(
            (
                403,
                {"message": "SAML"},
                {"X-GitHub-SSO": "required; url=https://github.com/orgs/a/sso?x=1"},
            ),
            "forbidden",
            403,
            id="403-sso",
        ),
        pytest.param(
            (403, {"message": "no"}, {"x-ratelimit-remaining": "0"}),
            "rate_limited",
            403,
            id="403-rate-limit",
        ),
        pytest.param((404, {"message": "Not Found"}), "not_found", 404, id="404-not-found"),
        pytest.param((500, {"message": "oops"}), "unreachable", 500, id="500-unreachable"),
        pytest.param(
            httpx.ConnectError("boom"), "unreachable", None, id="connect-error-unreachable"
        ),
        pytest.param(
            httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed"),
            "tls",
            None,
            id="tls",
        ),
    ],
)
async def test_an_error_is_a_structured_forge_error(mock_http, answer, reason, status):
    m = mock_http({("GET", f"{GH}/x"): answer})
    with pytest.raises(ForgeError) as e:
        await tr(m).rest("GET", "x")
    err = e.value
    assert (err.reason, err.status) == (reason, status)
    assert len(m.requests) == 1
    if status:
        assert f"(HTTP {status})" in str(err)
    if reason == "tls":
        assert "set ca_file on forge account corp" in str(err) and "refused" not in str(err)
    if "SSO" in str(answer):
        assert err.sso_url == "https://github.com/orgs/a/sso?x=1"


async def test_an_error_body_echoing_the_token_is_redacted(mock_http):
    redact.register(TOK)
    m = mock_http({("GET", f"{GH}/x"): (401, {"message": f"bad token {TOK}"})})
    with pytest.raises(ForgeError) as e:
        await tr(m).rest("GET", "x")
    assert TOK not in str(e.value)


GRAPHQL_URLS = [
    pytest.param(acct(), f"{GH}/graphql", id="github-com"),
    pytest.param(
        acct(web="https://acme.ghe.com"), "https://api.acme.ghe.com/graphql", id="ghe-cloud"
    ),
    pytest.param(
        acct(web="https://ghe.corp.example"), "https://ghe.corp.example/api/graphql", id="ghes"
    ),
    pytest.param(
        acct("gitlab", "https://code.corp.example/gitlab"),
        "https://code.corp.example/gitlab/api/graphql",
        id="gitlab",
    ),
]


@pytest.mark.parametrize("account, url", GRAPHQL_URLS)
async def test_graphql_posts_to_its_endpoint(mock_http, account, url):
    m = mock_http({("POST", url): (200, {"data": {"ok": 1}})})
    assert graphql_url(account) == url
    assert await tr(m, account).graphql("{ a }", {"n": 1}) == {"ok": 1}
    assert m.requests[0].read() == b'{"query":"{ a }","variables":{"n":1}}'


async def test_graphql_errors_raise_with_their_reason(mock_http):
    errors = [{"type": "NOT_FOUND", "message": "Could not resolve"}]
    m = mock_http({("POST", f"{GH}/graphql"): (200, {"errors": errors})})
    with pytest.raises(ForgeError, match="Could not resolve") as e:
        await tr(m).graphql("{ a }")
    assert e.value.reason == "not_found"


class _Ctx(ssl.SSLContext):
    """A real client context that counts what is loaded into it and claims `cas` CAs."""

    cas = 0

    def cert_store_stats(self):
        return {"x509_ca": self.cas}

    def load_verify_locations(self, *a, **kw):
        self.loaded = [*getattr(self, "loaded", []), a]


@pytest.mark.parametrize(
    "case",
    ["ca-file", "git-sslcainfo", "system", "system-empty-adds-certifi", "ca-file-missing-is-tls"],
)
async def test_the_ca_comes_from_ca_file_then_git_then_the_system(
    mock_http, monkeypatch, tmp_path, case
):
    made, git_argv, seen = [], [], {}

    def create(*a, **kw):
        ctx = _Ctx(ssl.PROTOCOL_TLS_CLIENT)
        ctx.cas = 0 if case == "system-empty-adds-certifi" else 1
        made.append((kw, ctx))
        return ctx

    def git_read(argv, *, input=None, timeout):
        git_argv.append(argv)
        out = "/etc/corp/ca.pem\n" if case == "git-sslcainfo" else ""
        return subprocess.CompletedProcess(argv, 0 if out else 1, out, "")

    real = httpx.AsyncClient

    class Spy(real):
        def __init__(self, *a, **kw):
            seen.update(kw)
            super().__init__(*a, **kw)

    monkeypatch.setattr(ssl, "create_default_context", create)
    monkeypatch.setattr(credentials, "neutral_run", git_read)
    monkeypatch.setattr(httpx, "AsyncClient", Spy)
    ca = tmp_path / "corp.pem"
    if case != "ca-file-missing-is-tls":
        ca.write_text("x")
    m = mock_http({("GET", f"{GH}/x"): (200, {})})
    account = acct(ca_file=str(ca)) if case.startswith("ca-file") else acct()
    if case == "ca-file-missing-is-tls":
        # create() would accept anything: a file that does not exist must fail before it
        monkeypatch.setattr(ssl, "create_default_context", lambda *a, **kw: pytest.fail("built"))
        with pytest.raises(ForgeError, match="corp.pem") as e:
            await tr(m, account).rest("GET", "x")
        assert e.value.reason == "tls" and m.requests == []
        return
    await tr(m, account).rest("GET", "x")
    (kw, ctx) = made[0]
    assert len(made) == 1 and seen["verify"] is ctx
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert kw.get("cafile") == {"ca-file": str(ca), "git-sslcainfo": "/etc/corp/ca.pem"}.get(case)
    assert bool(git_argv) == (case != "ca-file")
    assert git_argv == [] or git_argv[0][-1] == GH
    assert getattr(ctx, "loaded", []) == (
        [(certifi.where(),)] if case == "system-empty-adds-certifi" else []
    )
