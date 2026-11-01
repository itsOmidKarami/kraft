"""What a repo's own config can do to trust and to git-lfs on a credentialed call: it can name a
CA, an LFS endpoint, or a key at an LFS path, and none of it may move the header."""

# ruff: noqa: F811  (fixtures imported from the HTTPS rows)
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support.git_https import bare_header_env, plant_keys
from support.harness import commit_all, git, write

from kraft import builtins
from kraft.adapters.forge import git as forge_git
from kraft.adapters.forge import git_auth
from kraft.adapters.forge.models import ForgeError

from .test_git_auth_https import (  # noqa: F401
    _LFS_POINTER,
    OURS,
    _fetch_and_push,
    _script,
    planted,
    remote,
)


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
        pytest.param(("lfs.pushurl", "https://other.example/x"), id="lfs-pushurl"),
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
        pytest.param("ssh://git@other.example/x/info/lfs", True, id="ssh-endpoint"),
    ],
)
@pytest.mark.parametrize(
    "key", ["lfs.url", "lfs.pushurl", "remote.origin.lfsurl", "remote.origin.lfspushurl"]
)
async def test_a_base_lfsconfig_endpoint_is_refused_unless_it_is_origins_own(
    remote, fake_lfs, key, value, refused
):
    url = f"{remote.web.url}/team/app.git"
    value = value.format(url=url, stem=url.removesuffix(".git"), origin=remote.web.url)
    section, _, name = key.rpartition(".")
    head = "[lfs]" if section == "lfs" else '[remote "origin"]'
    _base_lfsconfig(remote, f"{head}\n\t{name} = {value}\n")
    if refused:
        with pytest.raises(ForgeError, match="base branch's .lfsconfig sends LFS") as caught:
            await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
        assert "push this branch yourself" in str(caught.value)
        assert "move LFS to origin" in str(caught.value)
        assert "other.example" not in str(caught.value) and value not in str(caught.value)
        assert not fake_lfs.exists()
    else:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
        assert fake_lfs.exists(), "git-lfs never ran: nothing was checked"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': server certificate verification failed. CAfile: /x/cacert.pem CRLfile: none",  # noqa: E501 (a captured message)
            id="tls-gnutls-server-certificate-verification-failed",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': SSL certificate verification failed: certificate signer not trusted. (CAfile: /x/cacert.pem CRLfile: none)",  # noqa: E501 (a captured message)
            id="tls-newer-ssl-certificate-verification-failed",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': SSL certificate problem: certificate has expired",  # noqa: E501 (a captured message)
            id="tls-openssl-ssl-certificate-problem",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': SSL certificate OpenSSL verify result: certificate has expired (10)",  # noqa: E501 (a captured message)
            id="tls-openssl-verify-result",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': self signed certificate in certificate chain",  # noqa: E501 (a captured message)
            id="tls-self-signed",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': Problem with the SSL CA cert (path? access rights?)",  # noqa: E501 (a captured message)
            id="tls-unreadable-ca-bundle",
        ),
        pytest.param(
            "error:0A000086:SSL routines::certificate verify failed",
            id="tls-go-x509-via-lfs",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': SSL: certificate subject name 'a' does not match target host name 'h'",  # noqa: E501 (a captured message)
            id="tls-name-mismatch",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': Peer certificate cannot be authenticated with given CA certificates",  # noqa: E501 (a captured message)
            id="tls-nss-peer-certificate",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': SSL certificate verification failed (CAfile: /x/cacert.pem)",  # noqa: E501 (a captured message)
            id="tls-newer-wording-alone",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': certificate signer not trusted",
            id="tls-signer-not-trusted-alone",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': unable to get local issuer certificate",
            id="tls-issuer-alone",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': SSL peer certificate or SSH remote key was not OK",  # noqa: E501 (a captured message)
            id="tls-ssh-remote-key-not-ok",
        ),
        pytest.param(
            "fatal: unable to access 'https://h/o/r.git/': no alternative certificate subject name matches target host name 'h'",  # noqa: E501 (a captured message)
            id="tls-no-alternative-subject-name",
        ),
    ],
)
async def test_a_git_tls_failure_is_a_stop_whatever_the_curl_behind_it_says(
    tmp_path, monkeypatch, text
):
    """Each wording stamps as a certificate stop (so a fetch stops instead of using a stale
    base), and a push, whose error passes through the same stamp, names ca_file."""
    from .test_git_auth import HTTPS, acct, auth, rig

    assert auth().stops(text) and auth().untrusted(text)
    exc = auth().stamp(ForgeError(text))
    assert (exc.reason, exc.account) == ("tls", "corp-gl")
    assert "verify origin's certificate" in str(exc) and "ca_file" in str(exc)

    async def run_git(repo, args, **_):
        raise ForgeError(f"git {args[1]} failed: {text}")

    repo, _ = rig(tmp_path, monkeypatch, remote=HTTPS)
    monkeypatch.setattr(forge_git, "run_git", run_git)
    with pytest.raises(ForgeError, match="ca_file") as caught:
        await forge_git.push(repo, "main", account=("corp-gl", acct()))
    assert caught.value.reason == "tls"


