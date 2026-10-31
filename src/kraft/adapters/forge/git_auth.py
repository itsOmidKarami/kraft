"""Git over HTTPS with an API account's token: one builder for the environment of a call that
talks to origin. The header lives in that call's environment copy, never in `os.environ`,
argv or `.git/config`, and is keyed on the account's own origin."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import certifi

from kraft import config, forges
from kraft.adapters.forge import credentials
from kraft.adapters.forge.models import ForgeError
from kraft.config import ConfigError, RepoEntry
from kraft.forges import ForgeAccount, ForgesFile
from kraft.worker import sandbox

#: Token sources Kraft injects; `git-credential` stays git's own.
INJECTED = frozenset({"command", "env", "file"})

AUTH_FAILED = re.compile(
    r"Authentication failed|could not read (Username|Password)|terminal prompts disabled"
    r"|returned error: 40[13]",
    re.IGNORECASE,
)

# GitHub answers a private repo it will not show a bad token with "not found".
_NOT_FOUND = re.compile(r"repository '.*' not found", re.IGNORECASE)
_REDIRECTED = re.compile(r"returned error: 30[1278]", re.IGNORECASE)
_TLS = re.compile(
    r"SSL certificate problem|Problem with the SSL CA cert|server certificate verification"
    r" failed|certificate verify failed",
    re.IGNORECASE,
)

_STRIPPED = ("GIT_CURL_VERBOSE", "GIT_TRANSFER_TRACE", "GIT_SSL_NO_VERIFY")
_TRACE2 = ("GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF")
_PROXY_VARS = ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")

Reader = Callable[[], Awaitable[tuple[Sequence[RepoEntry], ForgesFile | None]]]
_READER: Reader | None = None


@dataclass(frozen=True)
class GitAuth:
    name: str
    account: ForgeAccount
    origin: str  # https://host[:port], no trailing slash
    header: str = field(repr=False)  # Authorization: Basic <basic_form>
    #: The exact URLs git will dial on this origin (no userinfo, no query), each pinned too.
    urls: tuple[str, ...] = ()
    #: The CA bundle and directory git is pinned to (never a repo-local one): the account's
    #: `ca_file`, else the environment's or the global gitconfig's, else the system's.
    ca_info: str = ""
    ca_path: str = ""

    def refused(self, text: str) -> bool:
        """Whether git's words say the forge refused the header's token."""
        return bool(
            AUTH_FAILED.search(text) or (self.account.forge == "github" and _NOT_FOUND.search(text))
        )

    def redirect_words(self) -> str:
        return (
            f"forge account {self.name}: origin redirects elsewhere; "
            "point origin at the new address, then retry"
        )

    def tls_words(self) -> str:
        return (
            f"forge account {self.name}: git could not verify origin's certificate; set "
            "ca_file on the account, or http.sslCAInfo in your global gitconfig, then retry"
        )

    @staticmethod
    def redirected(text: str) -> bool:
        return bool(_REDIRECTED.search(text))

    @staticmethod
    def untrusted(text: str) -> bool:
        return bool(_TLS.search(text))

    def stops(self, text: str) -> bool:
        """Whether git's words are a stop of the account's: the token was refused, the call was
        sent elsewhere, or the origin's certificate is not trusted (Kraft pins the CA)."""
        return self.refused(text) or self.redirected(text) or self.untrusted(text)

    def stop_words(self, text: str) -> str | None:
        """The fixed words for a redirect or an untrusted certificate, else None."""
        if self.redirected(text):
            return self.redirect_words()
        return self.tls_words() if self.untrusted(text) else None

    def stamp(self, exc: ForgeError, words: str | None = None) -> ForgeError:
        """Mark `exc` a token stop, in place, when git's words (`exc`'s own text unless `words`
        is given) say the forge refused the token or sent the call elsewhere (the redirect is
        not followed, so the token stays home)."""
        text = str(exc) if words is None else words
        if (fixed := self.stop_words(text)) is not None:
            exc.args = (fixed,)
            exc.reason = "auth" if self.redirected(text) else "tls"
        elif self.refused(text):
            exc.reason = "forbidden" if "returned error: 403" in text.lower() else "auth"
        else:
            return exc
        exc.account = self.name
        exc.source = self.account.source().label()
        return exc


def set_reader(fn: Reader | None) -> None:
    """The server installs its reader of repos.yaml and forges.yaml; none means no header."""
    global _READER
    _READER = fn


class Unread(list):
    """What `server_context` returns for the repos when repos.yaml does not load: no entries,
    and why. Falling back to git's own credential would hide that a token was never sent."""

    def __init__(self, why: str):
        super().__init__()
        self.why = why


