"""The server's side of a step that runs on a machine: it sends the job, relays the log
home, answers the agent's API calls, and takes the result back in before it says the step is
over. The stand-in (remote/standin.py) is the step's process on this machine; this is where
its output comes from."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from kraft import events, ids
from kraft.remote import blobs, bundle, files, protocol
from kraft.remote.hub import Claim
from kraft.remote.jobs import JobSpec
from kraft.vocab.events import RemoteEvent, SandboxEvent
from kraft.worker import callback

logger = logging.getLogger(__name__)
REMOTE_CONFIG_ERROR = 78
_current: Runs | None = None


def install(runs: Runs | None) -> None:
    global _current
    _current = runs


def current() -> Runs | None:
    return _current


@dataclass
class Run:
    spec: JobSpec
    claim: Claim
    entries: list[files.Entry]
    spool: Path
    folder: Path
    pushed: dict[str, str] = field(default_factory=dict)
    offset: int = 0
    state: str = "preparing"  # preparing | sent | running | delivering | finished
    exit_code: int | None = None
    delivered: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    received: dict[str, Path] = field(default_factory=dict)
    sink: blobs.BlobSink | None = None


class Runs:
    def __init__(self, hub, *, run_dirs, db, call) -> None:
        self.hub, self._run_dirs, self._db, self._call = hub, run_dirs, db, call
        self._runs: dict[str, Run] = {}
        self._asks: dict[str, asyncio.Future] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._synced: dict[str, str] = {}
        self._killed: dict[str, set[str]] = {}
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def find(self, work_item_id: str, session_id: str) -> Run | None:
        return self._runs.get(ids.session_ref(work_item_id, session_id))

    async def launch(self, spec, claim, *, entries, folder) -> Run:
        folder.mkdir(parents=True, exist_ok=True)
        spool = folder / "remote.log"
        spool.write_bytes(b"")
        run = Run(spec, claim, list(entries), spool, folder, sink=blobs.BlobSink(folder / "in"))
        self._runs[spec.key] = run
        self._spawn(self._drive(run))
        return run

    async def _drive(self, run: Run) -> None:
        while run.exit_code is None:
            try:
                await self._send_job(run)
                run.state = "sent"
                return
            except protocol.Closed:
                await self.hub.wait_online(run.claim.machine)

    async def _ask(self, machine: str, frame: dict) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._asks[frame["rid"]] = fut
        try:
            await self.hub.transport(machine).send(frame)
            return await asyncio.wait_for(fut, 30)
        except TimeoutError as exc:
            raise protocol.Closed("the machine did not answer") from exc
        finally:
            self._asks.pop(frame["rid"], None)

    async def _send_job(self, run: Run) -> None:
        spec, machine = run.spec, run.claim.machine
        worktree = Path(spec.server_worktree)
        reply = await self._ask(
            machine, {"t": "have", "rid": f"have:{spec.key}", "item": spec.work_item_id}
        )
        known = await asyncio.to_thread(bundle.usable, worktree, reply.get("commits", []))
        code = run.folder / "code.bundle"
        has_code = await asyncio.to_thread(bundle.make, worktree, spec.branch, known, code)
        archive = run.folder / "files.tgz"
        run.pushed = await asyncio.to_thread(files.pack, run.entries, archive)
        t = self.hub.transport(machine)
        names = (["code"] if has_code else []) + ["files"]
        await t.send({"t": "job", "id": spec.key, "spec": spec.to_frame(), "blobs": names})
        if has_code:
            await blobs.send_blob(t, job=spec.key, name="code", source=code)
        await blobs.send_blob(t, job=spec.key, name="files", source=archive)
        self._save(run)

    def _save(self, run: Run) -> None:
        (run.folder / "remote-run.json").write_text(
            json.dumps(
                {
                    "spec": run.spec.to_frame(),
                    "machine": run.claim.machine,
                    "pushed": run.pushed,
                    "entries": [{"path": str(e.path), "rw": e.rw} for e in run.entries],
                }
            )
        )

    # ── frames from the machine ──

    async def on_frame(self, machine: str, frame: dict) -> None:
        kind = frame.get("t")
        if kind == "have":
            if (fut := self._asks.get(frame.get("rid"))) is not None and not fut.done():
                fut.set_result(frame)
            return
        run = self._runs.get(frame.get("id"))
        if run is None or run.claim.machine != machine:
            return  # a run this server does not hold on that machine
        if kind == "started":
            run.state = "running"
        elif kind == "log":
            await self._log(run, frame)
        elif kind == "blob":
            if (path := run.sink.add(frame)) is not None:
                run.received[frame["name"]] = path
        elif kind == "rejected":
            self._spawn(self._reject(run, frame))
        elif kind == "finished":
            self._spawn(self._deliver(run, frame))
        elif kind == "api":
            self._spawn(self._api(run, frame))

    async def _log(self, run: Run, frame: dict) -> None:
        offset, data = frame["offset"], base64.b64decode(frame["data"])
        if offset > run.offset:
            await self._sync_one(run)
            return
        data = data[run.offset - offset :]
        if data:
            with open(run.spool, "ab") as fh:
                fh.write(data)
            run.offset += len(data)
            await self._notify(run)

    async def _notify(self, run: Run) -> None:
        async with run.changed:
            run.changed.notify_all()

    def _say(self, run: Run, line: str) -> None:
        with open(run.spool, "ab") as fh:
            fh.write(line.encode())
        run.offset += len(line.encode())

    async def _finish(self, run: Run, code: int) -> None:
        run.exit_code, run.state = code, "finished"
        self.hub.release(run.claim)
        with contextlib.suppress(protocol.Closed):
            await self.hub.transport(run.claim.machine).send({"t": "delivered", "id": run.spec.key})
        run.delivered.set()
        await self._notify(run)
        # long enough for the stand-in to read the exit
        asyncio.get_running_loop().call_later(600, self._runs.pop, run.spec.key, None)

    async def _reject(self, run: Run, frame: dict) -> None:
        self._say(run, f"kraft: {frame.get('reason', 'the machine could not start this step')}\n")
        await self._finish(
            run, REMOTE_CONFIG_ERROR if frame.get("outcome") == "config_error" else 1
        )

    async def _deliver(self, run: Run, frame: dict) -> None:
        if run.state in ("delivering", "finished"):
            return
        run.state = "delivering"
        spec, code = run.spec, int(frame.get("code", 1))
        lock = self._locks.setdefault(spec.work_item_id, asyncio.Lock())
        async with lock:
            try:
                if frame.get("tip"):
                    await self._publish(run)
                if frame.get("pushed") and "out" in run.received:
                    applied = await asyncio.to_thread(
                        files.apply_returned,
                        run.received["out"],
                        run.entries,
                        run.pushed,
                        run.folder / "conflicts",
                    )
                    for path, kept in zip(applied.conflicts, applied.kept, strict=True):
                        await self._event(
                            spec,
                            RemoteEvent.DOCS_CONFLICT,
                            {"session_id": spec.session_id, "file": str(path), "kept": str(kept)},
                        )
            except (bundle.BundleRefused, files.FilesRefused) as exc:
                where = run.claim.machine
                self._say(
                    run, f"\nkraft: the result of this step on {where} was not taken: {exc}\n"
                )
                code = code or 1
        await self._finish(run, code)

    async def _publish(self, run: Run) -> None:
        spec, repo = run.spec, Path(run.spec.server_worktree)
        old = self._synced.get(spec.work_item_id, spec.head)
        staging = f"refs/kraft/remote/{spec.work_item_id}/{spec.session_id}"
        commits = run.received["commits"]
        new = await asyncio.to_thread(
            bundle.fetch, repo, commits, branch=spec.branch, base=old, into_ref=staging
        )
        got = await asyncio.to_thread(
            bundle.publish, repo, spec.branch, old=old, new=new, key=spec.key
        )
        await asyncio.to_thread(bundle._git, repo, "update-ref", "-d", staging)
        if got.ok:
            self._synced[spec.work_item_id] = new
        else:
            await self._event(
                spec,
                SandboxEvent.BRANCH_NOT_SYNCED,
                {"session_id": spec.session_id, "branch": spec.branch, "reason": got.reason},
            )

    async def _event(self, spec: JobSpec, type_, payload: dict) -> None:
        await self._db.write(
            lambda c: events.append(c, spec.work_item_id, type_, payload, node_id=spec.node_id)
        )

    async def _api(self, run: Run, frame: dict) -> None:
        spec = run.spec
        scope = callback.SessionScope(spec.session_id, spec.work_item_id)
        method, path = frame["method"], frame["path"]
        if not callback.allowed(scope, method, path, thread_owner=self._thread_owner):
            status, headers = 403, {"content-type": "application/json"}
            body = b'{"detail": "not this session\'s to call"}'
        else:
            target = path + (f"?{frame['query']}" if frame.get("query") else "")
            status, headers, body = await self._call(
                method,
                target,
                spec.key,
                content=base64.b64decode(frame.get("body", "")),
                headers={
                    k: v for k, v in (frame.get("headers") or {}).items() if k == "content-type"
                },
            )
        with contextlib.suppress(protocol.Closed):
            await self.hub.transport(run.claim.machine).send(
                {
                    "t": "api_result",
                    "rid": frame["rid"],
                    "status": status,
                    "headers": {k: v for k, v in headers.items() if k == "content-type"},
                    "body": base64.b64encode(body).decode(),
                }
            )

    def _thread_owner(self, tid: str):
        from kraft import store

        row = self._db.read(lambda c: store.thread_row(c, tid))
        return row["work_item_id"] if row is not None else None

    # ── from the stand-in / the hub ──

    async def signal(self, run: Run, sig: str) -> None:
        try:
            await self.hub.transport(run.claim.machine).send(
                {"t": "signal", "id": run.spec.key, "sig": sig}
            )
        except protocol.Closed:
            if sig == "KILL" and run.exit_code is None:
                self._killed.setdefault(run.claim.machine, set()).add(run.spec.key)
                self._say(run, f"\nkraft: cancelled while {run.claim.machine} was offline\n")
                self._runs.pop(run.spec.key, None)
                self.hub.release(run.claim)
                run.exit_code, run.state = 143, "finished"
                run.delivered.set()
                await self._notify(run)

    async def wait_delivered(self, run: Run, timeout: float) -> bool:
        try:
            await asyncio.wait_for(run.delivered.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def _sync_one(self, run: Run) -> None:
        with contextlib.suppress(protocol.Closed):
            await self.hub.transport(run.claim.machine).send(
                {"t": "sync", "jobs": [{"id": run.spec.key, "offset": run.offset}]}
            )

    async def on_connect(self, machine: str) -> None:
        mine = [
            r for r in self._runs.values() if r.claim.machine == machine and r.state != "preparing"
        ]
        with contextlib.suppress(protocol.Closed):
            t = self.hub.transport(machine)
            for key in self._killed.pop(machine, ()):
                await t.send({"t": "signal", "id": key, "sig": "KILL"})
            await t.send(
                {"t": "sync", "jobs": [{"id": r.spec.key, "offset": r.offset} for r in mine]}
            )
