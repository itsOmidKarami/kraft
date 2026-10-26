"""Two ends of an in-process connection for `kraft.remote` tests."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from kraft import item_paths, store
from kraft.remote import blobs, bundle, protocol, registry
from kraft.remote import files as remote_files
from kraft.remote import runs as runs_mod
from kraft.remote.hub import Hub
from kraft.remote.jobs import JobSpec
from kraft.remote.listener import Listener
from kraft.remote.machine import Machine
from kraft.remote.protocol import Closed
from kraft.remote.runs import Run, Runs
from support.chain_builders import v1_node
from support.harness import git, make_repo, v1_chain

_EOF = object()


class MemoryTransport:
    def __init__(self, inbox: asyncio.Queue, outbox: asyncio.Queue) -> None:
        self._in, self._out, self.closed = inbox, outbox, False

    async def send(self, frame: dict) -> None:
        if self.closed:
            raise Closed("closed")
        self._out.put_nowait(json.loads(json.dumps(frame)))  # as a wire would: copies, JSON-only

    async def recv(self) -> dict:
        item = await self._in.get()
        if item is _EOF:
            self._in.put_nowait(_EOF)
            raise Closed("closed")
        return item

    async def close(self) -> None:
        self.closed = True
        self._out.put_nowait(_EOF)
        self._in.put_nowait(_EOF)


def memory_pair() -> tuple[MemoryTransport, MemoryTransport]:
    """`(a, b)`: what one sends the other receives; closing either ends both."""
    qa, qb = asyncio.Queue(), asyncio.Queue()
    return MemoryTransport(qa, qb), MemoryTransport(qb, qa)


TOKEN = "kwt_secret"


def workers_file(**machines) -> registry.WorkersFile:
    """Dial-in machines that all use `TOKEN`; each keyword is a machine's extra fields."""
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                n: {"token_sha256": registry.hash_token(TOKEN), **extra}
                for n, extra in machines.items()
            }
        }
    )


class Peer:
    """A machine for `attach` that only takes part in the handshake and the pings."""

    def __init__(self) -> None:
        self.task = None

    async def serve(self, t, welcome) -> None:
        await protocol.pump(t, on_frame=AsyncMock())


async def attach(
    hub: Hub, machine: Machine, name: str = "gpu", caps=None, token: str = TOKEN
) -> None:
    """Dial `machine` into `hub` over a fresh in-process pair, saying it understands `caps`
    (what the machine itself lists, by default); then wait for it to show online."""
    server, client = memory_pair()
    machine.server_t = server
    machine.accepted = asyncio.create_task(hub.accept(server))
    welcome = await protocol.machine_handshake(
        client,
        kraft_version="2.3.0",
        token=token,
        host="box",
        caps=machine.caps() if caps is None else caps,
    )
    machine.task = asyncio.create_task(machine.serve(client, welcome))
    await hub.wait_online(name)


_IDENTITY = {
    "GIT_AUTHOR_NAME": "k",
    "GIT_AUTHOR_EMAIL": "k@x",
    "GIT_COMMITTER_NAME": "k",
    "GIT_COMMITTER_EMAIL": "k@x",
}


