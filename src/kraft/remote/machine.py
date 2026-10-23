"""The machine's side of a connection: `kraft admin worker serve`.

Two ways in. Dial-in: this process opens a WebSocket to the server and keeps
redialling. `--stdio`: the server ran `ssh host kraft admin worker serve
--stdio`, and the protocol is this process's stdin and stdout."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from kraft import paths
from kraft.remote import blobs, files, protocol
from kraft.remote.forwarder import Forwarder
from kraft.remote.jobs import JobRunner, JobSpec, job_dir
from kraft.remote.paths import PathMap
from kraft.remote.transport import FRAME_LIMIT, StreamTransport, WebSocketTransport
from kraft.remote.workdir import Copy

CONNECT_PATH = "/api/workers/connect"
_SCHEMES = {"http": "ws", "https": "wss"}


def dial_url(server: str) -> str:
    """The WebSocket URL for a server's address. A path prefix (a reverse proxy
    that serves Kraft under one) is kept."""
    parts = urlsplit(server)
    scheme = _SCHEMES.get(parts.scheme)
    if scheme is None or not parts.netloc:
        raise ValueError(f"server {server!r} must be an http:// or https:// address")
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}{CONNECT_PATH}"


def _say(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


async def dial_in(
    server: str,
    token: str,
    *,
    kraft_version: str,
    stop: asyncio.Event,
    log: Callable[[str], None] = _say,
    first_delay: float = 1.0,
    max_delay: float = 30.0,
) -> None:
    """Stay connected to `server` until `stop` is set: redial after a refusal,
    a drop or an unreachable server, backing off from `first_delay` to
    `max_delay` (reset after a connection that was welcomed). A refused token
    or version is retried too: an upgrade or a fresh token fixes it."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import WebSocketException

    url, delay = dial_url(server), first_delay
    machine = Machine(paths.kraft_home(), kraft_version=kraft_version)

    async def once() -> bool:
        """One connection; True when the server welcomed it."""
        try:
            async with connect(
                url, max_size=FRAME_LIMIT, ping_interval=None, open_timeout=10
            ) as ws:
                t = WebSocketTransport(ws.send, ws.recv, ws.close)
                welcome = await protocol.machine_handshake(
                    t, kraft_version=kraft_version, token=token, host=socket.gethostname()
                )
                log(f"connected as {welcome['name']}")
                try:
                    await machine.serve(t, welcome)
                except protocol.Closed as exc:
                    log(f"disconnected: {exc}")
                return True
        except protocol.Refused as exc:
            log(f"refused: {exc}")
        except (OSError, TimeoutError, WebSocketException, protocol.Closed) as exc:
            log(f"cannot connect: {exc or type(exc).__name__}")
        return False

    while not stop.is_set():
        attempt = asyncio.ensure_future(once())
        stopper = asyncio.ensure_future(stop.wait())
        await asyncio.wait({attempt, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if not attempt.done():
            attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)
            break
        stopper.cancel()
        delay = first_delay if attempt.result() else min(delay * 2, max_delay)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), delay)


