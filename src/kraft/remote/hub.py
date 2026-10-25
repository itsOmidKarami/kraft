"""The server's side of the machines: who is connected, over which mode, and
why one is not, and which of them has a free slot for a step (`decide`, `claim`)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from kraft import caps
from kraft.config import RepoEntry
from kraft.remote import local_link, placement, protocol, registry
from kraft.remote.transport import FRAME_LIMIT, StreamTransport

logger = logging.getLogger(__name__)

REMOTE_COMMAND = ("kraft", "admin", "worker", "serve", "--stdio")
FIRST_DELAY_S, MAX_DELAY_S = 1.0, 30.0
#: How long a machine that turned a step away is left alone, unless it reports a new limit or
#: a step of its own ends first.
FULL_RETRY_S = 30.0


@dataclass(frozen=True)
class Claim:
    """One slot of one machine, held by a step from launch to the end of its session."""

    machine: str
    key: str  # "<work item id>/<session id>"


class PlacementRefused(Exception):
    """str() is the reason; a `config_error` at launch."""


@dataclass(frozen=True)
class Verdict:
    machine: str | None = None  # who would take it now
    wait: str | None = None  # "waiting for gpu-box" / "waiting for any machine allowed for kraft"
    refusal: str | None = None


def server_id(run_dir: Path) -> str:
    """This install's stable id (`run/server.id`), made once; a machine keeps one working area
    per server under it."""
    path = Path(run_dir) / "server.id"
    try:
        found = path.read_text().strip()
    except OSError:
        found = ""
    if not found:
        found = secrets.token_hex(6)
        path.write_text(found)
    return found


@dataclass
class _Entry:
    worker: registry.Worker
    online: bool = False
    reason: str = "never connected"
    last_seen: float | None = None
    kraft: str | None = None
    caps: frozenset[str] = frozenset()  # the connected machine's `protocol.CAPS`
    transport: protocol.Transport | None = None
    #: `mono()` when the connection ended, or the hub first knew the machine; None while online.
    offline_since: float | None = None
    offline_at: str | None = None  # the same moment as a UTC ISO time, for people
    dialer: asyncio.Task | None = None
    outcome: asyncio.Event = field(default_factory=asyncio.Event)
    #: The limit the machine set for itself (`machine.yaml`), as it last said; None: none.
    machine_capacity: int | None = None
    #: `mono()` until which the machine, having turned a step away, is not asked again.
    full_until: float | None = None


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
        mono: Callable[[], float] | None = None,
        server_id: str = "",
    ) -> None:
        self.kraft_version = kraft_version
        self.server_id = server_id
        self._ssh, self._remote = ssh_command, remote_command
        self._ping_s, self._dead_s, self._clock = ping_s, dead_s, clock
        # What `give_up_after` is judged on: monotonic, so time the server slept is not counted.
        self._mono = mono or (lambda: caps.monotonic())
        self._file = registry.WorkersFile()
        self._entries: dict[str, _Entry] = {}
        self._background: set[asyncio.Task] = set()
        self._claims: dict[str, set[str]] = {}  # machine -> claim keys
        self.runs = None  # set by the app: remote.runs.Runs

    # ── configuration ──

    @property
    def file(self) -> registry.WorkersFile:
        """The `workers.yaml` the hub is running."""
        return self._file

    def apply(self, file: registry.WorkersFile) -> None:
        """Make `file` the running set: start the dial loop of a new ssh
        machine, and drop a removed one or one whose credential changed (a
        revoked token must not keep a live connection)."""
        previous, self._entries, self._file = self._entries, {}, file
        if (link := local_link.current()) is not None:
            link.capacity = file.local.capacity if file.local is not None else None
        for name, worker in file.workers.items():
            entry = previous.pop(name, None)
            if entry is None:
                entry = _Entry(worker, offline_since=self._mono(), offline_at=self._now_iso())
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

    def _give_up_s(self, name: str) -> float | None:
        worker = self._file.workers.get(name)
        return worker.give_up_s if worker is not None else None

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).isoformat()

    def unreachable_since(self, name: str) -> str | None:
        """When `name` went out of reach (UTC ISO), or None while it is online or unknown."""
        entry = self._entries.get(name)
        return entry.offline_at if entry is not None else None

    def offline_for(self, name: str) -> float | None:
        """Seconds (awake time) since `name`'s connection ended, or since the hub first knew of it;
        None while it is online."""
        entry = self._entries.get(name)
        if entry is None or entry.offline_since is None:
            return None
        return self._mono() - entry.offline_since

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
                server_id=self.server_id,
                give_up_after_s=self._give_up_s,
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
        entry.offline_since = entry.offline_at = None
        entry.kraft, entry.caps, entry.last_seen = peer.kraft, peer.caps, self._clock()
        entry.machine_capacity = protocol.reported_capacity(peer.caps)
        entry.full_until = None
        entry.outcome.set()
        if self.runs is not None:  # tell the runs this machine is back
            self._spawn(self.runs.on_connect(peer.name))
        why = "disconnected"
        try:
            await protocol.pump(
                t,
                on_frame=lambda frame: self._on_frame(peer.name, frame),
                ping_s=self._ping_s,
                dead_s=self._dead_s,
                on_seen=lambda: setattr(entry, "last_seen", self._clock()),
            )
        except protocol.Closed as exc:
            why = f"disconnected: {exc}"
        finally:
            if entry.transport is t:
                entry.transport, entry.online, entry.reason = None, False, why
                entry.offline_since, entry.offline_at = self._mono(), self._now_iso()
                if self.runs is not None:
                    self._spawn(self.runs.on_disconnect(peer.name))
            await t.close()

    async def _on_frame(self, machine: str, frame: dict) -> None:
        if self.runs is not None:
            await self.runs.on_frame(machine, frame)

    # ── ssh ──

    async def _dial_ssh(self, name: str, entry: _Entry) -> None:
        delay = FIRST_DELAY_S
        while True:
            welcomed = False
            proc = None
            drain = None
            stderr_tail = bytearray()
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
                drain = asyncio.create_task(_drain(proc.stderr, stderr_tail))
                t = StreamTransport(proc.stdout, proc.stdin)
                peer = await protocol.server_handshake(
                    t,
                    kraft_version=self.kraft_version,
                    identify=lambda _: None,
                    expected_name=name,
                    server_id=self.server_id,
                    give_up_after_s=entry.worker.give_up_s,
                )
                welcomed = True
                await self._run(entry, peer, t)
            except protocol.Refused as refused:
                entry.reason = f"refused: {refused}"
            except (protocol.Closed, OSError) as exc:
                if proc is None:
                    entry.reason = f"could not run ssh: {exc}"
                elif not welcomed:
                    await asyncio.wait({drain}, timeout=1.0)  # the child is gone: let EOF land
                    tail = stderr_tail.decode(errors="replace").strip()[-300:]
                    entry.reason = f"ssh: {tail or exc}"
            finally:
                if proc is not None and proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                if drain is not None:
                    drain.cancel()
                entry.online = False if not entry.transport else entry.online
                entry.outcome.set()
            delay = FIRST_DELAY_S if welcomed else min(delay * 2, MAX_DELAY_S)
            await asyncio.sleep(delay)

    # ── placing ──

    def slots_used(self, name: str) -> int:
        return len(self._claims.get(name, ()))

    def limit(self, name: str) -> int:
        """Steps `name` may run at once: the lower of workers.yaml's `capacity` and the machine's
        own, whichever are set; one when neither is."""
        entry = self._entries.get(name)
        if entry is None:
            return 1
        set_ = [c for c in (entry.worker.capacity, entry.machine_capacity) if c is not None]
        return min(set_) if set_ else 1

    def limit_source(self, name: str) -> str:
        """Who set `limit`: "server" (also a tie), "machine", or "default" when neither did."""
        entry = self._entries.get(name)
        server = entry.worker.capacity if entry is not None else None
        machine = entry.machine_capacity if entry is not None else None
        if server is None and machine is None:
            return "default"
        if server is None or (machine is not None and machine < server):
            return "machine"
        return "server"

    def set_machine_capacity(self, name: str, capacity: int | None) -> None:
        """The machine says its own limit is `capacity` now. It may be asked again."""
        entry = self._entries.get(name)
        if entry is not None:
            entry.machine_capacity = capacity
            entry.full_until = None

    def mark_full(self, name: str) -> None:
        """`name` turned a step away: not offered another until it reports a new limit, one of
        its steps ends, or `FULL_RETRY_S` pass."""
        entry = self._entries.get(name)
        if entry is not None:
            entry.full_until = self._mono() + FULL_RETRY_S

    def _has_slot(self, name: str, needs: Collection[str]) -> bool:
        e = self._entries.get(name)
        return (
            e is not None
            and e.online
            and set(needs) <= e.caps
            and (e.full_until is None or self._mono() >= e.full_until)
            and self.slots_used(name) < self.limit(name)
        )

    def free(self, name: str, repos: Sequence[RepoEntry], needs: Collection[str] = ()) -> bool:
        """Could `name` take a step now: online, allowed for `repos`, listing every cap in
        `needs`, not just turned a step away, and a slot under its limit."""
        e = self._entries.get(name)
        return e is not None and placement.allows(e.worker, repos) and self._has_slot(name, needs)

    def decide(
        self,
        runner: str,
        repos: Sequence[RepoEntry],
        *,
        pinned: str | None = None,
        needs: Mapping[str, str] | Collection[str] = (),
    ) -> Verdict:
        """Who would take a step placed on `runner` now, or why it waits or is refused. `pinned`
        is the machine a resumed session must go back to. `needs` are caps the machine must list
        (a mapping gives each one's words for a refusal)."""
        label = placement.repos_label(repos)
        if pinned is not None:
            if pinned not in self._entries:
                return Verdict(
                    refusal=f"machine {pinned!r}, where this step started, "
                    "is no longer in workers.yaml"
                )
            names, wait_text = [pinned], f"waiting for {pinned}"
        elif runner == placement.ANY:
            names = [
                n for n, e in sorted(self._entries.items()) if placement.allows(e.worker, repos)
            ]
            if not names:
                return Verdict(
                    refusal="runner 'any' finds no machine in workers.yaml "
                    f"that may work on {label}"
                )
            wait_text = f"waiting for any machine allowed for {label}"
        else:
            if (why := placement.refusal(runner, workers=self._file, repos=repos)) is not None:
                return Verdict(refusal=why)
            names, wait_text = [runner], f"waiting for {runner}"
        named = runner != placement.ANY or pinned is not None
        first = self._entries.get(names[0])  # workers.yaml may name it before apply made it
        # A named machine that is up without a cap the step needs will never do; for `any`, so
        # are all of them when every candidate is up.
        lacking = [
            (n, cap)
            for n in names
            if (e := self._entries.get(n)) is not None and e.online
            for cap in needs
            if cap not in e.caps
        ]
        if lacking and (named or len({n for n, _ in lacking}) == len(names)):
            n, cap = lacking[0]
            what = needs.get(cap) if isinstance(needs, Mapping) else None
            return Verdict(
                refusal=f"{what or 'this step needs a capability of the machine'}: "
                f"machine {n!r} runs a Kraft without {cap!r}; update Kraft on that machine"
            )
        for n in names:
            if self._has_slot(n, needs):
                return Verdict(machine=n)
        if named:
            if first is None:
                return Verdict(wait=f"{wait_text} (offline: not connected yet)")
            if not first.online:
                return Verdict(wait=f"{wait_text} (offline: {first.reason})")
            return Verdict(wait=f"{wait_text} (full)")
        return Verdict(wait=wait_text)

    async def claim(
        self,
        runner: str,
        repos: Sequence[RepoEntry],
        key: str,
        *,
        pinned: str | None = None,
        needs: Mapping[str, str] | Collection[str] = (),
        on_wait: Callable[[str], None] | None = None,
        poll_s: float = 0.5,
    ) -> Claim:
        """A slot for `key`, waiting (and saying so through `on_wait`, once per reason) until a
        machine can take it. Raises `PlacementRefused`; cancelling the caller gives nothing back
        because nothing is held while it waits."""
        said = None
        while True:
            v = self.decide(runner, repos, pinned=pinned, needs=needs)
            if v.refusal:
                raise PlacementRefused(v.refusal)
            if v.machine:
                self._claims.setdefault(v.machine, set()).add(key)
                return Claim(v.machine, key)
            if on_wait is not None and v.wait != said:
                on_wait(v.wait)
                said = v.wait
            await asyncio.sleep(poll_s)

    def adopt(self, machine: str, key: str) -> Claim:
        """A slot for a run already running before a restart: taken whatever the capacity says."""
        self._claims.setdefault(machine, set()).add(key)
        return Claim(machine, key)

    def online_names(self) -> list[str]:
        return sorted(n for n, e in self._entries.items() if e.online)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def release(self, claim: Claim) -> None:
        self._claims.get(claim.machine, set()).discard(claim.key)
        if (entry := self._entries.get(claim.machine)) is not None:
            entry.full_until = None  # a slot is free: worth asking again

    def caps(self, name: str) -> frozenset[str]:
        """What `name`'s connection said it understands; none for a machine never connected."""
        entry = self._entries.get(name)
        return entry.caps if entry is not None else frozenset()

    def transport(self, name: str) -> protocol.Transport:
        entry = self._entries.get(name)
        if entry is None or entry.transport is None:
            raise protocol.Closed(f"{name} is offline")
        return entry.transport

    async def wait_online(self, name: str, poll_s: float = 0.2) -> None:
        while not ((entry := self._entries.get(name)) and entry.online):
            await asyncio.sleep(poll_s)

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
                "slots_used": self.slots_used(name),
                "capacity": self.limit(name),
                "capacity_source": self.limit_source(name),
                "machine_capacity": e.machine_capacity,
                "repos": e.worker.repos,
                "last_seen": (
                    datetime.fromtimestamp(e.last_seen, UTC).isoformat() if e.last_seen else None
                ),
                "kraft_version": e.kraft,
                "give_up_after": e.worker.give_up_after,
            }
            for name, e in sorted(self._entries.items())
        ]


async def _drain(stream, tail: bytearray) -> None:
    """Read a child's stderr until EOF, keeping only the last 2000 bytes, so a
    chatty ssh can never fill the pipe and block."""
    while chunk := await stream.read(65536):
        tail += chunk
        del tail[:-2000]