@dataclass
class Rig:
    hub: Hub
    runs: Runs
    machine: Machine
    listener: Listener
    repo: Path  # the server's worktree of the item, with the item branch checked out
    branch: str
    head: str
    wid: str
    run_dirs: Any
    database: Any
    calls: list

    def files(self, sid):
        return item_paths.session_files(self.run_dirs, self.wid, sid)

    def spec(self, argv, sid="s1", env=None, **extra) -> JobSpec:
        f = self.files(sid)
        return JobSpec(
            work_item_id=self.wid,
            session_id=sid,
            node_id="build",
            hook_point="build.main.a",
            argv=argv,
            cwd=str(self.repo),
            env={
                "KRAFT_WORK_ITEM_ID": self.wid,
                "KRAFT_SESSION_ID": f"{self.wid}/{sid}",
                "KRAFT_RESULT_PATH": str(f.result),
                "KRAFT_DOCS_DIR": str(item_paths.docs_dir(self.run_dirs, self.wid)),
                **(env or {}),
            },
            repo_env={},
            passthrough=[],
            setup_command=None,
            branch=self.branch,
            head=self.head,
            base=self.head,
            identity=_IDENTITY,
            server_run_dir=str(self.run_dirs.base),
            server_worktree=str(self.repo),
            **extra,
        )

    async def run(self, argv, sid="s1", env=None, runner="gpu") -> Run:
        f, docs = self.files(sid), item_paths.docs_dir(self.run_dirs, self.wid)
        f.writable.mkdir(parents=True, exist_ok=True)
        docs.mkdir(parents=True, exist_ok=True)
        spec = self.spec(
            argv, sid, env, dirs=[str(docs), str(f.writable)], rw=[str(docs), str(f.writable)]
        )
        claim = await self.hub.claim(runner, [], spec.key, poll_s=0.02)
        entries = remote_files.entries(results=[], ro=[], rw_dirs=[docs, f.writable], rw_files=[])
        return await self.runs.launch(spec, claim, entries=entries, folder=f.folder)

    async def done(self, run: Run, timeout: float = 20) -> Run:
        await asyncio.wait_for(run.delivered.wait(), timeout)
        return run

    def tip(self) -> str:
        return git(self.repo, "rev-parse", f"refs/heads/{self.branch}").strip()

    async def connect(self) -> None:
        await attach(self.hub, self.machine)

    async def disconnect(self) -> None:
        await self.machine.server_t.close()
        with contextlib.suppress(protocol.Closed):
            await self.machine.task

    async def reconnect(self) -> None:
        await self.disconnect()
        await self.connect()

    async def restart_server(self) -> None:
        """The server process restarts: a new hub, runs and listener over the same files and
        database; the machine redials; whatever the old ones were doing is dropped."""
        await self.listener.close()
        await self.disconnect()
        self.hub = Hub("2.3.0", server_id="srv1")
        self.hub.apply(workers_file(gpu={"capacity": 2}))
        self.runs = Runs(
            self.hub, run_dirs=self.run_dirs, db=self.database, call=_recording_call(self.calls)
        )
        self.hub.runs = self.runs
        runs_mod.install(self.runs)
        self.listener = Listener(self.runs, self.run_dirs.base)
        await self.listener.start()
        await self.connect()


def _recording_call(calls: list):
    async def call(method, target, caller, *, content=b"", headers=None):
        calls.append((method, target, caller))
        return 200, {"content-type": "application/json"}, b'{"ok": true}'

    return call


async def make_rig(
    tmp_path,
    database,
    run_dirs,
    item_on,
    *,
    online: bool = True,
    hub_kwargs=None,
    workers=None,
    machine_kwargs: dict | None = None,
) -> Rig:
    repo = make_repo(tmp_path, "server")
    it = await item_on(v1_chain([v1_node("build")], repo=repo), "build", repo=repo, wid="rig1")
    branch = database.read(lambda c: store.branch_of(c, it.id))
    git(repo, "checkout", "-q", "-b", branch)
    calls: list = []
    hub = Hub("2.3.0", server_id="srv1", **(hub_kwargs or {}))
    hub.apply(workers or workers_file(gpu={"capacity": 2}))
    runs = Runs(hub, run_dirs=run_dirs, db=database, call=_recording_call(calls))
    hub.runs = runs
    runs_mod.install(runs)
    listener = Listener(runs, run_dirs.base)
    await listener.start()
    machine = Machine(tmp_path / "machine-home", kraft_version="2.3.0", **(machine_kwargs or {}))
    rig = Rig(
        hub,
        runs,
        machine,
        listener,
        repo,
        branch,
        git(repo, "rev-parse", "HEAD").strip(),
        it.id,
        run_dirs,
        database,
        calls,
    )
    if online:
        await rig.connect()
    return rig


