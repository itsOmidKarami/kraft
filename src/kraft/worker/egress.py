"""The egress proxy a sandboxed session under `network:` reaches the world
through (sandbox part 2, P4; spec §1 and §4).

Policy and tunnelling only: one call to `EgressProxy.handle` is one accepted
connection from one session's channel, and everything it needs about that
session -- its phase's lists, where a refusal is recorded, which hosts it was
already refused -- arrives as an `EgressSession`. The listener, and so the
session's lifetime, is the channel's (`kraft.worker.channel`).

Per connection: parse `CONNECT host:port` or an absolute-form `http://`
request; serve host `kraft` as the worker API (spec §5, P5) for the session
the channel says it is, never forwarded; match the host against the lists
(deny wins); resolve it once on the host and refuse an always-denied address
or a non-public one the allow list does not name exactly; then dial the
address that was checked -- never the name again, so a DNS answer that
changes between check and dial (rebinding) cannot slip a different address
through. Outbound connections chain through the daemon's own
`HTTPS_PROXY`/`HTTP_PROXY` unless `NO_PROXY` covers the host; then the name
is still checked, but the upstream resolves it again and dials what it gets,
so the dialled-address guarantee holds only without one.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import re
import socket
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx

from kraft import store
from kraft.worker import callback, inject, session_mcp

#: What a connection a session's policy refused is recorded as.
SANDBOX_EGRESS_REFUSED = "sandbox_egress_refused"

#: The most a request line plus its headers may be (R9).
MAX_HEAD = 64 * 1024
#: Seconds a connection may sit before its request head is complete.
HEAD_TIMEOUT = 30.0
#: Seconds a dial to an origin or the upstream proxy may take.
CONNECT_TIMEOUT = 30.0
#: How long one in-process worker API call may take before the worker gets a
#: 504: every call is a short request, and a stuck one would hold its
#: connection (and the worker) forever.
CALL_TIMEOUT = 30.0
#: Distinct hosts a session's refusals are recorded for; past this, one last
#: event says the rest are not (they are still refused).
MAX_REFUSAL_EVENTS = 100
#: The most a worker API request may be: a permission hook's stdin carries the
#: tool call's whole input, a file being written among it.
MAX_WORKER_BODY = 16 * 1024 * 1024
#: How much of a refused worker API route its event records.
MAX_ROUTE = 256

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
#: RFC 9110: a token name, a colon, and a value of visible characters,
#: spaces, tabs and obs-text. No CR, LF, NUL or other control character, so
#: nothing forwarded can split into a second request.
_HEADER_LINE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[\t\x20-\x7e\x80-\xff]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


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
    #: The session's work item: all the worker API lets it act on.
    work_item_id: str = ""
    #: Hosts already recorded, so a client's retry loop cannot flood events.
    #: At most `MAX_REFUSAL_EVENTS` of them.
    refused: set[str] = field(default_factory=set)
    #: Set once the cap is reached and the one "suppressed" event is recorded.
    suppressed: bool = False
    #: The proxy-managed credentials this session's requests get (spec §6),
    #: each holding the daemon's real value in memory only.
    credentials: tuple[inject.InjectRule, ...] = ()
    #: Where the Kraft CA mints the leaf shown when TLS is terminated.
    run_dirs: object = None


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


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return None


def _with_embedded(addr) -> list:
    """`addr` and any IPv4 address an IPv6 one carries (mapped, compatible
    `::a.b.c.d`, translated `::ffff:0:a.b.c.d`, 6to4, NAT64), every one of
    which is checked: a route to the embedded address is a route to it."""
    if addr.version == 4:
        return [addr]
    embedded = [addr.ipv4_mapped, addr.sixtofour]
    if int(addr) >> 32 == 0 or addr in _NAT64 or addr in _TRANSLATED:
        embedded.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return [addr, *(e for e in embedded if e is not None)]


class EgressProxy:
    """One per daemon. `connect`, `getaddrinfo` and `environ` are seams for
    tests; the daemon takes the defaults. `app` is the daemon's own API,
    which serves the worker API in-process, beside the session MCP server
    (`session_mcp`) this proxy serves at `/mcp`; a proxy without one refuses
    host `kraft`."""

    def __init__(
        self,
        *,
        connect=asyncio.open_connection,
        getaddrinfo=None,
        environ: Mapping[str, str] = os.environ,
        app=None,
    ):
        self._connect = connect
        self._getaddrinfo = getaddrinfo
        self._environ = environ
        self._app = app
        self._mcp = session_mcp.build(self._ask_permission) if app is not None else None
        self._mcp_app = session_mcp.asgi_app(self._mcp) if self._mcp is not None else None

    def serving(self):
        """What must run, around the daemon's lifespan, while this proxy
        serves: the session MCP server's session manager."""
        return self._mcp.session_manager.run() if self._mcp is not None else nullcontext()

    async def _resolve(self, host: str, port: int) -> list[str]:
        if _address(host) is not None:
            return [host]
        lookup = self._getaddrinfo or asyncio.get_running_loop().getaddrinfo
        infos = await lookup(host, port, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(info[4][0] for info in infos))

    async def _own_addresses(self) -> set:
        # ponytail: the host's own addresses are loopback (refused anyway)
        # plus what its hostname resolves to. No interface enumeration, so a
        # second NIC's address is caught only by the non-public-address rule.
        try:
            names = await self._resolve(socket.gethostname(), 0)
        except OSError:
            return set()
        return {e for n in names if (a := _address(n)) is not None for e in _with_embedded(a)}

    async def resolve_and_check(self, host: str, port: int, *, allow: tuple[str, ...]) -> Verdict:
        """Resolve `host` once, on the host, and check every address it
        answers: loopback, link-local, unspecified, a metadata address or the
        host's own refuse outright (any one of them refuses the lot: a
        rebinding answer mixes a public address with a private one); any
        other non-global address (RFC 1918, ULA, shared, reserved) needs an
        allow entry naming `host` exactly, never a wildcard. An IPv4 address
        embedded in an IPv6 one is checked too. Allowed: the addresses to
        dial."""
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
            found = _address(raw)
            if found is None:
                return Verdict(False, f"{host} resolves to {raw!r}, not an address")
            for addr in _with_embedded(found):
                if (
                    addr.is_loopback
                    or addr.is_link_local
                    or addr.is_unspecified
                    or addr.is_multicast
                    or addr in _METADATA_ADDRESSES
                    or addr in own
                ):
                    return Verdict(False, f"{host} resolves to {addr}, which is always denied")
                if not addr.is_global and not exact:
                    return Verdict(
                        False,
                        f"{host} resolves to non-public address {addr}; only an allow entry "
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
            if method == "CONNECT" or self._app is None:
                why = "no worker API here" if self._app is None else "it is plain http://kraft"
                return await self._refuse(
                    writer, session, host, port, f"host 'kraft' is the worker API, and {why}"
                )
            return await self._worker_api(reader, writer, session, method, target, headers, rest)
        verdict = match(host, port, session.lists.allow, session.lists.deny)
        if verdict.allowed:
            verdict = await self.resolve_and_check(host, port, allow=session.lists.allow)
        if not verdict.allowed:
            return await self._refuse(writer, session, host, port, verdict.reason)

        if rules := inject.rules_for(session, host, port):
            if method != "CONNECT":
                return await self._refuse(
                    writer, session, host, port, "credentials are only injected over TLS"
                )
            return await inject.terminate(
                self, reader, writer, session, host, port, verdict.addresses, rules, rest
            )

        if method == "CONNECT":
            try:
                out_reader, out_writer, early = await self._tunnel(host, port, verdict.addresses)
            except (OSError, TimeoutError) as exc:
                return await _answer(writer, 502, "Bad Gateway", f"{host}:{port}: {exc}")
            try:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n" + early)
                if rest:
                    out_writer.write(rest)
                await asyncio.gather(
                    _pump(reader, out_writer), _pump(out_reader, writer), return_exceptions=True
                )
            finally:
                out_writer.close()
            return

        upstream = self._upstream("http", _norm(host))
        length = 0
        if upstream is not None:
            length = _content_length(headers)
            if isinstance(length, str):
                return await _answer(writer, 400, "Bad Request", length)
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
            # Rebuilt from what was checked, never the client's own target
            # or Host: an upstream parsing either differently would reach
            # a host the policy never saw.
            authority = f"[{host}]" if ":" in host else host
            if port != 80:
                authority += f":{port}"
            line = _origin_form(target)
            if upstream is not None:
                line = f"http://{authority}{line}"
            kept = "".join(f"{h}\r\n" for h in headers if _name(h) not in _HOP_HEADERS | {"host"})
            out_writer.write(
                f"{method} {line} HTTP/1.1\r\nHost: {authority}\r\n{kept}{auth_line}"
                "Connection: close\r\n\r\n".encode()
            )
            if upstream is not None:
                # One request's bytes and no more: whatever else the client
                # sends (a pipelined request) must never reach the upstream.
                out_writer.write(rest[:length])
                await asyncio.gather(
                    _copy(reader, out_writer, length - len(rest[:length])),
                    _pump(out_reader, writer),
                    return_exceptions=True,
                )
                return
            if rest:
                out_writer.write(rest)
            await asyncio.gather(
                _pump(reader, out_writer), _pump(out_reader, writer), return_exceptions=True
            )
        finally:
            out_writer.close()

    async def _tunnel(self, host: str, port: int, addresses: tuple[str, ...]):
        """A byte stream to `host:port`: the checked `addresses` dialled, or
        a CONNECT through the daemon's own upstream proxy. `(reader, writer,
        bytes the upstream sent past its 200)`; `OSError` or `TimeoutError`
        when there is none."""
        upstream = self._upstream("https", _norm(host))
        if upstream is None:
            out_reader, out_writer = await self._dial(addresses, port)
            return out_reader, out_writer, b""
        proxy_host, proxy_port, auth = upstream
        out_reader, out_writer = await asyncio.wait_for(
            self._connect(proxy_host, proxy_port), CONNECT_TIMEOUT
        )
        try:
            auth_line = f"Proxy-Authorization: {auth}\r\n" if auth else ""
            authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            out_writer.write(
                f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n{auth_line}\r\n".encode()
            )
            await out_writer.drain()
            reply, early = await asyncio.wait_for(_read_head(out_reader), CONNECT_TIMEOUT)
            status = (reply or b"").split(b"\r\n", 1)[0].split()
            if len(status) < 2 or status[1] != b"200":
                raise OSError("the upstream proxy refused the tunnel")
        except BaseException:
            out_writer.close()
            raise
        return out_reader, out_writer, early

    async def _worker_api(
        self, reader, writer, session: EgressSession, method, target, headers, rest
    ):
        """One worker API call: a `kraft` shim verb's form fields, turned into
        the API call it names and made in-process as the channel's session.
        No header the client sent is read but the body's length: not its
        `Authorization`, not an `X-Kraft-Session-Id`. `/mcp` is the session
        MCP server's (`_mcp_call`)."""
        # ponytail: no cap on one session's concurrent worker API connections,
        # nor on the permission_decision events its asks record -- parity with
        # a host worker, whose `kraft` and hook are unbounded too. A per-session
        # semaphore and event cap if a worker is ever seen flooding the daemon.
        url = urlsplit(target)
        if url.path == "/mcp":
            return await self._mcp_call(reader, writer, session, method, headers, rest)
        verb = _VERBS.get(url.path.removeprefix("/w/")) if url.path.startswith("/w/") else None
        if method != "POST" or verb is None:
            return await self._refuse_route(
                writer, session, f"{method} {url.path}", "not a worker API verb"
            )
        body = await _read_body(reader, writer, headers, rest)
        if body is None:
            return
        form = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
        scope = callback.scope_for_channel(session)
        call = verb(form, scope)
        if isinstance(call, str):
            return await _answer(writer, 400, "Bad Request", call)
        api_method, path, payload = call
        if not callback.allowed(scope, api_method, path, thread_owner=self._thread_owner):
            return await self._refuse_route(
                writer, session, f"{api_method} {path}", "not this session's to call"
            )
        try:
            reply = await self._call(self._app, api_method, path, scope.session_id, json=payload)
        except TimeoutError:
            return await _answer(writer, 504, "Gateway Timeout", "Kraft did not answer in time")
        status, text = reply.status_code, reply.content
        kind = reply.headers.get("content-type", "application/json")
        if verb is _permission_hook:
            # The hook's own answer, as the shim prints it; anything else is
            # the shim's to fail closed on.
            answer = reply.json() if status == 200 else {}
            if answer.get("code") != 0:
                return await _answer(
                    writer, 502, "Bad Gateway", f"the hook was not answered: {text[:200]!r}"
                )
            text, kind = answer["body"].encode(), "application/json"
        elif verb is _threads and status == 200 and form.get("open"):
            text = json.dumps([t for t in reply.json() if t["state"] != "resolved"]).encode()
        await _respond(writer, status, reply.reason_phrase, kind, text)

    async def _mcp_call(self, reader, writer, session: EgressSession, method, headers, rest):
        """One request to the session MCP server, passed through as it came
        but for who it comes from: the channel's session, whatever
        `X-Kraft-Session-Id` or `Authorization` the worker sent. Only the
        headers the MCP transport reads are passed."""
        scope = callback.scope_for_channel(session)
        if not callback.allowed(scope, method, "/mcp", thread_owner=self._thread_owner):
            return await self._refuse_route(
                writer, session, f"{method} /mcp", "not this session's to call"
            )
        if method == "GET":
            # Stateless: a GET would open an event stream nothing ever ends.
            return await _answer(
                writer, 405, "Method Not Allowed", "the session MCP server streams nothing"
            )
        body = await _read_body(reader, writer, headers, rest)
        if body is None:
            return
        passed = {_name(h): h.split(":", 1)[1].strip() for h in headers if _name(h) in _MCP_HEADERS}
        try:
            reply = await self._call(
                self._mcp_app, method, "/mcp", scope.session_id, content=body, headers=passed
            )
        except TimeoutError:
            return await _answer(writer, 504, "Gateway Timeout", "Kraft did not answer in time")
        kind = reply.headers.get("content-type", "application/json")
        await _respond(writer, reply.status_code, reply.reason_phrase, kind, reply.content)

    async def _ask_permission(self, session_id: str, ask: dict) -> tuple[int, object]:
        """The session MCP server's permission ask: the daemon's own
        permission route, the gate the host's MCP tool asks."""
        path = f"/api/worker-sessions/{session_id}/permission"
        # Inside an /mcp call's own bound: shorter, so the tool denies first.
        reply = await self._call(
            self._app, "POST", path, session_id, json=ask, timeout=CALL_TIMEOUT * 0.8
        )
        return reply.status_code, reply.json()

    async def _call(
        self, asgi, method: str, path: str, session_id: str, *, timeout: float | None = None, **kw
    ) -> httpx.Response:
        """`method path` in-process on `asgi` as `session_id`, with the
        daemon's own token: the one way a worker's callback reaches Kraft.
        `TimeoutError` after `timeout` (`CALL_TIMEOUT`)."""
        headers = {
            **kw.pop("headers", {}),
            "Authorization": f"Bearer {self._app.state.mcp_token}",
            "X-Kraft-Session-Id": session_id,
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi), base_url="http://kraft"
        ) as client:
            return await asyncio.wait_for(
                client.request(method, path, headers=headers, **kw), timeout or CALL_TIMEOUT
            )

    def _thread_owner(self, tid: str) -> str | None:
        row = self._app.state.db.read(lambda c: store.thread_row(c, tid))
        return row["work_item_id"] if row is not None else None

    async def _refuse_route(self, writer, session: EgressSession, route: str, reason: str):
        await self._refuse(writer, session, "kraft", 80, reason, route=route[:MAX_ROUTE])

    async def _refuse(
        self,
        writer,
        session: EgressSession,
        host: str,
        port: int,
        reason: str,
        *,
        route: str | None = None,
    ):
        """403, and one event per host -- per route, for the worker API --
        up to the session's cap."""
        await self.record(session, host, port, reason, route=route)
        await _answer(writer, 403, "Forbidden", reason)

    async def record(
        self, session: EgressSession, host: str, port: int, reason: str, *, route=None
    ) -> None:
        """`_refuse`'s event, for a refusal with no HTTP left to answer in."""
        host = _norm(host)
        key = route or host
        if key in session.refused or session.suppressed:
            pass
        elif len(session.refused) >= MAX_REFUSAL_EVENTS:
            session.suppressed = True
            await session.record_refusal(
                {
                    "session_id": session.session_id,
                    "phase": session.lists.phase,
                    "suppressed": True,
                    "reason": f"over {MAX_REFUSAL_EVENTS} hosts refused; "
                    "later refusals are not recorded",
                }
            )
        else:
            session.refused.add(key)
            await session.record_refusal(
                {
                    "session_id": session.session_id,
                    "host": host,
                    "port": port,
                    **({"route": route} if route else {}),
                    "phase": session.lists.phase,
                    "reason": reason,
                }
            )


