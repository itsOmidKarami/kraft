"""A local session is started by the local worker, not by the server's own `Popen`."""

import asyncio
import time
from pathlib import Path

import psutil
import pytest

from kraft.remote import local_link

from .test_subprocess import _cancelled, _row, _running, run  # noqa: F401


def _log(row) -> str:
    return Path(row["log_path"]).read_text()


async def test_local_session_is_started_by_the_worker(run):  # noqa: F811
    link = local_link.current()
    started = []
    real = link.jobs.spawn

    def spy(key, argv, **kw):
        started.append(key)
        return real(key, argv, **kw)

    link.jobs.spawn = spy
    status, row = await run(["/bin/sh", "-c", "echo ok"])

    assert (status, row["status"]) == ("done", "done")
    assert started == ["w1/s1"]
    assert _log(row).strip() == "ok"
    assert link.jobs.running() == []  # recorded, then reaped


async def test_local_session_cancel_ends_the_group_quickly(run, database):  # noqa: F811
    link = local_link.current()
    task = asyncio.create_task(run(["sleep", "30"]))
    row = await _running(database, "s1")
    assert link.jobs.running() == ["w1/s1"]
    begun = time.monotonic()

    await _cancelled(task)

    assert time.monotonic() - begun < 2
    assert not psutil.pid_exists(row["pid"]) or psutil.Process(row["pid"]).status() == "zombie"
    assert link.jobs.running() == []


async def test_escalation_launch_waits_for_the_worker(run, database):  # noqa: F811
    """The launches that skip placement wait here: the log says so before the session's output,
    the board gets one event, and the time cap does not run down meanwhile."""
    from kraft import caps

    link = local_link.current()
    link.set_connected(False)
    hit = caps.Hit("on.test.run", "time_cap", 1, 0.0)
    cap = caps.Deadline(caps.monotonic() + 0.5, hit)
    task = asyncio.create_task(run(["/bin/sh", "-c", "sleep 0.2; echo after"], time_cap=cap))
    await asyncio.sleep(0.6)
    link.set_connected(True)

    status, row = await task

    assert status == "done"
    log = _log(row)
    assert log.index("kraft: waiting for local") < log.index("after")
    waiting = database.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE type = 'remote_step_waiting'"
        ).fetchall()
    )
    assert len(waiting) == 1 and "waiting for local" in waiting[0]["payload"]


async def test_no_link_installed_is_a_config_error(run):  # noqa: F811
    local_link.install(None)

    status, row = await run(["true"])

    assert (status, row["status"]) == ("config_error", "config_error")
    assert "kraft: no local worker in this process" in _log(row)


@pytest.mark.parametrize("what", ["cwd", "command"])
async def test_a_session_the_worker_refuses_is_a_config_error(run, tmp_path, what):  # noqa: F811
    cmd = ["true"] if what == "cwd" else ["no-such-command-kraft"]
    kw = {"cwd": tmp_path / "gone"} if what == "cwd" else {}

    status, row = await run(cmd, **kw)

    assert status == "config_error"
    assert f"no such {'working directory' if what == 'cwd' else 'command'}" in _log(row)
