"""The checkout lease (`store.folder_holder`) while a stopped session's
processes die. A sibling of test_work_items.py, which is at its line budget."""

from __future__ import annotations

import subprocess

from support.harness import IN_PLACE, v1_named_chain

from kraft import store


async def test_a_stopped_sessions_processes_hold_the_checkout_until_they_are_gone(
    item_on, run_dirs, database
):
    """A pause writes its sessions stopped before their processes die (a
    flush, then a group kill): until then they still write in the checkout."""
    chain = v1_named_chain(run_dirs.base / "t")
    a = await item_on(chain, "implementation", wid="a", target=IN_PLACE)
    await item_on(chain, wid="b", target=IN_PLACE)
    dying = subprocess.Popen(["sleep", "30"], start_new_session=True)
    await a.session("s1", "implementation.main.implement", running=(dying.pid, 0.0))
    await database.write(lambda c: store.pause_work_item(c, "a", ["s1"]))
    await database.write(lambda c: store.pause_work_item(c, "b", []))

    try:
        assert database.read(lambda c: store.checkout_holder(c, "b")) == "a"
    finally:
        dying.kill()
        dying.wait()
    assert database.read(lambda c: store.checkout_holder(c, "b")) is None
