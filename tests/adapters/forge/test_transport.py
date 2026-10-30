"""The transport seam (`adapters/forge/transport.py`): reading `api -i` output,
mapping it to a `ForgeError`, and building the argv."""

from __future__ import annotations

import asyncio

import pytest
from support.harness import git

from kraft.adapters import forge
from kraft.adapters.forge import transport

from .nodes import Api


def rest(cli_name, path, repo, method="GET", **kw):
    return asyncio.run(transport.CliTransport(cli_name, repo).rest(method, path, **kw))


def gql(cli_name, repo, query="query { x }", **kw):
    return asyncio.run(transport.CliTransport(cli_name, repo).graphql(query, **kw))


@pytest.mark.parametrize(
    ("stdout", "status", "header", "body"),
    [
        (
            'HTTP/2.0 200 OK\nContent-Type: application/json\n\n{"a": 1}',
            200,
            ("content-type", "application/json"),
            '{"a": 1}',
        ),
        (
            "HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 201 Created\r\nX-A: b\r\n\r\n{}",
            201,
            ("x-a", "b"),
            "{}",
        ),
        ("HTTP/1.1 502 Bad Gateway\n\n<html>oops</html>", 502, None, "<html>oops</html>"),
        (
            "HTTP/2.0 200 OK\nContent-Type: text/plain\n\nintro\n\nHTTP/1.1 404 Not Found\nmore",
            200,
            ("content-type", "text/plain"),
            "intro\n\nHTTP/1.1 404 Not Found\nmore",
        ),
    ],
    ids=["http2", "continue-crlf", "html-502", "body-quotes-a-status-line"],
)
def test_parse_included(stdout, status, header, body):
    got = transport.parse_included(stdout)
    assert (got.status, got.text) == (status, body)
    assert header is None or got.headers[header[0]] == header[1]


def test_parse_included_without_a_status_line_is_none():
    assert transport.parse_included("To get started, run: gh auth login") is None


def test_a_non_json_body_raises_with_its_status():
    with pytest.raises(forge.ForgeError, match="HTTP 502.*<html>") as exc:
        transport.parse_included("HTTP/1.1 502 Bad Gateway\n\n<html>oops</html>").json()
    assert (exc.value.status, exc.value.reason) == (502, "unreachable")


@pytest.mark.parametrize("rc", [0, 1], ids=["exit-0", "exit-1"])
def test_graphql_errors_raise_with_their_reason_whatever_the_exit_code(cli, tmp_path, rc):
    body = '{"data": null, "errors": [{"type": "NOT_FOUND", "message": "no such PR"}]}'
    cli.stub("gh", routes={"api graphql": Api(200, body, rc=rc)})
    with pytest.raises(forge.ForgeError, match="no such PR") as exc:
        gql("gh", tmp_path)
    assert exc.value.reason == "not_found"


@pytest.mark.parametrize(
    ("errors", "reason"),
    [
        ([{"type": "FORBIDDEN"}], "forbidden"),
        ([{"type": "RATE_LIMITED"}], "rate_limited"),
        ([{"message": "Expected head oid X did not match"}], None),
        ([{"message": "Head branch was modified. Review and try the merge again."}], "head_moved"),
        ([{"message": "expectedHeadOid does not match"}], "head_moved"),
        ([{"message": "something else"}], None),
    ],
    ids=[
        "forbidden",
        "rate-limited",
        "oid-without-key",
        "modified",
        "expected-head-oid",
        "unmapped",
    ],
)
def test_graphql_reason(errors, reason):
    assert transport.graphql_reason(errors) == reason


def test_a_graphql_success_returns_its_data(cli, tmp_path):
    cli.stub("gh", routes={"api graphql": '{"data": {"viewer": 1}}'})
    assert gql("gh", tmp_path) == {"viewer": 1}


def test_a_failed_call_names_method_and_path_not_argv(cli, tmp_path):
    cli.stub("gh", routes={"api *": Api(409, '{"message": "Head branch was modified"}')})
    with pytest.raises(forge.ForgeError) as exc:
        rest("gh", "repos/{owner}/{repo}/pulls/7/merge", tmp_path, "PUT", fields={"sha": "abc"})
    text = str(exc.value)
    assert text.startswith("PUT repos/{owner}/{repo}/pulls/7/merge: Head branch was modified")
    assert "sha=" not in text
    assert (exc.value.status, exc.value.reason) == (409, "conflict")


def test_no_request_made_carries_no_reason(cli, tmp_path):
    login = "To get started with GitHub CLI, please run:  gh auth login"
    cli.stub("gh", routes={"api *": "x"}, default="x")
    (cli.bindir / "gh").write_text(f"#!/bin/sh\necho '{login}' >&2\nexit 4\n")
    with pytest.raises(forge.ForgeError, match="gh auth login") as exc:
        rest("gh", "user", tmp_path)
    assert (exc.value.status, exc.value.reason) == (None, None)
    assert forge.failure_cause(str(exc.value)) == "forge_auth"