def _program(path, marker):
    _script(path, f"env >> {marker}; exit 1")
    return path


@pytest.fixture
def programs(remote, tmp_path):
    """Every config key that makes git run a program or fetch a URL of its own picking during a
    fetch and a push, planted in the repo's config; each program records its environment."""
    marks = {k: tmp_path / f"{k}-ran" for k in ("gpg", "alternate")}
    git(remote.repo, "config", "gpg.program", str(_program(tmp_path / "gpg.sh", marks["gpg"])))
    git(remote.repo, "config", "push.gpgSign", "true")
    git(remote.bare, "config", "receive.certNonceSeed", "seed")  # the server asks for a signature
    alt = _program(tmp_path / "alt.sh", marks["alternate"])
    git(remote.repo, "config", "core.alternateRefsCommand", str(alt))
    other = tmp_path / "other.git"
    git(tmp_path, "init", "--bare", "-q", str(other))
    common = git(remote.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    (Path(common) / "objects" / "info" / "alternates").write_text(f"{other}/objects\n")
    git(remote.repo, "config", "fetch.bundleURI", f"{remote.web.url}/other/x.bundle")
    return marks


async def test_no_program_launching_key_runs_with_the_header_env(remote, programs, monkeypatch):
    """The control (only the header pinned) runs the signer, the alternate-refs command and asks
    for a bundle at a URL on the origin, each with the header in view; the pins stop all three."""
    for k in [k for k in os.environ if k.startswith("GIT_CONFIG_")]:
        monkeypatch.delenv(k)
    with monkeypatch.context() as bare:
        bare.setattr(git_auth, "git_env", bare_header_env)
        await _fetch_and_push(remote)
    for name, mark in programs.items():
        assert mark.exists() and "Authorization" in mark.read_text(), f"{name} did not run"
    assert any(p == "/other/x.bundle" and a == OURS for p, a in remote.web.requests), "no bundle"
    for mark in programs.values():
        mark.unlink()
    remote.web.requests.clear()
    await _fetch_and_push(remote)
    assert not any(mark.exists() for mark in programs.values())
    assert not [p for p, _ in remote.web.requests if p.startswith("/other/")]
    assert remote.web.requests and {a for _, a in remote.web.requests} == {OURS}


async def test_another_url_on_the_origin_never_receives_the_header(remote, planted):
    """The class behind a `fetch.bundleURI`: git dials another URL on the origin. With the header
    pinned at the origin, that URL carried it (and a path-ranked proxy read it); pinned at the
    exact URLs only, it carries none. A `bundleURI` is also pinned empty (the programs row)."""
    other = f"{remote.web.url}/other/x.git"
    git(remote.repo, "config", "fetch.bundleURI", other)
    plant_keys(remote.repo, ["proxy"], planted.proxy, [f"http.{remote.web.url}/other"])

    async def dial_other(env_for):
        built = await git_auth.auth_for_account(remote.repo, *remote.pinned, push=True)
        subprocess.run(
            ["git", "ls-remote", other],
            cwd=remote.repo,
            env=env_for(os.environ, built),
            capture_output=True,
            timeout=60,
        )

    remote.web.requests.clear()
    await dial_other(bare_header_env)
    assert planted.proxy_hits, "the control never reached the planted proxy: no proof"
    planted.proxy_hits.clear()
    # No proxy now: the server itself records what header each URL carried.
    git(remote.repo, "config", "--unset-all", f"http.{remote.web.url}/other.proxy")
    await dial_other(bare_header_env)
    assert OURS in [a for p, a in remote.web.requests if p.startswith("/other/")]
    remote.web.requests.clear()
    await dial_other(git_auth.git_env)
    seen = [a for p, a in remote.web.requests if p.startswith("/other/")]
    assert seen and OURS not in seen


@pytest.mark.parametrize(
    "keys",
    [
        pytest.param(
            [("lfs.customtransfer.evil.path", "/bin/true")], id="custom-transfer-agent-path"
        ),
        pytest.param([("lfs.standalonetransferagent", "evil")], id="standalone-transfer-agent"),
    ],
)
async def test_an_lfs_transfer_agent_is_refused_before_git_lfs_runs(remote, fake_lfs, keys):
    for key in keys:
        git(remote.repo, "config", *key)
    with pytest.raises(ForgeError, match="custom transfer agent") as caught:
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    assert "/bin/true" not in str(caught.value) and "`" not in str(caught.value)
    assert not fake_lfs.exists()


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed")
async def test_an_ssh_lfs_endpoint_never_runs_ssh_with_the_header_env(
    remote, tmp_path, monkeypatch
):
    """`lfs.url=ssh://...` with a `core.sshCommand`: git-lfs ran the command with the header env
    (the control). The push refuses it, and `GIT_SSH_COMMAND` fails at once even when nothing
    refuses (the last call). The same `ssh://` in the base `.lfsconfig` is a row of
    `test_a_base_lfsconfig_endpoint_is_refused_unless_it_is_origins_own`."""
    marker = tmp_path / "ssh-ran"
    ssh = _program(tmp_path / "ssh.sh", marker)
    git(remote.repo, "config", "core.sshCommand", str(ssh))
    git(remote.repo, "lfs", "install", "--local")
    git(remote.repo, "lfs", "track", "*.bin")
    write(remote.repo, "a.bin", "hello lfs\n")
    commit_all(remote.repo, "lfs")
    head = git(remote.repo, "rev-parse", "HEAD")
    git(remote.repo, "config", "lfs.url", "ssh://git@other.example/x")
    built = await git_auth.auth_for_account(remote.repo, *remote.pinned, push=True)
    env = bare_header_env(os.environ, built)
    subprocess.run(
        ["git", "lfs", "pre-push", "origin", "origin"],
        cwd=remote.repo,
        env=env,
        input=f"refs/heads/kraft/abc {head} refs/heads/kraft/abc {'0' * 40}\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert marker.exists() and "Authorization" in marker.read_text(), "ssh never ran: no proof"
    marker.unlink()
    with pytest.raises(ForgeError, match="pointed elsewhere"):
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    env = git_auth.git_env(os.environ, built)
    subprocess.run(
        ["git", "lfs", "pre-push", "origin", "origin"],
        cwd=remote.repo,
        env=env,
        input=f"refs/heads/kraft/abc {head} refs/heads/kraft/abc {'0' * 40}\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert not marker.exists()


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed")
@pytest.mark.parametrize(
    "dotgit", [pytest.param(True, id="dot-git"), pytest.param(False, id="no-dot-git")]
)
async def test_fetch_push_and_lfs_still_carry_the_header_at_the_exact_urls(remote, dotgit):
    """The header is pinned at the exact URL only, one spelling: `/info/refs`, `/git-upload-pack`
    and git-lfs's `/info/lfs/objects/batch` (under `<url>.git` for a remote without `.git`) all
    still carry it, for a remote with and without a `.git` suffix."""
    git(
        remote.repo,
        "remote",
        "set-url",
        "origin",
        f"{remote.web.url}/team/app" + (".git" if dotgit else ""),
    )
    git(remote.repo, "lfs", "install", "--local")
    git(remote.repo, "lfs", "track", "*.bin")
    write(remote.repo, "a.bin", "hello lfs\n")
    commit_all(remote.repo, "lfs")
    remote.web.requests.clear()
    await builtins.upstream_head(remote.repo, "main")
    with contextlib.suppress(ForgeError):
        await forge_git.push(remote.repo, "kraft/abc", account=remote.pinned)
    tails = {p.removeprefix("/team/app").removeprefix(".git") for p, _ in remote.web.requests}
    assert {"/info/refs", "/git-upload-pack", "/info/lfs/objects/batch"} <= tails
    assert remote.web.requests and {a for _, a in remote.web.requests} == {OURS}
