"""`kraft.adapters.subprocess.run_task` and a pause that lands before its
session reads running: the row has no pid yet, so the pause has nothing to
signal and the launch itself has to stand down."""

import asyncio
import os
import time

import pytest
from support.harness import own_files
from support.worktree import make_item

from kraft import events, store
from kraft.adapters import subprocess as sp


def _group_gone(pgid: int, within: float = 2.0) -> bool:
    """Whether process group `pgid` has no member left: the `sh` wrapper and
    the command it forked. Waits out the reap after a SIGKILL; macOS answers
    a zombie-only group with `PermissionError`."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.02)
    return False


def _create_then_pause(original):
    def create_session(conn, **kwargs):
        original(conn, **kwargs)
        store.stop_escalation_session(conn, "w1", kwargs["id"])

    return create_session


def _pause_then_running(original):
    def session_running(conn, session_id, *args, **kwargs):
        store.stop_escalation_session(conn, "w1", session_id)
        return original(conn, session_id, *args, **kwargs)

    return session_running


def _item_paused_first(original):
    """The item paused before this task's session row existed: a broken-base
    pause between two tasks of one node, with nothing yet to mark or signal."""

    def create_session(conn, **kwargs):
        conn.execute("UPDATE work_items SET status = 'paused' WHERE id = 'w1'")
        return original(conn, **kwargs)

    return create_session


def _cancel_on_paused(original, task):
    """`session_status` that cancels `task` the first time it reads paused:
    the `finally`'s first await, on a launch whose `session_running` missed.
    Retry's `_stop_live_sessions` writes every live session before it
    cancels, so its cancel can land there."""

    def session_status(conn, *args, **kwargs):
        status = original(conn, *args, **kwargs)
        if status == "paused" and not task.cancelling():
            task.cancel()
        return status

    return session_status


@pytest.mark.parametrize(
    ("seam", "pause", "launches", "cancel"),
    [
        pytest.param("create_session", _create_then_pause, False, False, id="before-the-check"),
        pytest.param("session_running", _pause_then_running, True, False, id="after-popen"),
        pytest.param("create_session", _item_paused_first, True, False, id="item-paused-first"),
        pytest.param(
            "session_running", _pause_then_running, True, True, id="after-popen-then-cancel"
        ),
    ],
)
async def test_run_task_never_runs_a_session_paused_before_it_started(
    database, run_dirs, tmp_path, monkeypatch, seam, pause, launches, cancel
):
    """Kraft-qx1q: a row marked paused between `create_session` and Popen
    must never launch -- nothing SIGTERMs a session with no pid yet. A pause
    landing after the pre-launch check but before `session_running` has
    nothing to signal either: the child just launched is stopped, and no
    `worker_session_started` follows the `worker_session_paused` -- even when
    the walk is cancelled while that stop is under way."""
    await make_item(database, "/r")
    monkeypatch.setattr(store, seam, pause(getattr(store, seam)))
    children = []
    real_popen = sp.subprocess.Popen

    def popen(*a, **kw):
        children.append(proc := real_popen(*a, **kw))
        return proc

    monkeypatch.setattr(sp.subprocess, "Popen", popen)

    launch = asyncio.ensure_future(
        sp.run_task(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="w1",
            node_id="verify",
            hook_point="on.test.run",
            cwd=tmp_path,
            cmd=["sleep", "30"],
        )
    )
    if cancel:
        monkeypatch.setattr(
            store, "session_status", _cancel_on_paused(store.session_status, launch)
        )
    try:
        # A child left running would hold this for its 30s, past the timeout.
        status = await asyncio.wait_for(launch, timeout=15)
    except asyncio.CancelledError:
        status = "cancelled"

    row = database.read(lambda c: store.session_status(c, "s1", work_item_id="w1"))
    assert (status, row) == ("cancelled" if cancel else "paused", "paused")
    assert [p.pid for p in children if not _group_gone(p.pid)] == []
    assert bool(children) == launches
    assert own_files(run_dirs, "s1").log.exists() == launches
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
    assert "worker_session_started" not in types
    assert types[-1] == "worker_session_paused"
