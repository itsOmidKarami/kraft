"""Newline-delimited JSON over a byte stream (ssh), and JSON text over a
WebSocket, both as `protocol.Transport`."""

from __future__ import annotations

import asyncio
import json

import pytest

from kraft.remote import protocol, transport


async def _stream_pair():
    """A real connected socket pair as two StreamTransports."""
    ready = asyncio.get_running_loop().create_future()

    async def accepted(reader, writer):
        ready.set_result((reader, writer))

    server = await asyncio.start_server(accepted, "127.0.0.1", 0, limit=transport.FRAME_LIMIT)
    port = server.sockets[0].getsockname()[1]
    cr, cw = await asyncio.open_connection("127.0.0.1", port, limit=transport.FRAME_LIMIT)
    sr, sw = await ready
    server.close()
    return transport.StreamTransport(cr, cw), transport.StreamTransport(sr, sw)


async def test_a_stream_carries_frames_both_ways_and_ends_with_closed():
    a, b = await _stream_pair()
    await a.send({"t": "ping", "n": 1})
    assert await b.recv() == {"t": "ping", "n": 1}
    await b.send({"t": "pong"})
    assert await a.recv() == {"t": "pong"}
    await a.close()
    with pytest.raises(protocol.Closed):
        await b.recv()


@pytest.mark.parametrize(
    "line",
    [b"not json\n", b"[1, 2]\n", b'"text"\n'],
    ids=["not-json", "array", "string"],
)
async def test_a_stream_line_that_is_not_a_frame_is_closed(line):
    a, b = await _stream_pair()
    a._writer.write(line)
    with pytest.raises(protocol.Closed, match="not a frame"):
        await b.recv()


async def test_an_oversized_stream_frame_is_closed_not_buffered():
    a, b = await _stream_pair()
    a._writer.write(b'{"t":"x","pad":"' + b"a" * (transport.FRAME_LIMIT + 10) + b'"}\n')
    with pytest.raises(protocol.Closed, match="too large"):
        await b.recv()


async def test_a_websocket_adapter_sends_json_text_and_wraps_every_failure_as_closed():
    sent, queue = [], asyncio.Queue()

    async def send(text):
        sent.append(text)

    async def recv():
        return await queue.get()

    async def close():
        sent.append("closed")

    ws = transport.WebSocketTransport(send, recv, close)
    await ws.send({"t": "ping"})
    assert json.loads(sent[0]) == {"t": "ping"}
    queue.put_nowait('{"t":"pong"}')
    assert await ws.recv() == {"t": "pong"}
    queue.put_nowait(b"binary")
    with pytest.raises(protocol.Closed, match="not a frame"):
        await ws.recv()

    async def broken():
        raise RuntimeError("socket gone")

    with pytest.raises(protocol.Closed):
        await transport.WebSocketTransport(send, broken, close).recv()
    await ws.close()
    assert sent[-1] == "closed"
