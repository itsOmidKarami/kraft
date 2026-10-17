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
    agent running in the checkout, however long it stays down. Never touched:
    a pid now someone else's, a pid leading no group of its own, and a
    worktree item's session, which holds no checkout."""
    chain = v1_named_chain(run_dirs.base / "t")
    it = await item_on(chain, "implementation", target=IN_PLACE)
    other = await item_on(chain, "implementation", wid="w2")
    left, long_ago, reused, follower, worktree = (
        subprocess.Popen(["sleep", "30"], start_new_session=n != 3) for n in range(5)
    )
    for item, sid, child, off in (
        (it, "s1", left, 0.0),
        (it, "s2", long_ago, 0.0),
        (it, "s3", reused, 0.5),
        (it, "s4", follower, 0.0),
        (other, "s1", worktree, 0.0),
    ):
        started = psutil.Process(child.pid).create_time() + off
        await item.session(sid, "implementation.main.implement", running=(child.pid, started))
    await database.write(lambda c: store.pause_work_item(c, it.id, ["s1", "s2", "s3", "s4"]))
    await database.write(lambda c: store.pause_work_item(c, other.id, ["s1"]))
    await database.write(
        lambda c: c.execute(
            "UPDATE worker_sessions SET exited_at = '2020-01-01T00:00:00+00:00' WHERE id = 's2'"
        )
    )

    try:
        assert await reattach.reap_stopped(database, grace=0.5) == sorted([left.pid, long_ago.pid])
        assert left.wait(timeout=5) is not None and long_ago.wait(timeout=5) is not None
        for child in (reused, follower, worktree):
            assert child.poll() is None, "a process Kraft cannot say it launched was signalled"
    finally:
        for child in (left, long_ago, reused, follower, worktree):
            child.kill()
            child.wait()