async def send_job(
    server, tmp_path, argv, *, sid="s1", lfs_objects: Mapping[str, Path] | None = None, **extra
) -> JobSpec:
    """Play the server: send a machine one job (a fresh repo branch, an empty `files` blob, and
    an `lfs-<oid>` blob for each of `lfs_objects`, `oid: file`)."""
    repo = make_repo(tmp_path, "server")
    git(repo, "checkout", "-q", "-b", "kraft/x")
    head = git(repo, "rev-parse", "HEAD").strip()
    code, tgz = tmp_path / "code.bundle", tmp_path / "files.tgz"
    bundle.make(repo, "kraft/x", [], code)
    remote_files.pack([], tgz)
    spec = JobSpec(
        work_item_id="w1", session_id=sid, node_id="n", hook_point="n.main.a", argv=argv,
        cwd=str(repo), env={"KRAFT_SESSION_ID": f"w1/{sid}", **extra.pop("env", {})},
        repo_env={}, passthrough=[],
        setup_command=None, branch="kraft/x", head=head, base=head, identity={},
        server_run_dir=str(tmp_path / "run"), server_worktree=str(repo), **extra,
    )  # fmt: skip
    names = ["code", *(f"lfs-{oid}" for oid in lfs_objects or {}), "files"]
    await server.send({"t": "job", "id": spec.key, "spec": spec.to_frame(), "blobs": names})
    await blobs.send_blob(server, job=spec.key, name="code", source=code)
    for oid, path in (lfs_objects or {}).items():
        await blobs.send_blob(server, job=spec.key, name=f"lfs-{oid}", source=path)
    await blobs.send_blob(server, job=spec.key, name="files", source=tgz)
    return spec


def lfs_pointer(body: bytes) -> tuple[str, str]:
    """`(oid, pointer text)` of an LFS object holding `body`."""
    oid = hashlib.sha256(body).hexdigest()
    return oid, f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(body)}\n"


def lfs_script(
    body: bytes,
    *,
    stored: bytes | None = None,
    store: bool = True,
    pointer: bool = True,
    under: str = "",
) -> str:
    """Python source for a job that puts an LFS object for `body` in its copy's store and commits
    a pointer to it (`b.bin`). `stored` writes other bytes under the object's name, `store=False`
    leaves the object out, `pointer=False` commits a note instead of a pointer, `under` puts the
    `.gitattributes` and `b.bin` in that directory."""
    oid, text = lfs_pointer(body)
    return f"""
import subprocess
from pathlib import Path
from kraft.remote import lfs
if {store!r}:
    p = lfs.object_path(lfs.store(Path(".")), {oid!r})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes({body if stored is None else stored!r})
Path({under!r}).mkdir(exist_ok=True)
Path({under!r}, ".gitattributes").write_text({LFS_ATTRIBUTES!r})
Path({under!r}, "b.bin").write_text({text if pointer else "a note, no pointer"!r})
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-qm", "lfs"], check=True)
"""


LFS_ATTRIBUTES = "*.bin filter=lfs -text\n"


def fake_git_lfs(monkeypatch) -> None:
    """A machine that says it has git-lfs without having it: nothing runs the filter. A real
    git-lfs would be called by `filter.lfs.required` on every `git add` of a matching file."""
    from kraft.remote import lfs

    monkeypatch.setattr(lfs, "available", lambda: True)
    monkeypatch.setattr(lfs, "configure", lambda bare: None)
    monkeypatch.setattr(lfs, "checkout", lambda root: None)


def stop_daemon(home: Path) -> None:
    """End the machine daemon a test's `serve --stdio` started under `home`."""
    from kraft.remote import daemon

    with contextlib.suppress(FileNotFoundError, ProcessLookupError, ValueError):
        pid = (daemon.socket_path(home).parent / daemon.PID_FILE).read_text()
        os.kill(int(pid), signal.SIGTERM)