# The worker API's verbs, one per `kraft` shim verb (spec §5): its form
# fields in, the API call it is out -- `(method, path, JSON body)`,
# or why the form is a 400. An `id` is the session's own item unless the
# form names one, which the allowlist then has to pass.
_Call = tuple[str, str, dict | None]


def _item(form: dict, scope: callback.SessionScope) -> str:
    return f"/api/work-items/{form.get('id') or scope.work_item_id}"


def _progress(form, scope) -> _Call | str:
    if "task" not in form:
        return "progress needs a task"
    return "POST", f"{_item(form, scope)}/progress", {"task": form["task"]}


def _retry(form, scope) -> _Call:
    steer = form.get("steer", "").strip()
    return "POST", f"{_item(form, scope)}/retry", {"steer": steer} if steer else {}


def _reply(form, scope) -> _Call | str:
    if not form.get("thread") or "body" not in form:
        return "reply needs a thread and a body"
    payload = {"body": form["body"], **({"claim": form["claim"]} if form.get("claim") else {})}
    return "POST", f"/api/threads/{form['thread']}/replies", payload


def _show(form, scope) -> _Call:
    return "GET", _item(form, scope), None


def _threads(form, scope) -> _Call:
    return "GET", f"{_item(form, scope)}/threads", None


def _permission_hook(form, scope) -> _Call | str:
    if not form.get("harness") or "stdin" not in form:
        return "permission-hook needs a harness and its stdin"
    path = f"/api/worker-sessions/{scope.session_id}/permission-hook"
    return "POST", path, {"harness": form["harness"], "stdin": form["stdin"]}


