import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
from support.harness import entry_of

from kraft import builtins as kraft_builtins
from kraft import events, store
from kraft.adapters import subprocess as sp
from kraft.paths import RunDirs
from kraft.worker import backends, channel, reattach
from kraft.worker.backends import docker as docker_backend
from kraft.worker.sandbox import SandboxNotReady

_CHAIN = """
- id: implementation
  kind: exec
  tasks: [{id: implement, kind: agent, harness: fake, prompt: p}]
"""
#: A definitely-dead pid, with a start time.
DEAD = (2_000_000_000, 123.0)


class Remote:
    """A backend sharing no filesystem with Kraft: the command writes its
    result somewhere of its own, and only `collect` brings it back."""

    kind = "remote"

    def __init__(self, tmp_path: Path):
        self.outbox = tmp_path / "remote-outbox"
        self.outbox.mkdir()
        self.closed: list[str] = []
        #: What `prepare` hands back (or, a str, the problem it raises), and
        #: the `ca_bundle` each `wrap` was given.
        self.prepared: Path | str | None = None
        self.wrapped_with: list = []
        #: Every session call, in order, and what `open_session` answers.
        self.calls: list[str] = []
        self.route = {"HTTPS_PROXY": "http://127.0.0.1:3128"}
        self.wrapped_env: list[dict] = []

    def home(self, run_dirs, work_item_id):
        return run_dirs.base / "remote-home" / work_item_id

    async def probe(self, sandbox, executable, env):
        return None

    async def prepare(self, sandbox):
        if isinstance(self.prepared, str):
            raise SandboxNotReady(self.prepared)
        return self.prepared

    def code_in(self, run_base, cwd, branch, **kw):
        return None

    def wrap(self, cmd, cwd, sandbox, results_dir, env=None, *, session_id=None, **kw):
        self.wrapped_with.append(kw.get("ca_bundle"))
        self.wrapped_env.append(env)
        self.calls.append("wrap")
        return ["env", f"KRAFT_RESULT_PATH={self.outbox / f'{session_id}.json'}", *cmd]

    def client_cwd(self, session_id):
        return None

    def launch_failed(self, cidfile):
        return False

    def code_out(self, refs, session_id):
        return None

    def code_out_item(self, run_base, work_item_id):
        return []

    async def collect(self, session_id, result_path):
        sent = self.outbox / f"{session_id}.json"
        if sent.exists():
            shutil.copyfile(sent, result_path)

    async def oom_killed(self, session_id):
        return None

    async def close(self, session_id):
        self.closed.append(session_id)
        self.calls.append("close")

    async def open_session(self, session_id, sandbox, sock_path):
        self.calls.append("open_session")
        self.sock_path = sock_path
        return self.route if sandbox.get("network") else {}

    async def close_session(self, session_id):
        self.calls.append("close_session")

    async def sweep(self, keep_sessions):
        return []

    def release(self, run_dirs, worktree, work_item_id):
        pass

    async def health(self, sandbox):
        return True, "remote"


@pytest.fixture
def remote(tmp_path, monkeypatch):
    backend = Remote(tmp_path)
    monkeypatch.setitem(backends._BACKENDS, backend.kind, backend)
    return backend


@pytest.fixture
def docker_closed(monkeypatch):
    closed = []

    async def teardown(session_id):
        closed.append(session_id)

    monkeypatch.setattr(docker_backend, "teardown", teardown)
    return closed


@pytest.mark.parametrize(
    ("kind", "asked"),
    [("docker", ["docker"]), (None, ["docker", "remote"]), ("retired", ["docker", "remote"])],
    ids=["recorded", "none-recorded", "no-longer-registered"],
)
def test_a_session_is_closed_by_the_backend_it_recorded_else_by_every_one(remote, kind, asked):
    assert [b.kind for b in backends.for_session(kind)] == asked


async def _run_on_remote(database, run_dirs, tmp_path, sandbox=None, **kw) -> str:
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )
    return await sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        cmd=["sh", "-c", """printf '{"status": "done_with_concerns"}' > "$KRAFT_RESULT_PATH" """],
        node_id="verify",
        hook_point="on.test.run",
        cwd=tmp_path,
        sandbox=sandbox or {"kind": "remote"},
        **kw,
    )


