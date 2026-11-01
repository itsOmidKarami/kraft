"""Every remote git call of an API account's repo, over a real HTTPS remote on 127.0.0.1: what
the wire sees (`support.git_https`) and what each site does when the token fails."""

from __future__ import annotations

import base64
import contextlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from support import worktree as wtree
from support.git_https import bare_header_env, git_https, plant_keys, recording_proxy
from support.harness import (
    commit_all,
    entry_of,
    git,
    make_repo_with_submodule,
    v1_chain,
    write,
)
from support.workspace import workspace_target

from kraft import builtins, reviewed_head
from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge import git_auth
from kraft.adapters.forge.github_api import GitHubApi
from kraft.adapters.forge.gitlab_api import GitLabApi
from kraft.adapters.forge.glab import GlabCli
from kraft.adapters.forge.models import ForgeError
from kraft.api import deps
from kraft.config import RepoEntry, git_read
from kraft.forges import ForgesFile
from kraft.templates.environment import WorkItemTarget

from .test_git_auth import TOK, acct, form, rig

OURS = f"Basic {form()}"
SOURCE = "env CORP_GL"
REDIRECT_WORDS = "origin redirects elsewhere; point origin at the new address, then retry"
_ONE_NODE = [
    {"id": "n", "kind": "exec", "tasks": [{"id": "t", "kind": "subprocess", "command": "true"}]}
]


def _bare(root, name="app"):
    path = root / "team" / f"{name}.git"
    git(root.parent, "init", "--bare", "-q", "-b", "main", str(path))
    return path


def _install(entries, accounts):
    async def read():
        return entries, ForgesFile(accounts=accounts)

    git_auth.set_reader(read)


@pytest.fixture
def remote(request, tmp_path, monkeypatch):
    """A repo on `kraft/abc`, one commit ahead of `main`, whose origin is an HTTPS remote that
    wants the account's token. `request.param` (indirect) may set `server` (the token the
    remote wants, None for any), `source` (the account's token source), `token` (False: the
    variable is never set) and `redirect` (an origin that never answers: `info/refs` goes there)."""
    opts = getattr(request, "param", {})
    srv = tmp_path / "srv"
    with git_https(srv, token=opts.get("server", TOK), forge=opts.get("forge", "gitlab")) as web:
        monkeypatch.setenv("GIT_SSL_CAINFO", str(web.ca))
        web.redirect = "https://127.0.0.1:9" if opts.get("redirect") else None
        account = acct(web.url, forge=opts.get("forge", "gitlab"), **opts.get("source", {}))
        repo, _ = rig(
            tmp_path,
            monkeypatch,
            remote=f"{web.url}/team/app.git",
            accounts={"corp-gl": account},
            token=opts.get("token", True),
        )
        bare = _bare(srv)
        git(repo, "push", "-q", str(bare), "main")  # seeded by path: the remote wants a token
        git(repo, "checkout", "-q", "-b", "kraft/abc")
        write(repo, "feature.txt", "one\n")
        commit_all(repo, "feature")
        yield SimpleNamespace(
            web=web,
            bare=bare,
            repo=repo,
            account=account,
            tmp=tmp_path,
            pinned=("corp-gl", account),
        )


def _clean(*parts):
    """No raw token, Basic form or `Authorization: Basic` in any of `parts`."""
    text = " ".join(str(p) for p in parts)
    for secret in (TOK, form(), "Authorization: Basic"):
        assert secret not in text


def _ours_only(web):
    assert web.authorizations, "the remote saw no request: the row proves nothing"
    assert set(web.authorizations) == {OURS}


async def _push(r, db, dirs):
    await forge_git.push(r.repo, "kraft/abc", account=r.pinned)
    assert git(r.bare, "rev-parse", "kraft/abc") == git(r.repo, "rev-parse", "kraft/abc")


async def _base_fetch(r, db, dirs):
    assert await builtins.upstream_head(r.repo, "main") == git(r.bare, "rev-parse", "main")


async def _bring_home(r, db, dirs):
    _land(r)
    assert await reviewed_head.bring_home(r.repo, "kraft/abc", in_place=False)


