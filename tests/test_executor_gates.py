import asyncio
import json

import pytest

from kraft import db, events, executor, store
from kraft.executor import gates as gates_module


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


def test_review_gates_reads_the_items_own_node_override_not_the_templates(tmp_path, monkeypatch):
    """UI v2 · 04 point 1: the template turns `auto_escalate` on for this gate,
    but the item's own `node_overrides` turns it back off -- agent gate review
    must not fire. If `review_gates` read `chain_definition` straight, this
    node would still show `auto_escalate: true` and `gate_review.review`
    would be called (and this test's monkeypatch would raise).
    """
    chain = {
        "nodes": [
            {"id": "a", "tasks": [], "gate_after": "g", "auto_escalate": True},
        ]
    }

    def _boom(*a, **kw):
        raise AssertionError("gate_review.review must not run: the item's override disarmed it")

    monkeypatch.setattr(gates_module.gate_review, "review", _boom)

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
                    auto_gate=True,
                )
            )
            await database.write(
                lambda c: store.set_node_overrides(c, "w1", {"a": {"auto_escalate": False}})
            )
            await database.write(lambda c: events.append(c, "w1", "gate_requested", {"gate": "g"}))
            status = await gates_module.review_gates(
                "awaiting_gate", database, tmp_path, work_item_id="w1", registry=None
            )
            assert status == "awaiting_gate"
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
