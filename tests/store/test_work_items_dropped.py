"""A hold dropped by anything but the queue starting it: what its timeline keeps."""

import pytest
from support import schema

from kraft import events, store


@pytest.mark.parametrize(
    ("verb", "body", "drop", "detail"),
    [
        (
            "escalate",
            {"message": "look again"},
            lambda c: store.dequeue_work_item(c, "w1", why="paused"),
            "the queued escalate was dropped, with its message: look again",
        ),
        (
            "escalate",
            {"message": "look again"},
            lambda c: store.mark_needs_human(c, "w1", "a", "stopped", kind="failed"),
            "the queued escalate was dropped, with its message: look again",
        ),
        (
            "retry",
            {"steer": "s"},
            lambda c: store.dequeue_work_item(c, "w1", why="paused"),
            "the queued retry was dropped, with its steer: s",
        ),
    ],
    ids=["escalation-paused", "escalation-superseded", "retry-paused"],
)
async def test_a_dropped_hold_keeps_what_a_person_said_with_it(database, verb, body, drop, detail):
    """A queued escalation's message was never sent, so nothing else records it."""

    def seed(c):
        schema.insert_item(c, status="needs_human")
        assert store.queue_work_item(
            c, "w1", verb=verb, body=body, headers={}, from_statuses=["needs_human"]
        )

    await database.write(seed)

    await database.write(drop)

    log = database.read(lambda c: events.read_after(c, 0, "w1"))
    [dequeued] = [e["payload"] for e in log if e["type"] == "work_item_dequeued"]
    assert dequeued["detail"] == detail
