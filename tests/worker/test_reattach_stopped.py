"""`reattach.reap_stopped`: at startup, the process groups of in-place
sessions a stopped server never finished killing. A sibling of
test_reattach.py, which is at its line budget."""

from __future__ import annotations

import subprocess

import psutil
from support.harness import IN_PLACE, v1_named_chain

from kraft import store
from kraft.worker import reattach


async def test_start_up_stops_what_a_pause_left_running_and_nothing_else(
    item_on, run_dirs, database
):
    """A server stopped between a pause's write and its kill leaves the
    agent running in the checkout; a pid now someone else's is never touched."""
    it = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation", target=IN_PLACE)
    left, reused = (subprocess.Popen(["sleep", "30"], start_new_session=True) for _ in range(2))
    for sid, child, off in (("s1", left, 0.0), ("s2", reused, 1.0)):
        started = psutil.Process(child.pid).create_time() + off
        await it.session(sid, "implementation.main.implement", running=(child.pid, started))
    await database.write(lambda c: store.pause_work_item(c, it.id, ["s1", "s2"]))

    try:
        assert await reattach.reap_stopped(database, grace=0.5) == [left.pid]
        assert left.wait(timeout=5) is not None
        assert reused.poll() is None, "a process Kraft cannot say it launched was signalled"
    finally:
        for child in (left, reused):
            child.kill()
            child.wait()
