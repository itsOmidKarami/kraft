"""Reading a token from its source: never interactively, never quoting what a source printed."""

from __future__ import annotations

import base64
import os
import shutil
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
    # These would point git back at a repository; the hardening pins must survive.
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.setenv(var, str(repo))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/dev/null")
    real_git = shutil.which("git")  # before the stub shadows it
    cli.stub("git", f"username=u\npassword={TOK}", record=True)
    # The real git, run where the stub runs: it must find no repository there.
    stub = cli.bindir / "git"
    stub.write_text(
        stub.read_text().replace(
            "pwd >",
            f"{real_git} rev-parse --git-dir >/dev/null 2>&1; echo $? > {stub}.rev; pwd >",
            1,
        )
    )
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
    for gone in ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_PARAMETERS"):
        assert gone not in rec["env"]
    assert rec["env"]["GIT_CONFIG_COUNT"] == "1"
    assert rec["env"]["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert not Path(rec["pwd"]).is_relative_to(repo)
    assert int((cli.bindir / "git.rev").read_text()) != 0
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
            {"from": "command", "command": f"sh -c '{LEAK}; exit 1'"},
            None,
            False,
            "command exited 1; its output is not shown",
            id="command-exits-1",
        ),
        pytest.param(
            {"from": "command", "command": "sh -c 'true; echo LEAKMARK >&2'"},
            None,
            False,
            "the command printed nothing",
            id="command-empty",
        ),
        pytest.param(
            {"from": "command", "command": "sh -c 'echo LEAKMARK; echo LEAKMARK'"},
            None,
            False,
            "the command printed more than one line; its output is not shown",
            id="command-two-lines",
        ),
        pytest.param(
            {"from": "git-credential"},
            "echo LEAKMARK; echo \"fatal: could not read Username for 'https://x': "
            'terminal prompts disabled" >&2; exit 128',
            False,
            "git has no saved credential for code.corp.example:8443",
            id="git-no-credential-real-git",
        ),
        pytest.param(
            {"from": "command", "command": f"A=1 echo {TOK}"},
            None,
            False,
            "the token command could not run (FileNotFoundError)",
            id="command-has-no-shell",
        ),
        pytest.param(
            {"from": "command", "command": "echo 'unterminated"},
            None,
            False,
            "the token command is not valid shell-style quoting",
            id="command-bad-quoting",
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
        await read_token(
            account(**token), repos=[], worktrees=tmp_path / "wt", expose=(), ssh_hint=ssh
        )
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
        pytest.param(
            "unset-var", "names $KRAFT_NO_SUCH_DIR, a variable that is not set", id="file-unset-var"
        ),
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
    with pytest.raises(NoToken) as exc:
        await read(tmp_path, repo, expose=expose, **{"from": "file", "file": str(path)})
    assert message in str(exc.value)
    assert TOK not in str(exc.value)


@pytest.mark.parametrize(
    "platform, escaped",
    [("darwin", False), ("linux", False), ("linux", True)],
    ids=["darwin", "linux", "descendant-left-the-session"],
)
async def test_a_hung_lookup_times_out_naming_what_may_wait(
    cli, tmp_path, monkeypatch, platform, escaped
):
    pidfile = tmp_path / "child.pid"
    sleeper = "python3 -c 'import os,time; os.setsid(); time.sleep(30)'" if escaped else "sleep 30"
    git_script(cli, f"{sleeper} &\necho $! > {pidfile}\nwait")
    monkeypatch.setattr(credentials, "CREDENTIAL_TIMEOUT_S", 0.5)
    monkeypatch.setattr(sys, "platform", platform)
    procs = []
    real_popen = credentials.subprocess.Popen
    monkeypatch.setattr(
        credentials.subprocess,
        "Popen",
        lambda *a, **k: procs.append(real_popen(*a, **k)) or procs[-1],
    )
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
    assert procs[0].returncode is not None  # reaped, not a zombie
    pid = int(pidfile.read_text())
    if escaped:  # killpg cannot reach it; the lookup still returns and reaps its child
        os.kill(pid, 9)
        return
    for _ in range(50):  # the kill is a signal; the process goes a moment later
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize(
    ("forge", "user"),
    [
        pytest.param("gitlab", "oauth2", id="gitlab-oauth2"),
        pytest.param("github", "x-access-token", id="github-x-access-token"),
        pytest.param(None, "oauth2", id="no-forge-keeps-the-kinds-user"),
    ],
)
async def test_a_read_token_and_its_basic_form_are_registered(tmp_path, forge, user):
    acc = ForgeAccount(
        forge=forge or "gitlab",
        client="api",
        web_url=URL,
        token=TokenSource(**{"from": "command", "command": f"echo {TOK}"}),
    )
    tok = await read_token(acc, repos=[], worktrees=tmp_path / "wt", expose=[])
    sent = basic_form(tok, "unknown", forge=forge)
    assert base64.b64decode(sent).decode() == f"{user}:{tok}"
    assert redact.redact(f"{tok} {sent}") == "<redacted> <redacted>"


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


_ENV_FILE = (
    "accounts:\n  gl:\n    forge: gitlab\n    client: api\n    web_url: {url}\n    token: {tok}\n"
)
_SRC = "{from: env, env: CORP_GL}"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param({"from": "git-credential"}, id="git-credential"),
        pytest.param({"from": "command", "command": "echo 'two words here'"}, id="command"),
        pytest.param({"from": "env", "env": "MY_FORGE_TOKEN"}, id="env"),
        pytest.param({"from": "file", "file": "FILE"}, id="file"),
    ],
)
async def test_a_token_with_whitespace_inside_is_refused_without_quoting_it(
    cli, tmp_path, monkeypatch, token
):
    blob = f"# comment\n{TOK}"
    if token["from"] == "git-credential":
        cli.stub("git", f"username=u\npassword=two words here {TOK}")
    elif token["from"] == "env":
        monkeypatch.setitem(credentials._TAKEN, "MY_FORGE_TOKEN", blob)
    elif token["from"] == "file":
        path = secret_file(tmp_path)
        path.write_text(blob)
        token = {**token, "file": str(path)}
    with pytest.raises(NoToken) as exc:
        await read(tmp_path, **token)
    assert str(exc.value) == "the token contains whitespace or a control character; it is not shown"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param({"from": "command", "command": "yes"}, id="command-stdout-endless"),
        pytest.param(
            {"from": "command", "command": "sh -c 'yes >&2'"}, id="command-stderr-endless"
        ),
    ],
)
async def test_a_command_that_prints_endlessly_is_refused_fast(tmp_path, token):
    start = time.monotonic()
    with pytest.raises(NoToken) as exc:
        await read(tmp_path, **token)
    assert time.monotonic() - start < 5
    assert str(exc.value) == "the token command printed too much; its output is not shown"


