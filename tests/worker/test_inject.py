"""`kraft.worker.inject`: credential injection at the egress proxy, over real
TLS on local sockets. The "internet" is a fake resolver plus a connector
that sends every dial to one local TLS origin (or the fake corporate proxy
in front of it), whose certificates a separate CA signed -- trusted only as
the sandbox extra CA -- and which records every request it was sent. The
worker trusts the session's Kraft CA, as its bundle does."""

from __future__ import annotations

import asyncio
import os
import socket
import ssl
import threading
import time

import pytest

from kraft.paths import RunDirs
from kraft.worker import ca, egress, inject

API, OTHER = "api.example.com", "other.example.com"
_DNS = {API: "93.184.216.34", OTHER: "93.184.216.35", "proxy.corp": "10.9.9.9"}
REAL, SENTINEL = "sk-real-VALUE", "sk-sentinel"
RULES = (
    inject.InjectRule("API_KEY", API, "x-api-key", SENTINEL, value=REAL),
    inject.InjectRule("TOKEN", API, "authorization", "tok-sentinel", "Bearer %s", "tok-real"),
)


async def _getaddrinfo(host, port, **_):
    if host not in _DNS:
        raise socket.gaierror(socket.EAI_NONAME, "unknown")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_DNS[host], port))]


async def _read_request(reader) -> bytes:
    """One request as the origin got it, head and body (length or chunked)."""
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode().lower().split("\r\n")
    if "transfer-encoding: chunked" in lines:
        body = b""
        while (line := await reader.readuntil(b"\r\n")) != b"0\r\n":
            body += await reader.readexactly(int(line, 16) + 2)
        return head + body + line + await reader.readuntil(b"\r\n")
    length = next((int(v.split(":")[1]) for v in lines if v.startswith("content-length")), 0)
    return head + await reader.readexactly(length)


#: A connection closed from the other side, either way.
_GONE = (asyncio.IncompleteReadError, OSError)


