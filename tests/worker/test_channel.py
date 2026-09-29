"""`kraft.worker.channel`: one unix-socket listener per session under
`run/sn/<short>/s.sock`, or the one loopback mTLS listener for sessions on
the TLS transport, serving `egress.EgressProxy` on the daemon's loop."""

from __future__ import annotations

import asyncio
import functools
import shutil
import socket
import ssl
import stat
import tempfile
from pathlib import Path

import pytest

from kraft import events, store
from kraft.paths import RunDirs
from kraft.worker import ca, channel, egress
from kraft.worker.sandbox import SandboxNotReady

_DENY_ALL = egress.PhaseLists("runtime", (), ("**",))


@pytest.fixture
def short_run():
    """A run dir short enough for a socket path: pytest's own tmp_path on
    macOS alone is past the 104 bytes a unix socket path may be."""
    base = Path(tempfile.mkdtemp(prefix="kraft-sn-", dir="/tmp"))
    yield RunDirs(base).ensure()
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
async def registry(database, short_run):
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    reg = channel.ChannelRegistry(short_run, database)
    yield reg
    await reg.close_all()


async def _ask(path, request: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(request)
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return answer


async def test_a_sessions_socket_serves_the_proxy_and_records_against_its_item(
    registry, database, short_run
):
    path = await registry.open("0123456789abcdef0123", "w1", _DENY_ALL)
    assert path == short_run.base / "sn" / "0123456789abcdef" / "s.sock"
    # Only the daemon's user may reach a session's socket, or its directory.
    assert [stat.S_IMODE(d.stat().st_mode) for d in (short_run.sockets, path.parent)] == [
        0o700,
        0o700,
    ]

    answer = await _ask(path, b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 403 ")
    rows = database.read(lambda c: events.read_after(c, 0, "w1"))
    refused = [r for r in rows if r["type"] == egress.SANDBOX_EGRESS_REFUSED]
    assert [r["payload"]["session_id"] for r in refused] == ["0123456789abcdef0123"]


async def test_close_stops_the_listener_and_removes_its_directory(registry):
    path = await registry.open("0123456789abcdef0123", "w1", _DENY_ALL)
    await registry.close("0123456789abcdef0123")

    assert not path.parent.exists()
    with pytest.raises(OSError):
        await asyncio.open_unix_connection(str(path))
    # Closing what is not open is a no-op, as every teardown path expects.
    await registry.close("0123456789abcdef0123")


async def test_shutdown_keeps_the_directory_a_live_relay_mounts(registry):
    """A graceful restart: the relay bind-mounts the socket's directory, so a
    new directory (a new inode) would cut an adopted session off for good.
    Only the socket goes; the next `open` listens in the same directory."""
    path = await registry.open("0123456789abcdef0123", "w1", _DENY_ALL)
    inode = path.parent.stat().st_ino
    await registry.close_all()

    assert path.parent.stat().st_ino == inode and not path.exists()
    assert await registry.open("0123456789abcdef0123", "w1", _DENY_ALL) == path
    assert path.parent.stat().st_ino == inode
    assert (await _ask(path, b"CONNECT a.io:443 HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 403 ")


async def test_a_socket_path_too_long_for_a_unix_socket_is_refused_by_name(database, tmp_path):
    deep = RunDirs(tmp_path / ("d" * 100)).ensure()
    reg = channel.ChannelRegistry(deep, database)
    with pytest.raises(SandboxNotReady, match="too long for a unix socket"):
        await reg.open("0123456789abcdef0123", "w1", _DENY_ALL)


@pytest.fixture
async def listener(registry, short_run):
    tls = channel.TLSListener(registry, short_run)
    await tls.start()
    yield tls
    await tls.close()


async def _ask_tls(run_dirs, session_id: str | None, request: bytes) -> bytes:
    """Dial the listener as a relay B would, presenting `session_id`'s
    certificate (none for None), and send `request` if any: sending into a
    refused connection races its alert with a reset. A blocking socket, in a thread: asyncio's
    streams read the server's refusal alert as a plain EOF."""
    context = ssl.create_default_context(cafile=ca.ensure_ca(run_dirs)[0])
    if session_id is not None:
        context.load_cert_chain(*ca.mint_session_cert(run_dirs, session_id))
    port = int((run_dirs.ca / "tls-port").read_text())

    def ask() -> bytes:
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
            context.wrap_socket(
                raw, server_hostname="127.0.0.1", suppress_ragged_eofs=False
            ) as tls,
        ):
            if request:
                tls.sendall(request)
            return b"".join(iter(lambda: tls.recv(65536), b""))

    return await asyncio.to_thread(ask)


async def test_a_session_certificate_reaches_its_own_session_over_tls(
    registry, listener, database, short_run
):
    for sid in ("01JSESSION0000000000000001", "01JSESSION0000000000000002"):
        # Registered for the TLS transport: no socket made for it.
        assert await registry.open(sid, "w1", _DENY_ALL, transport="tls") is None
    assert not short_run.sockets.exists()
    assert listener._server.sockets[0].getsockname()[0] == "127.0.0.1"

    answer = await _ask_tls(
        short_run, "01JSESSION0000000000000002", b"CONNECT a.io:443 HTTP/1.1\r\n\r\n"
    )

    assert answer.startswith(b"HTTP/1.1 403 ")
    rows = database.read(lambda c: events.read_after(c, 0, "w1"))
    refused = [r for r in rows if r["type"] == egress.SANDBOX_EGRESS_REFUSED]
    assert [r["payload"]["session_id"] for r in refused] == ["01JSESSION0000000000000002"]


@pytest.mark.parametrize("state", ["never-opened", "closed", "unix-channel"])
async def test_a_certificate_naming_no_open_tls_session_gets_nothing(
    registry, listener, short_run, state
):
    """The certificate chains to the CA, so the handshake succeeds; then the
    connection closes with not one byte, not even a 403."""
    sid = "0123456789abcdef0123"
    if state != "never-opened":
        await registry.open(
            sid, "w1", _DENY_ALL, transport="unix" if state == "unix-channel" else "tls"
        )
    if state == "closed":
        await registry.close(sid)

    assert await _ask_tls(short_run, sid, b"CONNECT a.io:443 HTTP/1.1\r\n\r\n") == b""


async def test_a_client_without_a_certificate_is_refused_at_the_handshake(
    registry, listener, short_run
):
    await registry.open("0123456789abcdef0123", "w1", _DENY_ALL, transport="tls")

    # asyncio drops a refused handshake without sending its alert: an EOF
    # mid-protocol, where an accepted connection is closed cleanly.
    with pytest.raises(ssl.SSLEOFError):
        await _ask_tls(short_run, None, b"")


async def test_the_tls_port_survives_a_restart_and_a_taken_one_is_replaced(registry, short_run):
    tls = channel.TLSListener(registry, short_run)
    port = await tls.start()
    await tls.close()
    assert await tls.start() == port
    await tls.close()

    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", port))
        squatter.listen()
        moved = await tls.start()
        await tls.close()

    assert moved != port
    assert int((short_run.ca / "tls-port").read_text()) == moved


async def test_doctor_hears_only_krafts_own_listener_and_only_while_it_listens(
    registry, short_run, tmp_path
):
    problem = functools.partial(asyncio.to_thread, channel.tls_listener_problem, short_run)
    assert "no egress TLS listener has started" in await problem()
    tls = channel.TLSListener(registry, short_run)
    port = await tls.start()
    assert await problem() is None
    await tls.close()
    assert f"did not answer on 127.0.0.1:{port}" in await problem()

    # Another TLS server on the port, its certificate signed by another CA.
    other = RunDirs(tmp_path / "other")
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(*ca.server_cert(other))
    squatter = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port, ssl=context)
    try:
        assert "did not answer" in await problem()
    finally:
        squatter.close()


@pytest.mark.parametrize("transport", ["unix", "tls"])
async def test_closing_a_session_ends_the_tunnels_it_already_has(
    registry, database, short_run, transport
):
    """Forgetting a session cuts what it has open, not only what it would
    open next: an idle tunnel ends when its session closes."""
    held = []  # the upstream's connections, open and silent until the end
    upstream = await asyncio.start_server(lambda r, w: held.append(w), "127.0.0.1", 0)

    async def resolve(host, port, **kw):
        if host != "a.test":  # the host's own addresses
            return await asyncio.get_running_loop().getaddrinfo(host, port, **kw)
        return [(0, 0, 0, "", ("10.254.254.254", port))]

    async def connect(address, port):
        return await asyncio.open_connection("127.0.0.1", upstream.sockets[0].getsockname()[1])

    reg = channel.ChannelRegistry(
        short_run, database, egress.EgressProxy(getaddrinfo=resolve, connect=connect)
    )
    tls = channel.TLSListener(reg, short_run)
    sid = "0123456789abcdef0123"
    path = await reg.open(
        sid, "w1", egress.PhaseLists("runtime", ("a.test",), ()), transport=transport
    )
    if path is not None:
        reader, writer = await asyncio.open_unix_connection(str(path))
    else:
        context = ssl.create_default_context(cafile=ca.ensure_ca(short_run)[0])
        context.load_cert_chain(*ca.mint_session_cert(short_run, sid))
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", await tls.start(), ssl=context, server_hostname="127.0.0.1"
        )
    try:
        writer.write(b"CONNECT a.test:443 HTTP/1.1\r\n\r\n")
        assert (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)).startswith(
            b"HTTP/1.1 200"
        )
        await reg.close(sid)
        try:
            ended = await asyncio.wait_for(reader.read(), 5) == b""
        except OSError:  # SSLError and ConnectionError
            ended = True
        assert ended
    finally:
        writer.close()
        await tls.close()
        await reg.close_all()
        upstream.close()
