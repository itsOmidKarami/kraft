"""A gate rejection that breaches its loop cap stops the item as `cap`. A
sibling of test_gates.py, which is at its line ceiling."""

from __future__ import annotations

from kraft import executor

from .test_gates import _cap, _exec


async def test_apply_rejection_returns_the_reentry_index_then_stops_at_the_cap(item_on):
    it = await item_on(
        [_exec("implementation"), {"id": "gate", "kind": "gate", "reject_to": "implementation"}],
        repo="/r",
    )
    nodes = it.chain.chain.nodes
    results = [
        await executor.apply_rejection(
            it.database, _cap(2), work_item_id=it.id, nodes=nodes, gate="gate", note=note
        )
        for note in ("not yet", "still not", "no")
    ]

    # The third breaches attempts=2: no re-entry, and the item is parked.
    assert results == [0, 0, None]
    assert it.status() == "needs_human"
    assert it.row()["stop_kind"] == "cap"
