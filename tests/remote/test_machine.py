# tests/remote/test_machine.py
"""The machine's two ways in, against the real hub."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import sys

import pytest
from support.remote import memory_pair, send_job, stop_daemon
from websockets.asyncio.server import serve

from kraft.remote import blobs, bundle, machine, protocol, registry
from kraft.remote import machine as machine_mod
from kraft.remote.hub import Hub
from kraft.remote.machine import Machine
from kraft.remote.transport import FRAME_LIMIT, StreamTransport, WebSocketTransport
from kraft.remote.workdir import Copy

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
    assert not hub.snapshot()[0]["online"]
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
    assert all("cannot connect" in line for line in lines)


async def test_stdio_mode_handshakes_over_a_pipe_and_exits_when_ssh_closes_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KRAFT_HOME", str(tmp_path / "home"))
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "kraft",
        "admin",
        "worker",
        "serve",
        "--stdio",
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
    stop_daemon(tmp_path / "home")


async def _until(cond, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.01)


async def _job(tmp_path, argv, *, sid="s1", welcome=None, job=None, **machine_kw):
    """A `Machine` on one end of an in-process pair; the test plays the server and sends one job."""
    server, machine_t = memory_pair()
    machine = Machine(tmp_path / "home", kraft_version="2.3.0", **machine_kw)
    task = asyncio.create_task(machine.serve(machine_t, {"server": "srv", **(welcome or {})}))
    spec = await send_job(server, tmp_path, argv, sid=sid, **(job or {}))
    return machine, task, server, spec


async def _frames_until(t, kind):
    frames = []
    while True:
        frame = await asyncio.wait_for(t.recv(), 10)
        frames.append(frame)
        if frame["t"] == kind:
            return frames


async def _end(t, task):
    await t.close()
    with contextlib.suppress(protocol.Closed):
        await task


async def test_a_job_runs_on_the_machine_and_its_log_and_exit_come_back(tmp_path):
    machine, task, t, spec = await _job(tmp_path, [sys.executable, "-c", "print('hi')"])
    frames = await _frames_until(t, "finished")
    assert frames[0]["t"] == "started" and frames[-1]["code"] == 0 and frames[-1]["tip"] is None
    assert b"".join(base64.b64decode(f["data"]) for f in frames if f["t"] == "log") == b"hi\n"
    assert (
        tmp_path / "home" / "remote" / "srv" / "jobs" / "w1__s1" / "session.log"
    ).read_bytes() == b"hi\n"
    await _end(t, task)


async def test_a_job_that_commits_hands_in_a_bundle_of_exactly_its_commits(tmp_path):
    script = (
        "import subprocess; open('made.txt','w').write('x'); subprocess.run(['git','add','-A']); "
        "subprocess.run(['git','commit','-qm','m'])"
    )
    machine, task, t, spec = await _job(tmp_path, [sys.executable, "-c", script])
    frames = await _frames_until(t, "finished")
    sink = blobs.BlobSink(tmp_path / "got")
    got = [sink.add(f) for f in frames if f["t"] == "blob"]
    assert [f["name"] for f in frames if f["t"] == "blob"][0] == "commits" and got[-1] is not None
    assert frames[-1]["tip"]
    assert bundle._git(tmp_path / "server", "bundle", "verify", str(got[-1])).returncode == 0
    await _end(t, task)


async def test_a_finished_job_is_resent_from_the_offset_the_server_names(tmp_path):
    machine, task, t, spec = await _job(tmp_path, [sys.executable, "-c", "print('hello')"])
    await _frames_until(t, "finished")
    await t.send({"t": "sync", "jobs": [{"id": spec.key, "offset": 2}]})
    frames = await _frames_until(t, "finished")
    logs = [f for f in frames if f["t"] == "log"]
    assert logs[0]["offset"] == 2 and base64.b64decode(logs[0]["data"]) == b"llo\n"
    await _end(t, task)


async def test_a_job_whose_cli_is_not_on_the_machine_is_rejected_as_a_config_error(tmp_path):
    machine, task, t, spec = await _job(tmp_path, ["claude-not-installed"])
    frame = (await _frames_until(t, "rejected"))[-1]
    assert frame["outcome"] == "config_error" and "claude-not-installed" in frame["reason"]
    await _end(t, task)


async def test_a_job_the_machine_has_forgotten_is_rejected_when_the_server_asks_after_it(tmp_path):
    server, machine_t = memory_pair()
    task = asyncio.create_task(
        Machine(tmp_path / "home", kraft_version="2.3.0").serve(machine_t, {"server": "srv"})
    )
    await server.send({"t": "sync", "jobs": [{"id": "w1/gone", "offset": 0}]})
    frame = await asyncio.wait_for(server.recv(), 5)
    assert frame["t"] == "rejected" and frame["id"] == "w1/gone" and "no record" in frame["reason"]
    await _end(server, task)


async def test_the_agents_call_reaches_the_server_as_an_api_frame_and_its_answer_returns(tmp_path):
    machine, task, t, spec = await _job(tmp_path, ["sh", "-c", "sleep 2"])
    await _frames_until(t, "started")
    call = asyncio.create_task(
        machine.call_server(spec.key, "POST", "/api/work-items/w1/progress?x=1", b"{}")
    )
    frame = (await _frames_until(t, "api"))[-1]
    assert (frame["method"], frame["path"], frame["query"], frame["id"]) == (
        "POST", "/api/work-items/w1/progress", "x=1", spec.key,
    )  # fmt: skip
    await t.send(
        {
            "t": "api_result",
            "rid": frame["rid"],
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": base64.b64encode(b'{"ok":true}').decode(),
        }
    )
    assert await call == (200, {"content-type": "application/json"}, b'{"ok":true}')
    await t.send({"t": "signal", "id": spec.key, "sig": "KILL"})
    await _frames_until(t, "finished")
    await _end(t, task)


async def test_a_call_made_while_offline_is_sent_and_answered_after_reconnect(tmp_path):
    machine, task, t, spec = await _job(tmp_path, ["sh", "-c", "sleep 30"])
    await _frames_until(t, "started")
    await _end(t, task)
    call = asyncio.create_task(machine.call_server(spec.key, "GET", "/api/work-items/w1"))
    await asyncio.sleep(0.3)
    assert not call.done()
    server, machine_t = memory_pair()
    serving = asyncio.create_task(machine.serve(machine_t, {"server": "srv"}))
    frame = (await _frames_until(server, "api"))[-1]
    await server.send({"t": "api_result", "rid": frame["rid"], "status": 200, "body": ""})
    assert (await call)[0] == 200
    await server.send({"t": "signal", "id": spec.key, "sig": "KILL"})
    await _frames_until(server, "finished")
    await _end(server, serving)


async def test_a_call_in_flight_when_the_connection_drops_is_told_so(tmp_path):
    machine, task, t, spec = await _job(tmp_path, ["sh", "-c", "sleep 30"])
    await _frames_until(t, "started")
    call = asyncio.create_task(machine.call_server(spec.key, "POST", "/api/work-items/w1/x"))
    await _frames_until(t, "api")
    await _end(t, task)
    status, _, body = await asyncio.wait_for(call, 5)
    assert status == 503 and b"dropped" in body
    server, machine_t = memory_pair()
    serving = asyncio.create_task(machine.serve(machine_t, {"server": "srv"}))
    await server.send({"t": "signal", "id": spec.key, "sig": "KILL"})
    frames = await _frames_until(server, "finished")
    assert not [
        f for f in frames if f["t"] == "api"
    ]  # a call that may have been acted on is not resent
    await _end(server, serving)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _offline_job(tmp_path, give_up_after_s):
    """A `sleep 30` job on a machine whose connection has just dropped; the clock is the test's."""
    clock = _Clock()
    machine, task, t, spec = await _job(
        tmp_path,
        ["sh", "-c", "sleep 30"],
        welcome={"give_up_after_s": give_up_after_s},
        clock=clock,
        give_up_poll_s=0.02,
    )
    await _frames_until(t, "started")
    await _end(t, task)
    return machine, clock, spec


