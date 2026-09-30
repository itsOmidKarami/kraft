"""Proxy-managed credentials at the egress proxy (sandbox part 2, P6; spec §6).

A container under `network:` holds a sentinel where a managed credential's
variable would be; the daemon holds the real value, in memory, on the
session's `InjectRule`s. A CONNECT to a host one of those rules goes to is
not tunnelled blind: `terminate` answers the worker's TLS with a leaf the
Kraft CA minted for that host (the bundle the container trusts carries the
CA), speaks TLS to the real host itself, and passes HTTP/1.1 requests
between them, swapping the sentinel for the value on each. Every other host
stays a blind tunnel (`egress`).

What holds, and why:

- **Only where the phase names the host.** A rule applies when
  the session's allow list names its host exactly for this port; one
  reached only through a wildcard stays a blind tunnel, so the worker's
  sentinel is all that host ever sees. A plain `http://` request to such a
  host is refused: a credential is only ever injected over TLS.
- **The worker's TLS must name the CONNECT host.** A handshake whose server
  name (SNI) is anything else, or none, is refused; so is a request whose
  one `Host` is not that host. Dial address and `server_hostname` come from
  the checked CONNECT target alone, never from what the worker sends inside.
- **Sentinel or nothing.** Every instance of a rule's header must be
  exactly the sentinel in the rule's `format`, and at least one rule's
  header must be there; else 403, an event, and the connection closes --
  the request never reaches the host, so the worker cannot authenticate
  there with a key of its own, or use the injection for one it smuggled in.
  A daemon with no value refuses every such request: the sentinel is never
  forwarded in its place.
- **Replaced, never appended.** Every instance of the header is dropped and
  one is set, so no two `Authorization` headers reach the host. A value
  holding a control character is refused rather than split into headers.
- **A real HTTP/1.1 intermediary, strict about framing.** Heads are bounded
  (the stream limit, `egress.MAX_HEAD`) and parsed by `egress`'s rules: no
  bare LF, no control character, no folded line. A request with both
  Transfer-Encoding and Content-Length, any coding but `chunked`, or more
  than one length is a 400. Bodies are forwarded by their framing and no
  further, so whatever follows is the next request -- checked and swapped
  like the first (keep-alive and pipelining both). Responses stream back,
  chunked or by length; one delimited by close ends the connection.
- **HTTP/1.1 only.** ALPN offers `http/1.1` on both hops; a CLI that
  needs HTTP/2 or an upgrade (a 101 ends the connection) does not get it
  here. The upstream is verified against the daemon's own roots plus the
  sandbox extra CA (`docker_forward.extra_ca`), never the Kraft CA, never
  with verification off; the daemon's `HTTPS_PROXY` is dialled with a
  CONNECT first when it has one (`EgressProxy._tunnel`).
- **Where the value goes.** Only into the header on the upstream hop: never
  argv, the container's env, an event, a log line or the session's egress
  row (`InjectRule.to_json` leaves it out, and its `repr` hides it).
  Response headers pass back unmodified: an upstream that echoed the value
  would reach the worker, a limit this does not defend against.
- **Bounded.** A connection idle between requests closes after
  `IDLE_TIMEOUT`; any one read of a body or response waits `READ_TIMEOUT`.
"""

from __future__ import annotations

import asyncio
import re
import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from kraft.worker import ca, egress

#: Seconds a terminated connection may sit between one request and the next.
IDLE_TIMEOUT = 120.0
#: Seconds any one read of a request body or a response may wait: a model's
#: first token can take minutes.
READ_TIMEOUT = 600.0

_REQUEST_LINE = re.compile(r"([A-Z]+) (/[\x21-\x7e]*) (HTTP/1\.[01])")
_CHUNK_SIZE = re.compile(rb"[0-9A-Fa-f]{1,16}")
_CRLF_HEAD = b"\r\n\r\n"


