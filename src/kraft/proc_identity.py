"""Whether a pid is still the process Kraft started.

A leaf module (psutil and the stdlib only), with the one way a session's process group is
stopped: the local worker process imports it, and
that process must not load the server's code."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Callable

import psutil


def pid_alive(pid: int) -> bool:
    """True only if pid names a live process (a zombie/defunct child is NOT alive)."""
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return False


def identity_ok(pid: int, pid_start_time) -> bool:
    if pid_start_time is None or not pid_alive(pid):
        return False
    try:
        return same_start(psutil.Process(pid).create_time(), pid_start_time)
    except psutil.Error:
        return False


def same_start(observed: float, recorded: float) -> bool:
    """Whether a process's start time is the one recorded at its launch:
    exactly, or one whole second off. Linux adds a process's ticks since boot
    to the boot time in whole seconds (`btime`), which moves by one when the
    wall clock is stepped across a second, a leap second included. A reused
    pid would have to start a second apart to the tick (10 ms) as well. A
    larger step reads as another process, which is never signalled."""
    shift = observed - recorded
    return abs(shift - round(shift)) < 1e-6 and abs(round(shift)) <= 1


async def kill_group(pgid: int, grace: float, reap: Callable[[], object] | None = None) -> None:
    """SIGTERM `pgid` (again at the first liveness check), then SIGKILL
    whatever is still there after `grace` seconds. `frontend/e2e/serve.py`'s
    `_terminate` is the model this copies. The server's sessions and the local
    worker's (`remote.local_jobs`) both stop through it.

    The session's own child is already gone by the time this runs — this is
    for what it backgrounded into the same group and never waited on itself
    (a fixture server, `just dev`), which `start_new_session=True` put in the
    same process group as the child but this coroutine never touched
    (Kraft-1nye). `os.killpg` raises `ProcessLookupError` the moment the group
    has no members left, so the common case (nothing backgrounded) returns
    almost immediately rather than paying `grace`.

    `reap` is the leader's own `Popen.poll`, called before every liveness
    check. On a pause or skip this runs from `run_task`'s `finally` while the
    leader is still alive, so the SIGTERM above kills it but nothing reaps it
    -- the poll loop that normally would was cancelled out from under it. On
    Linux an unreaped zombie still counts as a group member, so `killpg(pgid,
    0)` kept succeeding and every cancel paid the full `grace` (10s by
    default) before falling through to SIGKILL. macOS raises
    `PermissionError` for a zombie-only group instead, which is why this
    passed locally and failed pause/resume, SIGTERM shutdown and the e2e
    pause flow in CI (Kraft-rki).

    The first liveness check is a second SIGTERM, not a probe: a member forked
    while the first one was being delivered (what `_wrap_with_exit_file`'s `sh`
    forks for the worker's command, on a cancel straight after launch) misses it
    and would otherwise outlive the whole `grace` -- a pause or skip then paid
    it, flaking under load.
    """
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    probe = signal.SIGTERM
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        if reap is not None:
            reap()
        try:
            os.killpg(pgid, probe)
            probe = 0
        except (ProcessLookupError, PermissionError):
            return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
