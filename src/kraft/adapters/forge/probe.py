"""The server proves each `client: api` account's token: who it signs in as, what it can
do, when it expires and which connected repos it sees. Results live in `st.forge_probes`
(memory only); no token is in any field."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx

from kraft import config, redact
from kraft.adapters.forge import credentials
from kraft.adapters.forge.http import HttpTransport
from kraft.adapters.forge.models import ForgeError
from kraft.forges import ForgeAccount, ForgesFile, account_for, project_path, remote_host

logger = logging.getLogger(__name__)

State = Literal["ok", "no_token", "refused", "forbidden", "tls", "unreachable"]

PROBE_INTERVAL_S = 6 * 3600
EXPIRY_WARN_DAYS = 7
PROBE_CALL_TIMEOUT_S = 15  # below CLI_TIMEOUT_S: `forge test` answers inside the client's 120s
PROBE_TIMEOUT_S = 90  # a whole account's probe, repos included
_STATE = {"auth": "refused", "forbidden": "forbidden", "tls": "tls"}
_BAD = frozenset({"no_token", "refused", "forbidden", "tls"})
_WELL_KNOWN_ENV = frozenset({"GITLAB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"})


@dataclass(frozen=True)
class Probe:
    account: str
    checked_at: str
    state: State
    detail: str
    login: str | None = None
    scopes: tuple[str, ...] | None = None
    expires_on: str | None = None
    warnings: tuple[str, ...] = ()
    sso_url: str = ""
    projects: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    def public(self) -> dict:
        return {"state": self.state, "expires_on": self.expires_on}

    def text(self) -> str:
        lines = [f"{self.state}: {self.detail}"]
        if self.login:
            lines.append(f"signed in as {self.login}")
        if self.scopes is not None:
            lines.append(f"scopes: {', '.join(self.scopes) or 'none'}")
        if self.expires_on:
            lines.append(f"expires on {self.expires_on}")
        lines += [f"warning: {w}" for w in self.warnings]
        lines += [
            f"{repo}: {p['project']} {'visible' if p['visible'] else 'not visible'}"
            + ("" if p["visible"] else f" ({p['detail']})")
            for repo, p in self.projects.items()
        ]
        return "\n".join(lines)


def _day(text) -> str | None:
    """`YYYY-MM-DD` from what the forge sent, or None: `/health` parses it on every poll."""
    try:
        return date.fromisoformat(str(text)[:10]).isoformat() if text else None
    except ValueError:
        return None


def _split(text) -> tuple[str, ...]:
    return tuple(text.replace(",", " ").split()) if isinstance(text, str) else tuple(text or ())


async def _info(account, http, web) -> tuple[tuple[str, ...] | None, str | None]:
    """A GitLab token's scopes and expiry, by what its kind can be; the sign-in already worked,
    so a refusal here only means no detail."""
    hows = credentials.KINDS[account.source().kind].gitlab_scopes
    for i, how in enumerate(hows):
        try:
            if how == "pat":
                body = (await http.rest("GET", "personal_access_tokens/self")).json()
                return _split(body.get("scopes")), body.get("expires_at")
            body = (await web.rest("GET", "oauth/token/info")).json()
            left = body.get("expires_in")
            until = (
                (datetime.now(UTC) + timedelta(seconds=left)).date().isoformat() if left else None
            )
            return _split(body.get("scope")), until
        except ForgeError as exc:
            if exc.reason not in ("auth", "not_found") or i == len(hows) - 1:
                break
    return None, None


async def probe_account(name: str, account: ForgeAccount, **kw) -> Probe:
    """`_probe_account`, bounded so `forge test` answers inside the client's 120s, and never
    raising: one account's surprise must not cost every other account its result."""
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            return await _probe_account(name, account, **kw)
    except TimeoutError:
        detail = f"the probe did not finish in {PROBE_TIMEOUT_S}s"
    except Exception as exc:  # noqa: BLE001 -- its text could hold what the forge sent
        detail = f"the probe failed ({type(exc).__name__})"
    return Probe(name, datetime.now(UTC).isoformat(timespec="seconds"), "unreachable", detail)