async def server_context(st) -> tuple[Sequence[RepoEntry], ForgesFile | None]:
    if st.forges is None or not any(_injected(a) for a in st.forges.accounts.values()):
        return [], st.forges  # no account could carry a header: read no repos.yaml
    try:
        repos = await asyncio.to_thread(config.load_repos, st.templates_dir / "repos.yaml")
    except ConfigError as exc:
        return Unread((str(exc).splitlines() or ["unreadable"])[0][:200]), st.forges
    return repos, st.forges


def _origin(account: ForgeAccount) -> str:
    _, host, port = forges.origin(account.web_url)
    host = f"[{host}]" if ":" in host else host  # an IPv6 literal
    return f"https://{host}" + (f":{port}" if port != 443 else "")


def _exact(origin: str, url: str) -> str | None:
    """`url` as git's `http.<url>.*` key wants it: the origin and the path, no trailing slash."""
    path = urlsplit(url).path.rstrip("/")
    return origin + path if path else None


def _injected(account: ForgeAccount) -> bool:
    return account.client == "api" and account.source().from_ in INJECTED


def _trust(account: ForgeAccount) -> tuple[str, str]:
    """(sslCAInfo, sslCAPath) from sources a repo cannot write: the account's `ca_file`, the
    environment, the system and global gitconfig (read from a neutral directory, so no repo's
    own config answers), then certifi's bundle. Always a bundle: an empty `sslCAInfo` is read
    as 'no CA at all', and an unpinned one lets a repo name its own."""
    asked = credentials.neutral_run(
        ["git", "config", "--get-urlmatch", "http", account.web_url], timeout=10
    )
    conf = dict(line.partition(" ")[::2] for line in asked.stdout.splitlines())
    info = (
        account.ca_file
        or os.environ.get("GIT_SSL_CAINFO")
        or conf.get("http.sslcainfo")
        or certifi.where()
    )
    path = os.environ.get("GIT_SSL_CAPATH") or conf.get("http.sslcapath") or ""
    return os.path.expanduser(info), os.path.expanduser(path)


async def _git(repo: Path, *args: str) -> list[str]:
    out = await asyncio.to_thread(config.git_read, repo, *args, expected_failure=True)
    return out.splitlines() if out else []


async def auth_for_account(
    repo: Path, name: str, account: ForgeAccount, *, push: bool = True
) -> GitAuth | None:
    """The header for `account` on `repo`'s origin, from the URL git will dial; None when
    that is SSH or local, or (a fetch) another origin. A push to another http(s) origin
    is refused. Needs no reader: a push passes the account its item pinned."""
    if not _injected(account):
        return None
    origin = _origin(account)
    urls = await _git(repo, "remote", "get-url", *(("--push", "--all") if push else ()), "origin")
    carry, exact, other = False, [], False
    for url in urls:
        scheme, host, port = forges.origin(url)
        if scheme not in ("http", "https"):
            other = True
            continue
        if scheme == "https" and (host, port) == forges.origin(account.web_url)[1:]:
            if "@" in urlsplit(url).netloc:
                # git ranks a key naming the user above any pin that does not.
                raise ForgeError(
                    f"forge account {name}: origin's URL names a user; remove it so Kraft "
                    "can send its token, then retry"
                )
            carry = True
            if (one := _exact(origin, url)) and one not in exact:
                exact.append(one)
        elif push:
            raise ForgeError(
                f"forge account {name}: {repo.name}'s push URL is not on {origin}, the "
                "account's address, so Kraft will not send its token there; point origin's "
                "push URL at it or at an SSH address, then retry"
            )
    if not carry:
        return None
    if other:
        # ssh, a `foo::` helper or a local path would run with the header env.
        raise ForgeError(
            f"forge account {name}: {repo.name} has a push URL that is not https on {origin} "
            "next to one that is, so Kraft will not send its token through it; point every "
            "push URL at the account's address, then retry"
        )
    if await _git(repo, "config", "--get", "remote.origin.vcs"):
        # git would run `git-remote-<vcs>` with the header in its environment.
        raise ForgeError(
            f"forge account {name}: origin uses a remote helper (remote.origin.vcs); Kraft "
            "sends its token only to git's own https transport, so unset it and retry"
        )
    source = account.source().label()
    try:
        token = await credentials.token_for(name, account)
    except credentials.NoToken as exc:
        raise ForgeError(
            f"forge account {name}: {exc}", reason="auth", account=name, source=source
        ) from None
    form = credentials.basic_form(token, account.source().kind, forge=account.forge)
    info, path = await asyncio.to_thread(_trust, account)
    header = f"Authorization: Basic {form}"
    return GitAuth(name, account, origin, header, tuple(exact), info, path)