class _Internet:
    def __init__(self, tmp_path):
        self.origin_ca = RunDirs(tmp_path / "origin").ensure()
        self.requests: list[bytes] = []
        self.dialled: list[tuple[str, int]] = []
        self.tunnels: list[bytes] = []
        self.alpn: list[str | None] = []
        self.signer = self.origin_ca

    def _context(self) -> ssl.SSLContext:
        contexts = {}
        for host in (API, OTHER):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*ca.mint_host_leaf(self.signer, host))
            ctx.set_alpn_protocols(["h2", "http/1.1"])
            contexts[host] = ctx

        def pick(sslobj, name, _):
            sslobj.context = contexts.get(name, contexts[API])

        contexts[API].sni_callback = pick
        return contexts[API]

    async def _origin(self, reader, writer):
        self.alpn.append(writer.get_extra_info("ssl_object").selected_alpn_protocol())
        try:
            while True:
                request = await _read_request(reader)
                self.requests.append(request)
                if b" /chunked " in request.split(b"\r\n", 1)[0]:
                    writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                    writer.write(b"2\r\nok\r\n0\r\n\r\n")
                else:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
        except _GONE:
            writer.close()

    async def _corporate(self, reader, writer):
        """An upstream proxy: records the CONNECT, then pipes to the origin."""
        self.tunnels.append(await reader.readuntil(b"\r\n\r\n"))
        out_reader, out_writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"HTTP/1.1 200 OK\r\n\r\n")
        await asyncio.gather(
            egress._pump(reader, out_writer),
            egress._pump(out_reader, writer),
            return_exceptions=True,
        )

    async def start(self):
        self.server = await asyncio.start_server(self._origin, "127.0.0.1", 0, ssl=self._context())
        self.corp = await asyncio.start_server(self._corporate, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def connect(self, host, port, **_):
        self.dialled.append((host, port))
        where = self.corp if host == "proxy.corp" else self.server
        return await asyncio.open_connection("127.0.0.1", where.sockets[0].getsockname()[1])


@pytest.fixture
async def internet(tmp_path):
    net = _Internet(tmp_path)
    yield net
    for server in (getattr(net, "server", None), getattr(net, "corp", None)):
        if server is not None:
            server.close()


@pytest.fixture
def proxy(internet, run_dirs, tmp_path):
    """`await proxy(allow=..., rules=..., env=...)`: the listener a session's
    channel would be; `.events` what it recorded."""
    servers = []

    async def start(allow=(API, OTHER), rules=RULES, env=None, signer=None):
        internet.signer = signer or internet.origin_ca
        await internet.start()
        environ = {
            "SSL_CERT_FILE": str(ca.ensure_ca(internet.origin_ca)[0]),
            "KRAFT_TEMPLATES_DIR": str(tmp_path / "templates"),
            **(env or {}),
        }
        p = egress.EgressProxy(connect=internet.connect, getaddrinfo=_getaddrinfo, environ=environ)
        events: list[dict] = []

        async def record(payload):
            events.append(payload)

        session = egress.EgressSession(
            "sess-1",
            egress.PhaseLists("runtime", tuple(allow), ()),
            record,
            credentials=rules,
            run_dirs=run_dirs,
        )
        server = await asyncio.start_server(lambda r, w: p.handle(r, w, session), "127.0.0.1", 0)
        servers.append(server)
        server.events = events
        return server

    yield start
    for server in servers:
        server.close()


async def _worker(server, host=API, *, sni=API, trust=None):
    """A worker's CLI: CONNECT through the proxy, then TLS as `sni`, trusting
    `trust` (default the Kraft CA its bundle holds)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    writer.write(f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode())
    assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    context = ssl.create_default_context(cafile=trust)
    context.set_alpn_protocols(["h2", "http/1.1"])
    if sni != host:
        # So that only the proxy's own check can refuse it.
        context.check_hostname = False
    await asyncio.wait_for(writer.start_tls(context, server_hostname=sni), 5)
    return reader, writer


def _request(*headers: str, target="/v1/messages", body=b"", host=API) -> bytes:
    lines = [f"POST {target} HTTP/1.1", f"Host: {host}", *headers]
    if body:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def test_the_upstream_context_has_the_3_13_verify_flags_on_every_python():
    """`ssl.create_default_context` sets VERIFY_X509_STRICT and
    VERIFY_X509_PARTIAL_CHAIN only from Python 3.13; the injector sets them by
    hand, so 3.12 verifies the same way -- notably a sandbox `ca_bundle` that
    is an intermediate, not a self-signed root, still anchors a chain."""
    wanted = ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN
    assert inject._client_context({}).verify_flags & wanted == wanted


async def test_the_sentinel_is_swapped_for_the_real_value_on_every_request(
    proxy, internet, run_dirs
):
    server = await proxy()
    reader, writer = await _worker(server, trust=ca.ensure_ca(run_dirs)[0])
    assert writer.get_extra_info("ssl_object").selected_alpn_protocol() == "http/1.1"
    # Pipelined, in one write: the second is checked and swapped as well.
    writer.write(
        _request(f"x-api-key: {SENTINEL}", f"X-Api-Key: {SENTINEL}", body=b"{}")
        + _request("Authorization: Bearer tok-sentinel", target="/chunked")
    )
    await writer.drain()
    first = await asyncio.wait_for(reader.readuntil(b"ok"), 5)
    second = await asyncio.wait_for(reader.readuntil(b"0\r\n\r\n"), 5)
    writer.close()

    assert first.endswith(b"Content-Length: 2\r\n\r\nok")
    assert second.startswith(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked")
    one, two = internet.requests
    assert one.count(b"x-api-key: sk-real-VALUE\r\n") == 1
    assert b"sk-sentinel" not in one.lower() and one.endswith(b"\r\n\r\n{}")
    assert b"authorization: Bearer tok-real\r\n" in two and b"tok-sentinel" not in two
    # One upstream connection, to the checked address, speaking HTTP/1.1.
    assert internet.dialled == [("93.184.216.34", 443)] and internet.alpn == ["http/1.1"]


@pytest.mark.parametrize(
    "request_, rules",
    [
        pytest.param(_request(), RULES, id="no-sentinel"),
        pytest.param(_request("x-api-key: sk-mine"), RULES, id="own-key"),
        pytest.param(
            _request(f"x-api-key: {SENTINEL}", "x-api-key: sk-mine"), RULES, id="one-of-two"
        ),
        pytest.param(_request("Authorization: tok-sentinel"), RULES, id="format-not-followed"),
        pytest.param(
            _request(f"x-api-key: {SENTINEL}"),
            (inject.InjectRule("API_KEY", API, "x-api-key", SENTINEL),),
            id="no-value-in-the-daemon",
        ),
        pytest.param(
            _request(f"x-api-key: {SENTINEL}"),
            (inject.InjectRule("API_KEY", API, "x-api-key", SENTINEL, value="a\r\nb: c"),),
            id="value-would-split",
        ),
        pytest.param(_request(f"x-api-key: {SENTINEL}", host=OTHER), RULES, id="host-other"),
        pytest.param(_request(f"x-api-key: {SENTINEL}", f"Host: {API}"), RULES, id="host-twice"),
    ],
)
async def test_a_request_not_carrying_the_sentinel_never_reaches_the_host(
    proxy, internet, run_dirs, request_, rules
):
    server = await proxy(rules=rules)
    reader, writer = await _worker(server, trust=ca.ensure_ca(run_dirs)[0])
    writer.write(request_)
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert answer.startswith(b"HTTP/1.1 403 ")
    assert internet.dialled == [] and internet.requests == []
    (event,) = server.events
    assert event["host"] == API and event["reason"] in answer.decode()
    assert REAL not in str(server.events)


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(
            _request(f"x-api-key: {SENTINEL}", "Transfer-Encoding: chunked", "Content-Length: 3"),
            id="te-and-cl",
        ),
        pytest.param(
            _request(f"x-api-key: {SENTINEL}", "Transfer-Encoding: gzip, chunked"), id="te-gzip"
        ),
        pytest.param(
            _request(f"x-api-key: {SENTINEL}", "Content-Length: 1", "Content-Length: 2"),
            id="two-lengths",
        ),
        pytest.param(_request(f"x-api-key: {SENTINEL}\nX-Smuggled: 1"), id="bare-lf"),
        pytest.param(_request(f"x-api-key: {SENTINEL}", " folded"), id="obs-fold"),
        pytest.param(
            _request(f"x-api-key: {SENTINEL}", target="https://evil.example/x"),
            id="absolute-target",
        ),
    ],
)
async def test_a_request_framed_ambiguously_is_a_400(proxy, internet, run_dirs, request_):
    server = await proxy()
    reader, writer = await _worker(server, trust=ca.ensure_ca(run_dirs)[0])
    writer.write(request_)
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert answer.startswith(b"HTTP/1.1 400 ")
    assert internet.dialled == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"Content-Length: 2\r\n\r\n{}", id="length"),
        pytest.param(b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n", id="chunked"),
    ],
)
async def test_what_follows_a_body_is_the_next_request_and_checked(proxy, internet, run_dirs, body):
    server = await proxy()
    reader, writer = await _worker(server, trust=ca.ensure_ca(run_dirs)[0])
    smuggled = b"GET /admin HTTP/1.1\r\nHost: api.example.com\r\n\r\n"
    writer.write(_request(f"x-api-key: {SENTINEL}")[:-2] + body + smuggled)
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert answer.startswith(b"HTTP/1.1 200 OK") and b"HTTP/1.1 403 " in answer
    (forwarded,) = internet.requests
    assert b"{}" in forwarded and b"/admin" not in forwarded


@pytest.mark.parametrize("sni", [OTHER, None], ids=["another-name", "no-name"])
async def test_the_workers_tls_must_name_the_connect_host(proxy, internet, run_dirs, sni):
    server = await proxy()
    with pytest.raises(OSError):
        await _worker(server, sni=sni, trust=ca.ensure_ca(run_dirs)[0])
    await asyncio.sleep(0.1)
    (event,) = server.events
    assert event["host"] == API and "not api.example.com" in event["reason"]
    assert internet.dialled == []


@pytest.mark.parametrize(
    "host, allow",
    [
        pytest.param(OTHER, (API, OTHER), id="no-rule-for-the-host"),
        pytest.param(API, ("*.example.com",), id="host-only-by-wildcard"),
    ],
)
async def test_a_host_no_rule_applies_to_is_a_blind_tunnel(proxy, internet, host, allow):
    server = await proxy(allow=allow)
    # Only the origin's own CA trusted: the worker sees the real host's cert.
    reader, writer = await _worker(
        server, host, sni=host, trust=ca.ensure_ca(internet.origin_ca)[0]
    )
    writer.write(_request(f"x-api-key: {SENTINEL}", host=host))
    await asyncio.wait_for(reader.readuntil(b"ok"), 5)
    writer.close()
    (request,) = internet.requests
    assert f"x-api-key: {SENTINEL}".encode() in request and REAL.encode() not in request


async def test_a_plain_http_request_to_an_inject_host_is_refused(proxy, internet):
    server = await proxy()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    writer.write(_request(f"x-api-key: {SENTINEL}", target="http://api.example.com/v1"))
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert answer.startswith(b"HTTP/1.1 403 ") and b"only injected over TLS" in answer
    assert internet.dialled == []


async def test_the_real_host_is_verified_and_never_by_the_kraft_ca(proxy, internet, run_dirs):
    # An upstream whose certificate the Kraft CA itself signed: not trusted.
    server = await proxy(signer=run_dirs)
    reader, writer = await _worker(server, trust=ca.ensure_ca(run_dirs)[0])
    writer.write(_request(f"x-api-key: {SENTINEL}"))
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert answer.startswith(b"HTTP/1.1 502 ") and b"CERTIFICATE_VERIFY_FAILED" in answer
    assert internet.requests == []


async def test_through_the_daemons_upstream_proxy_tls_goes_inside_its_tunnel(
    proxy, internet, run_dirs
):
    server = await proxy(env={"HTTPS_PROXY": "http://proxy.corp:3128"})
    reader, writer = await _worker(server, trust=ca.ensure_ca(run_dirs)[0])
    writer.write(_request(f"x-api-key: {SENTINEL}"))
    await asyncio.wait_for(reader.readuntil(b"ok"), 5)
    writer.close()
    assert internet.dialled == [("proxy.corp", 3128)]
    assert internet.tunnels[0].startswith(b"CONNECT api.example.com:443 HTTP/1.1\r\n")
    (request,) = internet.requests
    assert b"x-api-key: sk-real-VALUE\r\n" in request


def test_a_host_leaf_is_loaded_whole_while_another_connect_re_mints_it(run_dirs, monkeypatch):
    """A CA newer than the leaf re-mints it on the next CONNECT, one file at
    a time. A CONNECT that minted must load its pair before another re-mint
    gets between the two files: forced here by starting that re-mint right
    after the mint returns and letting it write the key only. Loaded torn,
    the certificate and key would not match (an `SSLError`)."""
    cert, _ = ca.ensure_ca(run_dirs)
    future = time.time() + 3600
    os.utime(cert, (future, future))  # every mint below re-mints
    key_written, finish = threading.Event(), threading.Event()
    real_mint, real_write_cert = ca.mint_host_leaf, ca._write_cert
    other, got_in = [], []

    def write_cert(path, cert):
        if threading.current_thread().name == "other":
            key_written.set()
            finish.wait(5)
        real_write_cert(path, cert)

    def mint_then_race(*args):
        pair = real_mint(*args)
        other.append(threading.Thread(target=real_mint, args=args, name="other"))
        other[0].start()
        got_in.append(key_written.wait(0.5))  # never, while the load holds the pair
        return pair

    monkeypatch.setattr(ca, "_write_cert", write_cert)
    monkeypatch.setattr(ca, "mint_host_leaf", mint_then_race)
    try:
        inject._server_context(run_dirs, "api.example.com", [])
    finally:
        finish.set()
        other[0].join()
    assert got_in == [False]
