# tests/remote/test_machine.py
"""The machine's two ways in, against the real hub."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import sys

import pytest
from support.harness import git, make_repo
from support.remote import memory_pair
from websockets.asyncio.server import serve

from kraft.remote import blobs, bundle, files, machine, protocol, registry
from kraft.remote.hub import Hub
from kraft.remote.jobs import JobSpec
from kraft.remote.machine import Machine
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


async def _job(tmp_path, argv, *, sid="s1"):
    """A `Machine` on one end of an in-process pair; the test plays the server and sends one job."""
    server, machine_t = memory_pair()
    machine = Machine(tmp_path / "home", kraft_version="2.3.0")
    task = asyncio.create_task(machine.serve(machine_t, {"server": "srv"}))
    repo = make_repo(tmp_path, "server")
    git(repo, "checkout", "-q", "-b", "kraft/x")
    head = git(repo, "rev-parse", "HEAD").strip()
    code, tgz = tmp_path / "code.bundle", tmp_path / "files.tgz"
    bundle.make(repo, "kraft/x", [], code)
    files.pack([], tgz)
    spec = JobSpec(
        work_item_id="w1", session_id=sid, node_id="n", hook_point="n.main.a", argv=argv,
        cwd=str(repo), env={"KRAFT_SESSION_ID": f"w1/{sid}"}, repo_env={}, passthrough=[],
        setup_command=None, branch="kraft/x", head=head, base=head, identity={},
        server_run_dir=str(tmp_path / "run"), server_worktree=str(repo),
    )  # fmt: skip
    await server.send(
        {"t": "job", "id": spec.key, "spec": spec.to_frame(), "blobs": ["code", "files"]}
    )
    await blobs.send_blob(server, job=spec.key, name="code", source=code)
    await blobs.send_blob(server, job=spec.key, name="files", source=tgz)
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
