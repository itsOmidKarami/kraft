"""Reading a token from its source: never interactively, never quoting what a source printed."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kraft import redact
from kraft.adapters.forge import credentials
from kraft.adapters.forge.credentials import NoToken, basic_form, prefix_warning, read_token
from kraft.config import RepoEntry
from kraft.forges import ForgeAccount, TokenSource
from kraft.worker.host_view import system_dirs

TOK = "tok-0123456789"
URL = "https://code.corp.example:8443"


@pytest.fixture(autouse=True)
def _fresh_taken(monkeypatch):
    monkeypatch.setattr(credentials, "_TAKEN", {})


def account(**token) -> ForgeAccount:
    return ForgeAccount(forge="gitlab", client="api", web_url=URL, token=TokenSource(**token))


async def read(tmp_path, repo=None, expose=(), **token) -> str:
    repos = [RepoEntry(path=str(repo))] if repo else []
    return await read_token(
        account(**token), repos=repos, worktrees=tmp_path / "wt", expose=list(expose)
    )


def secret_file(tmp_path, mode=0o600) -> Path:
    path = tmp_path / "outside" / "token"
    path.parent.mkdir(exist_ok=True)
    path.write_text(TOK + "\n")
    path.chmod(mode)
    return path


def git_script(cli, body: str):
    # Prints LEAKMARK on both streams, to prove no message quotes either.
    (cli.bindir / "git").write_text(f"#!/bin/sh\ncat >/dev/null\n{body}\n")
    (cli.bindir / "git").chmod(0o755)


async def test_git_credential_fill_runs_without_a_prompt_from_a_neutral_directory(
    cli, repo, tmp_path, monkeypatch
):
    for var in ("GIT_ASKPASS", "SSH_ASKPASS", "GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE"):
        monkeypatch.setenv(var, "inherited")
    cli.stub("git", f"username=u\npassword={TOK}", record=True)
    await read(tmp_path, repo, **{"from": "git-credential"})
    assert cli.argv("git") == ["credential", "fill"]
    rec = cli.recorded("git")
    assert rec["stdin"] == "protocol=https\nhost=code.corp.example:8443\n\n"
    for key, want in (
        ("GIT_TERMINAL_PROMPT", "0"),
        ("GIT_ASKPASS", ""),
        ("SSH_ASKPASS", ""),
        ("GCM_INTERACTIVE", "never"),
    ):
        assert rec["env"][key] == want
    assert not Path(rec["pwd"]).is_relative_to(repo)
    # The ceiling keeps git from finding a repo above the directory.
    probe = subprocess.run(
        ["git", "rev-parse"],
        cwd=rec["pwd"] if Path(rec["pwd"]).exists() else tmp_path,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": rec["env"]["GIT_CEILING_DIRECTORIES"]},
        capture_output=True,
        text=True,
        executable="/usr/bin/git",
    )
    assert probe.returncode != 0
    assert rec["sid"] == rec["pid"]


@pytest.mark.parametrize(
    "token, setup",
    [
        pytest.param({"from": "git-credential"}, "git", id="git-credential"),
        pytest.param({"from": "command", "command": f"printf '{TOK}\\n'"}, None, id="command"),
        pytest.param({"from": "env", "env": "MY_FORGE_TOKEN"}, "env", id="env"),
        pytest.param({"from": "file", "file": "FILE"}, "file", id="file"),
    ],
)
async def test_a_source_reads_its_token(cli, tmp_path, monkeypatch, token, setup):
    if setup == "git":
        cli.stub("git", f"username=u\npassword={TOK}")
    elif setup == "env":
        monkeypatch.setitem(credentials._TAKEN, "MY_FORGE_TOKEN", TOK)
    elif setup == "file":
        token = {**token, "file": str(secret_file(tmp_path))}
    assert await read(tmp_path, **token) == TOK


LEAK = "echo LEAKMARK; echo LEAKMARK >&2"


@pytest.mark.parametrize(
    "token, git_body, ssh, message",
    [
        pytest.param(
            {"from": "git-credential"},
            f"{LEAK}; echo username=u",
            False,
            "git has no saved credential for code.corp.example:8443",
            id="git-no-password",
        ),
        pytest.param(
            {"from": "git-credential"},
            f"{LEAK}; echo username=u",
            True,
            "git has no saved credential for code.corp.example:8443"
            "; your remote is SSH, so git never saved one",
            id="git-no-password-ssh-hint",
        ),
        pytest.param(
            {"from": "git-credential"},
            f"{LEAK}; exit 128",
            False,
            "git credential fill exited 128; its output is not shown",
            id="git-exits-128",
        ),
        pytest.param(
            {"from": "command", "command": f"{LEAK}; exit 1"},
            None,
            False,
            "command exited 1; its output is not shown",
            id="command-exits-1",
        ),
        pytest.param(
            {"from": "command", "command": "true; echo LEAKMARK >&2"},
            None,
            False,
            "the command printed nothing",
            id="command-empty",
        ),
        pytest.param(
            {"from": "command", "command": "echo LEAKMARK; echo LEAKMARK"},
            None,
            False,
            "the command printed more than one line; its output is not shown",
            id="command-two-lines",
        ),
        pytest.param(
            {"from": "env", "env": "NOT_TAKEN"},
            None,
            False,
            "NOT_TAKEN was not set in the server's environment when it started; "
            "set it there and restart",
            id="env-not-taken",
        ),
        pytest.param(
            {"from": "command", "command": "echo short"},
            None,
            False,
            "the token is shorter than 8 characters",
            id="too-short",
        ),
    ],
)
async def test_a_source_that_finds_nothing_says_why_without_its_output(
    cli, tmp_path, token, git_body, ssh, message
):
    if git_body:
        git_script(cli, git_body)
    with pytest.raises(NoToken) as exc:
        await read_token(account(**token), repos=[], worktrees=tmp_path / "wt", ssh_hint=ssh)
    assert str(exc.value) == message
    assert "LEAKMARK" not in str(exc.value)


@pytest.mark.parametrize(
    "case, message",
    [
        pytest.param("missing", "does not exist", id="file-missing"),
        pytest.param("symlink", "is a symlink", id="file-symlink"),
        pytest.param(
            "group", "readable by group or others; chmod 600 it", id="file-group-readable"
        ),
        pytest.param("other-owner", "owned by another user", id="file-other-owner"),
        pytest.param("unset-var", "variable that is not set", id="file-unset-var"),
        pytest.param("in-repo", "inside", id="file-inside-repo"),
        pytest.param("in-worktrees", "inside", id="file-inside-worktrees"),
        pytest.param("in-expose", "inside", id="file-inside-expose"),
        pytest.param("etc", "inside /etc", id="file-in-etc"),
        pytest.param("system", "inside", id="file-in-a-system-dir"),
    ],
)
async def test_a_file_that_is_not_safe_to_read_is_refused(
    repo, tmp_path, monkeypatch, case, message
):
    path = secret_file(tmp_path)
    expose: list[Path] = []
    if case == "missing":
        path = tmp_path / "nope"
    elif case == "symlink":
        path = tmp_path / "link"
        path.symlink_to(secret_file(tmp_path))
    elif case == "group":
        path.chmod(0o640)
    elif case == "other-owner":
        monkeypatch.setattr(os, "getuid", lambda: os.stat(path).st_uid + 1)
    elif case == "unset-var":
        path = Path("$KRAFT_NO_SUCH_DIR/token")
    elif case in {"in-repo", "in-worktrees"}:
        home = repo if case == "in-repo" else tmp_path / "wt"
        home.mkdir(exist_ok=True)
        path = home / "token"
        path.write_text(TOK)
        path.chmod(0o600)
    elif case == "in-expose":
        expose = [path.parent]
    else:
        path = Path("/etc/kraft-token" if case == "etc" else f"{system_dirs()[0]}/kraft-token")
        real = os.lstat(secret_file(tmp_path))
        monkeypatch.setattr(os, "lstat", lambda p: real)
    with pytest.raises(NoToken) as exc:
        await read(tmp_path, repo, expose=expose, **{"from": "file", "file": str(path)})
    assert message in str(exc.value)
    assert TOK not in str(exc.value)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
async def test_a_hung_lookup_times_out_naming_what_may_wait(cli, tmp_path, monkeypatch, platform):
    pidfile = tmp_path / "child.pid"
    git_script(cli, f"sleep 30 &\necho $! > {pidfile}\nwait")
    monkeypatch.setattr(credentials, "CREDENTIAL_TIMEOUT_S", 0.5)
    monkeypatch.setattr(sys, "platform", platform)
    start = time.monotonic()
    with pytest.raises(NoToken) as exc:
        await read(tmp_path, **{"from": "git-credential"})
    # Killing only the direct child leaves the sleeper holding the pipe for its 30s.
    assert time.monotonic() - start < 10
    text = str(exc.value)
    assert text.startswith("git credential fill did not answer in 0.5s: ")
    if platform == "darwin":
        assert "allow Keychain access for git" in text
    else:
        assert "may be waiting for input" in text and "Keychain" not in text
    pid = int(pidfile.read_text())
    for _ in range(50):  # the kill is a signal; the process goes a moment later
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_a_read_token_and_its_basic_form_are_registered(tmp_path):
    tok = await read(tmp_path, **{"from": "command", "command": f"echo {TOK}"})
    assert redact.redact(f"{tok} {basic_form(tok, 'pat')}") == "<redacted> <redacted>"


@pytest.mark.parametrize(
    "kind, scopes",
    [
        pytest.param("pat", ("pat",), id="pat"),
        pytest.param("oauth", ("oauth",), id="oauth"),
        pytest.param("unknown", ("pat", "oauth"), id="unknown"),
    ],
)
def test_the_kinds_table(kind, scopes):
    k = credentials.KINDS[kind]
    assert (k.header, k.git_username, k.gitlab_scopes) == ("Bearer", "oauth2", scopes)


@pytest.mark.parametrize(
    "token, kind, warning",
    [
        pytest.param(
            "ghp_abc",
            "oauth",
            "the token starts with ghp_, a pat token, but kind says oauth",
            id="ghp-says-oauth",
        ),
        pytest.param("glpat-abc", "pat", None, id="glpat-says-pat"),
        pytest.param("gho_abc", "unknown", None, id="gho-says-unknown"),
    ],
)
def test_a_prefix_that_contradicts_the_kind_is_a_warning(token, kind, warning):
    assert prefix_warning(token, kind) == warning
