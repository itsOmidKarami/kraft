"""The hub's view of each machine: online, offline and why."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from support.harness import entry_of, make_repo
from support.remote import Peer, attach, memory_pair
from support.stub_ssh import stub_ssh

from kraft.remote import protocol, registry
from kraft.remote.hub import FULL_RETRY_S, Hub, Verdict

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
        "capacity_source": "server",
        "machine_capacity": None,
        "configured_capacity": 2,
        "enabled": True,
        "ssh": None,
        "groups": [],
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
    assert hub.decide("home", _repos("kraft")).wait == "waiting for home (full)"
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
    assert not waiter.done() and seen == ["waiting for home (full)"]  # said once
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


async def test_offline_for_counts_from_the_drop_on_the_monotonic_clock():
    now = [500.0]
    hub = Hub("2.3.0", mono=lambda: now[0])
    hub.apply(_file())
    assert hub.offline_for("home") == 0  # the hub has known it, offline, since now
    machine, _, accepted = await _dial(hub)
    now[0] += 1000
    assert hub.offline_for("home") is None  # online
    await machine.close()
    await accepted
    now[0] += 7
    assert hub.offline_for("home") == 7


async def test_unreachable_since_names_the_moment_the_connection_ended_until_it_is_back():
    hub = Hub("2.3.0", clock=lambda: 1_700_000_000.0)
    hub.apply(_file())
    assert hub.unreachable_since("home") == "2023-11-14T22:13:20+00:00"
    machine, _, accepted = await _dial(hub)
    assert hub.unreachable_since("home") is None
    await machine.close()
    await accepted
    assert hub.unreachable_since("home") == "2023-11-14T22:13:20+00:00"
    assert hub.unreachable_since("nobody") is None


def _limits_file(capacity):
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                "gpu": {
                    "token_sha256": registry.hash_token(TOKEN),
                    **({} if capacity is None else {"capacity": capacity}),
                }
            }
        }
    )


async def _online(hub, *caps):
    await attach(hub, Peer(), "gpu", caps=frozenset(caps))


@pytest.mark.parametrize(
    ("server", "caps", "limit", "source"),
    [
        (None, (), 1, "default"),
        (3, (), 3, "server"),
        (None, ("capacity=2",), 2, "machine"),
        (3, ("capacity=2",), 2, "machine"),
        (2, ("capacity=5",), 2, "server"),
        (2, ("capacity=2",), 2, "server"),
    ],
    ids=["neither", "server", "machine", "machine-lower", "server-lower", "tie"],
)
async def test_the_limit_in_use_is_the_lower_of_the_servers_and_the_machines(
    server, caps, limit, source
):
    hub = Hub("2.3.0")
    hub.apply(_limits_file(server))
    await _online(hub, *caps)
    assert (hub.limit("gpu"), hub.limit_source("gpu")) == (limit, source)
    row = _row(hub, "gpu")
    assert (row["capacity"], row["capacity_source"]) == (limit, source)
    assert row["machine_capacity"] == protocol.reported_capacity(caps)
    claims = []
    for i in range(limit):
        claims.append(await hub.claim("gpu", [], f"w/{i}", poll_s=0.01))
    assert hub.decide("gpu", []).machine is None  # no slot past the limit


async def test_a_machine_reporting_a_new_limit_changes_the_one_in_use():
    hub = Hub("2.3.0")
    hub.apply(_limits_file(3))
    await _online(hub, "capacity=3")
    hub.set_machine_capacity("gpu", 1)
    assert hub.limit("gpu") == 1
    hub.set_machine_capacity("gpu", None)
    assert hub.limit("gpu") == 3


@pytest.mark.parametrize("ends", ["a report", "a step of its own ending", "the retry time"])
async def test_a_machine_that_turned_a_step_away_is_left_alone_until_something_changes(ends):
    now = [0.0]
    hub = Hub("2.3.0", mono=lambda: now[0])
    hub.apply(_limits_file(2))
    await _online(hub, "capacity=2")
    held = await hub.claim("gpu", [], "w/1", poll_s=0.01)
    hub.mark_full("gpu")
    assert hub.decide("gpu", []).wait == "waiting for gpu (full)" and not hub.free("gpu", [])
    {
        "a report": lambda: hub.set_machine_capacity("gpu", 2),
        "a step of its own ending": lambda: hub.release(held),
        "the retry time": lambda: now.__setitem__(0, FULL_RETRY_S),
    }[ends]()
    assert hub.free("gpu", [])


@pytest.mark.parametrize(
    ("caps", "machine", "wait"),
    [
        ((), None, "waiting for any machine allowed for this item"),
        (("codex_hook",), "gpu", None),
    ],
    ids=["machine-lacks-it", "machine-has-it"],
)
async def test_any_gives_a_step_that_needs_a_cap_only_to_a_machine_that_lists_it(
    caps, machine, wait
):
    hub = Hub("2.3.0")
    other = {"token_sha256": registry.hash_token("kwt_other")}  # never connected
    hub.apply(
        registry.WorkersFile.model_validate(
            {"workers": {"gpu": {"token_sha256": registry.hash_token(TOKEN)}, "other": other}}
        )
    )
    await _online(hub, *caps)
    verdict = hub.decide("any", [], needs={"codex_hook": "the hook"})
    assert (verdict.machine, verdict.wait, verdict.refusal) == (machine, wait, None)


@pytest.mark.parametrize("runner", ["gpu", "any"])
async def test_a_step_no_connected_machine_can_take_for_want_of_a_cap_is_refused_saying_so(runner):
    hub = Hub("2.3.0")
    hub.apply(_limits_file(None))
    await _online(hub)
    refusal = hub.decide(runner, [], needs={"codex_hook": "harness 'codex' cannot run the hook"})
    assert "harness 'codex' cannot run the hook" in refusal.refusal
    assert "update Kraft on that machine" in refusal.refusal
    assert hub.decide(runner, []).machine == "gpu"


@pytest.mark.parametrize(
    ("runner", "says", "machine"),
    [
        ("a-nolfs", "machine 'a-nolfs' has no git-lfs; install git-lfs there", None),
        ("any", None, "b-lfs"),
        ("pool", None, "b-lfs"),
    ],
    ids=["named-lacks-it", "any-picks-the-one-with-it", "group-picks-the-one-with-it"],
)
async def test_a_step_of_an_lfs_repo_goes_only_to_a_machine_that_lists_lfs(
    tmp_path, runner, says, machine
):
    repo = make_repo(tmp_path)
    (repo / ".gitattributes").write_text("*.bin filter=lfs -text\n")
    hub = Hub("2.3.0")
    hub.apply(
        registry.WorkersFile.model_validate(
            {
                "workers": {
                    n: {"token_sha256": registry.hash_token(f"kwt_{n}"), "groups": ["pool"]}
                    for n in ("a-nolfs", "b-lfs")
                }
            }
        )
    )
    await attach(hub, Peer(), "a-nolfs", caps=frozenset(), token="kwt_a-nolfs")
    await attach(hub, Peer(), "b-lfs", caps=frozenset({protocol.LFS}), token="kwt_b-lfs")
    verdict = hub.decide(runner, [entry_of({"path": str(repo), "name": "r"})])
    assert (verdict.machine, says is None or says in verdict.refusal) == (machine, True)
    if says:
        assert verdict.refusal.startswith("the repo uses Git LFS: ")
        assert verdict.refusal.endswith("(and update Kraft on it if it is older than the server)")


@pytest.mark.parametrize(("local", "capacity"), [({"capacity": 2}, 2), (None, None)])
async def test_applying_workers_yaml_hands_the_local_workers_limit_to_its_link(local, capacity):
    from kraft.remote import local_link

    hub = Hub("2.3.0")
    hub.apply(registry.WorkersFile.model_validate({"local": local} if local is not None else {}))
    assert local_link.current().capacity == capacity


def _grouped(**extra):
    """Two dial-in machines in group `gpu`, each with its own token, a limit of one."""
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                n: {
                    "token_sha256": registry.hash_token(f"kwt_{n}"),
                    "capacity": 1,
                    "groups": ["gpu"],
                    **extra.get(n, {}),
                }
                for n in ("a", "b")
            }
        }
    )


async def _both_online(hub):
    for n in ("a", "b"):
        await attach(hub, Peer(), n, caps=frozenset(), token=f"kwt_{n}")


async def test_a_group_goes_to_its_first_free_member_and_waits_when_none_is():
    hub = Hub("2.3.0")
    hub.apply(_grouped())
    await _both_online(hub)
    assert hub.decide("gpu", []).machine == "a"
    first = await hub.claim("gpu", [], "w/1", poll_s=0.01)
    assert first.machine == "a" and hub.decide("gpu", []).machine == "b"
    exclude_b = hub.decide("gpu", [], exclude={"b"})
    assert exclude_b.machine is None and exclude_b.wait == "waiting for a machine in gpu"
    await hub.claim("gpu", [], "w/2", poll_s=0.01)
    # both full: the wait names the group alone, no `(offline: ...)` for one member
    assert hub.decide("gpu", []) == Verdict(wait="waiting for a machine in gpu")
    assert await hub.claim("gpu", [], "w/3", wait=False) is None
    hub.release(first)
    assert (await hub.claim("gpu", [], "w/3", wait=False)).machine == "a"


async def test_a_group_member_that_is_excluded_is_passed_over_while_it_has_a_slot():
    hub = Hub("2.3.0")
    hub.apply(_grouped())
    await _both_online(hub)
    assert (await hub.claim("gpu", [], "w/1", wait=False, exclude={"a"})).machine == "b"


async def test_a_group_no_member_may_work_for_is_refused_naming_the_repo():
    hub = Hub("2.3.0")
    hub.apply(_grouped(a={"repos": ["kraft"]}, b={"repos": ["kraft"]}))
    refusal = hub.decide("gpu", _repos("infra")).refusal
    assert "group 'gpu'" in refusal and "infra" in refusal


async def test_a_group_still_hands_a_codex_step_only_to_a_member_that_lists_the_cap():
    hub = Hub("2.3.0")
    hub.apply(_grouped())
    await attach(hub, Peer(), "a", caps=frozenset(), token="kwt_a")
    await attach(hub, Peer(), "b", caps=frozenset({"codex_hook"}), token="kwt_b")
    assert hub.decide("gpu", [], needs={"codex_hook": "the hook"}).machine == "b"


def _disableable(**gpu):
    """`gpu` and `gpu-2`, both in group `pool`; `gpu` takes `gpu`'s overrides."""
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                n: {
                    "token_sha256": registry.hash_token(f"kwt_{n}"),
                    "groups": ["pool"],
                    **(gpu if n == "gpu" else {}),
                }
                for n in ("gpu", "gpu-2")
            }
        }
    )


