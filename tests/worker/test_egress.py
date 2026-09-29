"""`kraft.worker.egress`: the per-session egress proxy's policy and tunnelling,
against local asyncio servers. The "internet" is a fake resolver plus a
connector that sends every dial to one local echo server, recording what was
dialled -- so no test touches a real network, and each can see which address
the proxy chose."""

from __future__ import annotations

import asyncio
import socket

import pytest

from kraft.worker import egress

#: Name -> the addresses a fake DNS answers for it.
_DNS = {
    "api.example.com": ["93.184.216.34"],
    "db.internal": ["10.0.0.5"],
    "localhost": ["127.0.0.1"],
    "rebound.example.com": ["93.184.216.35", "127.0.0.1"],
    "metadata.example.com": ["169.254.169.254"],
    "v6-link-local.example.com": ["fe80::1"],
    "mapped-metadata.example.com": ["::ffff:100.100.100.200"],
    "self.example.com": ["192.0.2.7"],
    "metadata.google.internal": ["93.184.216.36"],
    "proxy.corp": ["10.9.9.9"],
    socket.gethostname(): ["192.0.2.7"],
}


async def _getaddrinfo(host, port, **_):
    if host not in _DNS:
        raise socket.gaierror(socket.EAI_NONAME, "unknown")
    return [
        (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
        for a in _DNS[host]
    ]


class _Internet:
    """Every dial reaches one local server, which echoes and remembers what
    it was sent; `dialled` is every (host, port) the proxy connected to."""

    def __init__(self, server_port: int, received: list[bytes]):
        self.port, self.received, self.dialled = server_port, received, []

    async def connect(self, host, port, **_):
        self.dialled.append((host, port))
        return await asyncio.open_connection("127.0.0.1", self.port)


@pytest.fixture
async def internet():
    received: list[bytes] = []

    async def echo(reader, writer):
        while data := await reader.read(65536):
            received.append(data)
            writer.write(data)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    yield _Internet(server.sockets[0].getsockname()[1], received)
    server.close()


class _Proxy:
    def __init__(self, port: int, session: egress.EgressSession):
        self.port, self.session, self.events = port, session, []

    async def send(self, request: bytes) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(request)
        await writer.drain()
        return reader, writer

    async def ask(self, request: bytes) -> bytes:
        reader, writer = await self.send(request)
        answer = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        return answer


@pytest.fixture
def proxy(internet, monkeypatch):
    """`await proxy(allow=..., deny=..., env=...)`: a listener running
    `EgressProxy.handle` for one session, the way a session's channel does."""
    servers = []

    async def start(allow=(), deny=(), env=None):
        proxy = egress.EgressProxy(
            connect=internet.connect, getaddrinfo=_getaddrinfo, environ=env or {}
        )
        events: list[dict] = []

        async def record(payload):
            events.append(payload)

        session = egress.EgressSession(
            "sess-1", egress.PhaseLists("runtime", tuple(allow), tuple(deny)), record
        )
        server = await asyncio.start_server(
            lambda r, w: proxy.handle(r, w, session), "127.0.0.1", 0
        )
        servers.append(server)
        handle = _Proxy(server.sockets[0].getsockname()[1], session)
        handle.events = events
        return handle

    yield start
    for server in servers:
        server.close()


async def test_an_allowed_host_is_tunnelled_to_the_address_it_checked(proxy, internet):
    p = await proxy(allow=["api.example.com"])
    reader, writer = await p.send(b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
    assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    writer.write(b"ping")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(4), 5) == b"ping"
    writer.close()
    # Dialled by the address the policy checked, never re-resolved by name.
    assert internet.dialled == [("93.184.216.34", 443)]


async def test_an_absolute_form_request_is_forwarded_in_origin_form(proxy, internet):
    p = await proxy(allow=["api.example.com"])
    reader, writer = await p.send(
        b"GET http://api.example.com/v1/x?q=1 HTTP/1.1\r\nHost: evil.example\r\n"
        b"Proxy-Authorization: Basic c2VjcmV0\r\nConnection: keep-alive\r\n\r\n"
    )
    echoed = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    writer.close()
    assert echoed == (
        b"GET /v1/x?q=1 HTTP/1.1\r\nHost: api.example.com\r\nConnection: close\r\n\r\n"
    )
    assert internet.dialled == [("93.184.216.34", 80)]


@pytest.mark.parametrize(
    ("host", "port", "allow", "deny", "allowed"),
    [
        ("api.example.com", 443, ["api.example.com"], [], True),
        ("API.Example.com.", 443, ["api.example.com"], [], True),
        ("api.example.com", 443, ["api.example.com"], ["api.example.com"], False),
        ("api.example.com", 443, ["**"], ["*.example.com"], False),
        ("api.example.com", 443, ["*.example.com"], [], True),
        ("a.b.example.com", 443, ["*.example.com"], [], False),
        ("example.com", 443, ["*.example.com"], [], False),
        ("anything.at.all", 22, ["*"], [], True),
        ("anything.at.all", 22, ["**"], [], True),
        ("api.example.com", 443, ["api.example.com:443"], [], True),
        ("api.example.com", 8443, ["api.example.com:443"], [], False),
        ("api.example.com", 443, [], [], False),
    ],
    ids=[
        "exact",
        "case-and-trailing-dot",
        "deny-wins",
        "wildcard-deny-wins-over-star-star",
        "wildcard-one-label",
        "wildcard-not-two-labels",
        "wildcard-not-the-bare-domain",
        "star",
        "star-star",
        "port-matches",
        "port-differs",
        "nothing-allowed",
    ],
)
def test_match(host, port, allow, deny, allowed):
    verdict = egress.match(host, port, tuple(allow), tuple(deny))
    assert verdict.allowed is allowed
    assert bool(verdict.reason) is not allowed


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "rebound.example.com",
        "metadata.example.com",
        "v6-link-local.example.com",
        "mapped-metadata.example.com",
        "self.example.com",
        "metadata.google.internal",
        "127.0.0.1",
        "169.254.169.254",
        "::7f00:1",
        "2002:7f00:1::",
        "64:ff9b::a9fe:a9fe",
        "::ffff:0:7f00:1",
    ],
    ids=[
        "loopback",
        "dns-rebound-to-loopback",
        "link-local-v4",
        "link-local-v6",
        "v4-mapped-metadata-address",
        "the-hosts-own-address",
        "metadata-name",
        "loopback-literal",
        "metadata-literal",
        "v4-compatible-loopback",
        "6to4-loopback",
        "nat64-metadata",
        "v4-translated-loopback",
    ],
)
async def test_always_denied_addresses_even_under_an_exact_allow(host):
    proxy = egress.EgressProxy(getaddrinfo=_getaddrinfo, environ={})
    verdict = await proxy.resolve_and_check(host, 443, allow=(host, "**"))
    assert not verdict.allowed and "always denied" in verdict.reason