_DEFAULTS = (
    _ENV_FILE.format(url=URL, tok=_SRC).replace(
        "client: api", "client: api\n    default_for_host: true"
    )
    + "  gl2:\n    forge: gitlab\n    client: api\n    default_for_host: true\n    web_url: "
    + URL
    + "\n    token: {from: command, command: x}\n"
)


@pytest.mark.parametrize(
    "text, passthrough",
    [
        pytest.param(_ENV_FILE.format(url=URL, tok=_SRC), False, id="loaded-file"),
        pytest.param(_ENV_FILE.format(url=URL, tok=_SRC) + "bad: [\n", False, id="unreadable-file"),
        pytest.param(_DEFAULTS, False, id="invalid-file"),
        pytest.param(
            _ENV_FILE.format(url=URL, tok='{from: env, env: "CORP_GL"}') + "bad: [\n",
            False,
            id="quoted-name",
        ),
        pytest.param(_ENV_FILE.format(url=URL, tok=_SRC), True, id="passthrough-names-it"),
        pytest.param(
            "# caf\xe9\n" + _ENV_FILE.format(url=URL, tok=_SRC) + "bad: [\n",
            False,
            id="non-utf8-file",
        ),
        pytest.param(
            _ENV_FILE.format(url=URL, tok='{"from": "env", "env": "CORP_GL"}') + "bad: [\n",
            False,
            id="quoted-key",
        ),
        pytest.param(
            _ENV_FILE.format(url=URL, tok="{from: env, env : CORP_GL}") + "bad: [\n",
            False,
            id="space-before-colon",
        ),
    ],
)
async def test_an_env_source_leaves_the_server_environment(
    tmp_path, monkeypatch, text, passthrough
):
    from kraft.adapters.forge import git
    from kraft.worker.env import worker_env

    monkeypatch.setattr(redact, "_secrets", ())
    monkeypatch.setenv("CORP_GL", TOK)
    path = tmp_path / "forges.yaml"
    path.write_bytes(text.encode("latin-1"))
    assert credentials.strip_env_sources(path) == ["CORP_GL"]
    assert redact.redact(TOK) == "<redacted>"  # registered at take time, before any probe
    assert "CORP_GL" not in os.environ
    assert credentials.env_token("CORP_GL") == TOK
    entry = RepoEntry(path=str(tmp_path), env_passthrough=["CORP_GL"]) if passthrough else None
    env = worker_env(entry)
    assert "CORP_GL" not in env
    # `env` stands for any child: git, a hook, the local worker.
    seen = await git.run_cli(tmp_path, ["env"], env=env if passthrough else None)
    assert TOK not in seen.stdout


