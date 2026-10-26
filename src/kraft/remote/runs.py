"""The server's side of a step that runs on a machine: it sends the job, relays the log
home, answers the agent's API calls, and takes the result back in before it says the step is
over. The stand-in (remote/standin.py) is the step's process on this machine; this is where
its output comes from."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from kraft import events, ids, item_paths, store
from kraft.remote import blobs, bundle, files, lfs, protocol
from kraft.remote.hub import Claim
from kraft.remote.jobs import JobSpec
from kraft.vocab.events import RemoteEvent, SandboxEvent
from kraft.worker import callback

logger = logging.getLogger(__name__)
REMOTE_CONFIG_ERROR = 78
#: The machine was out of reach for `give_up_after`: the step ends as a vanished process does.
REMOTE_GAVE_UP = 79
_current: Runs | None = None


def asgi_call(app):
    """`Runs`'s `call`: a worker API request made in-process, as the session a machine's connection
    holds, with the daemon's own token."""
    import httpx

    async def call(method, target, caller, *, content=b"", headers=None):
        h = {
            **(headers or {}),
            "Authorization": f"Bearer {app.state.mcp_token}",
            "X-Kraft-Session-Id": caller,
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            r = await asyncio.wait_for(c.request(method, target, headers=h, content=content), 60)
        return r.status_code, dict(r.headers), r.content

    return call


def install(runs: Runs | None) -> None:
    global _current
    _current = runs


def current() -> Runs | None:
    return _current


@dataclass(frozen=True)
class Reserved:
    """A machine's answer to `reserve`: `ok`, and its own limit (None: it sets none)."""

    ok: bool
    capacity: int | None = None


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
    capped: dict | None = None  # the cap the machine stopped the command at
    oom: dict | None = (
        None  # `{"memory", "confirmed"}`: its sandbox killed it under its memory limit
    )
    abandoned: bool = False  # given up on: whatever the machine hands in later is thrown away


class Runs:
    def __init__(self, hub, *, run_dirs, db, call) -> None:
        self.hub, self._run_dirs, self._db, self._call = hub, run_dirs, db, call
        self._runs: dict[str, Run] = {}
        self._asks: dict[str, asyncio.Future] = {}
        self._rids = itertools.count(1)
        # machine -> keys of steps it holds a slot for that this server has not sent yet
        self._reserved: dict[str, set[str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._synced: dict[str, str] = {}
        self._killed: dict[str, set[str]] = {}
        self._tasks: set[asyncio.Task] = set()
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def owns(self, key: str) -> bool:
        """Whether a run for step `key` is live: it holds the step's claim until it finishes."""
        run = self._runs.get(key)
        return run is not None and run.exit_code is None

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
        machine = run.claim.machine
        # placement keys on the connected repo's path; the worktree is what is actually sent
        if protocol.LFS not in self.hub.caps(machine) and await asyncio.to_thread(
            lfs.uses_lfs, run.spec.server_worktree
        ):
            await self._reject(run, {"outcome": "config_error", "reason": lfs.no_git_lfs(machine)})
            return
        while run.exit_code is None:
            try:
                await self._send_job(run)
                run.state = "sent"
                self._reserved.get(run.claim.machine, set()).discard(run.spec.key)  # the job has it
                return
            except protocol.Closed:
                await self.hub.wait_online(run.claim.machine)

    async def _ask(self, machine: str, frame: dict, *, timeout: float = 30) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._asks[frame["rid"]] = fut
        try:
            await self.hub.transport(machine).send(frame)
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError as exc:
            raise protocol.Closed("the machine did not answer") from exc
        finally:
            self._asks.pop(frame["rid"], None)

    async def reserve(self, machine: str, key: str, *, timeout: float = 30) -> Reserved:
        """Ask `machine` to hold a slot for `key`. A refusal says the machine's limit; a machine
        that does not answer, or whose connection is lost, reads as `ok: False` with no limit,
        not as full: the caller gives the slot back (`unreserve`) and waits for the machine."""
        rid = f"reserve:{key}:{next(self._rids)}"
        self._reserved.setdefault(machine, set()).add(key)  # held, as far as this side knows
        try:
            reply = await self._ask(
                machine, {"t": "reserve", "rid": rid, "id": key}, timeout=timeout
            )
        except protocol.Closed:
            return Reserved(False)
        ok = reply.get("ok") is True
        if not ok:
            self._reserved[machine].discard(key)
        return Reserved(ok, protocol.valid_capacity(reply.get("capacity")))

    async def unreserve(self, machine: str, key: str) -> None:
        """Give back the slot `reserve` held for `key`, if this server still holds it. Best
        effort: a machine that is away drops it on its next `reserves`."""
        held = self._reserved.get(machine)
        if held is None or key not in held:
            return
        held.discard(key)
        with contextlib.suppress(protocol.Closed):
            await self.hub.transport(machine).send({"t": "unreserve", "id": key})

    def unreserve_soon(self, machine: str, key: str) -> None:
        """`unreserve`, from code that cannot await (a `finally` of a cancelled task)."""
        if key in self._reserved.get(machine, ()):
            self._spawn(self.unreserve(machine, key))

    async def _send_job(self, run: Run) -> None:
        spec, machine = run.spec, run.claim.machine
        worktree = Path(spec.server_worktree)
        ask = {"t": "have", "rid": f"have:{spec.key}", "item": spec.work_item_id}
        wanted: list[str] = []
        if await asyncio.to_thread(lfs.uses_lfs, worktree):
            wanted = await asyncio.to_thread(self._lfs_to_send, worktree, spec.head)
            ask["lfs"] = wanted
        reply = await self._ask(machine, ask)
        held = reply.get("lfs")
        held = (
            set(held) if isinstance(held, list) and all(isinstance(o, str) for o in held) else set()
        )
        wanted = [oid for oid in wanted if oid not in held]
        known = await asyncio.to_thread(bundle.usable, worktree, reply.get("commits", []))
        code = run.folder / "code.bundle"
        has_code = await asyncio.to_thread(bundle.make, worktree, spec.branch, known, code)
        archive = run.folder / "files.tgz"
        run.pushed = await asyncio.to_thread(files.pack, run.entries, archive)
        t = self.hub.transport(machine)
        names = (["code"] if has_code else []) + [f"lfs-{oid}" for oid in wanted] + ["files"]
        await t.send({"t": "job", "id": spec.key, "spec": spec.to_frame(), "blobs": names})
        if has_code:
            await blobs.send_blob(t, job=spec.key, name="code", source=code)
        store = lfs.store(worktree)
        for oid in wanted:
            await blobs.send_blob(
                t, job=spec.key, name=f"lfs-{oid}", source=lfs.object_path(store, oid)
            )
        await blobs.send_blob(t, job=spec.key, name="files", source=archive)
        self._save(run)

    @staticmethod
    def _lfs_to_send(worktree: Path, head: str) -> list[str]:
        """The objects `head` checks out that the server's store holds."""
        have = set(lfs.held(lfs.store(worktree)))
        return sorted(oid for oid in lfs.tree_pointers(worktree, head) if oid in have)

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
        if kind == "capacity":
            self.hub.set_machine_capacity(machine, protocol.valid_capacity(frame.get("capacity")))
            return
        if kind in ("have", "reserved"):
            if (fut := self._asks.get(frame.get("rid"))) is not None and not fut.done():
                fut.set_result(frame)
            return
        run = self._runs.get(frame.get("id"))
        if run is None or run.claim.machine != machine:
            return  # a run this server does not hold on that machine
        if run.abandoned:
            if kind in ("finished", "rejected"):  # the machine may drop the job now
                with contextlib.suppress(protocol.Closed):
                    await self.hub.transport(machine).send({"t": "delivered", "id": run.spec.key})
                    self._runs.pop(run.spec.key, None)
            return
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
        elif kind == "event":
            self._spawn(self._machine_event(run, frame))

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
        await self.unreserve(run.claim.machine, run.spec.key)  # a job never sent has no use for it
        with contextlib.suppress(protocol.Closed):
            await self.hub.transport(run.claim.machine).send({"t": "delivered", "id": run.spec.key})
        run.delivered.set()
        await self._notify(run)
        if not run.abandoned:
            # long enough for the stand-in to read the exit; a given-up run stays until the
            # machine, however late it returns, hands in and is told `delivered`
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
        run.capped = frame.get("capped")
        run.oom = frame.get("oom")
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
        old = spec.head
        # A co-task sent the same head compares against the tip the first one moved to; a tip the
        # branch has since left (a local commit, a rebase) is not this step's base.
        if (synced := self._synced.get(spec.work_item_id)) and synced != old:
            ancestor = await asyncio.to_thread(
                bundle._git, repo, "merge-base", "--is-ancestor", old, synced
            )
            if ancestor.returncode == 0:
                old = synced
        staging = f"refs/kraft/remote/{spec.work_item_id}/{spec.session_id}"
        commits = run.received["commits"]
        try:
            new = await asyncio.to_thread(
                bundle.fetch, repo, commits, branch=spec.branch, base=old, into_ref=staging
            )
            await asyncio.to_thread(self._take_lfs, run, repo, old, new)
            got = await asyncio.to_thread(
                bundle.publish, repo, spec.branch, old=old, new=new, key=spec.key
            )
        finally:
            await asyncio.to_thread(bundle._git, repo, "update-ref", "-d", staging)
        if got.ok:
            self._synced[spec.work_item_id] = new
        else:
            await self._event(
                spec,
                SandboxEvent.BRANCH_NOT_SYNCED,
                {"session_id": spec.session_id, "branch": spec.branch, "reason": got.reason},
            )

    @staticmethod
    def _take_lfs(run: Run, repo: Path, old: str, new: str) -> None:
        """Store the LFS objects the machine returned, once every one is shown to be what a new
        pointer names. Refuses the result when one is not, or a new pointer's object is nowhere."""
        got = {n.removeprefix("lfs-"): p for n, p in run.received.items() if n.startswith("lfs-")}
        if not got and not lfs.uses_lfs(repo):
            return
        expected = lfs.new_pointers(repo, new, [old])
        if unnamed := sorted(set(got) - set(expected)):
            raise bundle.BundleRefused(
                f"the machine returned an LFS object no new pointer names: {unnamed[0][:80]}"
            )
        store = lfs.store(repo)
        if unsent := sorted(
            oid for oid in set(expected) - set(got) if not lfs.object_path(store, oid).is_file()
        ):
            raise bundle.BundleRefused(
                f"the machine's commits point to LFS object {unsent[0]}, which it did not send"
            )
        try:
            lfs.take(store, {oid: (path, expected[oid]) for oid, path in got.items()})
        except lfs.LfsRefused as exc:
            raise bundle.BundleRefused(str(exc)) from exc

    async def _event(self, spec: JobSpec, type_, payload: dict) -> None:
        await self._db.write(
            lambda c: events.append(c, spec.work_item_id, type_, payload, node_id=spec.node_id)
        )

    #: What a machine may add to an item's history: the sandbox events it sees. Its memory-limit
    #: kill arrives on `finished` instead, for the server to record as a local one is.
    _MACHINE_EVENTS = frozenset(SandboxEvent) - {SandboxEvent.OOM_KILLED}

    async def _machine_event(self, run: Run, frame: dict) -> None:
        type_, payload = frame.get("type"), frame.get("payload")
        if type_ not in self._MACHINE_EVENTS or not isinstance(payload, dict):
            self._say(
                run, f"\nkraft: {run.claim.machine} sent an event of a kind it may not: {type_!r}\n"
            )
            return
        await self._event(run.spec, SandboxEvent(type_), payload)

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
                await self.unreserve(run.claim.machine, run.spec.key)
                run.exit_code, run.state = 143, "finished"
                run.delivered.set()
                await self._notify(run)

    async def watch(self, poll_s: float = 30.0) -> None:
        """Forever: give up on every run whose machine was out of reach for its `give_up_after`.
        Only time the server was awake counts (`Hub.offline_for`); `never` is never given up on."""
        while True:
            for run in [r for r in self._runs.values() if r.exit_code is None]:
                worker = self.hub.file.workers.get(run.claim.machine)
                gone = self.hub.offline_for(run.claim.machine)
                if worker is None or gone is None or worker.give_up_s is None:
                    continue
                if gone >= worker.give_up_s:
                    await self.give_up(
                        run,
                        f"{run.claim.machine} has been unreachable for {worker.give_up_after}",
                        after=worker.give_up_after,
                    )
            await asyncio.sleep(poll_s)

    async def give_up(self, run: Run, why: str, *, after: str) -> None:
        """End a run whose machine is not coming back in time: its session becomes `unknown`, the
        machine is told to stop it when it returns, and what it hands in then is ignored."""
        if run.exit_code is not None:
            return
        machine, spec = run.claim.machine, run.spec
        self._say(run, f"\nkraft: {why}; this step is given up\n")
        await self._event(
            spec,
            RemoteEvent.STEP_GIVEN_UP,
            {"session_id": spec.session_id, "machine": machine, "after": after},
        )
        self._killed.setdefault(machine, set()).add(spec.key)
        run.abandoned = True
        await self._finish(run, REMOTE_GAVE_UP)

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

    async def recover(self, work_item_id: str, session_id: str) -> Run | None:
        """The run of a step this process did not start: a stand-in that survived a restart, or one
        reattach respawned. Rebuilt from what `launch` saved and what the spool holds; None when
        the session is not a running remote one."""
        key = ids.session_ref(work_item_id, session_id)
        if key in self._runs:
            return self._runs[key]
        row = self._db.read(
            lambda c: c.execute(
                "SELECT status, runner FROM worker_sessions WHERE work_item_id = ? AND id = ?",
                (work_item_id, session_id),
            ).fetchone()
        )
        if (
            row is None
            or row["runner"] is None
            or row["status"]
            not in (
                "pending",
                "running",
                "paused",
            )
        ):
            return None
        folder = item_paths.session_files(self._run_dirs, work_item_id, session_id).folder
        try:
            saved = json.loads((folder / "remote-run.json").read_text())
        except (OSError, ValueError):
            return None
        if saved["machine"] not in self.hub.names():
            return None
        run = Run(
            JobSpec.from_frame(saved["spec"]),
            self.hub.adopt(saved["machine"], key),
            [files.Entry(Path(e["path"]), e["rw"]) for e in saved["entries"]],
            folder / "remote.log",
            folder,
            pushed=saved["pushed"],
            state="running",
            sink=blobs.BlobSink(folder / "in"),
        )
        run.offset = run.spool.stat().st_size if run.spool.exists() else 0
        self._runs[key] = run
        await self._sync_one(run)
        return run

    def forget_soon(self, work_item_id: str) -> None:
        """Tell every connected machine to drop its copy of an archived or abandoned item. Plain,
        and safe from a worker thread (the archive path runs there); best effort, since a machine
        that is away is told again with `keep` when it comes back."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._spawn, self._forget(work_item_id))

    async def _forget(self, work_item_id: str) -> None:
        for name in self.hub.online_names():
            with contextlib.suppress(protocol.Closed):
                await self.hub.transport(name).send({"t": "forget", "item": work_item_id})

    async def _note_machine(self, machine: str, type_: RemoteEvent) -> None:
        """`type_` on every item with an unfinished step on `machine`, so the item's history
        explains a gap."""
        for run in [r for r in self._runs.values() if r.claim.machine == machine]:
            if run.exit_code is None:
                await self._event(
                    run.spec, type_, {"machine": machine, "session_id": run.spec.session_id}
                )

    async def on_disconnect(self, machine: str) -> None:
        await self._note_machine(machine, RemoteEvent.MACHINE_DISCONNECTED)

    async def on_connect(self, machine: str) -> None:
        await self._note_machine(machine, RemoteEvent.MACHINE_RECONNECTED)
        mine = [
            r for r in self._runs.values() if r.claim.machine == machine and r.state != "preparing"
        ]
        with contextlib.suppress(protocol.Closed):
            t = self.hub.transport(machine)
            if protocol.RESERVE in self.hub.caps(machine):
                await t.send({"t": "reserves", "ids": sorted(self._reserved.get(machine, ()))})
            for key in self._killed.pop(machine, ()):
                await t.send({"t": "signal", "id": key, "sig": "KILL"})
            await t.send(
                {"t": "sync", "jobs": [{"id": r.spec.key, "offset": r.offset} for r in mine]}
            )
            live = await self._db.write(store.live_item_ids)
            await t.send({"t": "keep", "items": live})