async def run_stdio(*, kraft_version: str, stdin=None, stdout=None) -> None:
    """The ssh side: handshake with no token, then keep alive until ssh closes
    the pipe. `stdout` is the binary file the protocol is written to, which is
    not `sys.stdout`: anything else this process prints must not corrupt it."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=FRAME_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), stdin or sys.stdin)
    w_transport, w_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, stdout or sys.stdout
    )
    t = StreamTransport(reader, asyncio.StreamWriter(w_transport, w_protocol, reader, loop))
    try:
        machine = Machine(paths.kraft_home(), kraft_version=kraft_version)
        welcome = await protocol.machine_handshake(
            t, kraft_version=kraft_version, host=socket.gethostname()
        )
        await machine.serve(t, welcome)
    except (protocol.Closed, protocol.Refused):
        pass  # ssh closed the pipe, or the server said no: either way this process is done
    finally:
        await t.close()


class _Job:
    def __init__(self, spec: JobSpec, wanted: set[str]) -> None:
        self.spec, self.wanted = spec, wanted
        self.got: dict[str, Path] = {}
        self.state = "receiving"  # receiving | running | finished
        self.started = False
        self.runner: JobRunner | None = None
        self.task: asyncio.Task | None = None
        self.sent: list[tuple[str, Path]] = []  # blobs handed in, for a resend
        self.final: dict | None = None  # the `finished` or `rejected` frame


class Machine:
    """What a machine does with a connection: it keeps its jobs, working copies and `delivered`
    bookkeeping across connections, and `serve` runs once per connection."""

    def __init__(self, home: Path, *, kraft_version: str, kill_grace_s: float = 5.0) -> None:
        self.home, self.kraft_version, self._grace = Path(home), kraft_version, kill_grace_s
        self._t: protocol.Transport | None = None
        self._area: Path = self.home / "remote" / "default"
        self._sink: blobs.BlobSink | None = None
        self._jobs: dict[str, _Job] = {}
        self._api: dict[str, asyncio.Future] = {}
        self._rid = itertools.count(1)
        self._item_locks: dict[
            str, asyncio.Lock
        ] = {}  # item -> preparing its copy, one job at a time
        self._delivered: dict[str, list[str]] = {}  # item -> tips the server has already taken
        self._background: set[asyncio.Task] = set()

    async def serve(self, t, welcome: dict) -> None:
        """Until the connection ends (raises `protocol.Closed`)."""
        self._area = self.home / "remote" / str(welcome.get("server") or "default")
        self._area.mkdir(parents=True, exist_ok=True)
        self._sink = blobs.BlobSink(self._area / "blobs")
        self._t = t
        try:
            await protocol.pump(t, on_frame=self._on_frame)
        finally:
            if self._t is t:
                self._t = None
            for key in [k for k, j in self._jobs.items() if j.state == "receiving"]:
                del self._jobs[key]  # a half-received job: the server sends it again

    async def _emit(self, frame: dict) -> None:
        if self._t is None:
            raise protocol.Closed("not connected")
        await self._t.send(frame)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _on_frame(self, frame: dict) -> None:
        kind = frame.get("t")
        if kind == "have":
            wid = frame["item"]
            await self._emit(
                {
                    "t": "have",
                    "rid": frame["rid"],
                    "item": wid,
                    "commits": Copy(self._area / "items" / wid / "repo").commits(),
                }
            )
        elif kind == "job":
            job = _Job(JobSpec.from_frame(frame["spec"]), set(frame["blobs"]))
            self._jobs[job.spec.key] = job
            self._maybe_start(job)
        elif kind == "blob":
            if (path := self._sink.add(frame)) is not None and (
                job := self._jobs.get(frame["id"])
            ) is not None:
                job.got[frame["name"]] = path
                self._maybe_start(job)
        elif kind == "signal":
            if (job := self._jobs.get(frame["id"])) is not None and job.runner is not None:
                job.runner.signal(frame["sig"])
        elif kind == "delivered":
            self._jobs.pop(frame["id"], None)
        elif kind == "api_result":
            if (fut := self._api.pop(frame["rid"], None)) is not None and not fut.done():
                fut.set_result(frame)
        elif kind == "sync":
            for item in frame["jobs"]:
                self._spawn(self._resync(item["id"], int(item["offset"])))

    def _maybe_start(self, job: _Job) -> None:
        if job.state == "receiving" and job.wanted <= set(job.got):
            job.state = "running"
            job.task = self._spawn(self._run(job))

    async def _reject(self, job: _Job, outcome: str, reason: str) -> None:
        job.state = "finished"
        job.final = {"t": "rejected", "id": job.spec.key, "outcome": outcome, "reason": reason}
        with contextlib.suppress(protocol.Closed):
            await self._emit(job.final)

    async def _run(self, job: _Job) -> None:
        spec = job.spec
        copy = Copy(self._area / "items" / spec.work_item_id / "repo")
        pathmap = PathMap(
            ((spec.server_run_dir, str(self._area / "run")), (spec.server_worktree, str(copy.root)))
        )

        def mapper(name: str) -> Path:
            if not pathmap.covers("/" + name):
                raise files.FilesRefused(f"{name} is outside the paths this job may use")
            return pathmap.path("/" + name)

        async with self._item_locks.setdefault(
            spec.work_item_id, asyncio.Lock()
        ):  # co-tasks share one copy
            try:
                others = self._co_tasks(job)
                if not others:  # a co-task is using the copy: it is already at this head
                    await asyncio.to_thread(
                        copy.update, job.got.get("code"), branch=spec.branch, head=spec.head
                    )
                pushed = await asyncio.to_thread(
                    lambda: files.unpack(job.got["files"], mapper, keep_existing=bool(others))
                )
                for d in spec.dirs:
                    mapper(d.lstrip("/")).mkdir(parents=True, exist_ok=True)
            except (subprocess.CalledProcessError, OSError, files.FilesRefused) as exc:
                await self._reject(
                    job,
                    "failed",
                    f"the machine could not prepare this step: {getattr(exc, 'stderr', None) or exc}",
                )
                return
            job.started = True
        forwarder = Forwarder(lambda m, target, h, b: self._forward(job, m, target, h, b))
        port = await forwarder.start()
        job.runner = JobRunner(spec, area=self._area, api_port=port, kill_grace_s=self._grace)
        try:
            out = await job.runner.run(
                self._emit
            )  # raises Closed while offline: the runner waits for `sync`
        finally:
            await forwarder.close()
        if out.rejected is not None:
            await self._reject(job, out.outcome or "failed", out.rejected)
            return
        await self._finish(job, out.code, copy, mapper, pushed)

    def _co_tasks(self, job: _Job) -> list[_Job]:
        return [
            j
            for j in self._jobs.values()
            if j is not job
            and j.spec.work_item_id == job.spec.work_item_id
            and j.started
            and j.state == "running"
        ]

    async def _finish(
        self, job: _Job, code: int, copy: Copy, mapper, pushed: dict[str, str]
    ) -> None:
        spec = job.spec
        if not self._co_tasks(job):
            with contextlib.suppress(Exception):  # the sweep is best effort, as on the server
                await copy.sweep(
                    branch=spec.branch, base=spec.base, node_id=spec.node_id, identity=spec.identity
                )
        folder = job_dir(self._area, spec.key)
        tip = await asyncio.to_thread(
            copy.bundle_since,
            spec.branch,
            [spec.head, *self._delivered.get(spec.work_item_id, [])],
            folder / "commits.bundle",
        )
        entries = [files.Entry(Path(p), True) for p in spec.rw]
        changed = await asyncio.to_thread(
            files.changed, entries, mapper, pushed, folder / "out.tgz"
        )
        job.sent = ([("commits", folder / "commits.bundle")] if tip else []) + (
            [("out", folder / "out.tgz")] if changed else []
        )
        if tip:
            self._delivered.setdefault(spec.work_item_id, []).append(tip)
        job.final = {"t": "finished", "id": spec.key, "code": code, "tip": tip, "pushed": changed}
        job.state = "finished"
        await self._hand_in(job)

    async def _hand_in(self, job: _Job) -> None:
        with contextlib.suppress(protocol.Closed):
            for name, path in job.sent:
                await blobs.send_blob(self._t, job=job.spec.key, name=name, source=path)
            await self._emit(job.final)

    async def _resync(self, key: str, offset: int) -> None:
        job = self._jobs.get(key)
        with contextlib.suppress(protocol.Closed):
            if job is None:
                await self._emit(
                    {
                        "t": "rejected",
                        "id": key,
                        "outcome": "failed",
                        "reason": "the machine has no record of this session (it was restarted)",
                    }
                )
            elif job.state == "running" and job.runner is not None:
                await self._emit({"t": "started", "id": key})
                job.runner.rewind(offset)
            elif job.state == "finished" and job.final["t"] == "rejected":
                await self._emit(job.final)
            elif job.state == "finished" and job.runner is not None:
                await job.runner.replay(self._emit, offset)
                await self._hand_in(job)

    async def _forward(self, job: _Job, method: str, target: str, headers: dict, body: bytes):
        path, _, query = target.partition("?")
        rid = f"{job.spec.key}#{next(self._rid)}"
        fut = asyncio.get_running_loop().create_future()
        self._api[rid] = fut
        frame = {
            "t": "api",
            "id": job.spec.key,
            "rid": rid,
            "method": method,
            "path": path,
            "query": query,
            "headers": {"content-type": headers.get("content-type", "")},
            "body": base64.b64encode(body).decode(),
        }
        deadline = time.monotonic() + 60
        while True:
            try:
                await self._emit(frame)
                break
            except protocol.Closed:
                if time.monotonic() > deadline:
                    self._api.pop(rid, None)
                    return _TIMED_OUT
                await asyncio.sleep(0.2)
        try:
            answer = await asyncio.wait_for(fut, 60)
        except TimeoutError:
            self._api.pop(rid, None)
            return _TIMED_OUT
        return answer["status"], answer.get("headers", {}), base64.b64decode(answer.get("body", ""))

    async def call_server(self, job_id: str, method: str, path: str, body: bytes = b""):
        """The forwarder's code path, for a test to call without a socket."""
        return await self._forward(self._jobs[job_id], method, path, {}, body)


_TIMED_OUT = (
    504,
    {"content-type": "application/json"},
    b'{"detail": "Kraft did not answer in time"}',
)
