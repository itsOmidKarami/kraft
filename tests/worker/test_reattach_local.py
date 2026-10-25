"""A restart asks the local worker whether it holds each live local session."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import psutil
import pytest
from support.local_worker import ScriptedLink

from kraft.remote import local_link
from kraft.worker import reattach
from worker.test_reattach import _CHAIN, DEAD, IMPLEMENT, _sleeper


@pytest.fixture
async def item(item_on):
    return await item_on(_CHAIN, "implementation")


async def test_live_local_session_is_adopted_when_the_worker_holds_it(item, database, run_dirs):
    link = ScriptedLink([True])
    local_link.install(link)
    with _sleeper() as (proc, started):
        await item.session("s1", IMPLEMENT, running=(proc.pid, started))
        summary, adopted = await reattach.reattach(database, run_dirs)
        task = adopted["w1-s1"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert link.calls == [["w1/s1"]]
    assert summary.unknown == [] and summary.resolved_from_file == []


async def test_a_live_process_the_worker_does_not_hold_is_not_adopted(item, database, run_dirs):
    """The worker's answer decides, not the pid: a process the worker says is not the session."""
    link = ScriptedLink([False, False])
    local_link.install(link)
    with _sleeper() as (proc, started):
        await item.session("s1", IMPLEMENT, running=(proc.pid, started))
        summary, adopted = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert adopted == {} and summary.unknown == ["w1/s1"]


@pytest.mark.parametrize(
    ("path", "evidence", "expected"),
    [
        ("implementation.main.implement", "result", "done"),
        ("verify.main.check", "exit", "done"),
        ("verify.main.check", None, "unknown"),
    ],
    ids=["result-file-present", "exit-file-present", "no-evidence"],
)
async def test_local_row_the_worker_does_not_hold_ends_by_evidence(
    item_on, database, run_dirs, path, evidence, expected
):
    local_link.install(ScriptedLink([False, False]))
    it = await item_on(_CHAIN, "verify")
    await it.session("s1", path, running=DEAD)
    if evidence == "result":
        (run_dirs.results / "s1.json").write_text('{"status": "done"}')
    if evidence == "exit":
        (run_dirs.results / "s1.exit").write_text("0")

    await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)

    assert it.sessions()[0]["status"] == expected  # never "failed" for want of evidence


async def test_a_live_session_started_before_the_worker_existed_is_adopted(
    item, database, run_dirs
):
    """A session an older server forked is no child of the worker: it is held by pid identity,
    and the worker's table lists it afterwards."""
    link = local_link.current()
    with _sleeper() as (proc, started):
        await item.session("s1", IMPLEMENT, running=(proc.pid, started))
        _, adopted = await reattach.reattach(database, run_dirs)
        task = adopted["w1-s1"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert link.jobs.running() == ["w1/s1"]


async def test_a_remote_row_keeps_the_identity_check(item, database, run_dirs):
    """A remote step's stand-in is a process of this server, not of the worker."""
    link = ScriptedLink([True])
    local_link.install(link)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        started = psutil.Process(proc.pid).create_time()
        await item.session("s1", IMPLEMENT, running=(proc.pid, started), runner="gpu")
        _, adopted = await reattach.reattach(database, run_dirs, grace_retry_delay_s=0)
        asked = list(link.calls)
        for task in adopted.values():
            task.cancel()
        await asyncio.gather(*adopted.values(), return_exceptions=True)
    finally:
        proc.kill()
        proc.wait()

    assert asked == []


@pytest.mark.parametrize(
    ("stopping", "closed"),
    [({}, []), ({"keep_local": False}, ["w1/s1"]), (None, ["w1/s1"])],
    ids=["kept-by-a-clean-stop", "now-closes-it", "cancelled-while-running-closes-it"],
)
async def test_adopted_sandboxed_session_survives_a_second_stop(
    item, database, run_dirs, monkeypatch, stopping, closed
):
    """Stop, start, stop again: the second stop cancels the *adopted* task, and its `finally`
    must not close the container (or the route out) of a session the stop leaves running."""
    from kraft.adapters import subprocess as sp

    closes = []

    async def close_sandbox(kind, work_item_id, session_id, *, egress=False):
        closes.append(f"{work_item_id}/{session_id}")

    monkeypatch.setattr(reattach, "_close_sandbox", close_sandbox)
    with _sleeper() as (proc, started):
        await item.session("s1", IMPLEMENT, running=(proc.pid, started), sandbox="docker")
        _, adopted = await reattach.reattach(database, run_dirs)
        task = adopted["w1-s1"]
        await asyncio.sleep(0.1)  # the task is inside its poll loop
        if stopping is not None:
            sp.server_stopping(**stopping)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert closes == closed
