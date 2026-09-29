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

On a VM-backed runtime (P4b) a bind-mounted host socket cannot be connected
to, so a session registered with `transport="tls"` gets no socket: its relay
B dials the daemon's one `TLSListener` instead, and the session is the
subject of the client certificate it presents (`worker.ca`).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path

from kraft import events
from kraft.worker import ca
from kraft.worker.egress import SANDBOX_EGRESS_REFUSED, EgressProxy, EgressSession, PhaseLists
from kraft.worker.sandbox import SandboxNotReady

#: The longest a unix socket path may be, less its terminating NUL.
_SUN_PATH_MAX = (104 if sys.platform == "darwin" else 108) - 1
#: How much of the session id names its socket directory: at most, and at
#: least (fewer would make two live sessions' sockets likely to collide).
_SHORT_MAX, _SHORT_MIN = 16, 8
_SOCKET = "s.sock"
#: Under `run/ca/`: the port the TLS listener last bound, tried first again.
_PORT_FILE = "tls-port"

logger = logging.getLogger(__name__)


@dataclass
class _Channel:
    session: EgressSession
    #: The unix socket and its listener; both None under the TLS transport.
    path: Path | None = None
    server: asyncio.Server | None = None
    #: Every connection being served, over either transport: cancelled when
    #: the channel closes, or a tunnel would outlive its session.
    serving: set[asyncio.Task] = field(default_factory=set)


class ChannelRegistry:
    """Opens and closes sessions' listeners on the running loop."""

    def __init__(self, run_dirs, db, proxy: EgressProxy | None = None):
        self._run_dirs = run_dirs
        self._db = db
        self._proxy = proxy or EgressProxy()
        self._open: dict[str, _Channel] = {}

    @property
    def proxy(self) -> EgressProxy:
        """The one proxy every session's connections are handed to, over
        either transport."""
        return self._proxy

    async def _serve(self, channel: _Channel, reader, writer) -> None:
        task = asyncio.current_task()
        channel.serving.add(task)
        try:
            await self._proxy.handle(reader, writer, channel.session)
        finally:
            channel.serving.discard(task)

    async def serve_tls(self, session_id: str, reader, writer) -> None:
        """A TLS connection whose certificate names `session_id`: served if
        that is an open TLS-transport session, else closed with nothing
        written (no HTTP framing is owed to a stale relay)."""
        channel = self._open.get(session_id)
        if channel is None or channel.path is not None:
            writer.close()
            return
        await self._serve(channel, reader, writer)

    def tls_session(self, session_id: str) -> EgressSession | None:
        """The session a client certificate's subject names, if it is open
        and registered for the TLS transport; None refuses the connection."""
        channel = self._open.get(session_id)
        return channel.session if channel is not None and channel.path is None else None

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

    async def open(
        self, session_id: str, work_item_id: str, lists: PhaseLists, *, transport: str = "unix"
    ) -> Path | None:
        """Listen on the session's socket; the path to mount into its relay.
        A stale socket left by a daemon that died is replaced. Under
        `transport="tls"`, only register the session for the `TLSListener`:
        no socket, and None."""

        async def record(payload: dict) -> None:
            await self._db.write(
                lambda c: events.append(c, work_item_id, SANDBOX_EGRESS_REFUSED, payload)
            )

        session = EgressSession(session_id, lists, record)
        if transport == "tls":
            if session_id in self._open:
                raise SandboxNotReady(f"session {session_id} already has an egress channel")
            self._open[session_id] = _Channel(session)
            return None
        path = self.socket_path(session_id)
        if any(c.path == path for c in self._open.values()):
            raise SandboxNotReady(f"another session's egress socket is already at {path}")
        # Only the daemon's user may reach a session's socket. An existing
        # directory is reused, never replaced: a live relay mounts it.
        for directory in (self._run_dirs.sockets, path.parent):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        path.unlink(missing_ok=True)
        channel = _Channel(session, path)
        channel.server = await asyncio.start_unix_server(
            lambda r, w: self._serve(channel, r, w), path=str(path)
        )
        self._open[session_id] = channel
        return path

    async def close(self, session_id: str, *, keep_dir: bool = False) -> None:
        """Stop the listener and remove its directory (with `keep_dir`, only
        its socket). Best-effort and bounded, like a sandbox's `close`:
        nothing here may hold up the rest of a session's teardown. A no-op
        for a session with no channel."""
        channel = self._open.pop(session_id, None)
        if channel is None:
            return
        for task in channel.serving:
            task.cancel()
        if channel.serving:
            await asyncio.wait(channel.serving, timeout=5)
        if channel.server is None:
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
        keep = {c.path.parent for c in self._open.values() if c.path is not None}
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


