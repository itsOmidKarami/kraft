"""`lifecycle._terminate` against a real process. A sibling of
test_lifecycle.py, which is at its line budget."""

from __future__ import annotations

import signal
import subprocess

import psutil

from kraft.api.routes import lifecycle


def test_pause_interrupts_a_real_agent_kraft_launched():
    """The identity and group checks pass for the child Kraft really launched."""
    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        lifecycle._terminate(child.pid, started=psutil.Process(child.pid).create_time())
        assert child.wait(timeout=5) == -signal.SIGINT
    finally:
        child.kill()
        child.wait()
