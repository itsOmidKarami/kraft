"""The egress channels a process gives the sandboxes it builds: one registry for every `Machine`
the process hosts. The sandbox backends read it from `channel.current()`, which is one per process,
so a daemon serving two Kraft servers cannot have a registry for each; this routes a worker's call
and a sandbox event to the `Machine` whose job holds that session."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

from kraft import paths
from kraft.paths import RunDirs
from kraft.worker import channel
from kraft.worker.egress import EgressProxy

if TYPE_CHECKING:
    from kraft.remote.machine import Machine

_NO_JOB = (404, {"content-type": "application/json"}, b'{"detail": "no such job here"}')


def machine_run_dirs() -> RunDirs:
    """Where the sandbox backends keep their state on this machine (ref stores, the CA, sockets,
    sandbox homes): its own run dir, as the backends assume."""
    return RunDirs(Path(os.environ.get("KRAFT_RUN_DIR") or paths.default_run_dir()).resolve())


class Channels:
    """`registry` is what `channel.install` takes; the session MCP server and the mTLS listener
    it needs are run once, here, for as long as the process lives."""

    def __init__(self, run_dirs: RunDirs | None = None) -> None:
        self._run_dirs = run_dirs
        self._registry: channel.ChannelRegistry | None = None
        self._machines: list[Machine] = []
        self._mcp: asyncio.Task | None = None
        self._tls: channel.TLSListener | None = None
        self._tls_lock = asyncio.Lock()

    @property
    def run_dirs(self) -> RunDirs:
        """Read from the environment on first use, not when the process starts."""
        if self._run_dirs is None:
            self._run_dirs = machine_run_dirs()
        return self._run_dirs

    @property
    def registry(self) -> channel.ChannelRegistry:
        if self._registry is None:
            self._registry = channel.ChannelRegistry(
                self.run_dirs, None, EgressProxy(api=self._proxy_api), record=self._record
            )
        return self._registry

    def add(self, machine: Machine) -> None:
        self._machines.append(machine)

    def _owner(self, work_item_id: str, session_id: str) -> Machine | None:
        key = f"{work_item_id}/{session_id}"
        return next((m for m in self._machines if m.holds(key)), None) or next(
            (m for m in self._machines if m.runs_item(work_item_id)), None
        )

    async def _proxy_api(self, method: str, target: str, caller: str, kw: dict):
        owner = next((m for m in self._machines if m.holds(caller)), None)
        if owner is None:
            return _NO_JOB
        return await owner.proxy_api(method, target, caller, kw)

    async def _record(self, work_item_id: str, session_id: str, type_: str, payload: dict):
        if (owner := self._owner(work_item_id, session_id)) is not None:
            await owner.record_event(work_item_id, session_id, type_, payload)

    async def serve_mcp(self) -> None:
        """Run the session MCP server the proxy serves, once."""
        if self._mcp is not None:
            return
        ready = asyncio.Event()

        async def hold() -> None:
            async with self.registry.proxy.serving():
                ready.set()
                await asyncio.Event().wait()

        self._mcp = asyncio.create_task(hold())
        await ready.wait()

    async def ensure_tls(self) -> None:
        """The one mTLS listener a VM-backed runtime's relay dials, started when first needed."""
        async with self._tls_lock:
            if self._tls is None:
                tls = channel.TLSListener(self.registry, self.run_dirs)
                await tls.start()
                self._tls = tls

    async def close(self) -> None:
        if self._mcp is not None:
            self._mcp.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._mcp
