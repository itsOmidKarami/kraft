"""`stop_kind` on the needs_human stops `kraft.worker.reattach` writes itself:
a crashed escalation resume (`infra`) and a capped adopted session (`cap`). A
sibling of test_reattach.py."""

from __future__ import annotations

from kraft import caps
from kraft.worker import reattach

from .test_reattach import _CHAIN, DEAD, IMPLEMENT


async def test_a_crashed_escalation_resume_marks_the_work_item_needs_human(
    item_on, database, run_dirs, monkeypatch
):
    """`_guarded_resume_adopted_escalation`'s own catch (Kraft-atdbw): a crash
    consuming a deferred self-retry after a restart must still stop the item
    for a human, not vanish into asyncio's default handler."""
    item = await item_on(_CHAIN, "implementation")

    async def _boom(db, run_dirs, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(reattach, "_resume_adopted_escalation", _boom)

    await reattach._guarded_resume_adopted_escalation(
        database,
        run_dirs,
        work_item_id=item.id,
        node_id="implementation",
        session_id="s1",
        policy=None,
        launch_factory=None,
        bd_cwd=None,
        on_approve=None,
    )

    assert item.status() == "needs_human"
    assert item.row()["stop_kind"] == "infra"
    reason = item.events("work_item_needs_human")[0]["payload"]["reason"]
    assert "resume_after_escalation crashed" in reason and "boom" in reason


async def test_a_capped_adopted_session_is_stopped_for_a_human(item_on, database, run_dirs):
    """`_stop_at_cap`: an adopted session whose time cap ran out while nobody
    was watching still stops the item, under `cap` (Kraft UI v2 · B1's
    `_stop_for_wait_timeout` sibling for a session Kraft is polling itself)."""
    item = await item_on(_CHAIN, "implementation")
    await item.session("s1", IMPLEMENT)
    row = database.read(
        lambda c: c.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    )
    hit = caps.Hit(scope="", field="time_cap_minutes", minutes=1, remaining_s=0.0)

    await reattach._stop_at_cap(database, row, DEAD[0], hit)

    assert item.status() == "needs_human"
    assert item.row()["stop_kind"] == "cap"
    assert item.sessions()[0]["status"] == "capped_out"
    assert item.events("work_item_needs_human")[-1]["payload"]["limit"] == {
        "path": "",
        "key": "time_cap_minutes",
        "value": 1,
    }
