"""`kraft.adapters.subprocess.run_task` and a pause that lands before its
session reads running: the row has no pid yet, so the pause has nothing to
signal and the launch itself has to stand down."""

import asyncio

import pytest
from support.harness import own_files
from support.worktree import make_item

from kraft import events, store
from kraft.adapters import subprocess as sp

from .test_subprocess import _gone


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


@pytest.mark.parametrize(
    ("seam", "pause", "launches"),
    [
        pytest.param("create_session", _create_then_pause, False, id="before-the-check"),
        pytest.param("session_running", _pause_then_running, True, id="after-popen"),
    ],
)
async def test_run_task_never_runs_a_session_paused_before_it_started(
    database, run_dirs, tmp_path, monkeypatch, seam, pause, launches
):
    """Kraft-qx1q: a row marked paused between `create_session` and Popen
    must never launch -- nothing SIGTERMs a session with no pid yet. A pause
    landing after the pre-launch check but before `session_running` has
    nothing to signal either: the child just launched is stopped, and no
    `worker_session_started` follows the `worker_session_paused`."""
    await make_item(database, "/r")
    monkeypatch.setattr(store, seam, pause(getattr(store, seam)))
    children = []
    real_popen = sp.subprocess.Popen

    def popen(*a, **kw):
        children.append(proc := real_popen(*a, **kw))
        return proc

    monkeypatch.setattr(sp.subprocess, "Popen", popen)

    launch = sp.run_task(
        database,
        run_dirs,
        session_id="s1",
        work_item_id="w1",
        node_id="verify",
        hook_point="on.test.run",
        cwd=tmp_path,
        cmd=["sleep", "30"],
    )
    # A child left running would hold this for its 30s, past the timeout.
    status = await asyncio.wait_for(launch, timeout=15)

    row = database.read(lambda c: store.session_status(c, "s1", work_item_id="w1"))
    assert (status, row) == ("paused", "paused")
    assert [p.pid for p in children if not _gone(p.pid)] == []
    assert bool(children) == launches
    assert own_files(run_dirs, "s1").log.exists() == launches
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
    assert "worker_session_started" not in types
    assert types[-1] == "worker_session_paused"
