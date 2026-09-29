"""`kraft.worker.ca`: the Kraft CA and what it signs, proven by real TLS
handshakes under the stdlib's default contexts (`VERIFY_X509_STRICT` on)."""

from __future__ import annotations

import asyncio
import os
import ssl
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from kraft.paths import RunDirs
from kraft.worker import ca


async def _handshake(
    server_run: RunDirs,
    client: tuple | None,
    hostname: str = "127.0.0.1",
    *,
    server: tuple | None = None,
) -> str | None:
    """Dial a loopback TLS server holding `server` (cert, key; default
    `server_run`'s listener certificate) and trusting only its CA,
    presenting `client` (cert, key; None for none, and then none is asked
    for) and verifying the server as `hostname`. The CN the server saw
    (`""` for no client certificate), or None when the handshake was
    refused."""
    ca_cert, _ = ca.ensure_ca(server_run)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=ca_cert)
    server_ctx.load_cert_chain(*(server or ca.server_cert(server_run)))
    server_ctx.verify_mode = ssl.CERT_REQUIRED if client else ssl.CERT_NONE
    client_ctx = ssl.create_default_context(cafile=ca_cert)
    # As every CLI's TLS stack does: the name is in the SAN or nowhere.
    client_ctx.hostname_checks_common_name = False
    if client:
        client_ctx.load_cert_chain(*client)
    # The point of the test: never relaxed, on either side.
    assert server_ctx.verify_flags & client_ctx.verify_flags & ssl.VERIFY_X509_STRICT

    seen: list[str] = []

    async def serve(reader, writer):
        peer = writer.get_extra_info("peercert") or {"subject": ()}
        subject = dict(rdn[0] for rdn in peer["subject"])
        seen.append(subject.get("commonName", ""))
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=client_ctx, server_hostname=hostname
        )
        answer = await asyncio.wait_for(reader.read(), 5)
        writer.close()
    except OSError:  # SSLError and ConnectionError
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


@pytest.mark.parametrize("hostname", ca.GATEWAY_HOSTS)
async def test_the_listener_verifies_as_each_name_a_relay_dials_it_by(run_dirs, hostname):
    """Relay B reaches the daemon as its runtime's gateway name, never as
    127.0.0.1, and socat checks the certificate against that name."""
    client = ca.mint_session_cert(run_dirs, "s1")

    assert await _handshake(run_dirs, client, hostname) == "s1"


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


@pytest.mark.parametrize(
    ("dialled", "verifies"),
    [("api.anthropic.com", True), ("other.example.com", False)],
    ids=["its-host", "another-host"],
)
async def test_a_host_leaf_verifies_strictly_as_its_host_and_no_other(run_dirs, dialled, verifies):
    """What the proxy shows a worker's client for an inject domain (spec §6):
    a CLI dialling that host accepts it, one dialling any other refuses it.
    Asked with a spelling DNS allows but a certificate name never carries."""
    leaf = ca.mint_host_leaf(run_dirs, "API.Anthropic.com.")

    seen = await _handshake(run_dirs, None, dialled, server=leaf)

    assert (seen is not None) is verifies


def test_a_host_leaf_is_cached_per_host_until_the_ca_changes(run_dirs):
    cert, key = ca.mint_host_leaf(run_dirs, "api.openai.com")
    first = cert.read_bytes()

    assert ca.mint_host_leaf(run_dirs, "API.openai.com.")[0].read_bytes() == first
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    # Older than the root (rotated by hand): one it signed is minted again.
    os.utime(cert, (0, 0))
    assert ca.mint_host_leaf(run_dirs, "api.openai.com")[0].read_bytes() != first


@pytest.mark.parametrize("host", ["*.anthropic.com", "api.openai.com:443", "../ca", ""])
def test_a_host_leaf_is_only_for_an_exact_host(run_dirs, host):
    """Injection is always to an exact domain, and the name is a directory."""
    with pytest.raises(ValueError, match="exact host"):
        ca.mint_host_leaf(run_dirs, host)


def test_a_host_leaf_minted_by_many_connections_at_once_is_one_usable_pair(run_dirs):
    """The proxy mints on each CONNECT's thread, and a CLI opens several at
    once: every one must get a certificate and key that belong together."""
    ca.ensure_ca(run_dirs)

    def load(_):
        pair = ca.mint_host_leaf(run_dirs, "api.example.com")
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(*pair)
        return pair

    with ThreadPoolExecutor(8) as pool:
        pairs = list(pool.map(load, range(8)))
    assert len(set(pairs)) == 1
