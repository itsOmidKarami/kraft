import asyncio
import json

import pytest

from kraft import db, executor, store


def test_apply_rejection_returns_the_reentry_index_then_stops_at_the_cap(tmp_path):
    from kraft import policy as _policy

    chain = {
        "nodes": [
            {"id": "implementation", "tasks": [], "gate_after": None},
            {
                "id": "human_review",
                "tasks": [],
                "gate_after": "human_review_approval",
                "reject_to": "implementation",
            },
        ]
    }
    policy = _policy.Policy(loops={}, default=_policy.Cap(attempts=2, wall_clock_s=3600))

    async def scenario():
        database = await db.Database.open(tmp_path / "k.db")
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w1",
                    bead_id=None,
                    title="t",
                    repo="/r",
                    chain_template="default",
                    chain_definition=json.dumps(chain),
                )
            )
            first = await executor.apply_rejection(
                database,
                policy,
                work_item_id="w1",
                chain=chain,
                gate="human_review_approval",
                note="not yet",
            )
            second = await executor.apply_rejection(
                database,
                policy,
                work_item_id="w1",
                chain=chain,
                gate="human_review_approval",
                note="still not",
            )
            third = await executor.apply_rejection(
                database,
                policy,
                work_item_id="w1",
                chain=chain,
                gate="human_review_approval",
                note="no",
            )
            assert (first, second) == (0, 0)
            # Third breaches attempts=2: no re-entry, and the item is parked.
            assert third is None
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id = 'w1'").fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reject_target_rejects_a_forward_node_with_a_value_error():
    chain = {
        "nodes": [
            {"id": "a", "tasks": [], "gate_after": "g"},
            {"id": "b", "tasks": [], "gate_after": None},
        ]
    }
    with pytest.raises(ValueError):
        executor.reject_target(chain, 0, "b")