@pytest.mark.parametrize(
    ("host", "allow", "allowed"),
    [
        ("db.internal", ["*.internal"], False),
        ("db.internal", ["**"], False),
        ("db.internal", ["db.internal"], True),
        ("db.internal", ["db.internal:443"], True),
        ("10.0.0.5", ["10.0.0.5"], True),
        ("10.0.0.5", ["*"], False),
        ("100.100.1.1", ["*"], False),
        ("100.100.1.1", ["100.100.1.1"], True),
        ("64:ff9b::a00:1", ["*"], False),
    ],
    ids=[
        "one-label-wildcard",
        "star-star",
        "exact",
        "exact-with-port",
        "literal",
        "star",
        "shared-address-space",
        "shared-address-space-exact",
        "nat64-private",
    ],
)
async def test_a_non_public_address_needs_an_exact_allow_entry(host, allow, allowed):
    proxy = egress.EgressProxy(getaddrinfo=_getaddrinfo, environ={})
    verdict = await proxy.resolve_and_check(host, 443, allow=tuple(allow))
    assert verdict.allowed is allowed
    assert verdict.addresses == (tuple(_DNS.get(host, [host])) if allowed else ())


async def test_a_refusal_is_one_403_line_recorded_once_per_host(proxy, internet):
    p = await proxy(allow=["api.example.com", "localhost"], deny=["api.example.com"])
    for _ in range(2):
        answer = await p.ask(b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
        assert answer.startswith(b"HTTP/1.1 403 Forbidden\r\n")
        assert answer.endswith(b"\r\n\r\nkraft: api.example.com is on the deny list\n")
    await p.ask(b"CONNECT localhost:443 HTTP/1.1\r\n\r\n")
    assert p.events == [
        {
            "session_id": "sess-1",
            "host": "api.example.com",
            "port": 443,
            "phase": "runtime",
            "reason": "api.example.com is on the deny list",
        },
        {
            "session_id": "sess-1",
            "host": "localhost",
            "port": 443,
            "phase": "runtime",
            "reason": "localhost resolves to 127.0.0.1, which is always denied",
        },
    ]
    assert internet.dialled == []


async def test_refusal_events_are_capped_per_session(proxy, monkeypatch):
    """A client walking many hosts cannot flood the timeline: past the cap
    one last event says the rest are not recorded, and each is still a 403."""
    monkeypatch.setattr(egress, "MAX_REFUSAL_EVENTS", 2)
    p = await proxy()
    for host in ("a.test", "b.test", "c.test", "d.test"):
        answer = await p.ask(f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode())
        assert answer.startswith(b"HTTP/1.1 403 ")
    assert [e.get("host") for e in p.events] == ["a.test", "b.test", None]
    assert p.events[-1]["suppressed"] is True
    assert p.session.refused == {"a.test", "b.test"}


@pytest.mark.parametrize(
    "request_line",
    [b"CONNECT kraft:80 HTTP/1.1", b"GET http://kraft/v1/progress HTTP/1.1"],
    ids=["connect", "absolute-form"],
)
async def test_host_kraft_is_refused_by_a_proxy_with_no_worker_api(proxy, internet, request_line):
    """Never forwarded, whatever the allow list says: host `kraft` is the
    worker API or nothing (`test_egress_worker_api.py` has the API)."""
    p = await proxy(allow=["**", "kraft"])
    answer = await p.ask(request_line + b"\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 403 ")
    assert b"host 'kraft' is the worker API, and no worker API here" in answer
    assert internet.dialled == []


@pytest.mark.parametrize(
    ("env", "dialled", "sent"),
    [
        (
            {"HTTPS_PROXY": "http://user:pw@proxy.corp:3128"},
            ("proxy.corp", 3128),
            b"CONNECT api.example.com:443 HTTP/1.1\r\nHost: api.example.com:443\r\n"
            b"Proxy-Authorization: Basic dXNlcjpwdw==\r\n\r\n",
        ),
        (
            {"https_proxy": "http://proxy.corp:3128", "NO_PROXY": ".example.com"},
            ("93.184.216.34", 443),
            b"",
        ),
    ],
    ids=["chained", "no-proxy-bypasses"],
)
async def test_chains_to_the_daemons_upstream_proxy(proxy, internet, env, dialled, sent):
    p = await proxy(allow=["api.example.com"], env=env)
    reader, writer = await p.send(b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
    if sent:
        # The echo server plays the upstream: its "answer" is our CONNECT, so
        # the proxy sees no 200 and refuses; what matters is what it sent.
        answer = await asyncio.wait_for(reader.read(), 5)
        assert answer.startswith(b"HTTP/1.1 502 ")
        assert internet.received == [sent]
    else:
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200 ")
    writer.close()
    assert internet.dialled == [dialled]


@pytest.mark.parametrize(
    ("sent", "forwarded"),
    [
        (
            b"POST http://api.example.com/a HTTP/1.1\r\nHost: api.example.com\r\n"
            b"Content-Length: 3\r\n\r\nabcGET http://evil.example/ HTTP/1.1\r\n\r\n",
            b"POST http://api.example.com/a HTTP/1.1\r\nHost: api.example.com\r\n"
            b"Content-Length: 3\r\nConnection: close\r\n\r\nabc",
        ),
        (
            b"GET http://evil.example\\@api.example.com/x HTTP/1.1\r\nHost: evil.example\r\n\r\n",
            b"GET http://api.example.com/x HTTP/1.1\r\nHost: api.example.com\r\n"
            b"Connection: close\r\n\r\n",
        ),
    ],
    ids=["pipelined-request-dropped", "backslash-userinfo"],
)
async def test_an_upstream_proxy_gets_exactly_the_one_request_that_was_checked(
    proxy, internet, sent, forwarded
):
    """Chained upstream, the request line and Host are rebuilt from what was
    checked and only one request's bytes go on: the upstream never sees a
    second request, or a URL it might parse as a different host."""
    p = await proxy(allow=["api.example.com"], env={"HTTP_PROXY": "http://proxy.corp:3128"})
    reader, writer = await p.send(sent)
    echoed = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    writer.close()
    assert echoed == forwarded.partition(b"\r\n\r\n")[0] + b"\r\n\r\n"
    assert b"".join(internet.received) == forwarded
    assert internet.dialled == [("proxy.corp", 3128)]


async def test_a_request_sent_later_never_reaches_the_upstream(proxy, internet):
    """Once the one request is on its way, nothing more is read from the
    client: a second request written after the first was forwarded stays put."""
    p = await proxy(allow=["api.example.com"], env={"HTTP_PROXY": "http://proxy.corp:3128"})
    reader, writer = await p.send(b"GET http://api.example.com/a HTTP/1.1\r\n\r\n")
    first = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    writer.write(b"GET http://evil.example/ HTTP/1.1\r\n\r\n")
    await writer.drain()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 0.3)
    writer.close()
    assert b"".join(internet.received) == first


async def test_a_chunked_request_is_refused_before_an_upstream_proxy(proxy, internet):
    p = await proxy(allow=["api.example.com"], env={"HTTP_PROXY": "http://proxy.corp:3128"})
    answer = await p.ask(
        b"POST http://api.example.com/ HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
    )
    assert answer.startswith(b"HTTP/1.1 400 Bad Request\r\n")
    assert internet.dialled == []


@pytest.mark.parametrize(
    "head",
    [
        b"nonsense\r\n\r\n",
        b"BREW http://api.example.com/ HTTP/1.1\r\n\r\n",
        b"GET https://api.example.com/ HTTP/1.1\r\n\r\n",
        b"GET /relative HTTP/1.1\r\n\r\n",
        b"CONNECT api.example.com:99999 HTTP/1.1\r\n\r\n",
        b"CONNECT api.example.com HTTP/1.1\r\n\r\n",
        b"GET http://api.example.com/ HTTP/1.1\r\nX: " + b"a" * egress.MAX_HEAD + b"\r\n\r\n",
        b"GET http://a.io/ HTTP/1.1\r\nX: a\n\nGET http://evil.com/lf HTTP/1.1\nHost: evil.com\n"
        b"Y: z\r\n\r\n",
        b"GET http://api.example.com/ HTTP/1.1\r\nno colon here\r\n\r\n",
        b"GET http://api.example.com/\x00 HTTP/1.1\r\n\r\n",
    ],
    ids=[
        "garbage",
        "other-method",
        "https-without-connect",
        "origin-form",
        "port-out-of-range",
        "connect-without-port",
        "oversized-head",
        "bare-lf-smuggles-a-request",
        "header-without-a-name",
        "nul-in-request-line",
    ],
)
async def test_a_malformed_request_gets_400(proxy, internet, head):
    p = await proxy(allow=["**"])
    assert (await p.ask(head)).startswith(b"HTTP/1.1 400 Bad Request\r\n")
    assert internet.dialled == [] and p.events == []


async def test_an_idle_connection_is_dropped_before_a_request(proxy, internet, monkeypatch):
    monkeypatch.setattr(egress, "HEAD_TIMEOUT", 0.05)
    p = await proxy(allow=["**"])
    assert await p.ask(b"CONNECT api.exa") == b""
    assert internet.dialled == []