async def _probe_account(
    name: str,
    account: ForgeAccount,
    *,
    repos: Sequence[config.RepoEntry],
    worktrees: Path,
    expose: Sequence[Path] = (),
    transport: httpx.AsyncBaseTransport | None = None,
) -> Probe:
    checked = datetime.now(UTC).isoformat(timespec="seconds")

    def done(state: State, detail: str, **kw) -> Probe:
        return Probe(name, checked, state, redact.redact(detail), **kw)

    source = account.source()
    try:
        token = await credentials.read_token(
            account, repos=repos, worktrees=worktrees, expose=expose
        )
    except credentials.NoToken as exc:
        return done("no_token", str(exc))

    def http(base="api") -> HttpTransport:
        return HttpTransport(
            name, account, token, base=base, timeout=PROBE_CALL_TIMEOUT_S, transport=transport
        )

    api = http()
    github = account.forge == "github"
    try:
        who = await api.rest("GET", "user")
        body = who.json()
        if not isinstance(body, dict):
            raise ForgeError("GET user: answered without an object", reason="unreachable")
        login = body.get("login" if github else "username")
        if github:
            scopes = (
                _split(who.headers["x-oauth-scopes"]) if "x-oauth-scopes" in who.headers else None
            )
            expires = (who.headers.get("github-authentication-token-expiration") or "")[:10] or None
        else:
            scopes, expires = await _info(account, api, http("web"))
        expires = _day(expires)
    except ForgeError as exc:
        return done(_STATE.get(exc.reason, "unreachable"), str(exc), sso_url=exc.sso_url)

    warnings = [w for w in (credentials.prefix_warning(token, source.kind),) if w]
    if source.from_ == "env" and source.env in _WELL_KNOWN_ENV:
        warnings.append(
            f"{source.env} is read by glab/gh for any host; Kraft takes it out of every process "
            "it starts, so a client: cli repo's gh/glab no longer sees it"
        )
    projects = {}
    mine = ForgesFile(accounts={name: account})
    for repo in repos:
        remote = await asyncio.to_thread(
            config.git_read, Path(repo.path), "remote", "get-url", "origin", expected_failure=True
        )
        if not account_for(repo, mine, remote_host(remote) if remote else None):
            continue
        path = project_path(remote or "", account)
        if path is None:
            continue
        try:
            await api.rest("GET", f"repos/{path}" if github else f"projects/{quote(path, safe='')}")
            projects[str(repo.path)] = {"project": path, "visible": True, "detail": ""}
        except ForgeError as exc:
            projects[str(repo.path)] = {
                "project": path,
                "visible": False,
                "detail": redact.redact(f"{path}: HTTP {exc.status}" if exc.status else str(exc)),
            }
    return done(
        "ok",
        f"signed in as {login}",
        login=login,
        scopes=scopes,
        expires_on=expires,
        warnings=tuple(warnings),
        projects=projects,
    )


def _api_accounts(st) -> dict[str, ForgeAccount]:
    return {n: a for n, a in (st.forges.accounts if st.forges else {}).items() if a.client == "api"}


def _expose(st, repos: Sequence[config.RepoEntry]) -> list[Path]:
    """Every `sandbox.expose` root, the instance's and each repo's: a token file there is
    readable by a worker."""
    boxes = [st.instance_policy.sandbox, *(r.effective_sandbox for r in repos)]
    return [Path(e).expanduser() for box in boxes if box for e in box.expose or ()]


async def refresh(st, accounts: Mapping[str, ForgeAccount] | None = None) -> dict[str, Probe]:
    """Probe `accounts` (every API account by default). Kept only for an account the server
    still runs: one removed meanwhile, or not reloaded yet, must not reach `/health`."""
    accounts = _api_accounts(st) if accounts is None else accounts
    if not accounts:
        return {}
    try:
        repos = await asyncio.to_thread(config.load_repos, st.templates_dir / "repos.yaml")
    except config.ConfigError:
        repos = []  # repos.yaml's own check says why
    expose = _expose(st, repos)
    probes = await asyncio.gather(
        *(
            probe_account(
                n,
                a,
                repos=repos,
                worktrees=st.run_dirs.worktrees,
                expose=expose,
                transport=getattr(st, "forge_http_transport", None),
            )
            for n, a in accounts.items()
        )
    )
    running = _api_accounts(st)
    for p in probes:
        if p.account in running:
            st.forge_probes[p.account] = p
    return {p.account: p for p in probes}


def kick(st, names: Iterable[str] | None = None) -> None:
    """Probe these accounts (all when None) soon; the loop does it."""
    st.forge_probe_due.update(_api_accounts(st) if names is None else names)
    st.forge_probe_wake.set()


async def loop(app) -> None:
    st = app.state
    due = None  # the first pass, and the timer's, take every account
    while True:
        accounts = _api_accounts(st)
        try:
            await refresh(
                st, accounts if due is None else {n: a for n, a in accounts.items() if n in due}
            )
        except Exception:  # noqa: BLE001 -- a failed pass never ends the loop
            logger.exception("forge token probe failed")
        try:
            await asyncio.wait_for(st.forge_probe_wake.wait(), PROBE_INTERVAL_S)
            due = set(st.forge_probe_due)
        except TimeoutError:
            due = None
        st.forge_probe_due.clear()
        st.forge_probe_wake.clear()


def health_rows(probes: Mapping[str, Probe], today: date) -> tuple[dict[str, dict], list[str]]:
    """`/health`'s `forge_tokens`, and the accounts that make the server degraded."""
    bad = [
        n
        for n, p in probes.items()
        if p.state in _BAD
        or (p.expires_on and (date.fromisoformat(p.expires_on) - today).days <= EXPIRY_WARN_DAYS)
    ]
    return {n: p.public() for n, p in probes.items()}, bad
