"""The handshake and keep-alive, over an in-memory connection."""

from __future__ import annotations

import asyncio
import re

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
    assert (peer.name, peer.kraft, peer.protocol, peer.host) == (
        "gpu",
        "2.3.1",
        protocol.PROTOCOL_VERSION,
        "box",
    )
    assert welcome["name"] == "gpu" and welcome["kraft"] == "2.3.0"


@pytest.mark.parametrize("give_up", [7200.0, None], ids=["two-hours", "never"])
async def test_the_welcome_carries_how_long_the_machine_waits_for_the_server(give_up):
    server, machine = memory_pair()
    _, welcome = await asyncio.gather(
        _server(server, give_up_after_s=give_up),
        protocol.machine_handshake(machine, kraft_version="2.3.0", token="good"),
    )
    assert welcome["give_up_after_s"] == give_up


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
        ({"token": "good", "protocol": 99}, f"server speaks {protocol.PROTOCOL_VERSION}", "gpu"),
        ({"token": "good", "protocol": 99}, "machine speaks 99", "gpu"),
    ],
    ids=["bad-token", "newer-machine-names-server-version", "newer-machine-names-its-own"],
)
async def test_a_refused_hello_says_why_to_both_sides(hello, reason, name):
    server, machine = memory_pair()
    frame = {"t": "hello", "protocol": protocol.PROTOCOL_VERSION, "kraft": "2.9.9", **hello}

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
                kraft_version="2.9.9",
                token=hello["token"],
                protocol_version=hello.get("protocol", protocol.PROTOCOL_VERSION),
            ),
            _server(server2),
        )


@pytest.mark.parametrize(
    ("machine_kraft", "reason"),
    [
        (
            "3.0.0",
            "update kraft on this machine to 2.3.0: it runs 3.0.0, another major version",
        ),
        (
            "2.9.9",
            "incompatible protocol: server speaks 2 (kraft 2.3.0), machine speaks 99 (kraft 2.9.9)",
        ),
    ],
    ids=["another-major", "same-major-other-protocol"],
)
async def test_protocol_refusal_text(machine_kraft, reason):
    server, machine = memory_pair()
    with pytest.raises(protocol.Refused, match=re.escape(reason)) as refused:
        await asyncio.gather(
            _server(server),
            protocol.machine_handshake(
                machine, kraft_version=machine_kraft, token="good", protocol_version=99
            ),
        )
    assert str(refused.value) == reason and refused.value.machine_protocol == 99


@pytest.mark.parametrize(
    ("server_kraft", "machine_kraft", "refusal"),
    [
        (
            "2.3.0",
            "1.9.0",
            "update kraft on this machine to 2.3.0: it runs 1.9.0, another major version",
        ),
        ("3.0.0", "2.3.1.dev15+gabc", None),
        ("2.3.1.dev15+gabc", "3.0.0", None),
        ("2.4.0", "2.3.1", None),
    ],
    ids=["another-major-same-protocol", "dev-machine", "dev-server", "same-major"],
)
async def test_another_major_is_turned_away_whatever_the_protocol(
    server_kraft, machine_kraft, refusal
):
    server, machine = memory_pair()
    handshakes = asyncio.gather(
        protocol.server_handshake(server, kraft_version=server_kraft, identify=lambda token: "gpu"),
        protocol.machine_handshake(machine, kraft_version=machine_kraft, token="good"),
    )
    if refusal is None:
        peer, welcome = await handshakes
        assert (peer.name, welcome["kraft"]) == ("gpu", server_kraft)
        return
    with pytest.raises(protocol.Refused) as refused:
        await handshakes
    assert str(refused.value) == refusal
    assert refused.value.machine_protocol == protocol.PROTOCOL_VERSION


@pytest.mark.parametrize(
    ("hello_caps", "expected"),
    [
        ([], frozenset()),
        (["capacity", "x"], frozenset({"capacity", "x"})),
        (None, frozenset()),
        ("capacity", frozenset()),
        (["ok", 3], frozenset()),
    ],
    ids=["none-sent", "caps-listed", "old-machine-no-caps", "caps-garbage", "caps-mixed"],
)
async def test_handshake_caps(hello_caps, expected):
    server, machine = memory_pair()
    hello = {"t": "hello", "protocol": protocol.PROTOCOL_VERSION, "kraft": "2.3.0", "token": "good"}
    if hello_caps is not None:
        hello["caps"] = hello_caps

    async def client():
        await machine.send(hello)
        return await machine.recv()

    peer, welcome = await asyncio.gather(_server(server), client())
    assert peer.caps == expected
    assert welcome["caps"] == sorted(protocol.CAPS)


async def test_the_machine_hello_lists_its_caps():
    server, machine = memory_pair()
    peer, _ = await asyncio.gather(
        _server(server),
        protocol.machine_handshake(machine, kraft_version="2.3.0", token="good", caps=["a", "b"]),
    )
    assert peer.caps == frozenset({"a", "b"})


@pytest.mark.parametrize(
    ("capacity", "reported"),
    [(None, None), (3, 3)],
    ids=["no-limit", "limit"],
)
@pytest.mark.parametrize("has_lfs", [True, False], ids=["lfs", "no-lfs"])
async def test_a_machines_own_limit_travels_in_its_hello_caps(capacity, reported, has_lfs):
    server, machine = memory_pair()
    peer, _ = await asyncio.gather(
        _server(server),
        protocol.machine_handshake(
            machine,
            kraft_version="2.3.0",
            token="good",
            caps=protocol.machine_caps(capacity, lfs=has_lfs),
        ),
    )
    assert protocol.RESERVE in peer.caps
    assert (protocol.LFS in peer.caps) is has_lfs
    assert protocol.reported_capacity(peer.caps) == reported


@pytest.mark.parametrize(
    "caps",
    [{"codex_hook"}, {"capacity=x"}, {"capacity=0"}, {"capacity=-2"}, {"capacity="}],
    ids=["absent", "word", "zero", "negative", "empty"],
)
def test_a_limit_that_is_not_reported_cleanly_reads_as_none(caps):
    assert protocol.reported_capacity(caps) is None


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


async def test_welcome_names_the_server_install():
    server, machine = memory_pair()
    task = asyncio.create_task(
        protocol.server_handshake(
            server, kraft_version="2.3.0", identify=lambda t: "gpu", server_id="abc123"
        )
    )
    welcome = await protocol.machine_handshake(machine, kraft_version="2.3.0", token="t")
    await task
    assert welcome["server"] == "abc123"
