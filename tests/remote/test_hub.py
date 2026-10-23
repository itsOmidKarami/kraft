"""The hub's view of each machine: online, offline and why."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from support.remote import memory_pair
from support.stub_ssh import stub_ssh

from kraft.remote import protocol, registry
from kraft.remote.hub import Hub

TOKEN = "kwt_secret"


def _file(**extra):
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                "home": {
                    "token_sha256": registry.hash_token(TOKEN),
                    "capacity": 2,
                    "repos": ["kraft"],
                },
                **extra,
            }
        }
    )


async def _dial(hub, token=TOKEN, *, version=protocol.PROTOCOL_VERSION):
    """A machine connecting; returns its transport and what the handshake said."""
    server, machine = memory_pair()
    accepted = asyncio.create_task(hub.accept(server))
    try:
        welcome = await protocol.machine_handshake(
            machine, kraft_version="2.3.0", token=token, host="box", protocol_version=version
        )
    except protocol.Refused as refused:
        welcome = refused
    return machine, welcome, accepted


def _row(hub, name="home"):
    return next(r for r in hub.snapshot() if r["name"] == name)


async def test_an_unconnected_machine_lists_offline_and_never_connected():
    hub = Hub("2.3.0")
    hub.apply(_file())
    assert _row(hub) | {"last_seen": None} == {
        "name": "home",
        "mode": "dial-in",
        "online": False,
        "reason": "never connected",
        "slots_used": 0,
        "capacity": 2,
        "repos": ["kraft"],
        "last_seen": None,
        "kraft_version": None,
        "give_up_after": "2h",
    }


async def test_a_dial_in_machine_with_its_token_is_online_with_its_version():
    hub = Hub("2.3.0")
    hub.apply(_file())
    machine, welcome, accepted = await _dial(hub)
    assert welcome["name"] == "home"
    row = _row(hub)
    assert row["online"] and row["kraft_version"] == "2.3.0" and row["last_seen"]
    await machine.close()
    await accepted
    row = _row(hub)
    assert not row["online"] and row["reason"].startswith("disconnected")


async def test_a_wrong_token_connects_nothing():
    hub = Hub("2.3.0")
    hub.apply(_file())
    _, refused, accepted = await _dial(hub, token="kwt_wrong")
    assert isinstance(refused, protocol.Refused) and "invalid token" in str(refused)
    with pytest.raises(protocol.Refused):
        await accepted
    assert not _row(hub)["online"]


async def test_a_version_mismatch_shows_offline_with_both_versions():
    hub = Hub("2.3.0")
    hub.apply(_file())
    _, refused, accepted = await _dial(hub, version=99)
    with pytest.raises(protocol.Refused):
        await accepted
    row = _row(hub)
    assert not row["online"]
    assert row["reason"].startswith("refused: incompatible protocol")
    assert "server speaks 1" in row["reason"] and "machine speaks 99" in row["reason"]


async def test_a_second_connection_replaces_the_first():
    hub = Hub("2.3.0")
    hub.apply(_file())
    first, _, first_task = await _dial(hub)
    second, _, _ = await _dial(hub)
    await asyncio.wait_for(first_task, 2)  # the old link was closed by the server
    assert _row(hub)["online"]
    await second.close()


async def test_removing_a_machine_or_changing_its_token_drops_its_live_connection():
    hub = Hub("2.3.0")
    hub.apply(_file())
    machine, _, accepted = await _dial(hub)
    hub.apply(registry.WorkersFile())  # removed
    await asyncio.wait_for(accepted, 2)
    assert hub.snapshot() == []
    with pytest.raises(protocol.Closed):
        await machine.recv()


async def test_a_new_token_hash_drops_the_old_connection():
    hub = Hub("2.3.0")
    hub.apply(_file())
    _machine, _, accepted = await _dial(hub)
    changed = registry.WorkersFile.model_validate(
        {"workers": {"home": {"token_sha256": registry.hash_token("kwt_new")}}}
    )
    hub.apply(changed)
    await asyncio.wait_for(accepted, 2)
    assert not _row(hub)["online"]


async def test_last_seen_moves_with_every_frame(monkeypatch):
    now = [1000.0]
    hub = Hub("2.3.0", clock=lambda: now[0])
    hub.apply(_file())
    machine, _, _ = await _dial(hub)
    before = _row(hub)["last_seen"]
    now[0] += 60
    await machine.send({"t": "pong"})
    await asyncio.sleep(0.01)
    assert _row(hub)["last_seen"] != before
    await machine.close()


# ── ssh mode ──


async def test_an_ssh_machine_is_dialled_by_the_server_and_redialled_after_it_drops(
    tmp_path, monkeypatch
):
    bin_dir = stub_ssh(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    hub = Hub(
        "2.3.0",
        remote_command=(sys.executable, "-m", "kraft", "admin", "worker", "serve", "--stdio"),
    )
    hub.apply(registry.WorkersFile.model_validate({"workers": {"boxed": {"ssh": "boxed"}}}))
    outcome = await hub.probe("boxed", timeout=20)
    assert outcome == {"connected": True, "detail": "connected"}
    assert _row(hub, "boxed")["online"]
    await hub.close()
    assert _row_names(hub) == []


def _row_names(hub):
    return [r["name"] for r in hub.snapshot()]


async def test_ssh_that_cannot_run_reports_why_instead_of_hanging(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no ssh at all
    hub = Hub("2.3.0")
    hub.apply(registry.WorkersFile.model_validate({"workers": {"boxed": {"ssh": "boxed"}}}))
    outcome = await hub.probe("boxed", timeout=5)
    assert outcome["connected"] is False and "could not run ssh" in outcome["detail"]
    await hub.close()