def _alive(machine, spec) -> bool:
    return machine._jobs[spec.key].runner._proc.poll() is None


async def _rejoin(machine, spec):
    """The server comes back; the job it was running is finished, so it hands in."""
    server, machine_t = memory_pair()
    serving = asyncio.create_task(machine.serve(machine_t, {"server": "srv"}))
    await server.send({"t": "signal", "id": spec.key, "sig": "KILL"})
    await server.send({"t": "sync", "jobs": [{"id": spec.key, "offset": 0}]})
    await _frames_until(server, "finished")
    await _end(server, serving)


async def test_a_machine_without_kraft_for_give_up_after_stops_its_jobs(tmp_path):
    machine, clock, spec = await _offline_job(tmp_path, 2)
    clock.now = 1.9
    await asyncio.sleep(0.2)
    assert _alive(machine, spec)
    clock.now = 2.1
    await _until(lambda: not _alive(machine, spec))
    log = tmp_path / "home" / "remote" / "srv" / "jobs" / "w1__s1" / "session.log"
    assert log.read_text().endswith("kraft: stopped on this machine after 2s without Kraft\n")
    await _rejoin(machine, spec)


async def test_never_keeps_a_job_running_however_long_kraft_is_gone(tmp_path):
    machine, clock, spec = await _offline_job(tmp_path, None)
    clock.now = 86400
    await asyncio.sleep(0.3)
    assert _alive(machine, spec)
    await _rejoin(machine, spec)