async def _base_check(r, db, dirs):
    assert await deps.base_branch_or_422(str(r.repo), "main") == "main"


def _land(r):
    """Another clone pushes a commit to the remote's `kraft/abc`, by path."""
    git(r.repo, "push", "-q", str(r.bare), "kraft/abc")
    other = r.tmp / "other"
    git(r.tmp, "clone", "-q", str(r.bare), str(other))
    git(other, "checkout", "-q", "kraft/abc")
    write(other, "landed.txt", "x\n")
    commit_all(other, "landed")
    git(other, "push", "-q", "origin", "kraft/abc")


async def _workspace(r, db):
    """A workspace root whose gitlink names a commit only the remote has; the member `repos/pkg`
    is connected with the account."""
    root, sub = make_repo_with_submodule(r.tmp, submodule_path="repos/pkg")
    member = _bare(r.web.root, "pkg")
    git(sub, "remote", "add", "origin", f"{r.web.url}/team/pkg.git")
    git(sub, "push", "-q", str(member), "main")
    clone = r.tmp / "pkg-clone"
    git(r.tmp, "clone", "-q", str(member), str(clone))
    write(clone, "only-remote.txt", "x\n")
    sha = commit_all(clone, "only the remote has it")
    git(clone, "push", "-q", "origin", "main")
    git(root, "update-index", "--cacheinfo", f"160000,{sha},repos/pkg")
    git(root, "commit", "-q", "-m", "move the pointer")
    _install(
        [RepoEntry(path=str(sub), forge="gitlab", forge_account="corp-gl")],
        {"corp-gl": r.account},
    )
    chain = v1_chain(_ONE_NODE, repo=root, target=workspace_target({"pkg": "repos/pkg"}))
    await wtree.make_item(db, root, materialized_chain=chain.to_json())
    return root, {"pkg": entry_of({"id": "pkg", "path": str(sub)})}, sha


async def _member_fetch(r, db, dirs):
    root, members, sha = await _workspace(r, db)
    worktree = await wtree.ensure(db, dirs, root, repositories=members)
    assert git_read(worktree / "repos" / "pkg", "rev-parse", "HEAD") == sha


@pytest.mark.parametrize(
    "site",
    [
        pytest.param(_push, id="push"),
        pytest.param(_base_fetch, id="base-fetch"),
        pytest.param(_bring_home, id="bring-home"),
        pytest.param(_member_fetch, id="member-checkout-fetch"),
        pytest.param(_base_check, id="base-branch-check"),
    ],
)
async def test_every_remote_git_call_carries_the_accounts_header(remote, database, run_dirs, site):
    await site(remote, database, run_dirs)
    _ours_only(remote.web)


