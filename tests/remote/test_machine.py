# tests/remote/test_machine.py
"""The machine's two ways in, against the real hub."""

from __future__ import annotations

import asyncio
import sys

import pytest
from websockets.asyncio.server import serve

from kraft.remote import machine, protocol, registry
from kraft.remote.hub import Hub
from kraft.remote.transport import FRAME_LIMIT, StreamTransport, WebSocketTransport

TOKEN = "kwt_machine"


@pytest.mark.parametrize(
    ("server", "url"),
    [
        ("http://h:8765", "ws://h:8765/api/workers/connect"),
        ("https://kraft.example/", "wss://kraft.example/api/workers/connect"),
        ("https://x/kraft", "wss://x/kraft/api/workers/connect"),
    ],
    ids=["http", "https-trailing-slash", "path-prefix"],
)
def test_the_server_url_becomes_the_websocket_url(server, url):
    assert machine.dial_url(server) == url


def test_a_url_that_is_not_http_is_refused():
    with pytest.raises(ValueError, match="http"):
        machine.dial_url("ftp://h")


async def _hub_over_websocket():
    hub = Hub("2.3.0", ping_s=0.05, dead_s=2)
    hub.apply(
        registry.WorkersFile.model_validate(
            {"workers": {"home": {"token_sha256": registry.hash_token(TOKEN)}}}
        )
    )

    async def handler(ws):
        try:
            await hub.accept(WebSocketTransport(ws.send, ws.recv, ws.close))
        except protocol.Refused:
            pass

    server = await serve(handler, "127.0.0.1", 0)
    return hub, server, f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"


async def test_a_machine_dials_in_stays_online_and_redials_after_the_server_drops_it():
    hub, server, url = await _hub_over_websocket()
    stop, lines = asyncio.Event(), []
    task = asyncio.create_task(
        machine.dial_in(
            url,
            TOKEN,
            kraft_version="2.3.0",
            stop=stop,
            log=lines.append,
            first_delay=0.01,
            max_delay=0.05,
        )
    )
    await _until(lambda: hub.snapshot()[0]["online"])
    await hub._entries["home"].transport.close()  # the server drops it
    await _until(lambda: not hub.snapshot()[0]["online"])
    await _until(lambda: hub.snapshot()[0]["online"])  # the machine came back by itself
    stop.set()
    await task
    assert any("connected as home" in line for line in lines)
    server.close()
    await hub.close()


async def test_a_wrong_token_is_logged_and_retried_not_fatal():
    hub, server, url = await _hub_over_websocket()
    stop, lines = asyncio.Event(), []
    task = asyncio.create_task(
        machine.dial_in(
            url,
            "kwt_wrong",
            kraft_version="2.3.0",
            stop=stop,
            log=lines.append,
            first_delay=0.01,
            max_delay=0.02,
        )
    )
    await _until(lambda: sum("refused: invalid token" in line for line in lines) >= 2)
    stop.set()
    await task
    server.close()
    await hub.close()


async def test_an_unreachable_server_is_logged_and_retried():
    stop, lines = asyncio.Event(), []
    task = asyncio.create_task(
        machine.dial_in(
            "http://127.0.0.1:1",
            TOKEN,
            kraft_version="2.3.0",
            stop=stop,
            log=lines.append,
            first_delay=0.01,
            max_delay=0.02,
        )
    )
    await _until(lambda: sum("cannot connect" in line for line in lines) >= 2)
    stop.set()
    await task


@pytest.mark.skip(reason="needs task 8's `worker serve`")
async def test_stdio_mode_handshakes_over_a_pipe_and_exits_when_ssh_closes_it():
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "kraft",
        "admin",
        "worker",
        "serve",
        "--stdio",  # wired in Task 8
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=FRAME_LIMIT,
    )
    t = StreamTransport(proc.stdout, proc.stdin)
    peer = await protocol.server_handshake(
        t, kraft_version="2.3.0", identify=lambda _: None, expected_name="boxed"
    )
    assert peer.name == "boxed" and peer.protocol == protocol.PROTOCOL_VERSION
    await t.close()
    assert await asyncio.wait_for(proc.wait(), 10) == 0


async def _until(cond, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)