async def test_a_reconnect_before_give_up_after_keeps_the_job(tmp_path):
    machine, clock, spec = await _offline_job(tmp_path, 2)
    clock.now = 1
    server, machine_t = memory_pair()
    serving = asyncio.create_task(machine.serve(machine_t, {"server": "srv", "give_up_after_s": 2}))
    await asyncio.sleep(0.1)
    clock.now = 10
    await asyncio.sleep(0.3)
    assert _alive(machine, spec)
    await server.send({"t": "signal", "id": spec.key, "sig": "KILL"})
    await _frames_until(server, "finished")
    await _end(server, serving)


def _make_copy(machine, wid, sid="s1"):
    """What a machine keeps of an item: its copy, the files that travelled, a job folder."""
    area = machine.home / "remote" / "srv"
    paths = [
        area / "items" / wid / "repo" / "a.txt",
        area / "run" / "items" / wid / "docs" / "spec.md",
        area / "jobs" / f"{wid}__{sid}" / "session.log",
        area / "blobs" / f"{wid}_{sid}.code",
    ]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return paths


@pytest.mark.parametrize("running", [False, True], ids=["finished", "running"])
async def test_forget_removes_an_items_copy_but_not_one_that_is_running(tmp_path, running):
    machine, task, t, spec = await _job(tmp_path, ["sh", "-c", "sleep 30" if running else "true"])
    await _frames_until(t, "started")
    if not running:
        await _frames_until(t, "finished")
    await t.send({"t": "forget", "item": "w1"})
    await t.send({"t": "ping"})
    await _frames_until(t, "pong")  # the machine has handled everything sent before
    area = tmp_path / "home" / "remote" / "srv"
    assert (area / "items" / "w1").exists() is running
    assert (spec.key in machine._jobs) is True if running else (spec.key not in machine._jobs)
    if running:
        await t.send({"t": "signal", "id": spec.key, "sig": "KILL"})
        await _frames_until(t, "finished")
    await _end(t, task)


async def test_keep_removes_every_item_the_server_no_longer_lists(tmp_path):
    server, machine_t = memory_pair()
    machine = Machine(tmp_path / "home", kraft_version="2.3.0")
    task = asyncio.create_task(machine.serve(machine_t, {"server": "srv"}))
    kept, gone = _make_copy(machine, "live"), _make_copy(machine, "archived")
    await server.send({"t": "keep", "items": ["live"]})
    await server.send({"t": "ping"})
    await _frames_until(server, "pong")
    assert all(p.exists() for p in kept) and not any(p.exists() for p in gone)
    await _end(server, task)


async def _sandboxed_job(tmp_path, short_run, monkeypatch, argv, backend=None, **job):
    from support.fake_backend import Remote

    from kraft.worker import backends

    fake = Remote(tmp_path)
    vars(fake).update(backend or {})
    monkeypatch.setitem(backends._BACKENDS, fake.kind, fake)
    monkeypatch.setenv("KRAFT_RUN_DIR", str(short_run.base))
    result = str(tmp_path / "home" / "remote" / "srv" / "run" / "result.json")
    job = {"sandbox": {"kind": "remote"}, "env": {"KRAFT_RESULT_PATH": result}, **job}
    machine, task, t, spec = await _job(tmp_path, argv, welcome={"name": "gpu"}, job=job)
    return fake, machine, task, t


