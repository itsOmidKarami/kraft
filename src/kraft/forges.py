"""`forges.yaml`: named forge accounts. An account records a host's forge and web address,
so connect does not ask about a self-hosted host whose name does not say which forge it
is. A `client: cli` account carries no token (`gh`/`glab` keep their own sign-in); a
`client: api` account names where Kraft reads its token from, and never holds the token."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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


def origin(url: str) -> tuple[str, str, int]:
    """(scheme, host, port) of an address: the host in `web_host`'s form, the port 443 when none."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ("", "", 443)
    return (parts.scheme, web_host(url), port or 443)


def project_path(remote: str, account: ForgeAccount) -> str | None:
    """The remote's project path: without `.git`, and without the account's web_url path
    prefix (`/gitlab`) when the remote is on the account's host."""
    path = remote_path(remote)
    if path is None:
        return None
    if remote_host(remote) == web_host(account.web_url):
        prefix = urlsplit(account.web_url).path.strip("/")
        if prefix and path.startswith(prefix + "/"):
            path = path[len(prefix) + 1 :]
    return path or None


def env_name_problem(name: str) -> str | None:
    """Why an `env` token source may not name `name`, else None. Worded to follow `token.env`."""
    # Inside: worker.env imports a lot, and forges is imported early.
    from kraft.worker.env import BASELINE
    from kraft.worker.sandbox import FORWARDED_ENV

    # Never quote an arbitrary name: a token pasted here would reach the open /health.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return "must name an environment variable: letters, digits and _"
    if name.startswith("KRAFT_"):
        return "names a KRAFT_ variable, which Kraft sets for itself"
    if name.startswith("GIT_"):
        return "names a GIT_ variable, which git reads"
    if name in FORWARDED_ENV:
        return f"names {name}, which Kraft passes to agents"
    if name in BASELINE:
        return f"names {name}, which every Kraft child needs"
    return None


class TokenSource(BaseModel):
    """Where an API account's token comes from. Holds a name or a path, never the token."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    from_: Literal["git-credential", "command", "env", "file"] = Field(
        "git-credential", alias="from"
    )
    kind: Literal["pat", "oauth", "unknown"] = "unknown"
    command: str | None = None
    env: str | None = None
    file: str | None = None

    @model_validator(mode="after")
    def _one_key(self) -> TokenSource:
        given = {k for k in ("command", "env", "file") if getattr(self, k) is not None}
        want = set() if self.from_ == "git-credential" else {self.from_}
        if given != want:
            need = f"only `{self.from_}`" if want else "no other key"
            raise ValueError(f"token from: {self.from_} takes {need}")
        if self.env is not None and (why := env_name_problem(self.env)):
            raise ValueError(f"token.env {why}")
        if self.file is not None and not self.file.startswith(("/", "~/", "$")):
            raise ValueError("token.file must be an absolute path, or start with ~/ or $")
        return self

    def label(self) -> str:
        """What names the source in an answer: never the command text, never a token."""
        if self.from_ == "env":
            return f"env {self.env}"
        if self.from_ == "file":
            return f"file {self.file}"
        return self.from_


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
    client: Literal["cli", "api"] = "cli"
    web_url: str
    api_url: str | None = None
    default_for_host: bool = False
    ca_file: str | None = None
    token: TokenSource | None = None

    @field_validator("web_url")
    @classmethod
    def _web(cls, value: str) -> str:
        return _https(value, "web_url")

    @field_validator("api_url")
    @classmethod
    def _api(cls, value: str | None) -> str | None:
        return None if value is None else _https(value, "api_url")

    @model_validator(mode="after")
    def _same_origin(self) -> ForgeAccount:
        if self.client == "cli" and (self.token is not None or self.ca_file is not None):
            raise ValueError(
                "a client: cli account carries no token: gh/glab keep their own sign-in"
            )
        if self.api_url is None:
            return self
        web, api = origin(self.web_url), origin(self.api_url)
        host = web[1]
        official = (host == "github.com" and api[1] == "api.github.com") or (
            host.endswith(".ghe.com") and api[1] == f"api.{host}"
        )
        if official:
            same = api[2] == 443 and web[2] == 443
        else:
            same = api[1:] == web[1:]
        if not same:
            raise ValueError(
                f"api_url {api[0]}://{api[1]}:{api[2]} must be web_url's origin "
                f"{web[0]}://{web[1]}:{web[2]}"
            )
        return self

    def source(self) -> TokenSource:
        return self.token or TokenSource()

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

    @classmethod
    def load(cls, path) -> ForgesFile:
        """As `_Model.load`, but a file that does not parse is named by its line and
        column only. YAML's own message can quote the file (the line, an alias, a
        tag), and this one is shown on the open `/health` and is where a token
        will be pasted."""
        try:
            return super().load(path)
        except config.ConfigError as exc:
            cause = exc.__cause__
            if (
                isinstance(cause, ValidationError)
                and cause.errors()[0]["type"] == "extra_forbidden"
            ):
                # The unknown key is not named: a token pasted as a key would be quoted.
                parent = ".".join(str(p) for p in cause.errors()[0]["loc"][:-1]) or "the top level"
                raise config.ConfigError(
                    f"{cls.FILE}: {parent} has a key forges.yaml does not know"
                ) from None
            if not isinstance(cause, yaml.MarkedYAMLError):
                # Not `raise`: the chained ValidationError carries `input_value='glpat-...'`.
                raise config.ConfigError(str(exc)) from None
            mark = cause.problem_mark
            where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            raise config.ConfigError(f"{cls.FILE}: not valid YAML{where}") from None

    def save(self, path) -> None:
        """Written over the file's own text: hand comments stay, defaults are not spelled out."""
        data = self.model_dump(exclude_defaults=True, by_alias=True)
        for name, acct in self.accounts.items():
            if acct.token is not None:  # `from` is always written, even the default
                written = data["accounts"][name]
                written["token"] = {"from": acct.token.from_, **written.get("token", {})}
        config.write_preserving(path, data)