async def test_another_origin_sees_no_header(remote, tmp_path, monkeypatch):
    """`origin` stays on A, so `auth_for_account` builds A's header; git then dials B (as an
    `ls-remote <url>` would, or a second host a fetch reaches) and B must not see it."""
    with git_https(tmp_path / "srv2") as second:
        bundle = tmp_path / "ca-both.pem"
        bundle.write_text(remote.web.ca.read_text() + second.ca.read_text())
        monkeypatch.setenv("GIT_SSL_CAINFO", str(bundle))
        _bare(second.root)
        built = await git_auth.auth_for_account(remote.repo, *remote.pinned, push=False)
        assert built is not None and built.origin == remote.web.url
        env = git_auth.git_env(builtins.promptless_git_env(remote.repo), built)
        done = subprocess.run(
            ["git", "ls-remote", f"{second.url}/team/app.git"],
            cwd=remote.repo,
            env=env,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        assert second.authorizations and set(second.authorizations) == {None}
        assert remote.web.authorizations == []


async def test_a_remote_proxy_in_the_repos_config_never_sees_the_header(remote, monkeypatch):
    """`remote.origin.proxy` beats every `http.<url>.proxy`: the control (plain git, same config)
    reaches the proxy; the pinned fetch does not, and the remote sees our header."""
    for k in ("no_proxy", "NO_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    with recording_proxy() as (seen, proxy):
        git(remote.repo, "config", "remote.origin.proxy", proxy)
        subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=remote.repo, env=os.environ)
        assert seen, "the config's proxy was not used: no proof"
        seen.clear()
        await _base_fetch(remote, None, None)
        assert seen == []
    _ours_only(remote.web)


async def test_a_redirect_to_another_host_sees_no_header(remote, tmp_path, monkeypatch):
    with git_https(tmp_path / "srv2") as second:
        bundle = tmp_path / "ca-both.pem"
        bundle.write_text(remote.web.ca.read_text() + second.ca.read_text())
        monkeypatch.setenv("GIT_SSL_CAINFO", str(bundle))
        git(remote.repo, "push", "-q", str(_bare(second.root)), "main")
        remote.web.redirect = second.url
        with pytest.raises(builtins.BaseFetchRefused, match=REDIRECT_WORDS) as caught:
            await builtins.upstream_head(remote.repo, "main")
        _clean(caught.value, caught.value.reason, caught.value.args)
        assert remote.web.authorizations == [OURS], "the redirecting origin was asked"
        assert second.authorizations == [], "git followed the redirect"


async def test_a_persisted_header_never_makes_two_authorizations(remote):
    git(remote.repo, "config", f"http.{remote.web.url}/.extraheader", "AUTHORIZATION: basic OLD")
    await _base_fetch(remote, None, None)
    assert remote.web.all_authorizations
    assert all(seen == [OURS] for seen in remote.web.all_authorizations)


@pytest.mark.parametrize(
    "scoped",
    [
        pytest.param(False, id="helper-never-asked-on-a-401"),
        pytest.param(True, id="url-scoped-helper-as-gh-setup-git-writes-it"),
    ],
)
@pytest.mark.parametrize(
    "remote", [pytest.param({"server": "another-token"}, id="wrong-token")], indirect=True
)
async def test_a_persons_credential_helper_is_never_asked(remote, tmp_path, scoped):
    marker = tmp_path / "asked"
    script = tmp_path / "helper.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    key = f"credential.{remote.web.url}.helper" if scoped else "credential.helper"
    if scoped:
        git(remote.repo, "config", key, "")  # gh writes an empty one first, then its own
    git(remote.repo, "config", "--add", key, f"!{script}")
    with pytest.raises(builtins.BaseFetchRefused) as caught:
        await builtins.upstream_head(remote.repo, "main")
    assert caught.value.reason == "auth"
    assert remote.web.authorizations, "the remote saw no request: the row proves nothing"
    assert not marker.exists()


async def test_a_path_scoped_saved_header_never_beats_ours(remote):
    """git takes the more specific key; ours names the exact URL, and a tie goes to the env."""
    old = "AUTHORIZATION: basic b2xkOm9sZA=="
    git(remote.repo, "config", f"http.{remote.web.url}/team/app.git.extraheader", old)
    await _base_fetch(remote, None, None)
    _ours_only(remote.web)


async def test_a_path_scoped_follow_redirects_and_ssl_verify_never_beat_our_pins(
    remote, tmp_path, monkeypatch
):
    """The repo's own config scopes `followRedirects=true` and `sslVerify=false` to the URL:
    the control (no exact-URL pins) follows the redirect with the header; with them it does not."""
    scoped = f"http.{remote.web.url}/team/app.git"
    git(remote.repo, "config", f"{scoped}.followRedirects", "true")
    git(remote.repo, "config", f"{scoped}.sslVerify", "false")
    with git_https(tmp_path / "srv2") as second:
        bundle = tmp_path / "ca-both.pem"
        bundle.write_text(remote.web.ca.read_text() + second.ca.read_text())
        monkeypatch.setenv("GIT_SSL_CAINFO", str(bundle))
        git(remote.repo, "push", "-q", str(_bare(second.root)), "main")
        remote.web.redirect = second.url
        with monkeypatch.context() as bare:
            bare.setattr(git_auth, "git_env", bare_header_env)
            await builtins.upstream_head(remote.repo, "main")
        assert OURS in second.authorizations, "the header was not followed: no proof"
        second.authorizations.clear()
        with pytest.raises(builtins.BaseFetchRefused, match=REDIRECT_WORDS):
            await builtins.upstream_head(remote.repo, "main")
        assert second.authorizations == []
        # TLS still verifies: with the CA gone the pinned run fails on the certificate, and
        # the control (a plain git, same config) gets past it to the 401.
        monkeypatch.delenv("GIT_SSL_CAINFO")
        remote.web.redirect = None
        plain = await _ls_remote(remote.repo)
        assert "certificate" not in plain.lower() and "Username" in plain
        remote.web.authorizations.clear()
        with pytest.raises(builtins.BaseFetchRefused, match="verify origin's certificate"):
            await builtins.upstream_head(remote.repo, "main")
        assert remote.web.authorizations == []


async def _ls_remote(repo) -> str:
    done = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=repo, capture_output=True, text=True, env=os.environ
    )
    return done.stderr


