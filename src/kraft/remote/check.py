"""`kraft admin worker check`: whether this machine has what a step needs.

Rows have the shape of `doctor`'s: `{name, ok, detail, skipped, warn}`. A
`warn` row is `ok`: it ran and could not confirm the answer."""

from __future__ import annotations

import os
import shutil
import socket

from kraft import harness
from kraft.remote import machine, protocol
from kraft.remote.transport import FRAME_LIMIT, WebSocketTransport
from kraft.worker import backends


def _row(name: str, ok: bool, detail: str = "", *, skipped: bool = False, warn: bool = False):
    return {"name": name, "ok": ok, "detail": detail, "skipped": skipped, "warn": warn}


async def run(
    *, server: str | None = None, token: str | None = None, kraft_version: str
) -> list[dict]:
    rows = [_git(), _git_lfs(), *_agents(), *await _sandboxes()]
    if server is not None:
        rows.append(await _server(server, token or "", kraft_version))
    return rows


def _git() -> dict:
    path = shutil.which("git")
    return _row("git", path is not None, path or "not on PATH")


def _git_lfs() -> dict:
    """Without git-lfs a machine is not given a step of an LFS repo; that is not a failure. The
    worker looks for git-lfs once, when it starts, so one installed later is listed only after the
    worker restarts."""
    path = shutil.which("git-lfs")
    if path is not None:
        return _row("git-lfs", True, path)
    return _row(
        "git-lfs",
        True,
        "not installed: LFS repos are not placed here; once installed, restart the worker "
        "(it looks for git-lfs only when it starts)",
        skipped=True,
    )


def _agents() -> list[dict]:
    """One row per agent CLI Kraft knows, and a failure when none is installed.
    A credential this check cannot see is only a warning: a CLI may keep its
    login in a store of its own."""
    rows, installed = [], 0
    for hid, h in sorted(harness.load(None).valid.items()):
        path = shutil.which(h.command[0])
        if path is None:
            rows.append(_row(f"agent: {hid}", True, "not installed", skipped=True))
            continue
        installed += 1
        rows.append(_row(f"agent: {hid}", True, path))
        wanted = sorted({c.env for c in h.credentials})
        if wanted and not any(os.environ.get(name) for name in wanted):
            rows.append(
                _row(
                    f"credential: {hid}",
                    True,
                    f"{' or '.join(wanted)} is not set here. A login the CLI keeps "
                    "itself is not visible to this check",
                    warn=True,
                )
            )
    if not installed:
        rows.append(_row("agent CLIs", False, "none of the agent CLIs Kraft knows is on PATH"))
    return rows


async def _sandboxes() -> list[dict]:
    """One row per sandbox backend, from its own health check: a sandboxed item's step needs a
    backend that works here, or it stops `config_error` rather than run unsandboxed. None usable
    is only a warning: a machine may be for unsandboxed items."""
    rows, usable = [], 0
    for backend in backends.every():
        try:
            ok, detail = await backend.health({"kind": backend.kind}, refresh=True)
        except Exception as exc:  # noqa: BLE001 -- a check reports, it does not crash
            ok, detail = False, f"could not check: {exc}"
        usable += ok
        rows.append(_row(f"sandbox: {backend.kind}", True, detail, skipped=not ok))
    if not usable:
        rows.append(
            _row("sandbox runtime", True, "none usable: sandboxed items cannot run here", warn=True)
        )
    return rows


async def _server(server: str, token: str, kraft_version: str) -> dict:
    """One handshake with the server, then hang up."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import WebSocketException

    try:
        url = machine.dial_url(server)
    except ValueError as exc:
        return _row("server", False, str(exc))
    try:
        async with connect(url, max_size=FRAME_LIMIT, ping_interval=None, open_timeout=10) as ws:
            t = WebSocketTransport(ws.send, ws.recv, ws.close)
            welcome = await protocol.machine_handshake(
                t, kraft_version=kraft_version, token=token, host=socket.gethostname()
            )
    except protocol.Refused as exc:
        return _row("server", False, f"refused: {exc}")
    except (OSError, TimeoutError, WebSocketException, protocol.Closed) as exc:
        return _row("server", False, f"could not connect to {url}: {exc or type(exc).__name__}")
    return _row("server", True, f"welcomed as {welcome['name']} (kraft {welcome['kraft']})")
