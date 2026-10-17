"""The checkout lease (`store.folder_holder`): which folder it is, and a
stopped session's processes while they die. A sibling of test_work_items.py,
which is at its line budget."""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil
import pytest
from support.harness import IN_PLACE, v1_named_chain

from kraft import store
from kraft.store import work_items


@pytest.mark.parametrize(
    ("off", "holds"),
    [(0.0, True), (0.5, False), (1.0, True), (-1.0, True), (2.0, False)],
    ids=["its-own-process", "pid-reused", "clock-stepped", "clock-stepped-back", "clock-leapt"],
)
@pytest.mark.parametrize("frozen", [False, True], ids=["now", "a-frozen-clock"])
async def test_a_stopped_sessions_processes_hold_the_checkout_until_they_are_gone(
    item_on, run_dirs, database, monkeypatch, off, holds, frozen
):
    """A pause writes its sessions stopped before their processes die (a
    flush, then a group kill): until then they still write in the checkout.
    A pid now someone else's holds nothing. The window reads the store's
    clock, as the stop wrote it. A wall clock stepped across a second moves
    Linux's start times by one whole second (`reattach._same_start`); a
    bigger step reads as someone else's."""
    if frozen:
        monkeypatch.setattr(work_items, "_now", lambda: "2020-01-01T00:00:00+00:00")
    chain = v1_named_chain(run_dirs.base / "t")
    a = await item_on(chain, "implementation", wid="a", target=IN_PLACE)
    await item_on(chain, wid="b", target=IN_PLACE)
    dying = subprocess.Popen(["sleep", "30"], start_new_session=True)
    started = psutil.Process(dying.pid).create_time() + off
    await a.session("s1", "implementation.main.implement", running=(dying.pid, started))
    await database.write(lambda c: store.pause_work_item(c, "a", ["s1"]))
    await database.write(lambda c: store.pause_work_item(c, "b", []))

    try:
        held = database.read(lambda c: store.checkout_holder(c, "b"))
    finally:
        dying.kill()
        dying.wait()
    assert held == ("a" if holds else None)
    assert database.read(lambda c: store.checkout_holder(c, "b")) is None


@pytest.mark.parametrize(
    ("linked", "spelling"),
    [("b", "link"), ("a", "link"), ("b", "case")],
    ids=["waiter-via-link", "holder-via-link", "waiter-in-another-case"],
)
async def test_the_lease_is_the_folder_not_its_spelling(
    item_on, run_dirs, database, repo, linked, spelling
):
    """Two `repos.yaml` spellings of one checkout share its lease: through a
    link, or, on a case-insensitive volume (macOS's default), in another case."""
    chain = v1_named_chain(run_dirs.base / "t")
    if spelling == "case":
        link = Path(str(repo).swapcase())
        if not link.exists():
            pytest.skip("this volume tells the cases of a name apart")
    else:
        link = repo.parent / "link"
        link.symlink_to(repo)
    await item_on(chain, wid="a", target=IN_PLACE, repo=link if linked == "a" else repo)
    await item_on(chain, wid="b", target=IN_PLACE, repo=link if linked == "b" else repo)
    await database.write(lambda c: store.pause_work_item(c, "b", []))

    assert database.read(lambda c: store.checkout_holder(c, "b")) == "a"
