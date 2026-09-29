"""`kraft.worker.ca`: the Kraft CA and what it signs, proven by real TLS
handshakes under the stdlib's default contexts (`VERIFY_X509_STRICT` on)."""

from __future__ import annotations

import asyncio
import ssl
import stat

from kraft.paths import RunDirs
from kraft.worker import ca


async def _handshake(server_run: RunDirs, client: tuple) -> str | None:
    """Dial a loopback TLS server holding `server_run`'s server certificate
    and trusting only its CA, presenting `client` (cert, key). The CN the
    server saw, or None when the handshake was refused."""
    ca_cert, _ = ca.ensure_ca(server_run)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=ca_cert)
    server_ctx.load_cert_chain(*ca.server_cert(server_run))
    server_ctx.verify_mode = ssl.CERT_REQUIRED
    client_ctx = ssl.create_default_context(cafile=ca_cert)
    client_ctx.load_cert_chain(*client)
    # The point of the test: never relaxed, on either side.
    assert server_ctx.verify_flags & client_ctx.verify_flags & ssl.VERIFY_X509_STRICT

    seen: list[str] = []

    async def serve(reader, writer):
        subject = dict(rdn[0] for rdn in writer.get_extra_info("peercert")["subject"])
        seen.append(subject["commonName"])
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=client_ctx, server_hostname="127.0.0.1"
        )
        answer = await asyncio.wait_for(reader.read(), 5)
        writer.close()
    except ssl.SSLError, ConnectionError:
        answer = b""
    finally:
        server.close()
        await server.wait_closed()
    return seen[0] if answer == b"ok" else None


def test_the_ca_is_generated_once_and_its_key_is_the_daemons_alone(run_dirs):
    first = [p.read_bytes() for p in ca.ensure_ca(run_dirs)]
    cert, key = ca.ensure_ca(run_dirs)

    assert [cert.read_bytes(), key.read_bytes()] == first
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(run_dirs.ca.stat().st_mode) == 0o700


async def test_a_session_certificate_passes_a_strict_handshake_as_its_session_id(run_dirs):
    client = ca.mint_session_cert(run_dirs, "01JSESSION0000000000000001")

    assert await _handshake(run_dirs, client) == "01JSESSION0000000000000001"


async def test_a_certificate_from_another_ca_is_refused(run_dirs, tmp_path):
    other = RunDirs(tmp_path / "other")
    client = ca.mint_session_cert(other, "01JSESSION0000000000000001")

    assert await _handshake(run_dirs, client) is None


def test_a_sessions_directory_holds_no_ca_key_and_is_discarded_whole(run_dirs):
    ca.mint_session_cert(run_dirs, "s1")
    directory = ca.session_dir(run_dirs, "s1")

    # What relay B mounts: its own pair and the CA's certificate, never the CA key.
    assert sorted(p.name for p in directory.iterdir()) == ["ca.pem", "client.pem", "key.pem"]
    assert stat.S_IMODE((directory / "key.pem").stat().st_mode) == 0o600
    ca.discard_session_cert(run_dirs, "s1")
    assert not directory.exists()