def test_a_refused_name_in_an_unreadable_file_is_left_alone(tmp_path, monkeypatch):
    from unittest import mock

    from kraft.worker import sandbox

    path = tmp_path / "forges.yaml"
    path.write_text("accounts: [\n  env: GIT_CONFIG_COUNT\n  env: PATH\n")
    with mock.patch.dict(os.environ):
        sandbox.harden_host_git_env()
        before = dict(os.environ)
        assert credentials.strip_env_sources(path) == []
        assert dict(os.environ) == before
    assert credentials._TAKEN == {}


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "accounts:\n  gl: {token: {from: file, file: '/s/tok'}}\nbad: [\n", id="plain"
        ),
        pytest.param(
            "# caf\xe9\naccounts:\n  gl: {token: {from: file, file: /s/tok}}\nbad: [\n",
            id="non-utf8-file",
        ),
        pytest.param(
            'accounts:\n  gl: {token: {"from": "file", "file": "/s/tok"}}\nbad: [\n',
            id="quoted-key",
        ),
        pytest.param(
            "accounts:\n  gl: {token: {from: file, file : /s/tok}}\nbad: [\n",
            id="space-before-colon",
        ),
    ],
)
def test_a_broken_file_still_names_its_token_files(tmp_path, monkeypatch, text):
    monkeypatch.setattr(credentials, "_SECRET_FILES", ())
    path = tmp_path / "forges.yaml"
    path.write_bytes(text.encode("latin-1"))
    credentials.set_secret_files(path)
    assert credentials.secret_files() == (Path("/s/tok"),)


def test_a_directory_named_as_a_token_file_is_not_protected(tmp_path, monkeypatch):
    # `file: ~/` would otherwise hide all of home from every sandbox (and every CLI under it).
    monkeypatch.setattr(credentials, "_SECRET_FILES", ())
    path = tmp_path / "forges.yaml"
    path.write_text(
        f"accounts:\n  gl: {{token: {{from: file, file: {tmp_path}}}}}\n"
        f"  gh: {{token: {{from: file, file: {tmp_path}/tok}}}}\nbad: [\n"
    )
    credentials.set_secret_files(path)
    assert credentials.secret_files() == (tmp_path.resolve() / "tok",)


def test_a_token_file_under_a_closed_directory_never_stops_boot(tmp_path, monkeypatch):
    # Python 3.12's Path.is_dir raises on EACCES; boot and reload call this unguarded.
    def denied(self, *a, **kw):
        raise PermissionError(13, "denied", str(self))

    monkeypatch.setattr(credentials, "_SECRET_FILES", ())
    monkeypatch.setattr(Path, "is_dir", denied)
    path = tmp_path / "forges.yaml"
    path.write_text("accounts:\n  gh: {token: {from: file, file: /closed/tok}}\nbad: [\n")
    credentials.set_secret_files(path)
    assert credentials.secret_files() == (Path("/closed/tok"),)


def test_a_pasted_token_is_0600_in_a_0700_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    secrets = tmp_path / "home" / "secrets"
    secrets.mkdir(parents=True)
    secrets.chmod(0o755)
    path = credentials.write_pasted("corp", TOK)
    assert path == secrets / "corp" and path.read_text().strip() == TOK
    assert path.stat().st_mode & 0o777 == 0o600
    assert secrets.stat().st_mode & 0o777 == 0o700
    credentials.write_pasted("corp", TOK + "2")
    assert path.read_text().strip() == TOK + "2"
    assert [p.name for p in secrets.iterdir()] == ["corp"]  # no temp file left


@pytest.mark.parametrize(
    ("steps", "reads"),
    [
        (["", ""], 1),
        (["", "other-api"], 2),
        (["", "fresh"], 2),
        (["", "forget"], 2),
    ],
    ids=["cached", "account-edited-misses", "fresh-reads-again", "forgotten-reads-again"],
)
async def test_a_token_is_read_once_per_account_until_it_changes(monkeypatch, steps, reads):
    seen = []

    async def counting(acct, **kw):
        seen.append(acct)
        return TOK

    monkeypatch.setattr(credentials, "read_token", counting)
    acct = account(**{"from": "env", "env": "X_TOK"})
    for step in steps:
        who = (
            acct.model_copy(update={"api_url": "https://api.example"})
            if step == "other-api"
            else acct
        )
        if step == "forget":
            credentials.forget_tokens()
        assert await credentials.token_for("a", who, fresh=step == "fresh") == TOK
    assert len(seen) == reads
    credentials.forget_tokens()
    assert not credentials._LOCKS  # one lock per edit would grow without end


async def test_concurrent_cold_reads_do_one_lookup(monkeypatch):
    import asyncio

    seen = []

    async def slow(acct, **kw):
        seen.append(1)
        await asyncio.sleep(0.01)
        return TOK

    monkeypatch.setattr(credentials, "read_token", slow)
    await asyncio.gather(
        *(credentials.token_for("a", account(**{"from": "env", "env": "X_TOK"})) for _ in range(5))
    )
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("context", "says"),
    [
        (None, "token reader is not running"),
        (
            credentials.ReadContext([], Path("wt"), [], repos_unread=True),
            "repos.yaml does not load",
        ),
    ],
    ids=["file-without-context-refused", "file-repos-unread-refused"],
)
async def test_a_file_token_needs_a_readable_context(monkeypatch, context, says):
    async def ctx():
        return context

    credentials.set_token_context(None if context is None else ctx)
    with pytest.raises(NoToken, match=says):
        await credentials.token_for("a", account(**{"from": "file", "file": "/x/tok"}))
