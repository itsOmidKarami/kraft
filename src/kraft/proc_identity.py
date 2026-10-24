"""Whether a pid is still the process Kraft started.

A leaf module (psutil and the stdlib only): the local worker process imports it, and
that process must not load the server's code."""

from __future__ import annotations

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
