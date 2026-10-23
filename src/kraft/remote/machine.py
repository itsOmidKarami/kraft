"""The machine's side of a connection: `kraft admin worker serve`.

Two ways in. Dial-in: this process opens a WebSocket to the server and keeps
redialling. `--stdio`: the server ran `ssh host kraft admin worker serve
--stdio`, and the protocol is this process's stdin and stdout, relayed to the
machine daemon (`kraft.remote.daemon`) that holds the jobs."""

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

from kraft import item_paths, paths
from kraft.remote import blobs, files, protocol
from kraft.remote.forwarder import Forwarder
from kraft.remote.jobs import JobRunner, JobSpec, job_dir
from kraft.remote.paths import PathMap
from kraft.remote.transport import FRAME_LIMIT, WebSocketTransport
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


def _spawn_daemon() -> None:
    """Start the machine daemon detached from this ssh session, its output in `daemon.log`."""
    remote = paths.kraft_home() / "remote"
    remote.mkdir(parents=True, exist_ok=True)
    with open(remote / "daemon.log", "ab", opener=paths.private) as log:
        subprocess.Popen(
            [sys.executable, "-m", "kraft", "admin", "worker", "serve", "--daemon"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )


async def run_stdio(*, kraft_version: str, stdin=None, stdout=None) -> None:
    """The ssh side: copy the protocol between this process's stdin and stdout and
    the machine daemon, which keeps the jobs when ssh drops. `stdout` is the binary
    file the protocol is written to, which is not `sys.stdout`: anything else this
    process prints must not corrupt it."""
    from kraft.remote import daemon

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=FRAME_LIMIT)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), stdin or sys.stdin)
    w_transport, w_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, stdout or sys.stdout
    )
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
    await daemon.relay(
        paths.kraft_home(),
        kraft_version=kraft_version,
        stdin=reader,
        stdout=writer,
        spawn=_spawn_daemon,
    )


class _Job:
    def __init__(self, spec: JobSpec, wanted: set[str]) -> None:
        self.spec, self.wanted = spec, wanted
        self.got: dict[str, Path] = {}
        self.state = "receiving"  # receiving | running | finished
        self.started = False
        self.runner: JobRunner | None = None
        self.stopped: str | None = None  # why the machine ended it without the server
        self.task: asyncio.Task | None = None
        self.sent: list[tuple[str, Path]] = []  # blobs handed in, for a resend
        self.final: dict | None = None  # the `finished` or `rejected` frame


