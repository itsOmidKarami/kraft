"""Each sandboxed session's egress channel under `network:` (sandbox part 2,
P4; spec §4): a unix socket at `run/sn/<short>/s.sock`, listened on by the
daemon's own asyncio loop, whose every connection is that session's and
nothing else's. Its relay container mounts the socket's directory -- not the
socket file, so a daemon restart that makes a new socket is still seen.

Backend-neutral plumbing: the relay is the backend's (`open_session`), the
listener and its policy are here and in `worker.egress`. `run_task` opens a
session's channel and closes it; reattach re-opens an adopted one's.

One registry per daemon, installed by the API's lifespan (`install`). A
process with none -- a CLI, a test that did not install one -- has no
channel, and a launch under `network:` there stops (`config_error`), never
runs with open egress.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from kraft import events
from kraft.worker.egress import SANDBOX_EGRESS_REFUSED, EgressProxy, EgressSession, PhaseLists
from kraft.worker.sandbox import SandboxNotReady

#: The longest a unix socket path may be, less its terminating NUL.
_SUN_PATH_MAX = (104 if sys.platform == "darwin" else 108) - 1
#: How much of the session id names its socket directory: at most, and at
#: least (fewer would make two live sessions' sockets likely to collide).
_SHORT_MAX, _SHORT_MIN = 16, 8
_SOCKET = "s.sock"


@dataclass
class _Channel:
    path: Path
    server: asyncio.Server


class ChannelRegistry:
    """Opens and closes sessions' listeners on the running loop."""

    def __init__(self, run_dirs, db, proxy: EgressProxy | None = None):
        self._run_dirs = run_dirs
        self._db = db
        self._proxy = proxy or EgressProxy()
        self._open: dict[str, _Channel] = {}

    def socket_path(self, session_id: str) -> Path:
        """`run/sn/<short>/s.sock`, or `SandboxNotReady` naming the path when
        even the shortest name would not fit a unix socket path."""
        base = self._run_dirs.sockets
        room = min(_SUN_PATH_MAX - len(f"{base}/") - len(f"/{_SOCKET}"), _SHORT_MAX)
        if room < _SHORT_MIN:
            raise SandboxNotReady(
                f"the egress socket under {base} would be too long for a unix socket "
                f"({_SUN_PATH_MAX} bytes at most): move KRAFT_HOME somewhere shorter"
            )
        return base / session_id[:room] / _SOCKET

    async def open(self, session_id: str, work_item_id: str, lists: PhaseLists) -> Path:
        """Listen on the session's socket; the path to mount into its relay.
        A stale socket left by a daemon that died is replaced."""
        path = self.socket_path(session_id)
        if any(c.path == path for c in self._open.values()):
            raise SandboxNotReady(f"another session's egress socket is already at {path}")
        # Only the daemon's user may reach a session's socket. An existing
        # directory is reused, never replaced: a live relay mounts it.
        for directory in (self._run_dirs.sockets, path.parent):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        path.unlink(missing_ok=True)

        async def record(payload: dict) -> None:
            await self._db.write(
                lambda c: events.append(c, work_item_id, SANDBOX_EGRESS_REFUSED, payload)
            )

        session = EgressSession(session_id, lists, record)
        server = await asyncio.start_unix_server(
            lambda r, w: self._proxy.handle(r, w, session), path=str(path)
        )
        self._open[session_id] = _Channel(path, server)
        return path

    async def close(self, session_id: str, *, keep_dir: bool = False) -> None:
        """Stop the listener and remove its directory (with `keep_dir`, only
        its socket). Best-effort and bounded, like a sandbox's `close`:
        nothing here may hold up the rest of a session's teardown. A no-op
        for a session with no channel."""
        channel = self._open.pop(session_id, None)
        if channel is None:
            return
        channel.server.close()
        try:
            await asyncio.wait_for(channel.server.wait_closed(), 5)
        except TimeoutError:
            pass
        if keep_dir:
            channel.path.unlink(missing_ok=True)
        else:
            shutil.rmtree(channel.path.parent, ignore_errors=True)

    async def close_all(self) -> None:
        """At shutdown: every listener stops, but its directory stays. The
        session may outlive the daemon, and its relay bind-mounts that
        directory; a new one after a restart would be an inode it never
        sees."""
        for session_id in list(self._open):
            await self.close(session_id, keep_dir=True)

    def sweep(self) -> list[str]:
        """Remove every socket directory no open channel owns: what dead
        sessions left behind. Run once reattach has re-opened the adopted
        sessions' channels. A socket probe's `probe-*` directory is not a
        session's: a doctor in another process may be using it. The names
        removed."""
        keep = {c.path.parent for c in self._open.values()}
        base = self._run_dirs.sockets
        stale = (
            [
                d
                for d in base.iterdir()
                if d.is_dir() and d not in keep and not d.name.startswith("probe-")
            ]
            if base.is_dir()
            else []
        )
        for directory in stale:
            shutil.rmtree(directory, ignore_errors=True)
        return [d.name for d in stale]


_CURRENT: ChannelRegistry | None = None


def install(registry: ChannelRegistry | None) -> None:
    """Make `registry` this process's (the API's lifespan), or remove it."""
    global _CURRENT
    _CURRENT = registry


def current() -> ChannelRegistry | None:
    return _CURRENT
