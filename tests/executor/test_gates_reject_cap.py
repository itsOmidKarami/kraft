"""A gate rejection that breaches its loop cap stops the item as `cap`. A
sibling of test_gates.py, which is at its line ceiling."""

from __future__ import annotations

import pytest

from kraft import executor, store
from kraft import policy as _policy

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


_FIX = {"fix_loop": {"tasks": [{"id": "fix", "kind": "subprocess", "command": "true"}]}}
_CAP = _policy.Cap(5, 3600)


async def _spend(it, counts):
    for key, n in counts.items():
        for _ in range(n):
            await it.database.write(lambda c, k=key: store.bump_counter(c, it.id, k, _CAP))


@pytest.mark.parametrize("by", ["human", "assistant", "agent", "kraft"])
async def test_a_rejection_gives_the_nodes_it_reruns_their_fix_rounds_back(item_on, by):
    """`a-gate-reject-gives-the-rerun-its-fix-rounds-back`: whoever rejected. The
    re-run measures new work, and what bounds it is the gate's own reject loop,
    which a rejection spends and only a person resets. A node before the re-entry
    point or after the gate is not run again, so its counter stands, as does a
    clock that is not a fix loop's."""
    it = await item_on(
        [
            {**_exec("spec"), **_FIX},
            {**_exec("implementation"), **_FIX},
            {**_exec("verification"), **_FIX},
            {"id": "gate", "kind": "gate", "reject_to": "implementation"},
            {**_exec("after"), **_FIX},
        ],
        repo="/r",
    )
    kept = {"spec.fix_loop": 1, "after.fix_loop": 1, "ci_infra:verification": 1}
    spent = {"implementation.fix_loop": 1, "verification.fix_loop": 2}
    await _spend(it, {**kept, **spent})

    await executor.apply_rejection(
        it.database,
        _cap(5),
        work_item_id=it.id,
        nodes=it.chain.chain.nodes,
        gate="gate",
        note="redo",
        by=by,
    )

    left = it.database.read(lambda c: store.cap_counts(c, it.id))
    assert left == {**kept, "gate_reject_loop": 1}
    assert [e["payload"] for e in it.events("cap_counters_reset")] == [
        {"by": by, "counters": spent}
    ]


async def test_a_rejection_that_re_runs_nothing_resets_nothing(item_on):
    """One that breached the reject loop parks the item: the retry a person
    answers with is what resets counters (`only-a-person-resets-a-cap-counter`).
    And a rejection with no fix loop spent in its span says nothing of a reset."""
    it = await item_on(
        [{**_exec("implementation"), **_FIX}, {"id": "gate", "kind": "gate"}], repo="/r"
    )
    nodes = it.chain.chain.nodes

    async def reject():
        return await executor.apply_rejection(
            it.database, _cap(1), work_item_id=it.id, nodes=nodes, gate="gate", note="no"
        )

    assert await reject() == 0
    assert it.events("cap_counters_reset") == []
    await _spend(it, {"implementation.fix_loop": 2})
    assert await reject() is None

    assert it.database.read(lambda c: store.cap_counts(c, it.id))["implementation.fix_loop"] == 2
    assert it.events("cap_counters_reset") == []


async def test_a_rejection_an_ended_item_did_not_take_resets_nothing(item_on):
    """The reset rides the write that records the rejection: an item that has
    ended records none (`store.write_status`), so nothing of it starts over."""
    it = await item_on(
        [{**_exec("implementation"), **_FIX}, {"id": "gate", "kind": "gate"}],
        repo="/r",
        status="completed",
    )
    await _spend(it, {"implementation.fix_loop": 2})

    await executor.apply_rejection(
        it.database, _cap(5), work_item_id=it.id, nodes=it.chain.chain.nodes, gate="gate", note="no"
    )

    assert it.events("gate_rejected") == []
    assert it.database.read(lambda c: store.cap_counts(c, it.id))["implementation.fix_loop"] == 2
    assert it.events("cap_counters_reset") == []