async def auth_for(repo: Path, *, push: bool = False) -> GitAuth | None:
    """The live account's header for a call in `repo` (a checkout, item worktree, in-place
    folder or member's linked worktree), for a fetch or read. A push uses the item's pinned
    account through `auth_for_account`."""
    if _READER is None:
        return None
    entries, file = await _READER()
    if file is None or not any(_injected(a) for a in file.accounts.values()):
        return None  # no account could carry a header: spare the git calls
    if isinstance(entries, Unread):
        # Without repos.yaml the repo's account is unknown: refuse only an https remote on a
        # host some injected-token account names; an SSH or local remote keeps running.
        fetch = await _git(repo, "remote", "get-url", "origin")
        hosts = {forges.origin(a.web_url) for a in file.accounts.values() if _injected(a)}
        if fetch and forges.origin(fetch[0]) in hosts:
            raise ForgeError(f"repos.yaml does not load ({entries.why}); fix it, then retry")
        return None
    common = await _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    root = Path(common[0]).parent.resolve()
    entry = next((e for e in entries if Path(e.path).expanduser().resolve() == root), None)
    fetch = await _git(repo, "remote", "get-url", "origin")
    if entry is None or not fetch:
        return None
    host = forges.remote_host(fetch[0])
    # As `run.api_account_for` does: an entry with no recorded forge never takes a host default,
    # so its backend stays the CLI and git keeps its own credential.
    found = forges.account_for(entry, file, host)
    if found is None:
        return None
    return await auth_for_account(repo, *found, push=push)


def git_env(base: Mapping[str, str], auth: GitAuth | None) -> dict[str, str]:
    """A copy of `base`, with `auth`'s header pinned after the hardening pins; `base`'s own
    content when there is none. Never touches `os.environ`."""
    env = dict(base)
    if auth is None:
        return env
    sandbox.harden_host_git_env(env)
    # GIT_CONFIG_PARAMETERS (`-c`) beats GIT_CONFIG_COUNT, so one a caller set could undo a pin.
    env.pop("GIT_CONFIG_PARAMETERS", None)
    for key in [k for k in env if k.startswith("GIT_TRACE") or k in _STRIPPED]:
        del env[key]
    # A gitconfig's trace2 target would log the header with no variable set: switch it off.
    env.update(dict.fromkeys(_TRACE2, "0"))
    # git takes the most specific `http.<url>.*` first, so a path-scoped key in a repo's own
    # config beats an origin-keyed pin: every URL git dials is pinned as well, and a tie goes
    # to this env entry. `remote.origin.proxy` beats every `http.<url>.proxy`, so it is pinned
    # as well. `http.sslCAInfo` stays unpinned (a corporate CA needs it): a CA a repo plants
    # there verifies whatever a man in the middle signs, which is why the proxy and the
    # resolve list, the ways to put one in the path, are pinned to the environment's own.
    proxy = next((env[k] for k in _PROXY_VARS if env.get(k)), "")  # as set, never a repo's
    pins = [
        ("credential.helper", ""),  # resets the helper list: no prompt, no `reject`
        ("remote.origin.proxy", proxy),
    ]
    for target in (f"{auth.origin}/.", *(f"{u}." for u in auth.urls)):
        pins += [
            (f"http.{target}extraHeader", ""),  # resets a persisted header: no two Authorizations
            (f"http.{target}extraHeader", auth.header),
            (f"http.{target}sslVerify", "true"),
            (f"http.{target}followRedirects", "false"),  # git re-sends extraHeader on a redirect
            (f"http.{target}proxy", proxy),
            (f"http.{target}sslCAPath", auth.ca_path),
            (f"http.{target}curloptResolve", ""),  # resets a pinned host -> address list
        ]
        pins.append((f"http.{target}sslCAInfo", auth.ca_info))  # a CA a repo cannot name
    start = int(env["GIT_CONFIG_COUNT"])
    env["GIT_CONFIG_COUNT"] = str(start + len(pins))
    for i, (k, v) in enumerate(pins, start):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    env.update(GIT_ASKPASS="", SSH_ASKPASS="", GIT_TERMINAL_PROMPT="0")
    return env


async def remote_env(repo: Path, base: Mapping[str, str], *, push: bool = False) -> dict[str, str]:
    return git_env(base, await auth_for(repo, push=push))
