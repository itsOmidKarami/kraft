"""Every remote git call of an API account's repo, over a real HTTPS remote on 127.0.0.1: what
the wire sees (`support.git_https`) and what each site does when the token fails."""

from __future__ import annotations

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
from support.git_https import git_https
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
REDIRECT_WORDS = "origin redirects elsewhere; point origin at the new address"
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
    with git_https(srv, token=opts.get("server", TOK)) as web:
        monkeypatch.setenv("GIT_SSL_CAINFO", str(web.ca))
        web.redirect = "https://127.0.0.1:9" if opts.get("redirect") else None
        account = acct(web.url, **opts.get("source", {}))
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
    with git_https(tmp_path / "srv2") as second:
        bundle = tmp_path / "ca-both.pem"
        bundle.write_text(remote.web.ca.read_text() + second.ca.read_text())
        monkeypatch.setenv("GIT_SSL_CAINFO", str(bundle))
        git(remote.repo, "push", "-q", str(_bare(second.root)), "main")
        git(
            remote.repo,
            "config",
            f"url.{second.url}/team/app.git.insteadOf",
            f"{remote.web.url}/team/app.git",
        )
        assert await builtins.upstream_head(remote.repo, "main")
        assert second.authorizations and set(second.authorizations) == {None}
        assert remote.web.authorizations == []


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
@pytest.mark.parametrize("remote", [{"server": "another-token"}], indirect=True)
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
            bare.setattr(git_auth, "_exact", lambda origin, url: None)
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
        await builtins.upstream_head(remote.repo, "main")  # a certificate error is a stale base
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


@pytest.mark.parametrize(
    ("remote", "site", "raises"),
    [
        pytest.param({"token": False}, _base_check, HTTPException, id="base-check-is-a-422"),
        pytest.param({"token": False}, _bring_home, None, id="bring-home-is-false"),
    ],
    indirect=["remote"],
)
async def test_an_unreadable_token_is_each_sites_own_refusal(
    remote, database, run_dirs, site, raises
):
    if raises is not None:
        with pytest.raises(raises) as caught:
            await site(remote, database, run_dirs)
        assert caught.value.status_code == 422 and "corp-gl" in str(caught.value.detail)
        return
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
    ("remote", "stops"),
    [
        pytest.param({"server": "another-token"}, "stops", id="wrong-token-stops"),
        pytest.param({"token": False}, "stops", id="unreadable-token-stops"),
        pytest.param({"server": "another-token"}, "worktree", id="ensure-worktree-cuts-nothing"),
        pytest.param({}, "offline", id="offline-keeps-the-stale-base"),
        pytest.param(
            {"source": {"from": "git-credential"}},
            "stale",
            id="git-credential-keeps-the-stale-base",
        ),
    ],
    indirect=["remote"],
)
async def test_a_base_fetch_the_token_cannot_authenticate_stops(remote, database, run_dirs, stops):
    stale = _fetched(remote)
    if stops in ("offline", "stale"):
        if stops == "offline":
            remote.web.stop()
        assert await builtins.upstream_head(remote.repo, "main") == stale
        return
    if stops == "worktree":
        await _item_on(database, remote.repo)
        with pytest.raises(builtins.BaseFetchRefused):
            await wtree.ensure(database, run_dirs, remote.repo)
        assert not (run_dirs.worktrees / "w1").exists(), "nothing was cut from a stale base"
        return
    with pytest.raises(builtins.BaseFetchRefused) as caught:
        await builtins.upstream_head(remote.repo, "main")
    exc = caught.value
    assert (exc.reason, exc.account, exc.source) == ("auth", "corp-gl", SOURCE)
    assert "`" not in str(exc)
    _clean(exc, exc.reason, exc.args)


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
    _clean(exc, exc.reason, exc.args)


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param("api", id="api-backend-pushes-with-its-pinned-account"),
        pytest.param("cli", id="cli-backend-pushes-with-no-header"),
        pytest.param("edited", id="an-edited-web-url-is-refused"),
    ],
)
async def test_a_push_follows_its_backend(remote, backend):
    if backend == "api":
        await GitLabApi("corp-gl", remote.account).push(repo=remote.repo, branch="kraft/abc")
        _ours_only(remote.web)
    elif backend == "cli":
        with pytest.raises(ForgeError):  # git's own credential: the remote wants a token
            await GlabCli().push(repo=remote.repo, branch="kraft/abc")
        assert remote.web.authorizations and set(remote.web.authorizations) == {None}
    else:
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


@pytest.mark.parametrize("remote", [{"server": "another-token"}], indirect=True)
async def test_a_verbose_curl_never_prints_the_header_into_a_stop(remote, monkeypatch):
    monkeypatch.setenv("GIT_CURL_VERBOSE", "1")  # beats GIT_TRACE_CURL, and prints to stderr
    monkeypatch.setenv("GIT_TRACE_REDACT", "0")
    with pytest.raises(ForgeError) as caught:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    _ours_only(remote.web)
    _clean(caught.value, caught.value.args)
