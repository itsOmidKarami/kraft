"""`kraft.adapters.subprocess.run_task` for an in-place item: a session that
ends before it launches leaves no row holding the checkout. A sibling of
test_subprocess.py, which is at its line budget."""

from __future__ import annotations

import asyncio

import pytest
from support.harness import IN_PLACE, v1_named_chain

from kraft import store
from kraft.adapters import subprocess as sp


@pytest.mark.parametrize(
    ("target", "stopping", "left"),
    [(IN_PLACE, False, "config_error"), (None, False, "pending"), (IN_PLACE, True, "pending")],
    ids=["in-place", "worktree-as-before", "in-place-at-shutdown"],
)
async def test_a_task_cancelled_before_its_launch_holds_no_checkout(
    item_on, run_dirs, database, monkeypatch, tmp_path, target, stopping, left
):
    """Cancelled at its last wait before the launch (a co-task's failure, a
    stop): an in-place row left `pending` would hold the lease until a restart
    once the item stops. A worktree item's is left to restart's reattach, and
    so is any at shutdown, which reattach stops as `infra`."""
    chain = v1_named_chain(run_dirs.base / "t")
    a = await item_on(chain, "implementation", wid="a", target=target)
    await item_on(chain, wid="b", target=IN_PLACE)
    await database.write(lambda c: store.pause_work_item(c, "b", []))

    async def cancelled(*_a, **_kw):
        raise asyncio.CancelledError

    monkeypatch.setattr(sp, "_item_identity", cancelled)
    if stopping:
        sp.server_stopping()
    with pytest.raises(asyncio.CancelledError):
        await sp.run_task(
            database,
            run_dirs,
            session_id="s1",
            work_item_id="a",
            node_id="implementation",
            hook_point="implementation.main.implement",
            cmd=["touch", "ran"],
            cwd=tmp_path,
        )
    await database.write(lambda c: store.mark_needs_human(c, "a", "n", "stop", kind="failed"))

    [session] = a.sessions()
    assert session["status"] == left
    assert not (tmp_path / "ran").exists()
    if left == "config_error":
        assert database.read(lambda c: store.checkout_holder(c, "b")) is None
        log = (run_dirs.base / session["log_path"]).read_text()
        assert "ended before its launch was recorded" in log