async def test_a_sandbox_the_machine_cannot_build_is_rejected_naming_it_and_nothing_runs(
    tmp_path, short_run, monkeypatch
):
    marker = tmp_path / "ran"
    fake_argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w')"]
    fake, machine, task, t = await _sandboxed_job(
        tmp_path, short_run, monkeypatch, fake_argv, {"unrunnable": "docker is not installed"}
    )
    frames = await _frames_until(t, "rejected")
    assert frames[-1]["outcome"] == "config_error" and frames[-1]["reason"].startswith("gpu: ")
    assert "docker is not installed" in frames[-1]["reason"] and not marker.exists()
    await _end(t, task)


async def test_a_sandboxed_step_runs_in_the_backend_and_its_oom_is_in_the_finished_frame(
    tmp_path, short_run, monkeypatch
):
    from kraft.worker.backends.docker import OomKill

    fake, machine, task, t = await _sandboxed_job(
        tmp_path, short_run, monkeypatch, ["sh", "-c", "echo hi"], {"oom": OomKill("1g", False)}
    )
    frames = await _frames_until(t, "finished")
    assert frames[-1]["oom"] == {"memory": "1g", "confirmed": False}
    assert "wrap" in fake.touched and b"hi" in b"".join(
        base64.b64decode(f["data"]) for f in frames if f["t"] == "log"
    )
    await _end(t, task)


PLANT = (
    "import subprocess; subprocess.run(['git', 'init', '-q', 'sub'], check=True); "
    "open('stray.txt', 'w').write('x')"
)


async def test_a_sandboxed_checkout_holding_a_repository_it_made_is_not_swept_and_is_rebuilt(
    tmp_path, short_run, monkeypatch
):
    fake, machine, task, t = await _sandboxed_job(
        tmp_path, short_run, monkeypatch, [sys.executable, "-c", PLANT]
    )
    frames = await _frames_until(t, "finished")
    assert frames[-1]["tip"] is None  # the sweep would have committed stray.txt
    log = b"".join(base64.b64decode(f["data"]) for f in frames if f["t"] == "log").decode()
    assert (
        "kraft: skipped the sweep on gpu: the sandboxed checkout holds git repositories "
        "Kraft did not create: sub\n" in log
    )
    copy = Copy(tmp_path / "home" / "remote" / "srv" / "items" / "w1" / "repo")
    assert (copy.root / "sub" / ".git").exists()
    copy.update(None, branch="kraft/x", head=copy._git("rev-parse", "HEAD").stdout.strip())
    assert not (copy.root / "sub").exists() and not (copy.root / "stray.txt").exists()
    await _end(t, task)


# ── the machine's own limit ──


async def _serve(machine, welcome=None):
    server, machine_t = memory_pair()
    task = asyncio.create_task(
        machine.serve(machine_t, {"server": "srv", "name": "gpu", **(welcome or {})})
    )
    return server, task


async def _reserve(t, key, rid="r"):
    await t.send({"t": "reserve", "rid": rid, "id": key})
    return (await _frames_until(t, "reserved"))[-1]


async def test_a_machine_holds_a_slot_for_a_step_until_its_job_or_an_unreserve(tmp_path):
    clock = _Clock()
    machine = Machine(tmp_path / "home", kraft_version="2.3.0", limit=lambda: 1, clock=clock)
    server, task = await _serve(machine)
    assert (await _reserve(server, "w1/a")) == {
        "t": "reserved", "rid": "r", "id": "w1/a", "ok": True, "capacity": 1,
    }  # fmt: skip
    assert (await _reserve(server, "w1/a"))["ok"] is True  # asking again changes nothing
    refused = await _reserve(server, "w1/b")
    assert (refused["ok"], refused["capacity"]) == (False, 1)
    await server.send({"t": "unreserve", "id": "w1/a"})
    assert (await _reserve(server, "w1/b"))["ok"] is True
    clock.now = machine_mod.RESERVE_TTL_S + 1  # the server never came back for it
    assert (await _reserve(server, "w1/c"))["ok"] is True
    await _end(server, task)