class Malformed(OSError):
    """A body or response the proxy cannot frame: the connection closes."""


@dataclass(frozen=True)
class InjectRule:
    """One place one managed credential goes: `header` on requests to
    exactly `domain`, carrying `format` with the value at its `%s`."""

    env: str
    domain: str
    header: str
    sentinel: str
    format: str = "%s"
    #: The daemon's own value; None when it has none. In memory only.
    value: str | None = field(default=None, repr=False, compare=False)

    def carrying(self, value: str) -> str:
        return self.format.replace("%s", value, 1)

    def to_json(self) -> dict:
        """As `worker_sessions.egress` holds it: everything but the value."""
        return {
            "env": self.env,
            "domain": self.domain,
            "header": self.header,
            "sentinel": self.sentinel,
            "format": self.format,
        }

    @classmethod
    def from_json(cls, data: dict, environ: Mapping[str, str]) -> InjectRule:
        return cls(**data, value=environ.get(data["env"]))


def rules(credentials: Iterable, environ: Mapping[str, str]) -> tuple[InjectRule, ...]:
    """One rule per place each managed credential (`SandboxCredential`,
    its sentinel set) goes, its value `environ`'s."""
    return tuple(
        InjectRule(
            cred.env,
            egress._norm(rule.domain),
            rule.header.lower(),
            cred.sentinel,
            rule.format or "%s",
            environ.get(cred.env),
        )
        for cred in credentials
        for rule in cred.inject
    )


def rules_for(session, host: str, port: int) -> tuple[InjectRule, ...]:
    """The session's rules for `host:port`, when its phase's allow list
    names that host exactly; none leaves it a blind tunnel."""
    host = egress._norm(host)
    found = tuple(r for r in session.credentials if r.domain == host)
    if found and any(egress._exact(p, host, port) for p in session.lists.allow):
        return found
    return ()


def _server_context(run_dirs, host: str, seen: list) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with ca.HOST_LEAF_LOCK:  # loaded whole: a re-mint rewrites the pair
        context.load_cert_chain(*ca.mint_host_leaf(run_dirs, host))
    context.set_alpn_protocols(["http/1.1"])

    def check(_sslobj, name, _context):
        seen.append(name)
        if name is None or egress._norm(name) != host:
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        return None

    context.sni_callback = check
    return context


def _client_context(environ: Mapping[str, str]) -> ssl.SSLContext:
    """The daemon's roots plus the sandbox extra CA; verification on."""
    from kraft.worker.backends.docker_forward import extra_ca

    context = ca.strict(ssl.create_default_context())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if (extra := extra_ca(environ)) is not None:
        context.load_verify_locations(cadata="\n".join(extra[1]))
    context.set_alpn_protocols(["http/1.1"])
    return context


