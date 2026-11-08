"""Start-up for `kraft console --server URL` (spec "Console / Start-up"): use
the session stored for the server, else ask for the password before the TUI
starts, and keep what the server issued."""

from __future__ import annotations

import asyncio
import getpass
from collections.abc import Awaitable, Callable

from kraft.client import remote

PLAIN_HTTP = "This address is plain http: the password will cross the network unencrypted."


def _password(shown: str) -> str:
    return getpass.getpass(f"Password for Kraft at {shown}: ")


def _go_on(warning: str) -> bool:
    return input(f"{warning} Sign in anyway? [y/N] ").strip().lower() in ("y", "yes")


def connect(
    url: str,
    *,
    ask_password: Callable[[str], str] = _password,
    ask_continue: Callable[[str], bool] = _go_on,
    sign_in: Callable[..., Awaitable[remote.Target]] = remote.sign_in,
    store: remote.SessionStore | None = None,
) -> remote.Target:
    """The target to run the console against. Raises `ClientError` for a bad
    address, a wrong password or an unreachable server, and `ValueError` when
    the person declines the plain-http warning."""
    key = remote.normalize_url(url)
    store = store or remote.SessionStore(remote.sessions_path())
    if (stored := store.get(key)) is not None:
        return remote.Target(key, stored)
    if remote.is_plain_http_remote(key) and not ask_continue(PLAIN_HTTP):
        raise ValueError("not signed in")
    password = ask_password(remote.shown(key))
    target = asyncio.run(sign_in(key, password, remote.console_label()))
    store.put(target.base, target.session)
    return target
