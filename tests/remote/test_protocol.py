"""The handshake and keep-alive, over an in-memory connection."""

from __future__ import annotations

import asyncio

import pytest
from support.remote import memory_pair

from kraft.remote import protocol


async def _server(t, *, identify=lambda token: "gpu" if token == "good" else None, **kw):
    return await protocol.server_handshake(t, kraft_version="2.3.0", identify=identify, **kw)


async def test_a_dial_in_machine_with_a_valid_token_is_welcomed_by_name():
    server, machine = memory_pair()
    peer, welcome = await asyncio.gather(
        _server(server),
        protocol.machine_handshake(machine, kraft_version="2.3.1", token="good", host="box"),
    )
    assert (peer.name, peer.kraft, peer.protocol, peer.host) == ("gpu", "2.3.1", 1, "box")
    assert welcome["name"] == "gpu" and welcome["kraft"] == "2.3.0"


async def test_an_ssh_machine_is_named_by_the_server_and_sends_no_token():
    server, machine = memory_pair()
    peer, _ = await asyncio.gather(
        _server(server, expected_name="ssh-box"),
        protocol.machine_handshake(machine, kraft_version="2.3.0"),
    )
    assert peer.name == "ssh-box"


@pytest.mark.parametrize(
    ("hello", "reason", "name"),
    [
        ({"token": "bad"}, "invalid token", None),
        ({"token": "good", "protocol": 99}, "server speaks 1", "gpu"),
        ({"token": "good", "protocol": 99}, "machine speaks 99", "gpu"),
    ],
    ids=["bad-token", "newer-machine-names-server-version", "newer-machine-names-its-own"],
)
async def test_a_refused_hello_says_why_to_both_sides(hello, reason, name):
    server, machine = memory_pair()
    frame = {"t": "hello", "protocol": 1, "kraft": "9.9.9", **hello}

    async def client():
        await machine.send(frame)
        return await machine.recv()

    with pytest.raises(protocol.Refused) as refused:
        await asyncio.gather(_server(server), client())
    assert reason in str(refused.value) and refused.value.name == name
    # the machine saw the same refusal, as a frame
    server2, machine2 = memory_pair()
    with pytest.raises(protocol.Refused, match=reason):
        await asyncio.gather(
            protocol.machine_handshake(
                machine2,
                kraft_version="9.9.9",
                token=hello["token"],
                protocol_version=hello.get("protocol", 1),
            ),
            _server(server2),
        )


async def test_a_silent_peer_times_out_the_handshake():
    server, _machine = memory_pair()
    with pytest.raises(protocol.Closed):
        await _server(server, timeout=0.05)


async def test_pump_answers_pings_and_hands_other_frames_on():
    a, b = memory_pair()
    seen = []

    async def on_frame(frame):
        seen.append(frame)

    task = asyncio.create_task(protocol.pump(a, on_frame=on_frame, ping_s=60, dead_s=60))
    await b.send({"t": "ping"})
    assert (await b.recv()) == {"t": "pong"}
    await b.send({"t": "job", "n": 1})
    await asyncio.sleep(0)
    await b.close()
    with pytest.raises(protocol.Closed):
        await task
    assert seen == [{"t": "job", "n": 1}]


async def test_pump_gives_up_on_a_peer_that_says_nothing():
    a, _b = memory_pair()
    with pytest.raises(protocol.Closed, match="no frame"):
        await protocol.pump(a, on_frame=lambda f: asyncio.sleep(0), ping_s=60, dead_s=0.05)
