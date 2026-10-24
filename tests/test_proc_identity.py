"""Whether a pid is still the process Kraft started."""

from __future__ import annotations

import os

import psutil
import pytest

from kraft import proc_identity


@pytest.mark.parametrize(
    "shift, same",
    [
        (0.0, True),
        (1.0, True),
        (-1.0, True),
        (0.4, False),
        (2.0, False),
        (3600.0, False),
    ],
    ids=[
        "exact",
        "one-second-later",
        "one-second-earlier",
        "fraction-off",
        "two-seconds",
        "an-hour",
    ],
)
def test_same_start(shift, same):
    assert proc_identity.same_start(1_000_000.0 + shift, 1_000_000.0) is same


@pytest.mark.parametrize(
    "pid, start_offset, expected",
    [
        (os.getpid(), 0.0, True),
        (os.getpid(), -3600.0, False),
        (os.getpid(), None, False),
        (2_000_000_000, 0.0, False),
    ],
    ids=["this-process", "start-an-hour-off", "no-start-time", "dead-pid"],
)
def test_identity_ok(pid, start_offset, expected):
    start = None
    if start_offset is not None:
        start = psutil.Process(os.getpid()).create_time() + start_offset
    assert proc_identity.identity_ok(pid, start) is expected


def test_pid_alive_is_false_for_a_dead_pid():
    assert proc_identity.pid_alive(2_000_000_000) is False