def test_rate_limited_403_carries_retry_after_and_the_sso_url(cli, tmp_path):
    headers = {"retry-after": "30", "x-github-sso": "required; url=https://github.com/sso?x=1"}
    cli.stub("gh", routes={"api *": Api(403, '{"message": "slow down"}', headers)})
    with pytest.raises(forge.ForgeError) as exc:
        rest("gh", "user", tmp_path)
    assert (exc.value.reason, exc.value.retry_after) == ("rate_limited", 30.0)
    assert exc.value.sso_url == "https://github.com/sso?x=1"


def test_a_spent_quota_waits_until_the_reset_the_forge_names(cli, tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "_now", lambda: 1_000_000.0)
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1000600"}
    cli.stub("gh", routes={"api *": Api(403, '{"message": "rate limit exceeded"}', headers)})
    with pytest.raises(forge.ForgeError) as exc:
        rest("gh", "user", tmp_path)
    assert (exc.value.reason, exc.value.retry_after) == ("rate_limited", 600.0)


def test_an_html_error_body_is_clipped_not_a_traceback(cli, tmp_path):
    cli.stub("gh", routes={"api *": Api(502, "<html>" + "x" * 5000)})
    with pytest.raises(forge.ForgeError) as exc:
        rest("gh", "user", tmp_path)
    assert exc.value.reason == "unreachable"
    assert len(str(exc.value)) < 400


@pytest.mark.parametrize(
    ("fields", "flags"),
    [
        ({"title": "@file"}, ["-f", "title=@file"]),
        ({"n": 7}, ["-F", "n=7"]),
        ({"draft": True}, ["-F", "draft=true"]),
        ({"owner": "{owner}"}, ["-F", "owner={owner}"]),
        ({"sha": "123"}, ["-f", "sha=123"]),
    ],
    ids=["str-raw", "int-typed", "bool-typed", "placeholder-typed", "numeric-string-stays-raw"],
)
def test_field_typing(cli, tmp_path, fields, flags):
    cli.stub("gh", "{}")
    rest("gh", "repos/{owner}/{repo}/pulls", tmp_path, "POST", fields=fields)
    argv = cli.argv("gh")
    assert argv[:5] == ["api", "repos/{owner}/{repo}/pulls", "-X", "POST", "-i"]
    assert argv[5:] == flags


@pytest.mark.parametrize(
    ("origin", "ssh_says", "host_args"),
    [
        ("https://github.example.com/a/b.git", None, ["--hostname", "github.example.com"]),
        ("git@ghe.corp:a/b.git", "ghe.corp", ["--hostname", "ghe.corp"]),
        ("https://github.com/a/b.git", None, []),
        (None, None, []),
        # An ssh alias is whatever `ssh -G` resolves it to, as gh reads it.
        ("git@github-work:o/r.git", "github.com", []),
        ("git@ghe-alias:a/b.git", "github.example.com", ["--hostname", "github.example.com"]),
        ("git@ssh.github.com:o/r.git", None, []),
        ("ssh://git@ssh.github.com:443/o/r.git", None, []),
    ],
    ids=[
        "https-enterprise",
        "scp-enterprise",
        "github-com",
        "no-origin",
        "alias-of-github-com",
        "alias-of-enterprise",
        "ssh-over-443-scp",
        "ssh-over-443-url",
    ],
)
def test_gh_names_the_enterprise_host(cli, repo, origin, ssh_says, host_args):
    if origin:
        git(repo, "remote", "add", "origin", origin)
    if ssh_says:
        cli.stub("ssh", f"hostname {ssh_says}")
    cli.stub("gh", "{}")
    rest("gh", "user", repo)
    assert cli.argv("gh")[5:] == host_args


def test_glab_never_gets_a_hostname(cli, repo):
    git(repo, "remote", "add", "origin", "https://gitlab.example.com/a/b.git")
    cli.stub("glab", "{}")
    rest("glab", "projects/1", repo)
    assert "--hostname" not in cli.argv("glab")


def test_stub_answers_an_included_call(cli, tmp_path):
    cli.stub("gh", routes={"api*pulls/7*": '{"number": 7}'})
    got = rest("gh", "repos/{owner}/{repo}/pulls/7", tmp_path)
    assert (got.status, got.json()) == (200, {"number": 7})
    bare = asyncio.run(forge.run_git(tmp_path, ["sh", "-c", "$0 api repos/x/pulls/7", "gh"]))
    assert bare.strip() == '{"number": 7}'


def test_an_error_status_raises_even_when_the_cli_exits_zero(cli, tmp_path):
    cli.stub("gh", routes={"api *": Api(404, '{"message": "Not Found"}', rc=0)})
    with pytest.raises(forge.ForgeError) as exc:
        rest("gh", "user", tmp_path)
    assert exc.value.reason == "not_found"