class Machine:
    """What a machine does with a connection: it keeps its jobs, working copies and `delivered`
    bookkeeping across connections, and `serve` runs once per connection."""

    def __init__(
        self,
        home: Path,
        *,
        kraft_version: str,
        kill_grace_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        give_up_poll_s: float = 1.0,
    ) -> None:
        self.home, self.kraft_version, self._grace = Path(home), kraft_version, kill_grace_s
        # monotonic: it does not advance while a laptop sleeps, so neither does the wait
        self._clock, self._give_up_poll = clock, give_up_poll_s
        self._give_up_s: float | None = (
            None  # the server's `give_up_after`, from its latest welcome
        )
        self._watchdog: asyncio.Task | None = None
        self._t: protocol.Transport | None = None
        self._area: Path = self.home / "remote" / "default"
        self._sink: blobs.BlobSink | None = None
        self._jobs: dict[str, _Job] = {}
        self._api: dict[str, asyncio.Future] = {}
        self._api_sent: dict[str, protocol.Transport] = {}  # rid -> the connection it went out on
        self._rid = itertools.count(1)
        self._item_locks: dict[
            str, asyncio.Lock
        ] = {}  # item -> preparing its copy, one job at a time
        self._delivered: dict[str, list[str]] = {}  # item -> tips the server has already taken
        self._background: set[asyncio.Task] = set()

    def busy(self) -> bool:
        """Does it hold a job: running, or finished and not yet taken by the server?"""
        return bool(self._jobs)

    async def serve(self, t, welcome: dict) -> None:
        """Until the connection ends (raises `protocol.Closed`)."""
        self._area = self.home / "remote" / str(welcome.get("server") or "default")
        self._area.mkdir(parents=True, exist_ok=True)
        self._sink = blobs.BlobSink(self._area / "blobs")
        self._give_up_s = welcome.get("give_up_after_s")
        self._t = t
        self._stop_watchdog()
        try:
            await protocol.pump(t, on_frame=self._on_frame)
        finally:
            if self._t is t:
                self._t = None
                if self._give_up_s is not None and self._running():
                    self._watchdog = asyncio.create_task(self._give_up(self._give_up_s))
            for rid in [r for r, sent_on in self._api_sent.items() if sent_on is t]:
                del self._api_sent[rid]  # answered on the connection that is gone: it never will be
                if (fut := self._api.pop(rid, None)) is not None and not fut.done():
                    fut.set_result(_DROPPED)
            for key in [k for k, j in self._jobs.items() if j.state == "receiving"]:
                del self._jobs[key]  # a half-received job: the server sends it again

    def _running(self) -> list[_Job]:
        return [j for j in self._jobs.values() if j.state == "running"]

    def _stop_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    async def _give_up(self, after_s: float) -> None:
        """No connection for `after_s` (by `clock`): stop every job still running."""
        since = self._clock()
        while self._clock() - since < after_s:
            await asyncio.sleep(self._give_up_poll)
        why = f"kraft: stopped on this machine after {_duration(after_s)} without Kraft\n"
        for job in self._running():
            job.stopped = why
            if job.runner is not None:
                with open(job.runner.log_path, "ab") as log:
                    log.write(why.encode())
                job.runner.signal("KILL")

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
            self._api_sent.pop(frame["rid"], None)
            if (fut := self._api.pop(frame["rid"], None)) is not None and not fut.done():
                fut.set_result(frame)
        elif kind == "forget":
            await asyncio.to_thread(self._forget_items, [frame["item"]])
        elif kind == "keep":
            kept = set(frame["items"])
            await asyncio.to_thread(
                lambda: self._forget_items([w for w in self._held_items() if w not in kept])
            )
        elif kind == "sync":
            for item in frame["jobs"]:
                self._spawn(self._resync(item["id"], int(item["offset"])))

    def _held_items(self) -> set[str]:
        """Every work item this machine keeps something of: a copy, a job folder, a job."""
        held = {j.spec.work_item_id for j in self._jobs.values()}
        for parent, split in (("items", None), ("jobs", "__")):
            if (folder := self._area / parent).is_dir():
                held |= {p.name.split(split)[0] if split else p.name for p in folder.iterdir()}
        return held

    def _forget_items(self, items: list[str]) -> None:
        """Remove what the machine keeps of each item (its copy, its job folders, the files that
        travelled), except an item with a job that has not finished: that one is asked about again
        on the next connect."""
        for wid in items:
            if any(
                j.spec.work_item_id == wid and j.state != "finished" for j in self._jobs.values()
            ):
                continue
            for key in [k for k, j in self._jobs.items() if j.spec.work_item_id == wid]:
                del self._jobs[key]
            self._delivered.pop(wid, None)
            self._item_locks.pop(wid, None)
            doomed = [
                self._area / "items" / wid,
                self._area / "run" / "items" / wid,
                *(self._area / "jobs").glob(f"{wid}__*"),
                *(self._area / "blobs").glob(f"{blobs.safe(wid)}_*"),
            ]
            for path in doomed:
                if path.is_dir() and not path.is_symlink():
                    item_paths.remove_tree(path)
                else:
                    path.unlink(missing_ok=True)

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
                why = getattr(exc, "stderr", None) or exc
                await self._reject(job, "failed", f"the machine could not prepare this step: {why}")
                return
            job.started = True
        if job.stopped is not None:  # given up on while it was being prepared
            await self._reject(job, "failed", job.stopped.strip())
            return
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
        await self._finish(job, out, copy, mapper, pushed)

    def _co_tasks(self, job: _Job) -> list[_Job]:
        return [
            j
            for j in self._jobs.values()
            if j is not job
            and j.spec.work_item_id == job.spec.work_item_id
            and j.started
            and j.state == "running"
        ]

    async def _finish(self, job: _Job, out, copy: Copy, mapper, pushed: dict[str, str]) -> None:
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
        job.final = {
            "t": "finished",
            "id": spec.key,
            "code": out.code,
            "tip": tip,
            "pushed": changed,
            "capped": out.capped,
        }
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
        try:
            while True:
                t = self._t
                if t is None:
                    if job.state == "finished":
                        return _DROPPED  # the agent is gone; nobody is waiting for this
                    await asyncio.sleep(0.2)
                    continue
                self._api_sent[rid] = t  # before the send: the connection may end during it
                try:
                    await t.send(frame)
                except protocol.Closed:
                    self._api_sent.pop(rid, None)  # not sent: wait for the next connection
                    if fut.done():
                        fut = self._api[rid] = asyncio.get_running_loop().create_future()
                    await asyncio.sleep(0.2)
                    continue
                break
            answer = await fut
        finally:
            self._api.pop(rid, None)
            self._api_sent.pop(rid, None)
        if isinstance(answer, tuple):
            return answer
        return answer["status"], answer.get("headers", {}), base64.b64decode(answer.get("body", ""))

    async def call_server(self, job_id: str, method: str, path: str, body: bytes = b""):
        """The forwarder's code path, for a test to call without a socket."""
        return await self._forward(self._jobs[job_id], method, path, {}, body)


#: What a call gets when the connection drops after it was sent. The server may have acted on
#: it, so it is not resent.
_DROPPED = (
    503,
    {"content-type": "application/json"},
    b'{"detail": "the connection to Kraft dropped during this call; try again"}',
)


def _duration(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{seconds:g}s"
