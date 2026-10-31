"""Every remote git call of an API account's repo, over a real HTTPS remote on 127.0.0.1: what
the wire sees (`support.git_https`) and what each site does when the token fails."""

from __future__ import annotations

import stat
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
        with pytest.raises(builtins.BaseFetchRefused, match="origin redirects elsewhere"):
            await builtins.upstream_head(remote.repo, "main")
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


@pytest.mark.parametrize("remote", [{"server": "another-token"}], indirect=True)
async def test_a_path_scoped_saved_header_overrides_ours_so_the_stop_blames_the_account(remote):
    """The known limit: git takes the more specific key, so the saved header goes out and ours
    never does. The token does not leak; the stop names the account all the same."""
    old = "AUTHORIZATION: basic b2xkOm9sZA=="
    git(remote.repo, "config", f"http.{remote.web.url}/team/app.git.extraheader", old)
    with pytest.raises(builtins.BaseFetchRefused) as caught:
        await builtins.upstream_head(remote.repo, "main")
    assert caught.value.account == "corp-gl"
    assert remote.web.authorizations and OURS not in remote.web.authorizations


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
    assert OURS.split()[1] not in logged and TOK not in logged


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


@pytest.mark.parametrize(
    "remote",
    [
        pytest.param({"server": "another-token"}, id="wrong-token"),
        pytest.param({"redirect": True}, id="redirect"),
    ],
    indirect=True,
)
async def test_a_push_the_token_cannot_authenticate_is_a_token_stop(remote):
    with pytest.raises(ForgeError) as caught:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    exc = caught.value
    assert (exc.reason, exc.account, exc.source) == ("auth", "corp-gl", SOURCE)
    assert git(remote.bare, "branch", "--list", "kraft/abc") == ""


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
