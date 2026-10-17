"""`gates.follow_up`: Kraft's own agents at a stop, replayed once the checkout
they waited for frees (`store.queue_follow_up`)."""

from __future__ import annotations

import pytest

from kraft import store
from kraft.executor import gates as gates_module

from .test_gates_feedback import NO_SETUP, _published_thread

_CHAIN = [
    {
        "id": "implementation",
        "kind": "exec",
        "tasks": [{"id": "build", "kind": "agent", "harness": "fake", "prompt": "implement"}],
    },
    {"id": "review", "kind": "gate", "reject_to": "implementation"},
]


@pytest.mark.parametrize(
    ("at_gate", "calls"),
    [
        (
            True,
            [("reply", "review"), ("review", "awaiting_gate"), ("escalate", "awaiting_gate")],
        ),
        (False, [("review", "needs_human"), ("escalate", "needs_human")]),
    ],
    ids=["at-a-gate", "stuck"],
)
async def test_a_follow_up_runs_what_the_walk_runs_where_it_stopped(
    item_on, monkeypatch, at_gate, calls
):
    """At a gate: the reply agent for its unanswered threads, then its review.
    At a stop: the auto-escalation. Each checks for itself what is due."""
    it = await item_on(_CHAIN, "review" if at_gate else "implementation", status="needs_human")
    if at_gate:
        await it.database.write(lambda c: store.request_gate(c, it.id, "review", "review"))
        await _published_thread(it, gate="review", label="question")
    seen = []

    async def reply(db, run_dirs, **kw):
        seen.append(("reply", kw["gate"]))

    def then(name):
        async def step(status, *a, **kw):
            seen.append((name, status))
            return status

        return step

    monkeypatch.setattr("kraft.review_reply.run", reply)
    monkeypatch.setattr(gates_module, "review_gates", then("review"))
    monkeypatch.setattr(gates_module, "auto_escalate_stuck", then("escalate"))

    await gates_module.follow_up(it.database, it.run_dirs, work_item_id=it.id, launch=NO_SETUP)

    assert seen == calls


async def test_a_gate_review_stands_aside_while_its_follow_up_waits(item_on):
    """The reply queued for the checkout runs first, and the review after it:
    a reviewer now would run in the checkout ahead of the queued start."""
    it = await item_on(_CHAIN, "review", status="queued", auto_gate=True)
    await it.database.write(lambda c: store.request_gate(c, it.id, "review", "review"))
    await it.database.write(
        lambda c: c.execute("UPDATE work_items SET status = 'queued' WHERE id = ?", (it.id,))
    )

    status = await gates_module.review_gates(
        "awaiting_gate", it.database, it.run_dirs, work_item_id=it.id, launch=NO_SETUP
    )

    assert status == "queued"
