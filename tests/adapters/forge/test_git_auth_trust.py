"""What a repo's own config can do to trust and to git-lfs on a credentialed call: it can name a
CA, an LFS endpoint, or a key at an LFS path, and none of it may move the header."""

# ruff: noqa: F811  (fixtures imported from the HTTPS rows)
from __future__ import annotations

import contextlib
import os
import shutil

import pytest
from support.git_https import bare_header_env, plant_keys
from support.harness import commit_all, git, write

from kraft import builtins
from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge import git_auth
from kraft.adapters.forge.models import ForgeError

from .test_git_auth_https import _LFS_POINTER, OURS, _script, planted, remote  # noqa: F401


@pytest.fixture
def untrusted(remote, tmp_path, monkeypatch):
    """The remote's CA is trusted by nothing the person set: no env, no global gitconfig, no
    `ca_file`. A repo that plants that CA at path rank would make the origin trusted."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in ("XDG_CONFIG_HOME", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH"):
        monkeypatch.delenv(k, raising=False)
    url = f"{remote.web.url}/team/app.git"
    git(remote.repo, "config", f"http.{url}.sslCAInfo", str(remote.web.ca))
    git(remote.repo, "config", f"http.{remote.web.url}/.sslCAInfo", str(remote.web.ca))
    return remote


async def test_a_repos_planted_ca_never_makes_an_untrusted_origin_trusted(untrusted, monkeypatch):
    with monkeypatch.context() as bare:  # the control: only the header pinned
        bare.setattr(git_auth, "git_env", bare_header_env)
        await builtins.upstream_head(untrusted.repo, "main")
    assert OURS in untrusted.web.authorizations, "the planted CA did not apply: no proof"
    untrusted.web.authorizations.clear()
    with pytest.raises(builtins.BaseFetchRefused, match="ca_file") as caught:
        await builtins.upstream_head(untrusted.repo, "main")
    assert (caught.value.reason, caught.value.account) == ("tls", "corp-gl")
    assert "`" not in str(caught.value) and "global gitconfig" in str(caught.value)
    assert untrusted.web.authorizations == []


async def test_a_push_to_an_origin_nothing_trusts_stops_naming_ca_file(untrusted):
    with pytest.raises(ForgeError, match="ca_file") as caught:
        await forge_git.push(untrusted.repo, "kraft/abc", account=untrusted.pinned)
    assert (caught.value.reason, caught.value.account) == ("tls", "corp-gl")
    assert untrusted.web.authorizations == []


@pytest.fixture
def fake_lfs(remote, tmp_path, monkeypatch):
    """An LFS pointer on the branch and a `git-lfs` that records that it ran."""
    ran = tmp_path / "lfs-ran"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _script(bindir / "git-lfs", f"echo ran >> {ran}\nexit 0")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    write(remote.repo, "big.bin", _LFS_POINTER)
    commit_all(remote.repo, "an lfs pointer")
    return ran


async def test_an_lfs_push_with_no_endpoint_override_runs_git_lfs(remote, fake_lfs):
    await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    assert fake_lfs.exists(), "git-lfs never ran: the refusal rows prove nothing"


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(("lfs.url", "https://other.example/x"), id="lfs-url"),
        pytest.param(("remote.origin.lfsurl", "https://other.example/x"), id="remote-lfsurl"),
        pytest.param(
            ("remote.origin.lfspushurl", "https://other.example/x"), id="remote-lfspushurl"
        ),
    ],
)
async def test_an_lfs_endpoint_a_repo_names_is_refused_before_git_lfs_runs(remote, fake_lfs, key):
    git(remote.repo, "config", *key)
    with pytest.raises(ForgeError, match="git-lfs is pointed elsewhere") as caught:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    assert "other.example" not in str(caught.value) and "`" not in str(caught.value)
    assert not fake_lfs.exists()


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed")
async def test_a_key_at_an_lfs_path_never_routes_the_header(planted, monkeypatch):
    """git-lfs 3.4.1 matches `http.<url>.*` against the remote URL it was given, not each request
    path, so keys at `<url>/info/lfs` never apply (the first phase shows it); the keys at the
    URL itself do, and the pins beat them (the second)."""
    r = planted.remote
    git(r.repo, "lfs", "install", "--local")
    git(r.repo, "lfs", "track", "*.bin")
    write(r.repo, "a.bin", "hello lfs\n")
    commit_all(r.repo, "lfs")
    url = f"{r.web.url}/team/app.git"
    deeper = [f"http.{url}/info/lfs", f"http.{url}/info/lfs/objects/batch"]
    plant_keys(r.repo, ["proxy"], planted.proxy, deeper)
    plant_keys(r.repo, ["sslVerify"], "false", deeper)

    async def both():
        for call in (
            lambda: builtins.upstream_head(r.repo, "main"),
            lambda: forge_git.push(r.repo, "kraft/abc", account=r.pinned),
        ):
            with contextlib.suppress(Exception):
                await call()

    with monkeypatch.context() as bare:
        bare.setattr(git_auth, "git_env", bare_header_env)
        await both()
    assert planted.proxy_hits == [], "git-lfs now applies deeper keys: pin them"
    planted.plant("proxy")  # at the URL itself, and the origin
    with monkeypatch.context() as bare:
        bare.setattr(git_auth, "git_env", bare_header_env)
        await both()
    assert planted.proxy_hits, "the control never reached the proxy: no proof"
    planted.proxy_hits.clear()
    await both()
    assert planted.proxy_hits == []


def _base_lfsconfig(r, text):
    """`.lfsconfig` already on the base branch (so the branch's own work did not move it)."""
    git(r.repo, "checkout", "-q", "main")
    write(r.repo, ".lfsconfig", text)
    commit_all(r.repo, "base lfsconfig")
    git(r.repo, "push", "-q", str(r.bare), "main")
    git(r.repo, "fetch", "-q", str(r.bare), "+refs/heads/main:refs/remotes/origin/main")
    git(r.repo, "checkout", "-q", "kraft/abc")
    git(r.repo, "rebase", "-q", "main")


@pytest.mark.parametrize(
    ("value", "refused"),
    [
        pytest.param("{url}/info/lfs", False, id="own-endpoint-is-fine"),
        pytest.param("{stem}/info/lfs", False, id="own-endpoint-without-dot-git-is-fine"),
        pytest.param("{url}/info/lfs/", False, id="own-endpoint-with-a-slash-is-fine"),
        pytest.param("https://other.example/x/info/lfs", True, id="other-host"),
        pytest.param("{origin}/team/other.git/info/lfs", True, id="other-path"),
        pytest.param("http://127.0.0.1:1/team/app.git/info/lfs", True, id="other-scheme-and-port"),
    ],
)
@pytest.mark.parametrize("key", ["lfs.url", "remote.origin.lfsurl", "remote.origin.lfspushurl"])
async def test_a_base_lfsconfig_endpoint_is_refused_unless_it_is_origins_own(
    remote, fake_lfs, key, value, refused
):
    url = f"{remote.web.url}/team/app.git"
    value = value.format(url=url, stem=url.removesuffix(".git"), origin=remote.web.url)
    section, _, name = key.rpartition(".")
    head = "[lfs]" if section == "lfs" else '[remote "origin"]'
    _base_lfsconfig(remote, f"{head}\n\t{name} = {value}\n")
    if refused:
        with pytest.raises(ForgeError, match="git-lfs is pointed elsewhere") as caught:
            await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
        assert "other.example" not in str(caught.value) and value not in str(caught.value)
        assert not fake_lfs.exists()
    else:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
        assert fake_lfs.exists(), "git-lfs never ran: nothing was checked"
