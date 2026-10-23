"""The hub's view of each machine: online, offline and why."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from support.remote import memory_pair
from support.stub_ssh import stub_ssh

from kraft.remote import protocol, registry
from kraft.remote.hub import Hub, Verdict

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


@pytest.mark.parametrize(("setting", "seconds"), [("30m", 1800.0), ("never", None)])
async def test_the_welcome_tells_a_machine_its_own_give_up_after(setting, seconds):
    hub = Hub("2.3.0")
    hub.apply(_file(home2={"token_sha256": registry.hash_token("kwt_b"), "give_up_after": setting}))
    machine, welcome, accepted = await _dial(hub, "kwt_b")
    assert welcome["give_up_after_s"] == seconds
    await machine.close()
    await accepted


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
    assert (
        f"server speaks {protocol.PROTOCOL_VERSION}" in row["reason"]
        and "machine speaks 99" in row["reason"]
    )


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


async def test_a_chatty_ssh_machine_stays_online_as_stderr_is_drained(tmp_path, monkeypatch):
    """A machine that writes more to stderr after the welcome than the stream
    reader buffers (twice FRAME_LIMIT) would block on it, and stop answering
    pings, if nobody read it."""
    bin_dir = stub_ssh(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    starts = tmp_path / "starts"
    machine = (
        "import json, sys\n"
        f"open({str(starts)!r}, 'a').write('.')\n"
        "send = lambda f: (sys.stdout.write(json.dumps(f) + '\\n'), sys.stdout.flush())\n"
        f"send({{'t': 'hello', 'protocol': {protocol.PROTOCOL_VERSION}, 'kraft': '2.3.0'}})\n"
        "sys.stdin.readline()\n"
        "sys.stderr.write('x' * 10_000_000)\n"
        "sys.stderr.flush()\n"
        "for line in sys.stdin:\n"
        "    if json.loads(line)['t'] == 'ping':\n"
        "        send({'t': 'pong'})\n"
    )
    hub = Hub("2.3.0", remote_command=(sys.executable, "-c", machine), ping_s=0.2, dead_s=1.5)
    hub.apply(registry.WorkersFile.model_validate({"workers": {"boxed": {"ssh": "boxed"}}}))
    assert (await hub.probe("boxed", timeout=20))["connected"]
    await asyncio.sleep(3)  # past dead_s: only answered pings keep it online
    assert _row(hub, "boxed")["online"]
    assert starts.read_text() == "."  # one connection, never redialled
    await hub.close()


def _row_names(hub):
    return [r["name"] for r in hub.snapshot()]


async def test_ssh_that_cannot_run_reports_why_instead_of_hanging(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # no ssh at all
    hub = Hub("2.3.0")
    hub.apply(registry.WorkersFile.model_validate({"workers": {"boxed": {"ssh": "boxed"}}}))
    outcome = await hub.probe("boxed", timeout=5)
    assert outcome["connected"] is False and "could not run ssh" in outcome["detail"]
    await hub.close()


def _repos(*names):
    from support.harness import entry_of

    return [entry_of({"path": f"/r/{n}", "name": n}) for n in names]


async def test_a_named_machine_offline_or_full_makes_the_step_wait_and_never_run_elsewhere():
    hub = Hub("2.3.0")
    hub._file = _file()  # named in the file, but apply has not made its entry: a wait
    not_yet = "waiting for home (offline: not connected yet)"
    assert hub.decide("home", _repos("kraft")).wait == not_yet
    hub.apply(_file())
    assert hub.decide("home", _repos("kraft")) == Verdict(
        wait="waiting for home (offline: never connected)"
    )
    machine, _w, accepted = await _dial(hub)
    first = await hub.claim("home", _repos("kraft"), "w/1", poll_s=0.01)
    await hub.claim("home", _repos("kraft"), "w/2", poll_s=0.01)  # capacity is 2
    assert _row(hub)["slots_used"] == 2
    assert hub.decide("home", _repos("kraft")).wait == "waiting for home"  # full, not offline
    hub.release(first)
    assert hub.decide("home", _repos("kraft")).machine == "home"
    await machine.close()
    await accepted


async def test_any_takes_the_first_connected_allowed_machine_with_a_free_slot_never_the_laptop():
    hub = Hub("2.3.0")
    hub.apply(
        _file(spare={"token_sha256": registry.hash_token("kwt_spare")})
    )  # `spare` takes any repo
    nobody = "waiting for any machine allowed for kraft"
    assert hub.decide("any", _repos("kraft")).wait == nobody  # nobody is connected
    machine, _w, accepted = await _dial(hub)  # home is; spare is not
    assert hub.decide("any", _repos("kraft")).machine == "home"
    infra = hub.decide("any", _repos("infra")).wait  # home may not take infra
    assert infra == "waiting for any machine allowed for infra"
    only_home = Hub("2.3.0")
    only_home.apply(_file())
    assert "no machine" in only_home.decide("any", _repos("infra")).refusal
    await machine.close()
    await accepted


def test_the_server_id_is_made_once_and_kept(tmp_path):
    from kraft.remote.hub import server_id

    first = server_id(tmp_path)
    assert first and server_id(tmp_path) == first


async def test_a_pinned_machine_removed_from_the_file_is_a_refusal_naming_it():
    hub = Hub("2.3.0")
    hub.apply(_file())
    assert "gone" in hub.decide("any", _repos("kraft"), pinned="gone").refusal


async def test_a_waiting_claim_is_granted_when_a_slot_frees_and_is_cancellable():
    hub = Hub("2.3.0")
    hub.apply(_file())
    machine, _w, accepted = await _dial(hub)
    held = [await hub.claim("home", _repos("kraft"), f"w/{i}", poll_s=0.01) for i in range(2)]
    seen = []
    waiter = asyncio.create_task(
        hub.claim("home", _repos("kraft"), "w/3", on_wait=seen.append, poll_s=0.01)
    )
    await asyncio.sleep(0.05)
    assert not waiter.done() and seen == ["waiting for home"]  # said once
    hub.release(held[0])
    assert (await asyncio.wait_for(waiter, 1)).machine == "home"
    cancelled = asyncio.create_task(hub.claim("home", _repos("kraft"), "w/4", poll_s=0.01))
    await asyncio.sleep(0.02)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert _row(hub)["slots_used"] == 2
    await machine.close()
    await accepted
