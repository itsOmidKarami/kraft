"""The egress proxy a sandboxed session under `network:` reaches the world
through (sandbox part 2, P4; spec §1 and §4).

Policy and tunnelling only: one call to `EgressProxy.handle` is one accepted
connection from one session's channel, and everything it needs about that
session -- its phase's lists, where a refusal is recorded, which hosts it was
already refused -- arrives as an `EgressSession`. The listener, and so the
session's lifetime, is the channel's (`kraft.worker.channel`).

Per connection: parse `CONNECT host:port` or an absolute-form `http://`
request; refuse host `kraft` (the worker API, not yet available); match the
host against the lists (deny wins); resolve it once on the host and refuse
an always-denied address or a private one the allow list does not name
exactly; then dial the address that was checked -- never the name again, so
a DNS answer that changes between check and dial (rebinding) cannot slip a
different address through. Outbound connections chain through the daemon's
own `HTTPS_PROXY`/`HTTP_PROXY` unless `NO_PROXY` covers the host.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
import socket
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

#: What a connection a session's policy refused is recorded as.
SANDBOX_EGRESS_REFUSED = "sandbox_egress_refused"

#: The most a request line plus its headers may be (R9).
MAX_HEAD = 64 * 1024
#: Seconds a connection may sit before its request head is complete.
HEAD_TIMEOUT = 30.0
#: Seconds a dial to an origin or the upstream proxy may take.
CONNECT_TIMEOUT = 30.0

#: Cloud metadata names, refused whatever they resolve to. Their usual
#: address (169.254.169.254) is link-local, and refused by address as well.
_METADATA_NAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
#: Metadata addresses that are neither link-local nor loopback: AWS's IPv6
#: endpoint (ULA, so private anyway, but never reachable by an exact entry
#: either) and Alibaba's (shared address space, not "private").
_METADATA_ADDRESSES = frozenset(
    {ipaddress.ip_address("fd00:ec2::254"), ipaddress.ip_address("100.100.100.200")}
)

#: Methods forwarded in absolute form. Anything else, bar CONNECT, is a 400.
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
#: Request headers the proxy consumes rather than passes on.
_HOP_HEADERS = frozenset({"proxy-authorization", "proxy-connection", "connection", "keep-alive"})
_CONNECT_TARGET = re.compile(r"\[?([^\[\]]+?)\]?:(\d{1,5})")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    #: Why not, for the 403 body and the event; empty when allowed.
    reason: str = ""
    #: The addresses checked and allowed, in the resolver's order: what to dial.
    addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhaseLists:
    """One phase's lists as a session runs under them, its harness's hosts
    already in `allow` for `runtime`."""

    phase: str
    allow: tuple[str, ...]
    deny: tuple[str, ...]

    @classmethod
    def of(cls, network: dict, phase: str, requires: tuple[str, ...] = ()) -> PhaseLists:
        """`phase`'s lists out of a dumped `sandbox.network`. The `runtime`
        phase also allows `requires`, the harness's own hosts (spec §1); its
        `deny` still wins over them."""
        lists = network.get(phase) or {}
        allow = tuple(lists.get("allow", ()))
        if phase == "runtime":
            allow = tuple(dict.fromkeys((*allow, *requires)))
        return cls(phase, allow, tuple(lists.get("deny", ())))

    def to_json(self) -> dict:
        """As `worker_sessions.egress` holds it."""
        return {"phase": self.phase, "allow": list(self.allow), "deny": list(self.deny)}

    @classmethod
    def from_json(cls, data: dict) -> PhaseLists:
        return cls(data["phase"], tuple(data["allow"]), tuple(data["deny"]))


@dataclass
class EgressSession:
    """What `handle` knows of the session a connection came from."""

    session_id: str
    lists: PhaseLists
    #: Records one refusal; gets the event payload. Called once per host.
    record_refusal: Callable[[dict], Awaitable[None]]
    #: Hosts already recorded, so a client's retry loop cannot flood events.
    refused: set[str] = field(default_factory=set)


def _split(pattern: str) -> tuple[str, int | None]:
    host, sep, port = pattern.rpartition(":")
    return (host, int(port)) if sep and port.isdigit() else (pattern, None)


def _norm(host: str) -> str:
    return host.lower().rstrip(".")


def _matches(pattern: str, host: str, port: int) -> bool:
    name, want = _split(pattern)
    if want is not None and want != port:
        return False
    name = _norm(name)
    if name in ("*", "**"):
        return True
    if name.startswith("*."):
        label, _, rest = host.partition(".")
        return bool(label) and rest == name[2:]
    return host == name


def _exact(pattern: str, host: str, port: int) -> bool:
    name, want = _split(pattern)
    return "*" not in name and _norm(name) == host and want in (None, port)


def match(host: str, port: int, allow: tuple[str, ...], deny: tuple[str, ...]) -> Verdict:
    """`host:port` against network-policy@1 lists: deny wins, and nothing is
    allowed that no allow entry names. `*.example.com` covers exactly one
    label in front of `example.com`; `*` and `**` cover everything."""
    host = _norm(host)
    if any(_matches(p, host, port) for p in deny):
        return Verdict(False, f"{host} is on the deny list")
    if any(_matches(p, host, port) for p in allow):
        return Verdict(True)
    return Verdict(False, f"{host}:{port} is not on the allow list")


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        addr = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped or addr


class EgressProxy:
    """One per daemon. `connect`, `getaddrinfo` and `environ` are seams for
    tests; the daemon takes the defaults."""

    def __init__(
        self,
        *,
        connect=asyncio.open_connection,
        getaddrinfo=None,
        environ: Mapping[str, str] = os.environ,
    ):
        self._connect = connect
        self._getaddrinfo = getaddrinfo
        self._environ = environ

    async def _resolve(self, host: str, port: int) -> list[str]:
        if _address(host) is not None:
            return [host]
        lookup = self._getaddrinfo or asyncio.get_running_loop().getaddrinfo
        infos = await lookup(host, port, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(info[4][0] for info in infos))

    async def _own_addresses(self) -> set:
        # ponytail: the host's own addresses are loopback (refused anyway)
        # plus what its hostname resolves to. No interface enumeration, so a
        # second NIC's address is caught only by the private-address rule.
        try:
            names = await self._resolve(socket.gethostname(), 0)
        except OSError:
            return set()
        return {a for n in names if (a := _address(n)) is not None}

    async def resolve_and_check(self, host: str, port: int, *, allow: tuple[str, ...]) -> Verdict:
        """Resolve `host` once, on the host, and check every address it
        answers: loopback, link-local, unspecified, a metadata address or the
        host's own refuse outright (any one of them refuses the lot: a
        rebinding answer mixes a public address with a private one); a
        private (RFC 1918, ULA) address needs an allow entry naming `host`
        exactly, never a wildcard. Allowed: the addresses to dial."""
        host = _norm(host)
        if host in _METADATA_NAMES:
            return Verdict(False, f"{host} is a cloud metadata name, which is always denied")
        try:
            addresses = await self._resolve(host, port)
        except OSError as exc:
            return Verdict(False, f"{host} does not resolve: {exc}")
        own = await self._own_addresses()
        exact = any(_exact(p, host, port) for p in allow)
        for raw in addresses:
            addr = _address(raw)
            if addr is None:
                return Verdict(False, f"{host} resolves to {raw!r}, not an address")
            if (
                addr.is_loopback
                or addr.is_link_local
                or addr.is_unspecified
                or addr.is_multicast
                or addr in _METADATA_ADDRESSES
                or addr in own
            ):
                return Verdict(False, f"{host} resolves to {addr}, which is always denied")
            if addr.is_private and not exact:
                return Verdict(
                    False,
                    f"{host} resolves to private address {addr}; only an allow entry "
                    f"naming {host} exactly reaches it",
                )
        return Verdict(True, addresses=tuple(addresses))

    def _upstream(self, scheme: str, host: str) -> tuple[str, int, str | None] | None:
        """The daemon's own proxy for `scheme`, unless `NO_PROXY` covers
        `host`: (host, port, Proxy-Authorization value or None)."""
        env = self._environ
        url = env.get(f"{scheme}_proxy") or env.get(f"{scheme.upper()}_PROXY")
        if not url:
            return None
        no_proxy = env.get("no_proxy") or env.get("NO_PROXY") or ""
        if no_proxy and urllib.request.proxy_bypass_environment(host, {"no": no_proxy}):
            return None
        parts = urlsplit(url if "://" in url else f"http://{url}")
        auth = None
        if parts.username is not None:
            creds = f"{unquote(parts.username)}:{unquote(parts.password or '')}"
            auth = "Basic " + base64.b64encode(creds.encode()).decode()
        return parts.hostname or "", parts.port or 80, auth

    async def _dial(self, addresses: tuple[str, ...], port: int):
        error: OSError | None = None
        for address in addresses:
            try:
                return await asyncio.wait_for(self._connect(address, port), CONNECT_TIMEOUT)
            except (OSError, TimeoutError) as exc:
                error = OSError(str(exc) or type(exc).__name__)
        raise error or OSError("nothing to dial")

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, session: EgressSession
    ) -> None:
        """One accepted connection, start to finish; always closes `writer`."""
        try:
            await self._handle(reader, writer, session)
        except OSError, asyncio.IncompleteReadError, TimeoutError:
            pass
        finally:
            writer.close()

    async def _handle(self, reader, writer, session: EgressSession) -> None:
        try:
            head, rest = await asyncio.wait_for(_read_head(reader), HEAD_TIMEOUT)
        except TimeoutError:
            return
        if head is None:
            return
        request = _parse(head)
        if isinstance(request, str):
            return await _answer(writer, 400, "Bad Request", request)
        method, host, port, target, headers = request

        if _norm(host) == "kraft":
            return await self._refuse(
                writer, session, host, port, "the worker API (host 'kraft') is not available yet"
            )
        verdict = match(host, port, session.lists.allow, session.lists.deny)
        if verdict.allowed:
            verdict = await self.resolve_and_check(host, port, allow=session.lists.allow)
        if not verdict.allowed:
            return await self._refuse(writer, session, host, port, verdict.reason)

        tunnel = method == "CONNECT"
        upstream = self._upstream("https" if tunnel else "http", _norm(host))
        try:
            if upstream is None:
                out_reader, out_writer = await self._dial(verdict.addresses, port)
            else:
                proxy_host, proxy_port, _ = upstream
                out_reader, out_writer = await asyncio.wait_for(
                    self._connect(proxy_host, proxy_port), CONNECT_TIMEOUT
                )
        except (OSError, TimeoutError) as exc:
            return await _answer(writer, 502, "Bad Gateway", f"{host}:{port}: {exc}")

        try:
            auth = upstream[2] if upstream is not None else None
            auth_line = f"Proxy-Authorization: {auth}\r\n" if auth else ""
            if tunnel and upstream is not None:
                authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
                out_writer.write(
                    f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n{auth_line}\r\n".encode()
                )
                await out_writer.drain()
                reply, early = await asyncio.wait_for(_read_head(out_reader), CONNECT_TIMEOUT)
                status = (reply or b"").split(b"\r\n", 1)[0].split()
                if len(status) < 2 or status[1] != b"200":
                    return await _answer(
                        writer, 502, "Bad Gateway", "the upstream proxy refused the tunnel"
                    )
            if tunnel:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if upstream is not None:
                    writer.write(early)
            else:
                line = target if upstream is not None else _origin_form(target)
                kept = "".join(f"{h}\r\n" for h in headers if _name(h) not in _HOP_HEADERS)
                out_writer.write(
                    f"{method} {line} HTTP/1.1\r\n{kept}{auth_line}"
                    "Connection: close\r\n\r\n".encode()
                )
            if rest:
                out_writer.write(rest)
            await asyncio.gather(
                _pump(reader, out_writer), _pump(out_reader, writer), return_exceptions=True
            )
        finally:
            out_writer.close()

    async def _refuse(self, writer, session: EgressSession, host: str, port: int, reason: str):
        host = _norm(host)
        if host not in session.refused:
            session.refused.add(host)
            await session.record_refusal(
                {
                    "session_id": session.session_id,
                    "host": host,
                    "port": port,
                    "phase": session.lists.phase,
                    "reason": reason,
                }
            )
        await _answer(writer, 403, "Forbidden", reason)


async def _read_head(reader: asyncio.StreamReader) -> tuple[bytes | None, bytes]:
    """The request (or response) head, and whatever arrived after it. None
    for a peer that closed first; a head past `MAX_HEAD` comes back whole so
    `_parse` refuses it."""
    head = b""
    while b"\r\n\r\n" not in head:
        if len(head) > MAX_HEAD:
            return head, b""
        chunk = await reader.read(8192)
        if not chunk:
            return None, b""
        head += chunk
    head, _, rest = head.partition(b"\r\n\r\n")
    return head, rest


def _parse(head: bytes) -> tuple[str, str, int, str, list[str]] | str:
    """(method, host, port, target, header lines), or why it is a 400."""
    if len(head) > MAX_HEAD:
        return f"the request head is over {MAX_HEAD} bytes"
    line, *headers = head.decode("latin-1").split("\r\n")
    parts = line.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return "not an HTTP/1 request line"
    method, target, _ = parts
    if method == "CONNECT":
        found = _CONNECT_TARGET.fullmatch(target)
        if not found:
            return "CONNECT needs host:port"
        host, port = found.group(1), int(found.group(2))
    elif method in _METHODS:
        url = urlsplit(target)
        if url.scheme != "http" or not url.hostname:
            return "a proxied request needs an absolute http:// URL (https goes by CONNECT)"
        try:
            host, port = url.hostname, url.port or 80
        except ValueError:
            return "the URL's port is not a port"
    else:
        return f"method {method!r} is not proxied"
    if not 0 < port < 65536:
        return f"port {port} is out of range"
    return method, host, port, target, [h for h in headers if h]


def _origin_form(target: str) -> str:
    url = urlsplit(target)
    return (url.path or "/") + (f"?{url.query}" if url.query else "")


def _name(header: str) -> str:
    return header.split(":", 1)[0].strip().lower()


async def _answer(writer: asyncio.StreamWriter, code: int, phrase: str, reason: str) -> None:
    body = f"kraft: {reason}\n".encode()
    writer.write(
        f"HTTP/1.1 {code} {phrase}\r\nContent-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    """Copy until `src` ends, then half-close `dst` so the far side sees EOF
    while the other direction finishes."""
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    finally:
        if dst.can_write_eof():
            dst.write_eof()