@pytest.mark.parametrize(
    ("runner", "machine", "wait"),
    [
        ("gpu", None, "waiting for gpu (disabled)"),
        ("any", "gpu-2", None),
        ("pool", "gpu-2", None),
    ],
    ids=["named", "any", "group"],
)
@pytest.mark.parametrize("online", [True, False], ids=["online", "offline"])
async def test_a_disabled_machine_takes_no_new_step_and_a_named_wait_says_it_is_disabled(
    runner, machine, wait, online
):
    hub = Hub("2.3.0")
    hub.apply(_disableable(enabled=False))
    await attach(hub, Peer(), "gpu-2", caps=frozenset(), token="kwt_gpu-2")
    if online:
        await attach(hub, Peer(), "gpu", caps=frozenset(), token="kwt_gpu")
    assert hub.free("gpu", []) is False
    verdict = hub.decide(runner, [])
    assert (verdict.machine, verdict.wait) == (machine, wait)
    assert _row(hub, "gpu")["enabled"] is False
    assert _row(hub, "gpu")["configured_capacity"] is None and _row(hub, "gpu")["ssh"] is None


async def test_a_machine_enabled_again_takes_a_step():
    hub = Hub("2.3.0")
    hub.apply(_disableable(enabled=False))
    await attach(hub, Peer(), "gpu", caps=frozenset(), token="kwt_gpu")
    assert hub.decide("gpu", []).wait == "waiting for gpu (disabled)"
    hub.apply(_disableable())
    assert hub.decide("gpu", []).machine == "gpu"


@pytest.mark.parametrize(
    ("local", "enabled"), [({"enabled": False}, False), ({"capacity": 2}, True), (None, True)]
)
async def test_applying_workers_yaml_hands_the_local_workers_enabled_to_its_link(local, enabled):
    from kraft.remote import local_link

    hub = Hub("2.3.0")
    hub.apply(registry.WorkersFile.model_validate({"local": local} if local is not None else {}))
    assert local_link.current().enabled is enabled
