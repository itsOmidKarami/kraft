"""The server's side of the machines: who is connected, over which mode, and
why one is not. Nothing here places work (slice 2); it keeps the connection
and tells the truth about it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from kraft.remote import protocol, registry
from kraft.remote.transport import FRAME_LIMIT, StreamTransport

logger = logging.getLogger(__name__)

REMOTE_COMMAND = ("kraft", "admin", "worker", "serve", "--stdio")
FIRST_DELAY_S, MAX_DELAY_S = 1.0, 30.0


@dataclass
class _Entry:
    worker: registry.Worker
    online: bool = False
    reason: str = "never connected"
    last_seen: float | None = None
    kraft: str | None = None
    transport: protocol.Transport | None = None
    dialer: asyncio.Task | None = None
    outcome: asyncio.Event = field(default_factory=asyncio.Event)


class Hub:
    def __init__(
        self,
        kraft_version: str,
        *,
        ssh_command: tuple[str, ...] = ("ssh",),
        remote_command: tuple[str, ...] = REMOTE_COMMAND,
        ping_s: float = protocol.PING_S,
        dead_s: float = protocol.DEAD_S,
        clock=time.time,
    ) -> None:
        self.kraft_version = kraft_version
        self._ssh, self._remote = ssh_command, remote_command
        self._ping_s, self._dead_s, self._clock = ping_s, dead_s, clock
        self._file = registry.WorkersFile()
        self._entries: dict[str, _Entry] = {}
        self._background: set[asyncio.Task] = set()

    # ── configuration ──

    def apply(self, file: registry.WorkersFile) -> None:
        """Make `file` the running set: start the dial loop of a new ssh
        machine, and drop a removed one or one whose credential changed (a
        revoked token must not keep a live connection)."""
        previous, self._entries, self._file = self._entries, {}, file
        for name, worker in file.workers.items():
            entry = previous.pop(name, None)
            if entry is None:
                entry = _Entry(worker)
            else:
                changed = (entry.worker.ssh, entry.worker.token_sha256) != (
                    worker.ssh,
                    worker.token_sha256,
                )
                entry.worker = worker
                if changed:
                    self._drop(entry)
                    entry.reason, entry.outcome = "never connected", asyncio.Event()
            self._entries[name] = entry
            if worker.ssh and (entry.dialer is None or entry.dialer.done()):
                entry.dialer = asyncio.create_task(self._dial_ssh(name, entry))
        for gone in previous.values():
            self._drop(gone)

    def _drop(self, entry: _Entry) -> None:
        if entry.dialer is not None:
            entry.dialer.cancel()
            entry.dialer = None
        if entry.transport is not None:
            self._spawn(entry.transport.close())

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def close(self) -> None:
        tasks = [e.dialer for e in self._entries.values() if e.dialer]
        for entry in self._entries.values():
            self._drop(entry)
        self._entries = {}
        await asyncio.gather(*tasks, *self._background, return_exceptions=True)

    # ── dial-in ──

    async def accept(self, t: protocol.Transport) -> None:
        """A machine dialled in. Returns when the connection ends; raises
        `Refused` (after telling the machine) when it is turned away."""
        try:
            peer = await protocol.server_handshake(
                t,
                kraft_version=self.kraft_version,
                identify=lambda token: registry.find_by_token(self._file, token),
            )
        except protocol.Refused as refused:
            if (entry := self._entries.get(refused.name or "")) is not None:
                entry.reason = f"refused: {refused}"
                entry.outcome.set()
            await t.close()
            raise
        except protocol.Closed:
            await t.close()
            return
        entry = self._entries.get(peer.name)
        if entry is None:  # removed between the hash match and now
            await t.close()
            return
        await self._run(entry, peer, t)

    async def _run(self, entry: _Entry, peer: protocol.Peer, t: protocol.Transport) -> None:
        if entry.transport is not None:
            self._spawn(
                entry.transport.close()
            )  # replaced: a half-dead old link must not lock the machine out
        entry.transport, entry.online, entry.reason = t, True, ""
        entry.kraft, entry.last_seen = peer.kraft, self._clock()
        entry.outcome.set()
        why = "disconnected"
        try:
            await protocol.pump(
                t,
                on_frame=self._on_frame,
                ping_s=self._ping_s,
                dead_s=self._dead_s,
                on_seen=lambda: setattr(entry, "last_seen", self._clock()),
            )
        except protocol.Closed as exc:
            why = f"disconnected: {exc}"
        finally:
            if entry.transport is t:
                entry.transport, entry.online, entry.reason = None, False, why
            await t.close()

    async def _on_frame(self, frame: dict) -> None:
        logger.debug("machine frame ignored (slice 1): %s", frame.get("t"))

    # ── ssh ──

    async def _dial_ssh(self, name: str, entry: _Entry) -> None:
        delay = FIRST_DELAY_S
        while True:
            welcomed = False
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._ssh,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ServerAliveInterval=15",
                    "--",
                    entry.worker.ssh,
                    *self._remote,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=FRAME_LIMIT,
                )
                t = StreamTransport(proc.stdout, proc.stdin)
                peer = await protocol.server_handshake(
                    t, kraft_version=self.kraft_version, identify=lambda _: None, expected_name=name
                )
                welcomed = True
                await self._run(entry, peer, t)
            except protocol.Refused as refused:
                entry.reason = f"refused: {refused}"
            except (protocol.Closed, OSError) as exc:
                if proc is None:
                    entry.reason = f"could not run ssh: {exc}"
                elif not welcomed:
                    entry.reason = f"ssh: {await _stderr_tail(proc) or exc}"
            finally:
                if proc is not None and proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                entry.online = False if not entry.transport else entry.online
                entry.outcome.set()
            delay = FIRST_DELAY_S if welcomed else min(delay * 2, MAX_DELAY_S)
            await asyncio.sleep(delay)

    # ── reading ──

    async def probe(self, name: str, timeout: float) -> dict:
        """The outcome of the first connection attempt to `name`."""
        entry = self._entries[name]
        try:
            await asyncio.wait_for(entry.outcome.wait(), timeout)
        except TimeoutError:
            return {"connected": False, "detail": f"no answer in {timeout:g}s"}
        return {"connected": entry.online, "detail": "connected" if entry.online else entry.reason}

    def snapshot(self) -> list[dict]:
        return [
            {
                "name": name,
                "mode": e.worker.mode,
                "online": e.online,
                "reason": e.reason,
                "slots_used": 0,
                "capacity": e.worker.capacity,
                "repos": e.worker.repos,
                "last_seen": (
                    datetime.fromtimestamp(e.last_seen, UTC).isoformat() if e.last_seen else None
                ),
                "kraft_version": e.kraft,
                "give_up_after": e.worker.give_up_after,
            }
            for name, e in sorted(self._entries.items())
        ]


async def _stderr_tail(proc) -> str:
    with contextlib.suppress(Exception):
        data = await asyncio.wait_for(proc.stderr.read(2000), 1.0)
        return data.decode(errors="replace").strip()[-300:]
    return ""