_VERBS: dict[str, Callable[[dict, callback.SessionScope], _Call | str]] = {
    "progress": _progress,
    "retry": _retry,
    "reply": _reply,
    "show": _show,
    "threads": _threads,
    "permission-hook": _permission_hook,
}


#: The request headers the MCP streamable HTTP transport reads; no other
#: header a worker sends reaches the session MCP server.
_MCP_HEADERS = ("content-type", "accept", "mcp-protocol-version", "mcp-session-id")


async def _read_body(reader, writer, headers: list[str], rest: bytes) -> bytes | None:
    """A worker API request's body, or None once a 400 or 413 has answered."""
    length = _content_length(headers)
    if isinstance(length, str):
        await _answer(writer, 400, "Bad Request", length)
        return None
    if length > MAX_WORKER_BODY:
        await _answer(writer, 413, "Content Too Large", "the request is too large")
        return None
    body = rest[:length]
    return body + await asyncio.wait_for(reader.readexactly(length - len(body)), HEAD_TIMEOUT)


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
    if _CONTROL.search(line):
        return "a control character in the request line"
    if not all(_HEADER_LINE.fullmatch(h) for h in headers):
        return "a header line that is not `name: value`, or holds a control character"
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


def _content_length(headers: list[str]) -> int | str:
    """The request body's length for a request forwarded upstream, or why it
    is a 400. A chunked body is refused: its end is where the upstream says,
    so one request cannot be forwarded and nothing after it."""
    if any(_name(h) == "transfer-encoding" for h in headers):
        return "Transfer-Encoding is not forwarded to an upstream proxy; send Content-Length"
    values = {h.split(":", 1)[1].strip() for h in headers if _name(h) == "content-length"}
    if not values:
        return 0
    value = values.pop()
    if values or not (value.isascii() and value.isdigit()):
        return "Content-Length must be one number"
    return int(value)


def _name(header: str) -> str:
    return header.split(":", 1)[0].strip().lower()


async def _answer(writer: asyncio.StreamWriter, code: int, phrase: str, reason: str) -> None:
    await _respond(writer, code, phrase, "text/plain", f"kraft: {reason}\n".encode())


async def _respond(writer, code: int, phrase: str, content_type: str, body: bytes) -> None:
    writer.write(
        f"HTTP/1.1 {code} {phrase}\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()


async def _copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter, remaining: int) -> None:
    """Copy exactly `remaining` bytes, then stop reading `src`."""
    while remaining > 0:
        data = await src.read(min(remaining, 65536))
        if not data:
            return
        dst.write(data)
        await dst.drain()
        remaining -= len(data)


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
