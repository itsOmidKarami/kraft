"""The builder and resolver for git over HTTPS with an API account's token."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

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


def acct(web=WEB, *, client="api", forge="gitlab", default=False, **token) -> ForgeAccount:
    token = token or {"from": "env", "env": "CORP_GL", "kind": "pat"}
    return ForgeAccount(
        forge=forge,
        client=client,
        web_url=web,
        default_for_host=default,
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
    entries = [entry or RepoEntry(path=str(repo), forge="gitlab", forge_account="corp-gl")]
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


def auth(origin=WEB, header=None) -> GitAuth:
    return GitAuth("corp-gl", acct(), origin, header or f"Authorization: Basic {form()}")


def pins(env) -> list[tuple[str, str]]:
    return [
        (env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
        for i in range(int(env["GIT_CONFIG_COUNT"]))
    ]


KEY = f"http.{WEB}/."
HEADER = "Authorization: Basic " + form()
PINS = [
    (f"{KEY}extraHeader", ""),
    (f"{KEY}extraHeader", HEADER),
    ("credential.helper", ""),
    (f"{KEY}sslVerify", "true"),
    (f"{KEY}followRedirects", "false"),
]
HARD = sandbox.hardened_git_env()
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
            auth(),
            lambda e: (
                (e["GIT_CONFIG_COUNT"], e["GIT_CONFIG_KEY_0"], e["GIT_CONFIG_VALUE_0"])
                == ("13", "core.hooksPath", os.devnull)
            ),
            id="hooks-pin-stays-first",
        ),
        pytest.param(
            {"PATH": "/bin"},
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


async def test_a_push_to_an_edited_web_url_is_refused_not_answered_by_gits_credential(
    tmp_path, monkeypatch
):
    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS)
    moved = acct("https://moved.example")
    with pytest.raises(ForgeError, match="not on https://moved.example"):
        await git_auth.auth_for_account(repo, "corp-gl", moved, push=True)


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


async def test_a_member_without_a_forge_takes_its_hosts_default(tmp_path, monkeypatch):
    repo, _ = rig(
        tmp_path,
        monkeypatch,
        remote=HTTPS,
        accounts={"corp-gl": acct(default=True)},
        entry=None,
    )
    entry = RepoEntry(path=str(repo))

    async def read():
        return [entry], ForgesFile(accounts={"corp-gl": acct(default=True)})

    git_auth.set_reader(read)
    got = await git_auth.auth_for(repo)
    assert got is not None and got.name == "corp-gl"


async def test_an_unreadable_token_is_an_auth_error(tmp_path, monkeypatch):
    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS, token=False)
    with pytest.raises(ForgeError) as caught:
        await git_auth.auth_for(repo)
    exc = caught.value
    assert (exc.reason, exc.account, exc.source) == ("auth", "corp-gl", "env CORP_GL")
    assert "set it there and restart" in str(exc)


@pytest.mark.parametrize(
    ("text", "stamped"),
    [
        pytest.param("fatal: Authentication failed for 'x'", "auth", id="authentication-failed"),
        pytest.param(
            "fatal: could not read Username for 'x'", "auth", id="could-not-read-username"
        ),
        pytest.param("The requested URL returned error: 401", "auth", id="returned-401"),
        pytest.param("The requested URL returned error: 403", "forbidden", id="returned-403"),
        pytest.param("fatal: unable to access: Connection refused", None, id="connection-refused"),
    ],
)
def test_a_git_auth_stamps_only_an_auth_failure(text, stamped):
    exc = auth().stamp(ForgeError(text))
    assert exc.reason == stamped
    assert exc.account == ("corp-gl" if stamped else "")
    assert exc.source == ("env CORP_GL" if stamped else "")


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
    from types import SimpleNamespace

    seen = []
    monkeypatch.setattr(config, "load_repos", lambda p: seen.append(p) or [])
    forges = None if accounts is None else ForgesFile(accounts=accounts)
    st = SimpleNamespace(forges=forges, templates_dir=tmp_path)
    assert await git_auth.server_context(st) == ([], forges)
    assert len(seen) == loads