def tls_port(run_dirs) -> int | None:
    """The port the TLS listener last bound, which a relay B dials; None
    before it has ever started."""
    try:
        return int((run_dirs.ca / _PORT_FILE).read_text())
    except OSError, ValueError:
        return None


def tls_listener_problem(run_dirs) -> str | None:
    """Doctor's: why the daemon's TLS listener did not answer on its
    persisted port as Kraft's own (a server certificate the Kraft CA signed),
    or None. Blocking. No client certificate is presented, so the listener
    serves it nothing."""
    port, ca_cert = tls_port(run_dirs), run_dirs.ca / "ca.pem"
    if port is None or not ca_cert.is_file():
        return f"no egress TLS listener has started under {run_dirs.ca}"
    try:
        context = ssl.create_default_context(cafile=ca_cert)
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
            context.wrap_socket(raw, server_hostname="127.0.0.1"),
        ):
            return None
    except OSError as exc:
        return (
            f"the egress TLS listener did not answer on 127.0.0.1:{port} ({exc}): a sandbox "
            "under `network:` on a runtime in a VM has no route out; restart the server"
        )


class TLSListener:
    """One per daemon: the loopback mTLS port every TLS-transport session's
    relay B dials. The session is the client certificate's subject CN, never
    the peer address (every relay B arrives from 127.0.0.1, spike 4.5a).

    `host` is a test-only seam: a Linux CI runner's containers reach the
    host through the bridge gateway, not its loopback. The daemon always
    listens on 127.0.0.1."""

    def __init__(self, registry: ChannelRegistry, run_dirs, *, host: str = "127.0.0.1"):
        self._registry, self._run_dirs, self._host = registry, run_dirs, host
        self._server: asyncio.Server | None = None

    def _context(self) -> ssl.SSLContext:
        ca_cert, _ = ca.ensure_ca(self._run_dirs)
        # The default context: VERIFY_X509_STRICT stays on.
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=ca_cert)
        context.load_cert_chain(*ca.server_cert(self._run_dirs))
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    async def start(self) -> int:
        """Listen, on the port persisted under `run/ca/` when there is one: a
        restarted daemon must keep the port a live relay B already dials.
        When that port is taken, any free one, persisted in its place -- and
        every live TLS session's relay B is cut off until it is reopened."""
        context = self._context()
        wanted = tls_port(self._run_dirs) or 0
        try:
            self._server = await asyncio.start_server(self._handle, self._host, wanted, ssl=context)
        except OSError as exc:
            if not wanted:
                raise
            logger.warning(
                "egress TLS port %d is taken (%s): listening on another; relays of "
                "sessions adopted from before this restart can no longer reach it",
                wanted,
                exc,
            )
            self._server = await asyncio.start_server(self._handle, self._host, 0, ssl=context)
        port = self._server.sockets[0].getsockname()[1]
        (self._run_dirs.ca / _PORT_FILE).write_text(f"{port}\n")
        return port

    async def _handle(self, reader, writer) -> None:
        """No certificate never gets here (`CERT_REQUIRED` refuses the
        handshake); the registry serves the session the certificate names."""
        subject = dict(rdn[0] for rdn in writer.get_extra_info("peercert")["subject"])
        await self._registry.serve_tls(subject.get("commonName", ""), reader, writer)

    async def close(self) -> None:
        """Stop listening, at shutdown; the port file stays for the next
        start."""
        if self._server is None:
            return
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), 5)
        except TimeoutError:
            pass
        self._server = None


_CURRENT: ChannelRegistry | None = None


def install(registry: ChannelRegistry | None) -> None:
    """Make `registry` this process's (the API's lifespan), or remove it."""
    global _CURRENT
    _CURRENT = registry


def current() -> ChannelRegistry | None:
    return _CURRENT
