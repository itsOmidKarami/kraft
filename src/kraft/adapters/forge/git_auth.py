"""Git over HTTPS with an API account's token: one builder for the environment of a call that
talks to origin. The header lives in that call's environment copy, never in `os.environ`,
argv or `.git/config`, and is keyed on the account's own origin."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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

_STRIPPED = ("GIT_CURL_VERBOSE", "GIT_TRANSFER_TRACE", "GIT_SSL_NO_VERIFY")
_TRACE2 = ("GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF")

Reader = Callable[[], Awaitable[tuple[Sequence[RepoEntry], ForgesFile | None]]]
_READER: Reader | None = None


@dataclass(frozen=True)
class GitAuth:
    name: str
    account: ForgeAccount
    origin: str  # https://host[:port], no trailing slash
    header: str  # Authorization: Basic <basic_form>

    def refused(self, text: str) -> bool:
        """Whether git's words say the forge refused the header's token."""
        return bool(
            AUTH_FAILED.search(text) or (self.account.forge == "github" and _NOT_FOUND.search(text))
        )

    def redirect_words(self) -> str:
        return (
            f"forge account {self.name}: origin redirects elsewhere; "
            "point origin at the new address"
        )

    def redirected(self, text: str) -> bool:
        return bool(_REDIRECTED.search(text))

    def stamp(self, exc: ForgeError) -> ForgeError:
        """Mark `exc` a token stop, in place, when git's words say the forge refused the token
        or sent the call elsewhere (the redirect is not followed, so the token stays home)."""
        text = str(exc)
        if self.redirected(text):
            exc.args = (self.redirect_words(),)
            exc.reason = "auth"
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


async def server_context(st) -> tuple[list[RepoEntry], ForgesFile | None]:
    if st.forges is None or not any(_injected(a) for a in st.forges.accounts.values()):
        return [], st.forges  # no account could carry a header: read no repos.yaml
    try:
        repos = await asyncio.to_thread(config.load_repos, st.templates_dir / "repos.yaml")
    except ConfigError:
        repos = []
    return repos, st.forges


def _origin(account: ForgeAccount) -> str:
    _, host, port = forges.origin(account.web_url)
    return f"https://{host}" + (f":{port}" if port != 443 else "")


def _injected(account: ForgeAccount) -> bool:
    return account.client == "api" and account.source().from_ in INJECTED


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
    carry = False
    for url in urls:
        scheme, host, port = forges.origin(url)
        if scheme not in ("http", "https"):
            continue
        if scheme == "https" and (host, port) == forges.origin(account.web_url)[1:]:
            carry = True
        elif push:
            raise ForgeError(
                f"forge account {name}: {repo.name}'s push URL is not on {origin}, the "
                "account's address, so Kraft will not send its token there; point origin's "
                "push URL at it or at an SSH address, then retry"
            )
    if not carry:
        return None
    source = account.source().label()
    try:
        token = await credentials.token_for(name, account)
    except credentials.NoToken as exc:
        raise ForgeError(
            f"forge account {name}: {exc}", reason="auth", account=name, source=source
        ) from None
    form = credentials.basic_form(token, account.source().kind, forge=account.forge)
    return GitAuth(name, account, origin, f"Authorization: Basic {form}")


async def auth_for(repo: Path, *, push: bool = False) -> GitAuth | None:
    """The live account's header for a call in `repo` (a checkout, item worktree, in-place
    folder or member's linked worktree), for a fetch or read. A push uses the item's pinned
    account through `auth_for_account`."""
    if _READER is None:
        return None
    entries, file = await _READER()
    if file is None or not any(_injected(a) for a in file.accounts.values()):
        return None  # no account could carry a header: spare the git calls
    common = await _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    root = Path(common[0]).parent.resolve()
    entry = next((e for e in entries if Path(e.path).expanduser().resolve() == root), None)
    fetch = await _git(repo, "remote", "get-url", "origin")
    if entry is None or not fetch:
        return None
    host = forges.remote_host(fetch[0])
    found = forges.account_for(entry, file, host)
    if found is None and not entry.forge_account and entry.forge is None:
        found = forges.default_account(file, host)  # a member with no recorded forge
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
    for key in [k for k in env if k.startswith("GIT_TRACE") or k in _STRIPPED]:
        del env[key]
    # A gitconfig's trace2 target would log the header with no variable set: switch it off.
    env.update(dict.fromkeys(_TRACE2, "0"))
    key = f"http.{auth.origin}/."
    pins = (
        (f"{key}extraHeader", ""),  # resets a persisted header: never two Authorizations
        (f"{key}extraHeader", auth.header),
        ("credential.helper", ""),  # resets the helper list: no prompt, no `reject`
        (f"{key}sslVerify", "true"),
        (f"{key}followRedirects", "false"),  # git re-sends extraHeader after a redirect
    )
    start = int(env["GIT_CONFIG_COUNT"])
    env["GIT_CONFIG_COUNT"] = str(start + len(pins))
    for i, (k, v) in enumerate(pins, start):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    env.update(GIT_ASKPASS="", SSH_ASKPASS="", GIT_TERMINAL_PROMPT="0")
    return env


async def remote_env(repo: Path, base: Mapping[str, str], *, push: bool = False) -> dict[str, str]:
    return git_env(base, await auth_for(repo, push=push))