async def terminate(
    proxy, reader, writer, session, host: str, port: int, addresses, found, rest: bytes
) -> None:
    """The CONNECT to `host:port` has passed policy and `found` are its
    rules: answer it, take over the worker's TLS, and pass its requests on
    to the host until either side is done."""
    from kraft.config import ConfigError

    host = egress._norm(host)
    if rest:
        return await egress._answer(
            writer, 400, "Bad Request", "bytes sent before the tunnel was established"
        )
    seen: list = []
    context = await asyncio.to_thread(_server_context, session.run_dirs, host, seen)
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    try:
        await asyncio.wait_for(writer.start_tls(context), egress.HEAD_TIMEOUT)
    except (OSError, TimeoutError):
        if seen and (seen[0] is None or egress._norm(seen[0]) != host):
            name = f"names {seen[0]!r}" if seen[0] else "names no host"
            await proxy.record(session, host, port, f"the worker's TLS {name}, not {host}")
        return

    upstream = None
    try:
        while True:
            try:
                head = await _until(reader, _CRLF_HEAD, IDLE_TIMEOUT)
            except Malformed:
                return await egress._answer(writer, 400, "Bad Request", "the head is too long")
            request = _parse(head)
            if isinstance(request, str):
                return await egress._answer(writer, 400, "Bad Request", request)
            method, target, version, headers = request
            length = _request_length(headers)
            if isinstance(length, str):
                return await egress._answer(writer, 400, "Bad Request", length)
            refusal = _misdirected(headers, host, port)
            swapped = refusal or _swap(headers, found)
            if isinstance(swapped, str):
                return await proxy._refuse(writer, session, host, port, swapped)
            if upstream is None:
                try:
                    upstream = await _open(proxy, host, port, addresses)
                except ConfigError as exc:
                    return await egress._answer(writer, 502, "Bad Gateway", str(exc))
                except (OSError, TimeoutError) as exc:
                    return await egress._answer(writer, 502, "Bad Gateway", f"{host}: {exc}")
            up_reader, up_writer = upstream
            up_writer.write(
                f"{method} {target} {version}\r\n".encode("latin-1")
                + "".join(f"{h}\r\n" for h in swapped).encode("latin-1")
                + b"\r\n"
            )
            keep = await _exchange(reader, up_writer, up_reader, writer, method, length)
            if not keep or version != "HTTP/1.1" or _closes(headers):
                return
    finally:
        if upstream is not None:
            upstream[1].close()


async def _open(proxy, host: str, port: int, addresses):
    """TLS to the real host: the checked address or the daemon's upstream
    proxy, verified as `host`."""
    out_reader, out_writer, early = await proxy._tunnel(host, port, addresses)
    try:
        if early:
            raise OSError("the upstream proxy sent bytes before TLS")
        context = _client_context(proxy._environ)
        await asyncio.wait_for(
            out_writer.start_tls(context, server_hostname=host), egress.CONNECT_TIMEOUT
        )
    except BaseException:
        out_writer.close()
        raise
    return out_reader, out_writer


def _parse(head: bytes) -> tuple[str, str, str, list[str]] | str:
    """(method, origin-form target, version, header lines), or why a 400."""
    line, *headers = head[: -len(_CRLF_HEAD)].decode("latin-1").split("\r\n")
    found = _REQUEST_LINE.fullmatch(line)
    if not found or found[1] not in egress._METHODS:
        return "not an origin-form HTTP/1 request line"
    if not all(egress._HEADER_LINE.fullmatch(h) for h in headers):
        return "a header line that is not `name: value`, or holds a control character"
    return found[1], found[2], found[3], headers


def _values(headers: list[str], name: str) -> list[str]:
    return [h.split(":", 1)[1].strip() for h in headers if egress._name(h) == name]


def _request_length(headers: list[str]) -> int | None | str:
    """The body's length, None for chunked, or why a 400."""
    codings = _values(headers, "transfer-encoding")
    if not codings:
        return egress._content_length(headers)
    if _values(headers, "content-length"):
        return "both Transfer-Encoding and Content-Length"
    if len(codings) != 1 or codings[0].lower() != "chunked":
        return "a Transfer-Encoding other than chunked"
    return None


def _misdirected(headers: list[str], host: str, port: int) -> str | None:
    hosts = _values(headers, "host")
    if len(hosts) == 1 and egress._norm(hosts[0]) in (host, f"{host}:{port}"):
        return None
    return f"a request inside the tunnel to {host} names another Host"


def _swap(headers: list[str], found: tuple[InjectRule, ...]) -> list[str] | str:
    """`headers` with each rule's header set to the real value, or why the
    request is refused."""
    swapped, carried = headers, False
    for rule in found:
        values = _values(swapped, rule.header)
        if not values:
            continue
        if any(v != rule.carrying(rule.sentinel) for v in values):
            return f"{rule.header} does not carry the sentinel {rule.env} is managed with"
        if rule.value is None:
            return f"the Kraft server has no {rule.env} to inject"
        if egress._CONTROL.search(rule.value):
            return f"the Kraft server's {rule.env} holds a character no header may"
        swapped = [h for h in swapped if egress._name(h) != rule.header]
        swapped.append(f"{rule.header}: {rule.carrying(rule.value)}")
        carried = True
    if not carried:
        return "no proxy-managed credential's sentinel in the request"
    return swapped