async def test_a_result_reaches_kraft_only_through_collect(database, run_dirs, tmp_path, remote):
    """The seam's one rule: nothing reads a sandboxed session's result before
    its backend has collected it, so a backend with no shared filesystem
    works unchanged."""
    status = await _run_on_remote(database, run_dirs, tmp_path)

    assert status == "done_with_concerns"
    row = database.read(
        lambda c: c.execute("SELECT sandbox FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    assert (row["sandbox"], remote.closed) == ("remote", ["s1"])


async def test_reattach_closes_a_dead_session_in_the_backend_it_ran_in(
    item_on, database, run_dirs, remote, docker_closed
):
    item = await item_on(_CHAIN, "implementation")
    await item.session("s1", "implementation.main.implement", running=DEAD, sandbox="remote")

    await reattach.reattach(database, run_dirs)

    assert (remote.closed, docker_closed) == (["s1"], [])


async def test_reattach_closes_a_session_that_recorded_no_backend_everywhere(
    item_on, database, run_dirs, remote, docker_closed
):
    """A row written before the column existed may have run in docker."""
    item = await item_on(_CHAIN, "implementation")
    await item.session("s1", "implementation.main.implement", running=DEAD)

    await reattach.reattach(database, run_dirs)

    assert (remote.closed, docker_closed) == (["s1"], ["s1"])


async def test_a_collect_that_fails_still_publishes_the_sessions_commits(
    database, run_dirs, tmp_path, remote, monkeypatch
):
    """A backend that copies results out can fail to; the branch the session
    committed must reach the repository all the same."""
    published = []

    async def fail(session_id, result_path):
        raise OSError("copy-out failed")

    monkeypatch.setattr(remote, "code_in", lambda *a, **kw: SimpleNamespace(carried=None))
    monkeypatch.setattr(remote, "code_out", lambda refs, sid: published.append(sid))
    monkeypatch.setattr(remote, "collect", fail)
    await database.write(
        lambda c: store.create_work_item(
            c,
            id="w1",
            bead_id="B",
            title="t",
            repo="/r",
            chain_template="quick-task",
            chain_definition="{}",
        )
    )

    with pytest.raises(OSError, match="copy-out failed"):
        await sp.run_task(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="w1",
            cmd=["true"],
            node_id="verify",
            hook_point="on.test.run",
            cwd=tmp_path,
            sandbox={"kind": "remote"},
        )

    assert (published, remote.closed) == (["s1"], ["s1"])


@pytest.mark.parametrize(
    "prepared", [Path("/run/sandbox-ca/abc.pem"), None], ids=["bundle", "no-extra-ca"]
)
async def test_a_session_is_wrapped_with_exactly_what_prepare_built(
    database, run_dirs, tmp_path, remote, prepared
):
    """Handed over, not remembered: no CA means no `ca_bundle` at all."""
    remote.prepared = prepared
    assert await _run_on_remote(database, run_dirs, tmp_path) == "done_with_concerns"
    assert remote.wrapped_with == [prepared]


async def test_a_session_whose_backend_is_not_ready_stops_before_it_launches(
    database, run_dirs, tmp_path, remote
):
    remote.prepared = "image 'x' roots could not be read"
    assert await _run_on_remote(database, run_dirs, tmp_path) == "config_error"
    assert remote.wrapped_with == []
    log = (run_dirs.logs / "s1.log").read_text()
    assert "roots could not be read" in log


# --- network: the session's egress route -------------------------------------------------

_POLICED = {"kind": "remote", "network": {"runtime": {"allow": ["a.io"], "deny": ["b.io"]}}}


@pytest.fixture
async def channels(database):
    """A channel registry installed as the daemon's, on a run dir short
    enough for a unix socket path."""
    base = Path(tempfile.mkdtemp(prefix="kraft-sn-", dir="/tmp"))
    registry = channel.ChannelRegistry(RunDirs(base).ensure(), database)
    channel.install(registry)
    yield registry
    await registry.close_all()
    channel.install(None)
    shutil.rmtree(base, ignore_errors=True)


async def test_a_session_under_network_gets_its_route_before_it_is_wrapped(
    database, run_dirs, tmp_path, remote, channels
):
    """Opened before the worker exists, closed after it (the worker, then
    its route); the proxy env wins over the task's own; the row keeps the
    lists a reattach re-opens with, the harness's hosts allowed too."""
    status = await _run_on_remote(
        database,
        run_dirs,
        tmp_path,
        _POLICED,
        env={"HTTPS_PROXY": "http://elsewhere:1"},
        network_requires=("api.anthropic.com",),
    )

    assert status == "done_with_concerns"
    assert remote.calls == ["open_session", "wrap", "close", "close_session"]
    assert remote.wrapped_env[0]["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    row = database.read(
        lambda c: c.execute("SELECT egress FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    assert json.loads(row["egress"]) == {
        "phase": "runtime",
        "allow": ["a.io", "api.anthropic.com"],
        "deny": ["b.io"],
        "transport": "unix",
    }
    assert not channels.socket_path("s1").exists()


@pytest.mark.parametrize("missing", ["channel", "route"])
async def test_network_without_a_channel_or_a_route_never_launches(
    database, run_dirs, tmp_path, remote, channels, missing
):
    """R3: fail closed. A process with no egress channel, or a backend that
    made no route, is a `config_error` -- never a launch with open egress."""
    if missing == "channel":
        channel.install(None)
    else:
        remote.route = {}
    assert await _run_on_remote(database, run_dirs, tmp_path, _POLICED) == "config_error"
    assert "wrap" not in remote.calls
    assert "egress" in (run_dirs.logs / "s1.log").read_text()


@pytest.mark.parametrize("broken", ["open_session", "wrap"])
async def test_a_launch_that_raises_after_its_channel_opened_still_closes_it(
    database, run_dirs, tmp_path, remote, channels, monkeypatch, broken
):
    """Any failure between opening the channel and the process taking it
    over closes relay and channel; nothing is left listening."""

    def fail(*args, **kwargs):
        remote.calls.append(broken)
        raise RuntimeError(f"{broken} broke")

    monkeypatch.setattr(remote, broken, fail)
    with pytest.raises(RuntimeError, match=f"{broken} broke"):
        await _run_on_remote(database, run_dirs, tmp_path, _POLICED)
    assert remote.calls[-1] == "close_session"
    assert not channels.socket_path("s1").exists()


async def test_reattach_reopens_an_adopted_sessions_channel_from_its_row(
    item_on, database, run_dirs, remote, channels, monkeypatch
):
    """The lists come off the row the session launched with, and the
    channel and route close when the adopted session ends."""
    adopted = asyncio.Event()
    release = asyncio.Event()

    async def adopt(db, session_id, pid, **kw):
        adopted.set()
        await release.wait()

    monkeypatch.setattr(reattach, "_adopt", adopt)
    item = await item_on(_CHAIN, "implementation")
    live = (os.getpid(), psutil.Process().create_time())
    await item.session("s1", "implementation.main.implement", running=live, sandbox="remote")
    deny_all = {"phase": "runtime", "allow": [], "deny": ["**"]}
    await database.write(lambda c: store.set_session_egress(c, "s1", deny_all))
    # A dead session's directory, which no adopted session owns.
    dead = channels.socket_path("gone-session").parent
    dead.mkdir(parents=True)
    # A doctor in another process may be mid-probe in here.
    probing = dead.with_name("probe-x")
    probing.mkdir()

    _, tasks = await reattach.reattach(database, run_dirs)
    await adopted.wait()
    assert not dead.exists() and probing.exists()
    reader, writer = await asyncio.open_unix_connection(str(channels.socket_path("s1")))
    writer.write(b"CONNECT a.io:443 HTTP/1.1\r\n\r\n")
    answer = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    release.set()
    await tasks["s1"]

    assert b"a.io is on the deny list" in answer
    assert remote.calls[-2:] == ["close", "close_session"]
    assert not channels.socket_path("s1").exists()


async def test_reattach_closes_a_dead_sessions_route_only_if_it_had_one(
    item_on, database, run_dirs, remote, docker_closed
):
    item = await item_on(_CHAIN, "implementation")
    await item.session("s1", "implementation.main.implement", running=DEAD, sandbox="remote")
    await item.session("s2", "implementation.main.implement", running=DEAD, sandbox="remote")
    await database.write(
        lambda c: store.set_session_egress(c, "s1", {"phase": "runtime", "allow": [], "deny": []})
    )

    await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert remote.calls == ["close", "close_session", "close"]


# --- network: the setup command's install phase ------------------------------------------

_SETUP_POLICED = {
    "kind": "remote",
    "network": {"install": {"allow": ["i.invalid"]}, "runtime": {"allow": ["r.invalid"]}},
}


async def test_a_setup_command_under_network_uses_the_install_list_not_runtime(
    database, remote, channels, tmp_path, monkeypatch
):
    """Spec §1: `install` applies to setup runs. The channel refuses a host
    only the runtime list allows, recorded against the item the worktree is
    named for; the route closes after the command."""
    await database.write(
        lambda c: store.create_work_item(
            c, id="w1", bead_id="B", title="t", repo="/r", chain_template="q", chain_definition="{}"
        )
    )
    worktree = tmp_path / "w1"
    worktree.mkdir()
    answers = []

    def run(args, **kwargs):
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(remote.sock_path))
            s.sendall(b"CONNECT r.invalid:443 HTTP/1.1\r\n\r\n")
            answers.append(s.makefile("rb").read())
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(kraft_builtins.subprocess, "run", run)

    await kraft_builtins.run_setup_command(
        worktree, tmp_path, entry_of({"setup_command": "true"}), sandbox=_SETUP_POLICED
    )

    assert answers[0].startswith(b"HTTP/1.1 403") and b"not on the allow list" in answers[0]
    evts = database.read(lambda c: events.read_after(c, 0, "w1"))
    assert [e["payload"]["phase"] for e in evts if e["type"] == "sandbox_egress_refused"] == [
        "install"
    ]
    assert remote.calls == ["open_session", "wrap", "close", "close_session"]
    assert remote.wrapped_env[0]["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    assert not remote.sock_path.exists()


@pytest.mark.parametrize("missing", ["channel", "route"])
async def test_a_setup_command_under_network_without_a_channel_or_route_never_runs(
    remote, channels, tmp_path, monkeypatch, missing
):
    """R3 for the setup command: fail closed, and close whatever was opened."""
    if missing == "channel":
        channel.install(None)
    else:
        remote.route = {}
    ran = []
    monkeypatch.setattr(kraft_builtins.subprocess, "run", lambda *a, **kw: ran.append(a))

    with pytest.raises(RuntimeError, match="cannot run: .*egress"):
        await kraft_builtins.run_setup_command(
            tmp_path, tmp_path, entry_of({"setup_command": "true"}), sandbox=_SETUP_POLICED
        )

    assert ran == [] and "wrap" not in remote.calls
    if missing == "route":
        assert remote.calls[-1] == "close_session" and not remote.sock_path.exists()
