"""`forges.yaml`: named forge accounts. Only `client: cli` exists until the API backends
land: an account records a host's forge and web address, so connect does not ask about
a self-hosted host whose name does not say which forge it is. It carries no
token; `gh`/`glab` keep their own sign-in."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from kraft import config


def web_host(url: str) -> str:
    """The host of a web address, lowercased, without a port or a trailing dot."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.removesuffix(".")


def _remote(remote: str):
    return next((m for rx in config._REMOTE if (m := rx.match(remote.strip()))), None)


def remote_host(remote: str) -> str | None:
    """The host of an origin URL (URL-style or scp-style), in `web_host`'s form."""
    match = _remote(remote)
    return match["host"].lower().removesuffix(".") if match else None


def remote_path(remote: str) -> str | None:
    """The path of an origin URL without its slashes or `.git`."""
    match = _remote(remote)
    return (match["path"].strip("/").removesuffix(".git") or None) if match else None


def default_account(forges: ForgesFile, host: str | None) -> tuple[str, ForgeAccount] | None:
    """The `default_for_host` account for `host`, whatever its forge."""
    if not host:
        return None
    return next(
        (
            (n, a)
            for n, a in forges.accounts.items()
            if a.default_for_host and web_host(a.web_url) == host
        ),
        None,
    )


def account_for(
    entry: config.RepoEntry, forges: ForgesFile, origin_host: str | None
) -> tuple[str, ForgeAccount] | None:
    """The account a repo uses: the one it names, else its host's default when
    that default is on the repo's forge, else none (the CLI, as today). A
    `forge_account` naming nothing is `account_problems`' to report, so None."""
    if entry.forge_account:
        acct = forges.accounts.get(entry.forge_account)
        return (entry.forge_account, acct) if acct else None
    found = default_account(forges, origin_host)
    return found if found and found[1].forge == entry.forge else None


def account_problems(forges: ForgesFile, repos: list[config.RepoEntry]) -> list[str]:
    """What forges.yaml and repos.yaml say that cannot both hold."""
    out = []
    for r in repos:
        if not r.forge_account:
            continue
        acct = forges.accounts.get(r.forge_account)
        if acct is None:
            out.append(
                f"repo {r.path}: forge_account {r.forge_account!r} names no account in forges.yaml"
            )
        elif acct.forge != r.forge:
            out.append(
                f"repo {r.path}: forge_account {r.forge_account!r} says forge: {acct.forge}, "
                f"the repo says forge: {r.forge}"
            )
    return out


def repo_problems(forges: ForgesFile, templates_dir: Path) -> list[str]:
    """`account_problems` against the repos.yaml in `templates_dir`; none when
    that file does not load (its own check names that)."""
    try:
        repos = config.load_repos(templates_dir / "repos.yaml")
    except config.ConfigError:
        return []
    return account_problems(forges, repos)


def default_api_url(forge: str, web_url: str) -> str:
    web_url = web_url.rstrip("/")
    host = web_host(web_url)
    if forge == "gitlab":
        return f"{web_url}/api/v4"
    if host == "github.com":
        return "https://api.github.com"
    if host.endswith(".ghe.com"):
        return f"https://api.{host}"
    return f"https://{urlsplit(web_url).netloc}/api/v3"


def _https(url: str, key: str) -> str:
    try:
        parts = urlsplit(url)
        ok = bool(parts.hostname) and parts.port != 0
    except ValueError:
        ok = False
    # No userinfo, query or fragment: a token pasted into an address would be kept and shown.
    if not ok or parts.scheme != "https" or parts.username or parts.password:
        raise ValueError(f"{key} must be a plain https:// address with a host, no credentials")
    if parts.query or parts.fragment:
        raise ValueError(f"{key} must not carry a query or fragment")
    return url.rstrip("/")


class ForgeAccount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    forge: Literal["github", "gitlab"]
    client: Literal["cli"] = "cli"
    web_url: str
    api_url: str | None = None
    default_for_host: bool = False

    @field_validator("client", mode="before")
    @classmethod
    def _cli_only(cls, value):
        if value == "api":
            raise ValueError("`client: api` arrives with the API backends; use `client: cli`")
        return value

    @field_validator("web_url")
    @classmethod
    def _web(cls, value: str) -> str:
        return _https(value, "web_url")

    @field_validator("api_url")
    @classmethod
    def _api(cls, value: str | None) -> str | None:
        return None if value is None else _https(value, "api_url")

    @model_validator(mode="after")
    def _same_host(self) -> ForgeAccount:
        if self.api_url is None:
            return self
        web, api = web_host(self.web_url), web_host(self.api_url)
        allowed = {web}
        if web == "github.com":
            allowed.add("api.github.com")
        if web.endswith(".ghe.com"):
            allowed.add(f"api.{web}")
        if api not in allowed:
            raise ValueError(f"api_url host {api!r} must be web_url's host {web!r}")
        return self

    def api(self) -> str:
        return self.api_url or default_api_url(self.forge, self.web_url)


class ForgesFile(config._Model):
    FILE = "forges.yaml"

    accounts: dict[str, ForgeAccount] = {}

    @field_validator("accounts", mode="before")
    @classmethod
    def _none_is_empty(cls, value):
        return {} if value is None else value

    @model_validator(mode="after")
    def _one_default_per_host(self) -> ForgesFile:
        seen: dict[str, str] = {}
        for name, acct in self.accounts.items():
            if not acct.default_for_host:
                continue
            host = web_host(acct.web_url)
            if host in seen:
                raise ValueError(
                    f"accounts {seen[host]!r} and {name!r} are both default_for_host for {host}"
                )
            seen[host] = name
        return self

    def save(self, path) -> None:
        """Written over the file's own text: hand comments stay, defaults are not spelled out."""
        config.write_preserving(path, self.model_dump(exclude_defaults=True))