async def test_the_job_a_reservation_was_for_runs_and_keeps_the_slot_while_it_does(tmp_path):
    machine = Machine(tmp_path / "home", kraft_version="2.3.0", limit=lambda: 1)
    server, task = await _serve(machine)
    await _reserve(server, "w1/a")
    await send_job(server, tmp_path, ["sh", "-c", "sleep 30"], sid="a")
    await _frames_until(server, "started")
    assert (await _reserve(server, "w1/b"))["ok"] is False
    await server.send({"t": "signal", "id": "w1/a", "sig": "KILL"})
    await _frames_until(server, "finished")
    await server.send({"t": "delivered", "id": "w1/a"})
    assert (await _reserve(server, "w1/b"))["ok"] is True
    await _end(server, task)


async def test_a_new_connection_keeps_only_the_reservations_the_server_still_holds(tmp_path):
    machine = Machine(tmp_path / "home", kraft_version="2.3.0", limit=lambda: 2)
    server, task = await _serve(machine)
    await _reserve(server, "w1/a")
    await _reserve(server, "w1/b")
    await _end(server, task)
    server, task = await _serve(machine)
    await server.send({"t": "reserves", "ids": ["w1/a"]})
    assert (await _reserve(server, "w1/c"))["ok"] is True  # w1/b was dropped, w1/a kept
    assert (await _reserve(server, "w1/d"))["ok"] is False
    await _end(server, task)


async def test_a_reserves_frame_does_not_drop_a_hold_made_after_the_connection_began(tmp_path):
    """The server's list was written before it asked for this one."""
    machine = Machine(tmp_path / "home", kraft_version="2.3.0", limit=lambda: 2)
    server, task = await _serve(machine)
    await _reserve(server, "w1/a")
    await server.send({"t": "reserves", "ids": []})
    assert (await _reserve(server, "w1/b"))["ok"] is True
    assert (await _reserve(server, "w1/c"))["ok"] is False  # w1/a and w1/b are both held
    await _end(server, task)


async def test_a_job_with_no_reservation_beyond_the_limit_is_refused_before_its_blobs(tmp_path):
    machine = Machine(tmp_path / "home", kraft_version="2.3.0", limit=lambda: 1)
    server, task = await _serve(machine)
    await send_job(server, tmp_path, ["sh", "-c", "sleep 30"], sid="a")
    await _frames_until(server, "started")
    (tmp_path / "second").mkdir()
    await send_job(server, tmp_path / "second", ["sh", "-c", "sleep 30"], sid="b")
    refused = (await _frames_until(server, "rejected"))[-1]
    assert (refused["id"], refused["outcome"]) == ("w1/b", "full")
    assert refused["reason"] == "gpu has no free slot of its own (1 of 1 running)"
    await server.send({"t": "sync", "jobs": [{"id": "w1/b", "offset": 0}]})
    assert (await _frames_until(server, "rejected"))[-1] == refused
    sink = tmp_path / "home" / "remote" / "srv" / "blobs"
    assert not list(sink.glob("w1_b.*"))  # none of its blobs was written
    assert list(sink.glob("w1_a.*"))
    await server.send({"t": "signal", "id": "w1/a", "sig": "KILL"})
    await _end(server, task)


@pytest.mark.parametrize(("first", "second"), [(3, 1), (1, 3)], ids=["lowered", "raised"])
async def test_the_machine_reports_a_changed_limit(tmp_path, first, second):
    limit = [first]
    machine = Machine(tmp_path / "home", kraft_version="2.3.0", limit=lambda: limit[0], ping_s=0.05)
    server, task = await _serve(machine)
    await _until(lambda: machine._t is not None)
    limit[0] = second
    frame = (await _frames_until(server, "capacity"))[-1]  # within a ping interval
    assert frame == {"t": "capacity", "capacity": second}
    await _reserve(server, "w1/a")
    await _reserve(server, "w1/b")  # nothing new to say: no second capacity frame
    await server.send({"t": "ping"})
    frames = await _frames_until(server, "pong")
    assert [f for f in frames if f["t"] == "capacity"] == []
    await _end(server, task)
