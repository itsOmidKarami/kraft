"""What the console (and later the VS Code extension) needs to use a Kraft on
another machine: the URL that keys a stored session, the sessions themselves,
and the password sign-in that makes one. A remote client authenticates with
the session cookie only; `transport.target()` carries it (see `transport.http`)."""

from __future__ import annotations

import json
import math
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from kraft import auth, config
from kraft.client import transport as transport_module
from kraft.paths import default_run_dir


@dataclass(frozen=True)
class Target:
    """The server a remote client talks to: its normalized base URL and the
    `kraft_session` value it signed in with."""

    base: str
    session: str


def _refuse(sentence: str) -> transport_module.ClientError:
    return transport_module.ClientError(sentence, kind=transport_module.Failure.REFUSED)


def normalize_url(text: str) -> str:
    """`scheme://host:port`, the key a session is stored under. Strict: a
    server at a path prefix, a zone id or credentials in the URL are refused."""
    raw = text.strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise _refuse(f"the server address must start with http:// or https://, not {raw!r}")
    if parts.path not in ("", "/") or "?" in raw or "#" in raw:
        raise _refuse(
            "the server address cannot have a path, query or fragment; "
            "Kraft behind a path prefix (https://proxy/kraft/) is not supported"
        )
    if parts.username is not None or parts.password is not None:
        raise _refuse("the server address cannot carry a user name or password")
    host = parts.hostname
    if not host:
        raise _refuse(f"the server address {raw!r} names no host")
    if "%" in host:
        raise _refuse("the server address cannot carry an IPv6 zone id")
    try:
        port = parts.port
    except ValueError:
        raise _refuse(f"the server address {raw!r} has a port that is not a number") from None
    port = port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{config.url_host(host)}:{port}"


def shown(url: str) -> str:
    """`url` without its scheme: how the console names the server."""
    return url.partition("://")[2]


def is_plain_http_remote(url: str) -> bool:
    """Whether signing in to `url` sends a password over http to a host that
    is not this machine."""
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname not in config.LOOPBACK


SESSIONS_FILE = "console-sessions.json"


def sessions_path() -> Path:
    """The console's stored sessions, in the run directory like `console-seen.json`."""
    return Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir()) / SESSIONS_FILE


class SessionStore:
    """Normalized URL -> session token, in one private file. Every change
    reads the file again and replaces it whole, atomically, so a reader never
    sees half a file. There is no lock, though: two consoles writing at the same
    moment can drop one entry, and the cost is one extra sign-in."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, str]:
        try:
            said = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(said, dict):
            return {}
        return {k: v for k, v in said.items() if isinstance(v, str)}

    def get(self, url: str) -> str | None:
        return self._read().get(url)

    def put(self, url: str, token: str) -> None:
        self._write({**self._read(), url: token})

    def forget(self, url: str, token: str) -> bool:
        """Delete `url`'s entry if it still holds `token`; a console that
        signed in again meanwhile keeps its new session."""
        sessions = self._read()
        if sessions.get(url) != token:
            return False
        del sessions[url]
        self._write(sessions)
        return True

    def _write(self, sessions: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp creates the file 0600, from the first byte: it holds credentials.
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".console-sessions-")
        try:
            with os.fdopen(fd, "w") as out:
                out.write(json.dumps(sessions))
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise


def console_label() -> str:
    """The session's name in the web UI's signed-in list (`frontend/src/ua.ts`
    shows a label starting `Kraft ` as given)."""
    return f"Kraft console on {socket.gethostname()}"


def _cookie_value(response: httpx.Response) -> str | None:
    for line in response.headers.get_list("set-cookie"):
        name, _, rest = line.partition("=")
        if name.strip() == auth.COOKIE:
            return rest.split(";", 1)[0].strip() or None
    return None


async def sign_in(
    url: str,
    password: str,
    label: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Target:
    """Sign in with the password and return the session. `label` is sent as
    the User-Agent, which the server stores as the session's name. The cookie
    is read from `Set-Cookie` by hand (the `Secure` flag is not checked), and
    redirects are not followed, so the password goes only where it was aimed."""
    base = normalize_url(url)
    try:
        async with httpx.AsyncClient(
            base_url=base,
            headers={"User-Agent": label},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        ) as session:
            response = await session.post(
                "/api/login", json={"password": password, "stay_signed_in": True}
            )
    except httpx.ConnectError as exc:
        raise (
            transport_module.tls_error(exc)
            or transport_module.ClientError(
                f"no Kraft server at {shown(base)}", kind=transport_module.Failure.UNREACHABLE
            )
        ) from exc
    except httpx.TimeoutException as exc:
        raise transport_module.ClientError(
            f"{shown(base)} did not answer the sign-in in time",
            kind=transport_module.Failure.INTERRUPTED,
        ) from exc
    except httpx.TransportError as exc:
        raise transport_module.ClientError(
            f"lost the connection to {shown(base)} during sign-in",
            kind=transport_module.Failure.INTERRUPTED,
        ) from exc
    if 300 <= response.status_code < 400:
        raise _refuse(
            f"{shown(base)} redirected the sign-in elsewhere; give the address it redirects to"
        )
    if response.status_code == 429:
        wait = response.headers.get("retry-after", "")
        minutes = f" Try again in {math.ceil(int(wait) / 60)} minutes." if wait.isdigit() else ""
        raise _refuse(f"too many failed sign-ins from this machine.{minutes}")
    if response.status_code >= 400:
        raise transport_module.status_error(
            response.status_code, transport_module._detail(response)
        )
    cookie = _cookie_value(response)
    if cookie is None:
        raise transport_module.ClientError(
            f"{shown(base)} signed us in but sent no session cookie",
            kind=transport_module.Failure.BAD_RESPONSE,
            status=response.status_code,
        )
    return Target(base, cookie)
