"""What the console (and later the VS Code extension) needs to use a Kraft on
another machine: the URL that keys a stored session, the sessions themselves,
and the password sign-in that makes one. A remote client authenticates with
the session cookie only; `transport.remote` carries it (see `transport.http`)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from kraft import config
from kraft.client import transport as transport_module


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
