"""The builder and resolver for git over HTTPS with an API account's token."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import certifi
import pytest
from support.harness import git, make_repo

from kraft import config
from kraft.adapters.forge import credentials, git_auth
from kraft.adapters.forge.git_auth import GitAuth, git_env, remote_env
from kraft.adapters.forge.models import ForgeError
from kraft.config import RepoEntry
from kraft.forges import ForgeAccount, ForgesFile, TokenSource
from kraft.worker import sandbox

TOK = "tok-0123456789"
WEB = "https://code.corp.example"
SSH = "git@code.corp.example:team/app.git"
HTTPS = "https://code.corp.example/team/app.git"


def acct(
    web=WEB, *, client="api", forge="gitlab", default=False, ca_file=None, **token
) -> ForgeAccount:
    token = token or {"from": "env", "env": "CORP_GL", "kind": "pat"}
    return ForgeAccount(
        forge=forge,
        client=client,
        web_url=web,
        default_for_host=default,
        ca_file=ca_file,
        token=TokenSource(**token) if client == "api" else None,
    )


def form(token=TOK, forge="gitlab") -> str:
    return credentials.basic_form(token, "pat", forge=forge)


def rig(
    tmp_path,
    monkeypatch,
    *,
    remote=SSH,
    config_lines=(),
    accounts=None,
    entry=None,
    entry_fields=None,
    reader=True,
    loaded=True,
    token=True,
) -> tuple[Path, list[str]]:
    """A repo on `remote` with an installed reader, and the git calls made after this."""
    repo = make_repo(tmp_path)
    git(repo, "remote", "add", "origin", remote)
    for args in config_lines:
        git(repo, "config", *args)
    if token:
        monkeypatch.setenv("CORP_GL", TOK)
        credentials.take_env(["CORP_GL"])
    accounts = {"corp-gl": acct()} if accounts is None else accounts
    fields = entry_fields or {"forge": "gitlab", "forge_account": "corp-gl"}
    entries = [entry or RepoEntry(path=str(repo), **fields)]
    forges = ForgesFile(accounts=accounts) if loaded else None

    async def read():
        return entries, forges

    git_auth.set_reader(read if reader else None)
    calls: list[str] = []
    real_read, real_run = config.git_read, subprocess.run
    monkeypatch.setattr(
        config, "git_read", lambda *a, **k: (calls.append("read"), real_read(*a, **k))[1]
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (calls.append("run"), real_run(*a, **k))[1]
    )
    return repo, calls


CA = "/ca/default.pem"


def auth(origin=WEB, header=None, urls=(), ca_info=CA, ca_path="") -> GitAuth:
    return GitAuth(
        "corp-gl",
        acct(),
        origin,
        header or f"Authorization: Basic {form()}",
        urls,
        ca_info,
        ca_path,
    )


def pins(env) -> list[tuple[str, str]]:
    return [
        (env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    ]


KEY = f"http.{WEB}/."
HEADER = "Authorization: Basic " + form()
HARD = sandbox.hardened_git_env()
PROXY = next((HARD[k] for k in git_auth._PROXY_VARS if HARD.get(k)), "")
URL = f"{WEB}/team/app.git"


def expected(*targets: str, ca: str = CA) -> list[tuple[str, str]]:
    """The pins after the hardening ones: the helper reset, then eight per origin or exact URL."""
    out = [("credential.helper", ""), ("remote.origin.proxy", PROXY)]
    for t in targets:
        out += [
            (f"http.{t}extraHeader", ""),
            (f"http.{t}extraHeader", HEADER),
            (f"http.{t}sslVerify", "true"),
            (f"http.{t}followRedirects", "false"),
            (f"http.{t}proxy", PROXY),
            (f"http.{t}sslCAPath", ""),
            (f"http.{t}curloptResolve", ""),
        ]
        out.append((f"http.{t}sslCAInfo", ca))
    return out


PINS = expected(f"{WEB}/.")
NOISE = {
    k: "1"
    for k in (
        "GIT_TRACE",
        "GIT_TRACE_CURL",
        "GIT_TRACE2_EVENT",
        "GIT_TRACE_REDACT",
        "GIT_CURL_VERBOSE",
        "GIT_TRANSFER_TRACE",
        "GIT_SSL_NO_VERIFY",
    )
}


@pytest.mark.parametrize(
    ("base", "built", "check"),
    [
        pytest.param(HARD, auth(), lambda e: pins(e) == pins(HARD) + PINS, id="appended"),
        pytest.param(
            HARD,
            auth(urls=(URL,)),
            lambda e: pins(e) == pins(HARD) + expected(f"{WEB}/.", f"{URL}."),
            id="every-dialed-url-is-pinned-too",
        ),
        pytest.param(
            HARD,
            auth(ca_info="/ca/corp.pem", ca_path="/ca/dir"),
            lambda e: (
                ("http.https://code.corp.example/.sslCAInfo", "/ca/corp.pem") in pins(e)
                and ("http.https://code.corp.example/.sslCAPath", "/ca/dir") in pins(e)
            ),
            id="trust-is-pinned-at-the-origin",
        ),
        pytest.param(
            HARD,
            auth(urls=(URL,), ca_info="/ca/corp.pem"),
            lambda e: pins(e) == pins(HARD) + expected(f"{WEB}/.", f"{URL}.", ca="/ca/corp.pem"),
            id="trust-is-pinned-at-every-dialed-url",
        ),
        pytest.param(
            {**HARD, "GIT_CONFIG_PARAMETERS": "'http.x.sslverify=false'"},
            auth(),
            lambda e: "GIT_CONFIG_PARAMETERS" not in e,
            id="config-parameters-stripped",
        ),
        pytest.param(
            {**HARD, "https_proxy": "http://proxy.example:3128"},
            auth(),
            lambda e: (
                ("http.https://code.corp.example/.proxy", "http://proxy.example:3128") in pins(e)
            ),
            id="proxy-is-pinned-to-the-environments-own",
        ),
        pytest.param(
            {**HARD, "https_proxy": "", "HTTPS_PROXY": "", "ALL_PROXY": "socks5://p.example:1080"},
            auth(),
            lambda e: (
                ("remote.origin.proxy", "socks5://p.example:1080") in pins(e)
                and ("http.https://code.corp.example/.proxy", "socks5://p.example:1080") in pins(e)
            ),
            id="all-proxy-is-the-fallback",
        ),
        pytest.param(
            {**HARD, "https_proxy": "http://proxy.example:3128"},
            auth(),
            lambda e: ("remote.origin.proxy", "http://proxy.example:3128") in pins(e),
            id="remote-origin-proxy-is-pinned",
        ),
        pytest.param(
            HARD,
            auth(),
            lambda e: (
                (e["GIT_CONFIG_COUNT"], e["GIT_CONFIG_KEY_0"], e["GIT_CONFIG_VALUE_0"])
                == (str(len(pins(HARD)) + len(PINS)), "core.hooksPath", os.devnull)
            ),
            id="hooks-pin-stays-first",
        ),
        pytest.param(
            {"PATH": "/bin", "https_proxy": PROXY},
            auth(),
            lambda e: pins(e) == pins(HARD) + PINS,
            id="unhardened-base-is-hardened-first",
        ),
        pytest.param(
            {**HARD, **NOISE},
            auth(),
            lambda e: (
                not [k for k in e if k.startswith("GIT_TRACE") and e[k] != "0"]
                and not {"GIT_CURL_VERBOSE", "GIT_TRANSFER_TRACE", "GIT_SSL_NO_VERIFY"} & set(e)
            ),
            id="trace-and-no-verify-removed",
        ),
        pytest.param(
            {**HARD, "GIT_TRACE2": "/tmp/x"},
            auth(),
            lambda e: (
                (e["GIT_TRACE2"], e["GIT_TRACE2_EVENT"], e["GIT_TRACE2_PERF"]) == ("0", "0", "0")
            ),
            id="trace2-switched-off",
        ),
        pytest.param(
            HARD,
            auth(),
            lambda e: (
                (e["GIT_ASKPASS"], e["SSH_ASKPASS"], e["GIT_TERMINAL_PROMPT"]) == ("", "", "0")
            ),
            id="askpass-emptied",
        ),
    ],
)
def test_the_header_is_appended_after_the_hardening_pins(base, built, check):
    assert check(git_env(base, built))


def test_no_auth_is_a_copy_of_the_base():
    assert git_env(HARD, None) == HARD and git_env(HARD, None) is not HARD


@pytest.mark.parametrize(
    ("web", "forge", "origin", "header"),
    [
        pytest.param(
            "https://CODE.corp.example:8443/gitlab",
            "gitlab",
            "https://code.corp.example:8443",
            None,
            id="key-is-the-origin-only",
        ),
        pytest.param(
            "https://github.example", "github", "https://github.example", "github", id="github-form"
        ),
    ],
)
async def test_an_account_keys_its_header_on_its_origin(
    tmp_path, monkeypatch, web, forge, origin, header
):
    host = web.split("/")[2].lower()
    repo, _ = rig(tmp_path, monkeypatch, remote=f"https://{host}/team/app.git")
    account = acct(web, forge=forge)
    got = await git_auth.auth_for_account(repo, "corp-gl", account, push=True)
    assert got is not None and got.origin == origin
    assert got.header == f"Authorization: Basic {form(forge=header or 'gitlab')}"


def test_the_builder_leaves_its_base_and_os_environ_alone():
    base, before, environ = {**HARD, **NOISE}, {**HARD, **NOISE}, dict(os.environ)
    git_env(base, auth())
    assert (base, dict(os.environ)) == (before, environ)


@pytest.mark.parametrize(
    ("setup", "no_git"),
    [
        pytest.param(dict(reader=False), True, id="no-reader"),
        pytest.param(dict(loaded=False), True, id="no-forges-yaml"),
        pytest.param(dict(accounts={"c": acct(client="cli")}), True, id="cli-accounts-only"),
        pytest.param(
            dict(accounts={"corp-gl": acct(**{"from": "git-credential"})}),
            True,
            id="api-git-credential-only",
        ),
        pytest.param(dict(), False, id="api-env-ssh-remote"),
        pytest.param(dict(remote="/nonexistent/bare.git"), False, id="api-env-local-path-remote"),
        pytest.param(dict(entry=RepoEntry(path="/elsewhere")), False, id="repo-not-connected"),
    ],
)
async def test_an_existing_user_gets_the_same_git_env(tmp_path, monkeypatch, setup, no_git):
    repo, calls = rig(tmp_path, monkeypatch, **setup)
    base = {**os.environ, "X": "1"}
    assert await remote_env(repo, base) == base
    assert calls == [] or not no_git


@pytest.mark.parametrize(
    ("remote", "extra", "push", "want"),
    [
        pytest.param(HTTPS, (), True, "carries", id="https-same-origin-carries"),
        pytest.param(
            HTTPS,
            (("remote.origin.pushurl", "https://other.example/team/app.git"),),
            True,
            "refused",
            id="push-elsewhere-refused",
        ),
        pytest.param(
            HTTPS,
            (
                ("remote.origin.pushurl", "https://code.corp.example/team/app.git"),
                ("--add", "remote.origin.pushurl", "https://other.example/team/app.git"),
            ),
            True,
            "refused",
            id="push-second-pushurl-elsewhere-refused",
        ),
        pytest.param(
            HTTPS,
            (("remote.origin.pushurl", "https://code.corp.example:8443/team/app.git"),),
            True,
            "refused",
            id="push-other-port-refused",
        ),
        pytest.param(
            HTTPS,
            (("url.https://other.example/.pushInsteadOf", "https://code.corp.example/"),),
            True,
            "refused",
            id="push-insteadof-elsewhere-refused",
        ),
        pytest.param(
            HTTPS,
            (("remote.origin.pushurl", "http://code.corp.example/team/app.git"),),
            True,
            "refused",
            id="push-http-refused",
        ),
        pytest.param(
            "https://other.example/team/app.git", (), False, "none", id="fetch-elsewhere-no-header"
        ),
        pytest.param(
            "https://CODE.corp.example:443/team/app.git",
            (),
            False,
            "carries",
            id="origin-is-folded",
        ),
        pytest.param(
            HTTPS,
            (("remote.origin.pushurl", HTTPS), ("--add", "remote.origin.pushurl", SSH)),
            True,
            "refused",
            id="push-https-next-to-ssh-refused",
        ),
        pytest.param(
            HTTPS,
            (("remote.origin.pushurl", HTTPS), ("--add", "remote.origin.pushurl", "foo::bar")),
            True,
            "refused",
            id="push-https-next-to-a-remote-helper-refused",
        ),
        pytest.param(
            "foo::https://code.corp.example/team/app.git",
            (),
            False,
            "none",
            id="helper-url-fetch-no-header",
        ),
        pytest.param(
            "foo::https://code.corp.example/team/app.git",
            (),
            True,
            "none",
            id="helper-url-push-no-header",
        ),
        pytest.param(
            HTTPS,
            (("remote.origin.pushurl", SSH),),
            True,
            "none",
            id="ssh-push-url-no-header",
        ),
    ],
)
async def test_the_dialed_url_decides(tmp_path, monkeypatch, remote, extra, push, want):
    repo, _ = rig(tmp_path, monkeypatch, remote=remote, config_lines=extra)
    name, account = "corp-gl", acct()
    if want == "refused":
        with pytest.raises(ForgeError) as caught:
            await git_auth.auth_for_account(repo, name, account, push=push)
        text = str(caught.value)
        assert "corp-gl" in text and WEB in text and "other.example" not in text
        assert "8443" not in text.replace(WEB, "")
        return
    got = await git_auth.auth_for_account(repo, name, account, push=push)
    assert (got is not None) == (want == "carries")
    if got:
        assert (got.origin, got.header) == (WEB, f"Authorization: Basic {form()}")


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param("git-credential", id="git-credential-account-pushes-with-no-header"),
        pytest.param("cli", id="a-cli-backend-pushes-with-no-header"),
    ],
)
async def test_a_push_the_account_does_not_inject_runs_no_git(tmp_path, monkeypatch, kind):
    repo, calls = rig(tmp_path, monkeypatch, remote=HTTPS)
    account = acct(client="cli") if kind == "cli" else acct(**{"from": "git-credential"})
    assert await git_auth.auth_for_account(repo, "corp-gl", account, push=True) is None
    assert calls == []


async def test_an_installed_reader_is_not_needed_for_a_pinned_push(tmp_path, monkeypatch):
    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS, reader=False)
    got = await git_auth.auth_for_account(repo, "corp-gl", acct(), push=True)
    assert got is not None and got.origin == WEB


async def test_a_member_linked_worktree_uses_its_own_account(tmp_path, monkeypatch):
    root = make_repo(tmp_path, "root")
    member = make_repo(tmp_path, "member")
    git(member, "remote", "add", "origin", "https://other.example/team/m.git")
    linked = tmp_path / "linked"
    git(member, "worktree", "add", "-b", "w", str(linked))
    monkeypatch.setenv("OTHER_GL", "other-0123456789")
    credentials.take_env(["OTHER_GL"])
    other = acct("https://other.example", **{"from": "env", "env": "OTHER_GL", "kind": "pat"})

    async def read():
        return (
            [
                RepoEntry(path=str(root)),
                RepoEntry(path=str(member), forge="gitlab", forge_account="other-gl"),
            ],
            ForgesFile(accounts={"corp-gl": acct(), "other-gl": other}),
        )

    git_auth.set_reader(read)
    got = await git_auth.auth_for(linked)
    assert got is not None and (got.name, got.origin) == ("other-gl", "https://other.example")


@pytest.mark.parametrize(
    "entry_id",
    [
        pytest.param("root", id="root-with-an-id-and-no-forge"),
        pytest.param("member", id="member-with-no-forge"),
        pytest.param(None, id="legacy-entry-without-forge"),
    ],
)
async def test_an_entry_without_a_forge_never_takes_a_host_default(tmp_path, monkeypatch, entry_id):
    """As `run.api_account_for`: its backend stays the CLI, so git keeps its own credential
    (no header, no helper reset) even with a `default_for_host` API account on its host."""
    repo, _ = rig(
        tmp_path,
        monkeypatch,
        remote=HTTPS,
        accounts={"corp-gl": acct(default=True)},
        entry_fields={"id": entry_id},
    )
    base = {**os.environ, "X": "1"}
    assert await git_auth.auth_for(repo) is None
    assert await remote_env(repo, base) == base


async def test_an_unreadable_token_is_an_auth_error(tmp_path, monkeypatch):
    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS, token=False)
    with pytest.raises(ForgeError) as caught:
        await git_auth.auth_for(repo)
    exc = caught.value
    assert (exc.reason, exc.account, exc.source) == ("auth", "corp-gl", "env CORP_GL")
    assert "set it there and restart" in str(exc)


@pytest.mark.parametrize(
    ("text", "stamped", "forge"),
    [
        pytest.param(
            "fatal: Authentication failed for 'x'", "auth", "gitlab", id="authentication-failed"
        ),
        pytest.param(
            "fatal: could not read Username for 'x'", "auth", "gitlab", id="could-not-read-username"
        ),
        pytest.param("The requested URL returned error: 401", "auth", "gitlab", id="returned-401"),
        pytest.param(
            "The requested URL returned error: 403", "forbidden", "gitlab", id="returned-403"
        ),
        pytest.param(
            "fatal: unable to access: Connection refused", None, "gitlab", id="connection-refused"
        ),
        pytest.param(
            "fatal: repository 'https://github.com/o/r/' not found",
            "auth",
            "github",
            id="github-not-found-is-a-token-problem",
        ),
        pytest.param(
            "fatal: repository 'https://gitlab.example/o/r/' not found",
            None,
            "gitlab",
            id="gitlab-not-found-is-not",
        ),
        pytest.param(
            "The requested URL returned error: 302", "auth", "gitlab", id="redirect-is-stamped"
        ),
    ],
)
def test_a_git_auth_stamps_only_an_auth_failure(text, stamped, forge):
    exc = GitAuth("corp-gl", acct(forge=forge), WEB, "h").stamp(ForgeError(text))
    assert exc.reason == stamped
    assert exc.account == ("corp-gl" if stamped else "")
    assert exc.source == ("env CORP_GL" if stamped else "")
    if "error: 302" in text:
        assert "origin redirects elsewhere" in str(exc) and "then retry" in str(exc)


@pytest.mark.parametrize(
    ("accounts", "loads"),
    [
        pytest.param({"c": acct(client="cli")}, 0, id="no-api-account-reads-no-repos"),
        pytest.param(None, 0, id="no-forges-reads-no-repos"),
        pytest.param({"c": acct()}, 1, id="env-account-reads-repos"),
    ],
)
async def test_the_server_reads_repos_yaml_only_for_an_account_that_could_carry_a_header(
    tmp_path, monkeypatch, accounts, loads
):
    seen = []
    monkeypatch.setattr(config, "load_repos", lambda p: seen.append(p) or [])
    forges = None if accounts is None else ForgesFile(accounts=accounts)
    st = SimpleNamespace(forges=forges, templates_dir=tmp_path)
    assert await git_auth.server_context(st) == ([], forges)
    assert len(seen) == loads


def test_a_git_auth_repr_shows_neither_the_token_nor_its_basic_form():
    text = repr(auth())
    assert TOK not in text and form() not in text and "Basic" not in text


@pytest.mark.parametrize(
    ("web", "key"),
    [
        pytest.param("https://[::1]:8443", "https://[::1]:8443", id="ipv6-is-bracketed"),
        pytest.param("https://[::1]", "https://[::1]", id="ipv6-on-443"),
    ],
)
def test_an_ipv6_origin_is_bracketed(web, key):
    assert git_auth._origin(acct(web)) == key


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("fatal: ... The requested URL returned error: 403", "forbidden", id="403"),
        pytest.param("fatal: ... The requested URL returned error: 401", "auth", id="401"),
    ],
)
async def test_a_refused_base_fetch_names_its_reason(tmp_path, monkeypatch, text, reason):
    from kraft import builtins

    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS)
    real = subprocess.run

    def fake(args, *a, **k):
        if "fetch" in args:
            return subprocess.CompletedProcess(args, 128, "", text)
        return real(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", fake)
    with pytest.raises(builtins.BaseFetchRefused) as caught:
        await builtins.upstream_head(repo, "main")
    assert (caught.value.reason, caught.value.account) == (reason, "corp-gl")


async def test_an_unreadable_repos_yaml_is_read_as_a_config_problem_by_the_server(tmp_path):
    (tmp_path / "repos.yaml").write_text("repos: [not: a list")
    st = SimpleNamespace(forges=ForgesFile(accounts={"corp-gl": acct()}), templates_dir=tmp_path)
    entries, _ = await git_auth.server_context(st)
    assert isinstance(entries, git_auth.Unread) and entries.why


@pytest.mark.parametrize(
    ("remote", "stops"),
    [
        pytest.param(HTTPS, True, id="https-on-an-accounts-host-stops"),
        pytest.param(SSH, False, id="ssh-remote-keeps-running"),
        pytest.param(
            "https://other.example/t/a.git", False, id="https-on-another-host-keeps-running"
        ),
        pytest.param(
            "https://code.corp.example:8443/t/a.git", False, id="same-host-other-port-keeps-running"
        ),
    ],
)
async def test_an_unreadable_repos_yaml_stops_only_a_remote_that_would_carry_a_header(
    tmp_path, monkeypatch, remote, stops
):
    from functools import partial

    repo, _ = rig(tmp_path, monkeypatch, remote=remote, reader=False)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "repos.yaml").write_text("repos: [not: a list")
    st = SimpleNamespace(forges=ForgesFile(accounts={"corp-gl": acct()}), templates_dir=cfg)
    git_auth.set_reader(partial(git_auth.server_context, st))
    base = {**os.environ, "X": "1"}
    if stops:
        with pytest.raises(ForgeError) as caught:
            await remote_env(repo, base)
        text = str(caught.value)
        assert text.startswith("repos.yaml does not load (") and text.endswith(
            "; fix it, then retry"
        )
        assert (caught.value.reason, caught.value.account) == (None, "")
        assert "`" not in text
    else:
        assert await remote_env(repo, base) == base


@pytest.mark.parametrize("push", [pytest.param(False, id="fetch"), pytest.param(True, id="push")])
async def test_a_remote_helper_on_origin_is_refused_before_any_git_carries_the_header(
    tmp_path, monkeypatch, push
):
    """`remote.origin.vcs` would start `git-remote-<vcs>` with the header env: a fake one on
    PATH records its env and must never run."""
    ran = tmp_path / "helper-ran"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    helper = bindir / "git-remote-evil"
    helper.write_text(f"#!/bin/sh\nenv >> {ran}\nexit 1\n")
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS, config_lines=[("remote.origin.vcs", "evil")])
    with pytest.raises(ForgeError) as caught:
        await git_auth.auth_for_account(repo, "corp-gl", acct(), push=push)
    text = str(caught.value)
    assert "corp-gl" in text and "remote helper" in text and "evil" not in text
    assert "`" not in text and not ran.exists()
    git_auth.set_reader(lambda: _reader_of(repo))
    with pytest.raises(ForgeError, match="remote helper"):
        await git_auth.auth_for(repo)


async def _reader_of(repo):
    return [RepoEntry(path=str(repo), forge="gitlab", forge_account="corp-gl")], ForgesFile(
        accounts={"corp-gl": acct()}
    )


@pytest.fixture
def neutral_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in ("XDG_CONFIG_HOME", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH", "GIT_CONFIG_GLOBAL"):
        monkeypatch.delenv(k, raising=False)
    return home


@pytest.mark.parametrize(
    ("ca_file", "env", "gitconfig", "want"),
    [
        pytest.param("/acct.pem", "/env.pem", "/global.pem", ("/acct.pem", ""), id="account-first"),
        pytest.param(None, "/env.pem", "/global.pem", ("/env.pem", ""), id="environment-next"),
        pytest.param(None, None, "/global.pem", ("/global.pem", ""), id="global-gitconfig-last"),
        pytest.param(None, None, None, (certifi.where(), ""), id="certifi-when-nothing-else"),
    ],
)
def test_trust_comes_from_sources_a_repo_cannot_write(
    neutral_home, monkeypatch, ca_file, env, gitconfig, want
):
    if gitconfig:
        (neutral_home / ".gitconfig").write_text(f'[http "{WEB}/"]\n\tsslCAInfo = {gitconfig}\n')
    if env:
        monkeypatch.setenv("GIT_SSL_CAINFO", env)
    assert git_auth._trust(acct(ca_file=ca_file)) == want


async def test_a_repos_own_ca_setting_is_never_the_trust_pin(tmp_path, monkeypatch, neutral_home):
    repo, _ = rig(
        tmp_path,
        monkeypatch,
        remote=HTTPS,
        config_lines=[("http.sslCAInfo", "/planted.pem"), (f"http.{WEB}/.sslCAPath", "/planted")],
    )
    got = await git_auth.auth_for_account(repo, "corp-gl", acct(), push=False)
    assert got is not None and "planted" not in f"{got.ca_info} {got.ca_path}"


@pytest.mark.parametrize("user", ["git@", "git:pw@"])
@pytest.mark.parametrize("push", [False, True], ids=["fetch", "push"])
async def test_a_url_that_names_a_user_is_refused_without_quoting_it(
    tmp_path, monkeypatch, user, push
):
    repo, _ = rig(tmp_path, monkeypatch, remote=f"https://{user}code.corp.example/team/app.git")
    with pytest.raises(ForgeError, match="names a user; remove it") as caught:
        await git_auth.auth_for_account(repo, "corp-gl", acct(), push=push)
    assert "git@" not in str(caught.value) and "pw" not in str(caught.value)
    assert "`" not in str(caught.value)
