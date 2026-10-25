"""A local session is started by the local worker, not by the server's own `Popen`."""

import asyncio
import time
from pathlib import Path

import psutil
import pytest

from kraft.remote import local_link

from .test_subprocess import _cancelled, _row, _running, docker, run  # noqa: F401


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


async def test_a_session_beyond_the_local_limit_waits_with_no_row_and_says_how_long(
    run,  # noqa: F811
    database,
    monkeypatch,
):
    import re

    from kraft import caps
    from kraft.adapters import subprocess as sp

    local_link.current().capacity = 1
    seen = []
    real = sp._run_task

    async def spy(*args, **kw):
        seen.append(kw)
        return await real(*args, **kw)

    monkeypatch.setattr(sp, "_run_task", spy)
    first = asyncio.create_task(run(["sleep", "30"], "s1"))
    await _running(database, "s1")
    cap = caps.Deadline(caps.monotonic() + 1000, caps.Hit("on.test.run", "time_cap_minutes", 1, 0))
    second = asyncio.create_task(run(["/bin/sh", "-c", "echo after"], "s2", time_cap=cap))
    await asyncio.sleep(0.7)

    assert _row(database, "s2") is None and not second.done()  # no session row while it waits
    await _cancelled(first)
    status, row = await asyncio.wait_for(second, 10)

    assert status == "done"
    assert re.fullmatch(r"kraft: waited \d\.\d s for a local slot", _log(row).splitlines()[0])
    waiting = database.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE type = 'remote_step_waiting'"
        ).fetchall()
    )
    assert len(waiting) == 1 and "waiting for local (1 of 1 running)" in waiting[0]["payload"]
    moved = seen[-1]["time_cap"]
    assert moved.at - cap.at >= 0.5 and moved.hit == cap.hit and seen[-1]["waited_s"] >= 0.5


@pytest.mark.parametrize("how", ["refused-spawn", "before-launch-raises"])
async def test_a_session_that_returns_early_gives_its_local_slot_back(run, how):  # noqa: F811
    link = local_link.current()
    link.capacity = 1

    async def refuses():
        raise RuntimeError("no lease")

    if how == "refused-spawn":
        status, _ = await run(["no-such-command-kraft"])
        assert status == "config_error"
    else:
        with pytest.raises(RuntimeError):
            await run(["true"], before_launch=refuses)

    assert link.free() and link.take("w1/next")


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


# --- a clean stop -------------------------------------------------------------------------


def _alive(pid: int) -> bool:
    return psutil.pid_exists(pid) and psutil.Process(pid).status() != "zombie"


def _end(pid: int) -> None:
    import os
    import signal

    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


async def _stopped_while_running(run, database, sid, *, stopping, before=None, **kw):  # noqa: F811
    """`run(["sleep", "30"])` cancelled once running, as the server's shutdown does when
    `stopping` has marked the loop; `before` runs between the two. The session row, and whether
    its process outlived the cancel (always ended before this returns)."""
    from kraft.adapters import subprocess as sp

    task = asyncio.create_task(run(["sleep", "30"], sid, **kw))
    row = await _running(database, sid)
    if stopping is not None:
        sp.server_stopping(**stopping)
    if before is not None:
        await before()
    await _cancelled(task)
    survived = _alive(row["pid"])
    after = _row(database, sid)
    _end(row["pid"])
    return after, survived


@pytest.mark.parametrize(
    ("stopping", "pause", "kept"),
    [
        ({}, False, True),
        ({"keep_local": False}, False, False),
        ({}, True, False),
        (None, False, False),
    ],
    ids=["kept", "now-stops-it", "paused-by-a-person", "not-stopping-a-cancel-is-a-stop"],
)
async def test_clean_stop_keep_rule(run, database, stopping, pause, kept):  # noqa: F811
    from kraft import store

    async def pause_it():
        await database.write(lambda c: store.pause_work_item(c, "w1", ["s1"]))

    row, survived = await _stopped_while_running(
        run, database, "s1", stopping=stopping, before=pause_it if pause else None, flush_grace=0
    )

    assert survived is kept
    if kept:
        assert row["status"] == "running"  # as after a crash: the next start adopts it


async def test_a_session_over_its_cap_is_capped_out_while_the_server_stops(run):  # noqa: F811
    from kraft import caps
    from kraft.adapters import subprocess as sp

    sp.server_stopping()
    cap = caps.Deadline(caps.monotonic() - 1, caps.Hit("on.test.run", "time_cap_minutes", 1, 0))

    status, row = await run(["sleep", "30"], time_cap=cap, group_kill_grace=1.0)

    assert (status, row["status"]) == (caps.TIME_CAPPED, "capped_out")
    assert not _alive(row["pid"])


async def test_a_session_that_exits_while_the_server_stops_is_closed_sandboxed(run, docker):  # noqa: F811
    from kraft.adapters import subprocess as sp

    from .test_subprocess import DOCKER

    sp.server_stopping()

    status, _ = await run(["true"], "s-exit", sandbox=DOCKER)

    assert status == "done"
    assert docker.read_text().splitlines() == ["kraft-w1-s-exit"]


@pytest.mark.parametrize("sandbox", [False, True], ids=["plain", "sandboxed"])
async def test_a_kept_sandboxed_session_keeps_its_container(run, database, docker, sandbox):  # noqa: F811
    from .test_subprocess import DOCKER

    row, survived = await _stopped_while_running(
        run, database, "s-kept", stopping={}, sandbox=DOCKER if sandbox else None
    )

    assert survived and row["status"] == "running"
    assert not docker.exists()  # `docker rm` never ran