def _closes(headers: list[str]) -> bool:
    return any(
        "close" in (t.strip().lower() for t in v.split(",")) for v in _values(headers, "connection")
    )


async def _exchange(reader, up_writer, up_reader, writer, method: str, length) -> bool:
    """The request's body up, its response down, both at once (a `100
    Continue` needs the response side while the body waits). Whether the
    connection may carry another request."""
    tasks = [
        asyncio.create_task(_body(reader, up_writer, length)),
        asyncio.create_task(_response(up_reader, writer, method)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
    return tasks[1].result()


async def _read(coro):
    return await asyncio.wait_for(coro, READ_TIMEOUT)


async def _until(src, separator: bytes, timeout: float = READ_TIMEOUT) -> bytes:
    """Up to and including `separator`, within the stream's limit."""
    try:
        return await asyncio.wait_for(src.readuntil(separator), timeout)
    except asyncio.LimitOverrunError as exc:
        raise Malformed("a line or head past the limit") from exc


async def _body(src, dst, length: int | None) -> None:
    if length is None:
        await _chunked(src, dst)
    else:
        await _exactly(src, dst, length)


async def _exactly(src, dst, remaining: int) -> None:
    while remaining > 0:
        data = await _read(src.read(min(remaining, 65536)))
        if not data:
            raise Malformed("the stream ended inside a body")
        dst.write(data)
        await dst.drain()
        remaining -= len(data)


async def _line(src) -> bytes:
    line = await _until(src, b"\r\n")
    if egress._CONTROL.search(line[:-2].decode("latin-1")):
        raise Malformed("a control character in a chunk line")
    return line


async def _chunked(src, dst) -> None:
    """One chunked body, validated as it goes, and not a byte past it."""
    while True:
        line = await _line(src)
        size = line[:-2].split(b";", 1)[0]
        if not _CHUNK_SIZE.fullmatch(size):
            raise Malformed("a chunk size that is not hex")
        dst.write(line)
        if not int(size, 16):
            break
        await _exactly(src, dst, int(size, 16))
        if await _read(src.readexactly(2)) != b"\r\n":
            raise Malformed("a chunk not ended by CRLF")
        dst.write(b"\r\n")
    while (line := await _line(src)) != b"\r\n":
        dst.write(line)
    dst.write(line)
    await dst.drain()


async def _response(src, dst, method: str) -> bool:
    """One response (after any 1xx), streamed as it arrives; whether the
    connection may carry another request."""
    while True:
        head = await _until(src, _CRLF_HEAD)
        dst.write(head)
        await dst.drain()
        status = head.split(b"\r\n", 1)[0].split(b" ", 2)
        if len(status) < 2 or not status[1].isdigit() or not status[0].startswith(b"HTTP/1."):
            raise Malformed("not an HTTP/1 status line")
        code = int(status[1])
        if code == 101:
            return False
        if code >= 200:
            break
    headers = head[: -len(_CRLF_HEAD)].decode("latin-1").split("\r\n")[1:]
    if method == "HEAD" or code in (204, 304):
        return not _closes(headers)
    codings = _values(headers, "transfer-encoding")
    length = egress._content_length(headers) if not codings else None
    if codings and codings[-1].lower().endswith("chunked"):
        await _chunked(src, dst)
    elif isinstance(length, int):
        await _exactly(src, dst, length)
    else:
        # Delimited by close: all of it, then no other request fits.
        while data := await _read(src.read(65536)):
            dst.write(data)
            await dst.drain()
        return False
    return not _closes(headers)