async def test_a_gitconfig_that_traces_never_logs_the_header(remote, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    trace = tmp_path / "trace.json"
    (home / ".gitconfig").write_text(
        f"[trace2]\n\teventTarget = {trace}\n\tconfigParams = http.*\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # The control: with the switch-off pins gone, that gitconfig does log the header.
    with monkeypatch.context() as bare:
        bare.setattr(git_auth, "_TRACE2", ())
        await _base_fetch(remote, None, None)
    assert OURS.split()[1] in trace.read_text(), "the gitconfig does not trace: no proof"
    trace.unlink()
    await _base_fetch(remote, None, None)
    logged = trace.read_text() if trace.exists() else ""
    _clean(logged)


@pytest.mark.parametrize("remote", [pytest.param({"token": False}, id="no-token")], indirect=True)
async def test_an_unreadable_token_is_a_422_at_the_base_branch_check(remote):
    with pytest.raises(HTTPException) as caught:
        await _base_check(remote, None, None)
    assert caught.value.status_code == 422 and "corp-gl" in str(caught.value.detail)


@pytest.mark.parametrize("remote", [pytest.param({"token": False}, id="no-token")], indirect=True)
async def test_an_unreadable_token_makes_bring_home_false(remote):
    _land(remote)
    before = git(remote.repo, "rev-parse", "kraft/abc")
    assert await reviewed_head.bring_home(remote.repo, "kraft/abc", in_place=False) is False
    assert git(remote.repo, "rev-parse", "kraft/abc") == before


@pytest.mark.parametrize(
    ("remote", "text"),
    [
        pytest.param({"token": False}, "set it there and restart", id="unreadable-token"),
        pytest.param({"server": "another-token"}, "could not fetch", id="refused-token"),
    ],
    indirect=["remote"],
)
async def test_a_member_checkout_the_token_fails_stops_naming_the_account(
    remote, database, run_dirs, text
):
    root, members, _ = await _workspace(remote, database)
    with pytest.raises(ForgeError, match=text) as caught:
        await wtree.ensure(database, run_dirs, root, repositories=members)
    exc = caught.value
    assert (exc.reason, exc.account, exc.source) == ("auth", "corp-gl", SOURCE)
    assert not (run_dirs.worktrees / "w1").exists()


async def _item_on(db, repo):
    target = WorkItemTarget.for_repository("target", base_branch="main")
    chain = v1_chain(_ONE_NODE, repo=repo, target=target)
    return await wtree.make_item(db, repo, materialized_chain=chain.to_json())


def _fetched(r):
    """`main` as a last fetch left it, then the remote moves on (by path)."""
    git(r.repo, "fetch", "-q", str(r.bare), "+refs/heads/main:refs/remotes/origin/main")
    stale = git(r.repo, "rev-parse", "refs/remotes/origin/main")
    write(r.repo, "newer.txt", "x\n")
    commit_all(r.repo, "newer")
    git(r.repo, "push", "-q", str(r.bare), "HEAD:main")
    git(r.repo, "reset", "-q", "--hard", "HEAD~1")
    return stale


@pytest.mark.parametrize(
    ("remote", "sent"),
    [
        pytest.param({"server": "another-token"}, True, id="wrong-token-stops"),
        pytest.param({"token": False}, False, id="unreadable-token-stops"),
    ],
    indirect=["remote"],
)
async def test_a_base_fetch_the_token_cannot_authenticate_stops(remote, sent):
    _fetched(remote)
    with pytest.raises(builtins.BaseFetchRefused) as caught:
        await builtins.upstream_head(remote.repo, "main")
    exc = caught.value
    assert (exc.reason, exc.account, exc.source) == ("auth", "corp-gl", SOURCE)
    assert "`" not in str(exc) and "then retry" in str(exc)
    if sent:
        assert remote.web.authorizations and set(remote.web.authorizations) == {OURS}
    else:
        assert remote.web.authorizations == [], "an unreadable token sends nothing"
    _clean(exc, exc.reason, exc.args)


@pytest.mark.parametrize(
    "remote", [pytest.param({"server": "another-token"}, id="wrong-token")], indirect=True
)
async def test_a_refused_base_fetch_cuts_no_worktree(remote, database, run_dirs):
    _fetched(remote)
    await _item_on(database, remote.repo)
    with pytest.raises(builtins.BaseFetchRefused):
        await wtree.ensure(database, run_dirs, remote.repo)
    assert not (run_dirs.worktrees / "w1").exists(), "nothing was cut from a stale base"


@pytest.mark.parametrize(
    ("remote", "offline"),
    [
        pytest.param({}, True, id="offline-keeps-the-stale-base"),
        pytest.param(
            {"source": {"from": "git-credential"}}, False, id="git-credential-keeps-the-stale-base"
        ),
    ],
    indirect=["remote"],
)
async def test_a_base_fetch_that_is_no_token_failure_keeps_the_stale_base(remote, offline):
    stale = _fetched(remote)
    if offline:
        remote.web.stop()
    assert await builtins.upstream_head(remote.repo, "main") == stale


@pytest.mark.parametrize(
    ("remote", "words"),
    [
        pytest.param(
            {"server": "another-token"},
            "could not read Username.*terminal prompts disabled",
            id="wrong-token",
        ),
        pytest.param({"redirect": True}, REDIRECT_WORDS, id="redirect"),
        pytest.param({"token": False}, "set it there and restart", id="unreadable-token"),
    ],
    indirect=["remote"],
)
async def test_a_push_the_token_cannot_authenticate_is_a_token_stop(remote, words):
    with pytest.raises(ForgeError, match=words) as caught:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    exc = caught.value
    assert (exc.reason, exc.account, exc.source) == ("auth", "corp-gl", SOURCE)
    assert git(remote.bare, "branch", "--list", "kraft/abc") == ""
    if "terminal prompts" in words:
        _ours_only(remote.web)  # the token was sent first, and the remote refused it
    _clean(exc, exc.reason, exc.args)


async def test_an_api_backend_pushes_with_its_pinned_account(remote):
    await GitLabApi("corp-gl", remote.account).push(repo=remote.repo, branch="kraft/abc")
    _ours_only(remote.web)


async def test_a_cli_backend_pushes_with_no_header(remote, monkeypatch):
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")  # git's own credential must not ask a TTY
    with pytest.raises(ForgeError):  # git's own credential: the remote wants a token
        await GlabCli().push(repo=remote.repo, branch="kraft/abc")
    assert remote.web.authorizations and set(remote.web.authorizations) == {None}


async def test_an_edited_web_url_refuses_the_push(remote):
    moved = acct("https://moved.example")
    with pytest.raises(ForgeError, match="not on https://moved.example"):
        await GitLabApi("corp-gl", moved).push(repo=remote.repo, branch="kraft/abc")
    assert remote.web.authorizations == []


_LFS_POINTER = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 12\n"
_SRC = Path(__file__).resolve().parents[3] / "src" / "kraft"


def _script(path, body):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
async def leaked(remote, tmp_path, monkeypatch):
    """One credentialed push (with an LFS pointer) and one base fetch, with every place a token
    could land watched: a git wrapper's argv log, a fake git-lfs's argv and env, hooks that dump
    their env, git's own traces (redaction off), `.git/config` and `os.environ`."""
    for k in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:
        monkeypatch.delenv(k)
    write(remote.repo, "big.bin", _LFS_POINTER)
    commit_all(remote.repo, "an lfs pointer")
    bin_, logs = tmp_path / "bin", tmp_path / "logs"
    bin_.mkdir()
    logs.mkdir()
    real = shutil.which("git")
    _script(bin_ / "git", f'printf \'%s\\n\' "$@" >> {logs}/argv\nexec {real} "$@"')
    _script(
        bin_ / "git-lfs", f"printf '%s\\n' \"$@\" >> {logs}/lfs-argv\nenv >> {logs}/lfs-env\nexit 0"
    )
    common = Path(git(remote.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    for name in ("pre-push", "reference-transaction"):
        _script(common / "hooks" / name, f"env >> {logs}/hook-env")
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    traces = {k: logs / k.lower() for k in ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_TRACE_PACKET")}
    for k, path in traces.items():
        monkeypatch.setenv(k, str(path))
    monkeypatch.setenv("GIT_TRACE_REDACT", "0")

    def read(name):
        path = logs / name
        text = path.read_text() if path.exists() else None
        path.unlink(missing_ok=True)
        return text

    await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    on_push = read("hook-env")
    await builtins.upstream_head(remote.repo, "main")
    return SimpleNamespace(
        web=remote.web,
        argv=read("argv"),
        hook_env_on_push=on_push,
        hook_env_on_fetch=read("hook-env"),
        lfs_argv=read("lfs-argv"),
        lfs_env=read("lfs-env"),
        trace=" ".join(p.read_text() for p in traces.values() if p.exists()),
        config=(common / "config").read_text(),
        environ=" ".join(os.environ.values()),
    )


@pytest.mark.parametrize(
    "place",
    [
        "argv",
        "hook_env_on_push",
        "hook_env_on_fetch",
        "config",
        "environ",
        "trace",
        "lfs_argv",
    ],
)
async def test_a_git_token_reaches_only_its_origin(leaked, place):
    _ours_only(leaked.web)
    assert len(leaked.web.authorizations) >= 2  # the push's calls and the fetch
    assert leaked.argv and leaked.lfs_argv, "the wrappers never ran: the row proves nothing"
    held = getattr(leaked, place)
    _clean(held or "")
    if place.startswith("hook_env"):
        assert held is None  # a hook that never ran wrote nothing


async def test_git_lfs_gets_the_header_in_its_config_not_its_argv(leaked):
    values = [
        v
        for k, v in (ln.split("=", 1) for ln in leaked.lfs_env.splitlines() if "=" in ln)
        if k.startswith("GIT_CONFIG_VALUE_")
    ]
    assert f"Authorization: Basic {form()}" in values
    _clean(leaked.lfs_argv)


def test_no_remote_or_worker_module_builds_a_credentialed_git_env():
    imports = re.compile(r"^\s*(from|import)\s.*\bgit_auth\b", re.MULTILINE)
    for pkg in ("remote", "worker"):
        for path in (_SRC / pkg).rglob("*.py"):
            assert not imports.search(path.read_text()), path
    callers = {
        p.relative_to(_SRC).as_posix()
        for p in _SRC.rglob("*.py")
        if "set_reader(" in p.read_text() and p.name != "git_auth.py"
    }
    assert callers == {"api/startup.py"}


@pytest.mark.parametrize(
    "remote", [pytest.param({"server": "another-token"}, id="wrong-token")], indirect=True
)
async def test_a_verbose_curl_never_prints_the_header_into_a_stop(remote, monkeypatch):
    monkeypatch.setenv("GIT_CURL_VERBOSE", "1")  # beats GIT_TRACE_CURL, and prints to stderr
    monkeypatch.setenv("GIT_TRACE_REDACT", "0")
    with pytest.raises(ForgeError) as caught:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    _ours_only(remote.web)
    _clean(caught.value, caught.value.args)


@pytest.mark.parametrize("remote", [{"forge": "github"}], indirect=True)
async def test_a_github_api_push_sends_the_x_access_token_form(remote):
    await GitHubApi("corp-gl", remote.account).push(repo=remote.repo, branch="kraft/abc")
    assert git(remote.bare, "rev-parse", "kraft/abc") == git(remote.repo, "rev-parse", "kraft/abc")
    assert set(remote.web.authorizations) == {
        "Basic " + base64.b64encode(f"x-access-token:{TOK}".encode()).decode()
    }


@pytest.fixture
def hooks(remote, tmp_path, monkeypatch):
    """Real hooks in the git directory that record their env and that they ran, with no
    GIT_CONFIG_* in the process env; `fire` pushes a new commit and returns what they saw."""
    for k in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:
        monkeypatch.delenv(k)
    seen = tmp_path / "hook-ran"
    common = Path(git(remote.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    for name in ("pre-push", "reference-transaction"):
        _script(common / "hooks" / name, f"echo {name} >> {seen}; env >> {seen}")

    async def fire(**kw):
        write(remote.repo, "n.txt", os.urandom(4).hex())
        commit_all(remote.repo, "more")  # the person's own commit runs the hooks too
        seen.unlink(missing_ok=True)
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned, **kw)
        return seen.read_text() if seen.exists() else ""

    return SimpleNamespace(fire=fire, seen=seen, remote=remote)


def _unhardened(monkeypatch):
    """No core.hooksPath pin: git's own hooks stay live (the controls)."""
    from kraft.worker import sandbox

    def none(env=None):
        (os.environ if env is None else env)["GIT_CONFIG_COUNT"] = "0"

    monkeypatch.setattr(sandbox, "harden_host_git_env", none)
    monkeypatch.setattr(
        sandbox, "hardened_git_env", lambda: {**os.environ, "GIT_CONFIG_COUNT": "0"}
    )


async def test_a_hook_sees_the_header_without_the_pins_and_nothing_with_them(hooks, monkeypatch):
    with monkeypatch.context() as bare:
        _unhardened(bare)
        control = await hooks.fire()
    assert "reference-transaction" in control and OURS.split()[1] in control, "no proof"
    assert await hooks.fire() == ""


async def test_no_verify_alone_stops_the_pre_push_hook(hooks, monkeypatch):
    """With hooksPath unpinned only `--no-verify` keeps pre-push from running: the control
    (a plain `git push`) runs it."""
    with monkeypatch.context() as bare:
        _unhardened(bare)
        await hooks.fire()
        hooks.seen.unlink()
        git(hooks.remote.repo, "push", "-q", str(hooks.remote.bare), "kraft/abc:refs/heads/ctl")
        assert "pre-push" in hooks.seen.read_text(), "pre-push is not live: no proof"
        assert "pre-push" not in await hooks.fire()


@pytest.fixture
def planted(remote, tmp_path, monkeypatch):
    """What a repo's own config can plant for the dialed URL, at the origin, path and user
    ranks: a recording proxy, a resolve to 127.0.0.2 where a TLS listener (a CA of its own) waits,
    that CA, `sslVerify=false` and `followRedirects=true`. Nothing may reach any of them."""
    for k in ("no_proxy", "NO_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    port = int(remote.web.url.rsplit(":", 1)[1])
    url = f"{remote.web.url}/team/app.git"
    user_url = url.replace("https://", "https://git@")
    ranks = [f"http.{remote.web.url}/", f"http.{url}", f"http.{user_url}"]
    try:
        listener_cm = git_https(tmp_path / "evil", bind=("127.0.0.2", port))
        listener = listener_cm.__enter__()
    except OSError:
        pytest.skip("cannot bind 127.0.0.2 on this machine")
    proxy_cm = recording_proxy()
    seen, proxy = proxy_cm.__enter__()

    def plant(*what):
        if "proxy" in what:
            plant_keys(remote.repo, ["proxy"], proxy, ranks)
            git(remote.repo, "config", "remote.origin.proxy", proxy)
            git(remote.repo, "config", f"remote.{url}.proxy", proxy)
        if "resolve" in what:
            plant_keys(remote.repo, ["curloptResolve"], f"127.0.0.1:{port}:127.0.0.2", ranks)
            plant_keys(remote.repo, ["sslCAInfo"], str(listener.ca), ranks)
            plant_keys(remote.repo, ["sslVerify"], "false", ranks)
            plant_keys(remote.repo, ["sslCAPath"], str(tmp_path / "evil"), ranks)
        plant_keys(remote.repo, ["followRedirects"], "true", ranks)

    yield SimpleNamespace(
        plant=plant, proxy_hits=seen, proxy=proxy, listener=listener, remote=remote
    )
    proxy_cm.__exit__(None, None, None)
    listener_cm.__exit__(None, None, None)


async def _fetch_and_push(r):
    """Both calls, whatever they answer: what matters is who they reached."""
    for call in (
        lambda: builtins.upstream_head(r.repo, "main"),
        lambda: forge_git.push(r.repo, "kraft/abc", account=r.pinned),
    ):
        with contextlib.suppress(Exception):
            await call()


@pytest.mark.parametrize(
    ("what", "leaks_to"),
    [
        pytest.param(("proxy",), "proxy", id="proxy-keys"),
        pytest.param(("resolve",), "listener", id="resolve-ca-and-verify-keys"),
        pytest.param(("proxy", "resolve"), "proxy", id="every-key"),
    ],
)
async def test_no_repo_config_key_routes_the_header_anywhere_but_the_origin(
    planted, monkeypatch, what, leaks_to
):
    r = planted.remote
    planted.plant(*what)
    with monkeypatch.context() as bare:
        bare.setattr(git_auth, "git_env", bare_header_env)
        await _fetch_and_push(r)
    reached = {
        "proxy": bool(planted.proxy_hits),
        "listener": OURS in planted.listener.authorizations,
    }
    assert reached[leaks_to], "the control leaked nowhere: the row proves nothing"
    planted.proxy_hits.clear()
    planted.listener.authorizations.clear()
    r.web.authorizations.clear()
    await _fetch_and_push(r)
    assert planted.proxy_hits == []
    assert planted.listener.authorizations == []
    _ours_only(r.web)


@pytest.mark.parametrize("user", ["git@", "git:pw@"])
@pytest.mark.parametrize("remote", [pytest.param({}, id="origin-names-a-user")], indirect=True)
async def test_an_origin_url_that_names_a_user_never_receives_the_header(remote, user):
    port = remote.web.url.rsplit(":", 1)[1]
    git(remote.repo, "remote", "set-url", "origin", f"https://{user}127.0.0.1:{port}/team/app.git")
    with pytest.raises(ForgeError, match="names a user") as caught:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    assert "git@" not in str(caught.value) and "pw" not in str(caught.value)
    with pytest.raises(builtins.BaseFetchRefused, match="names a user"):
        await builtins.upstream_head(remote.repo, "main")
    assert remote.web.authorizations == []


async def test_a_repo_section_named_by_the_url_never_receives_a_connect(remote, monkeypatch):
    """git applies `[remote "<url>"]` to a call that dials that URL; Kraft dials `origin`."""
    for k in ("no_proxy", "NO_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)
    url = f"{remote.web.url}/team/app.git"
    with recording_proxy() as (seen, proxy):
        git(remote.repo, "config", f"remote.{url}.proxy", proxy)
        subprocess.run(["git", "ls-remote", url], cwd=remote.repo, capture_output=True)
        assert seen, "git ignored the section: no proof"
        seen.clear()
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
        assert seen == []
    _ours_only(remote.web)


async def test_a_push_url_that_differs_from_the_fetch_url_is_refused(remote):
    git(remote.repo, "config", "remote.origin.pushurl", f"{remote.web.url}/team/other.git")
    with pytest.raises(ForgeError, match="push URL differs from its fetch URL"):
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    assert remote.web.authorizations == []


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed")
async def test_git_lfs_never_reaches_a_planted_proxy(planted, monkeypatch):
    r = planted.remote
    git(r.repo, "lfs", "install", "--local")
    git(r.repo, "lfs", "track", "*.bin")
    write(r.repo, "a.bin", "hello lfs\n")
    commit_all(r.repo, "lfs")
    planted.plant("proxy")
    with monkeypatch.context() as bare:
        bare.setattr(git_auth, "git_env", bare_header_env)
        await _fetch_and_push(r)
    assert planted.proxy_hits, "the control never reached the proxy: no proof"
    planted.proxy_hits.clear()
    await _fetch_and_push(r)
    assert planted.proxy_hits == []
