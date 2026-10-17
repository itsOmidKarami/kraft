"""The checkout lease (`store.folder_holder`) while a stopped session's
processes die. A sibling of test_work_items.py, which is at its line budget."""

from __future__ import annotations

import subprocess

import psutil
import pytest
from support.harness import IN_PLACE, v1_named_chain

from kraft import store
from kraft.store import work_items


@pytest.mark.parametrize("reused", [False, True], ids=["its-own-process", "pid-reused"])
@pytest.mark.parametrize("frozen", [False, True], ids=["now", "a-frozen-clock"])
async def test_a_stopped_sessions_processes_hold_the_checkout_until_they_are_gone(
    item_on, run_dirs, database, monkeypatch, reused, frozen
):
    """A pause writes its sessions stopped before their processes die (a
    flush, then a group kill): until then they still write in the checkout.
    A pid now someone else's holds nothing. The window reads the store's
    clock, as the stop wrote it."""
    if frozen:
        monkeypatch.setattr(work_items, "_now", lambda: "2020-01-01T00:00:00+00:00")
    chain = v1_named_chain(run_dirs.base / "t")
    a = await item_on(chain, "implementation", wid="a", target=IN_PLACE)
    await item_on(chain, wid="b", target=IN_PLACE)
    dying = subprocess.Popen(["sleep", "30"], start_new_session=True)
    started = psutil.Process(dying.pid).create_time() + (1.0 if reused else 0.0)
    await a.session("s1", "implementation.main.implement", running=(dying.pid, started))
    await database.write(lambda c: store.pause_work_item(c, "a", ["s1"]))
    await database.write(lambda c: store.pause_work_item(c, "b", []))

    try:
        held = database.read(lambda c: store.checkout_holder(c, "b"))
    finally:
        dying.kill()
        dying.wait()
    assert held == (None if reused else "a")
    assert database.read(lambda c: store.checkout_holder(c, "b")) is None
