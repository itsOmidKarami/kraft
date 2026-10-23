"""What a Kraft server and a machine say to each other (remote workers).

One JSON object per frame, `t` names it. Transport-neutral: a `Transport` is
a WebSocket (dial-in) or an ssh session's stdio (`kraft.remote.transport`).

Protocol 2. `id` is the job key `"<work item id>/<session id>"`. Frames:

- `hello`, `welcome`, `refused`: the handshake. `welcome` also names this
  install as `server` (`run/server.id`) and carries `give_up_after_s`: how long
  the machine keeps a job running without a connection (seconds, `null` for never).
- `ping`, `pong`: keep-alive.
- `have`, server to machine: `{rid}`; the machine answers `{rid, item, commits}`,
  up to 500 commits its copy of the item has.
- `job`, server to machine: `{id, spec, blobs}`. `spec` is `JobSpec.to_frame()`;
  `blobs` names the `code` and `files` blobs that follow.
- `blob`, both ways: `{id, name, seq, last, data}` with `data` in base64. `code`
  (a git bundle) and `files` (a tar.gz) go to the machine; `commits` and `out`
  come back.
- `started`, machine to server: `{id}`; setup is done and the command launched.
- `rejected`, machine to server: `{id, outcome, reason}`; the command never
  started. `outcome` is `config_error` or `failed`.
- `log`, machine to server: `{id, offset, data}`, bytes of `session.log` from
  `offset`.
- `sync`, server to machine on every connect: `{jobs: [{id, offset}]}`. The
  machine resends log from each offset, and a finished job's `finished` and blobs.
- `signal`, server to machine: `{id, sig}`, `INT` or `KILL`.
- `finished`, machine to server, after its blobs: `{id, code, tip?, pushed?, capped?}`; `capped` is
  the `caps.Hit` (a dict) of the time cap or idle limit that stopped the command on the machine.
- `delivered`, server to machine: `{id}`; the machine may drop the job's state.
- `api`, machine to server: `{id, rid, method, path, query, headers, body}`;
  `api_result`, back: `{rid, status, headers, body}`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

#: Bumped when a frame changes meaning. Equal or refused: there is no range.
PROTOCOL_VERSION = 2
HANDSHAKE_S = 10.0
PING_S = 20.0
DEAD_S = 60.0


class Closed(Exception):
    """The connection ended, or said something that is not a frame."""


class Refused(Exception):
    """The other side would not take this connection; `str()` is the reason."""

    def __init__(
        self,
        reason: str,
        *,
        name: str | None = None,
        server_protocol: int | None = None,
        machine_protocol: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.name, self.server_protocol, self.machine_protocol = (
            name,
            server_protocol,
            machine_protocol,
        )


class Transport(Protocol):
    async def send(self, frame: dict) -> None: ...
    async def recv(self) -> dict: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class Peer:
    name: str
    kraft: str
    protocol: int
    host: str | None


async def server_handshake(
    t: Transport,
    *,
    kraft_version: str,
    identify: Callable[[str], str | None],
    expected_name: str | None = None,
    server_id: str = "",
    give_up_after_s: float | None | Callable[[str], float | None] = None,
    timeout: float = HANDSHAKE_S,
) -> Peer:
    """Read the machine's `hello`, answer `welcome` or `refused`. `give_up_after_s`
    goes into the `welcome`: a number of seconds, or a function of the machine's name
    for a dial-in connection, whose name is only known once its token is.

    `expected_name` is an ssh machine (the server chose whom it dialled; no
    token is sent). Otherwise `identify(token)` maps a dial-in token to a
    machine name or None. The token is judged before the version, so a
    version refusal can name the machine it is about."""
    try:
        hello = await asyncio.wait_for(t.recv(), timeout)
    except TimeoutError as exc:
        raise Closed("no hello") from exc
    name = expected_name
    if name is None:
        token = hello.get("token")
        is_token = hello.get("t") == "hello" and isinstance(token, str) and token
        name = identify(token) if is_token else None
        if name is None:
            await _refuse(t, "invalid token")
    if hello.get("t") != "hello":
        await _refuse(t, "expected hello", name=name)
    machine = hello.get("protocol")
    machine_kraft = str(hello.get("kraft") or "unknown")
    if machine != PROTOCOL_VERSION:
        await _refuse(
            t,
            f"incompatible protocol: server speaks {PROTOCOL_VERSION} (kraft {kraft_version}), "
            f"machine speaks {machine} (kraft {machine_kraft})",
            name=name,
            server_protocol=PROTOCOL_VERSION,
            machine_protocol=machine if isinstance(machine, int) else None,
        )
    await t.send(
        {
            "t": "welcome",
            "protocol": PROTOCOL_VERSION,
            "kraft": kraft_version,
            "name": name,
            "server": server_id,
            "give_up_after_s": give_up_after_s(name)
            if callable(give_up_after_s)
            else give_up_after_s,
        }
    )
    return Peer(name, machine_kraft, machine, hello.get("host"))


async def _refuse(t: Transport, reason: str, **extra) -> None:
    frame = {"t": "refused", "reason": reason}
    frame.update({k: v for k, v in extra.items() if k != "name" and v is not None})
    with contextlib.suppress(Closed):
        await t.send(frame)
    raise Refused(reason, **extra)


async def machine_handshake(
    t: Transport,
    *,
    kraft_version: str,
    token: str | None = None,
    host: str | None = None,
    protocol_version: int = PROTOCOL_VERSION,
    timeout: float = HANDSHAKE_S,
) -> dict:
    """Send `hello`, return the server's `welcome`; `Refused` if it says no."""
    hello = {"t": "hello", "protocol": protocol_version, "kraft": kraft_version}
    if token is not None:
        hello["token"] = token
    if host:
        hello["host"] = host
    await t.send(hello)
    try:
        answer = await asyncio.wait_for(t.recv(), timeout)
    except TimeoutError as exc:
        raise Closed("no welcome") from exc
    if answer.get("t") == "refused":
        raise Refused(
            str(answer.get("reason") or "refused"),
            server_protocol=answer.get("server_protocol"),
            machine_protocol=answer.get("machine_protocol"),
        )
    if answer.get("t") != "welcome":
        raise Closed(f"expected welcome, got {answer.get('t')!r}")
    return answer


async def pump(
    t: Transport,
    *,
    on_frame: Callable[[dict], Awaitable[None]],
    ping_s: float = PING_S,
    dead_s: float = DEAD_S,
    on_seen: Callable[[], None] = lambda: None,
) -> None:
    """Read frames until the connection ends (raises `Closed`): answer pings,
    ping the other side every `ping_s`, hand every other frame to `on_frame`,
    and give up on a peer that has sent nothing for `dead_s`."""

    async def pinger() -> None:
        while True:
            await asyncio.sleep(ping_s)
            await t.send({"t": "ping"})

    ping = asyncio.create_task(pinger())
    try:
        while True:
            try:
                frame = await asyncio.wait_for(t.recv(), dead_s)
            except TimeoutError as exc:
                raise Closed(f"no frame in {dead_s:g}s") from exc
            on_seen()
            kind = frame.get("t")
            if kind == "ping":
                await t.send({"t": "pong"})
            elif kind != "pong":
                await on_frame(frame)
    finally:
        ping.cancel()
        with contextlib.suppress(asyncio.CancelledError, Closed):
            await ping
