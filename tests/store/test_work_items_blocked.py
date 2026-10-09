"""An item that comes after others: how the store holds, releases and drops it."""

import pytest
from support import schema

from kraft import events, store


def _blocked_on(c, status, wid="w1", dep="d1"):
    """`wid`, paused and then blocked, coming after `dep`, which is `status`."""
    schema.insert_item(c, wid=dep, status=status)
    schema.insert_item(c, wid=wid, status="paused")
    store.set_dependencies(c, wid, [dep])
    assert store.block_work_item(
        c, wid, verb="resume", body={}, headers={}, from_statuses=["paused"]
    )


def _item_status(c, wid="w1"):
    return c.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()[0]


@pytest.mark.parametrize("status", ["paused", "active", "waiting", "rate_limited", "needs_human"])
async def test_a_blocked_item_is_released_only_when_what_it_comes_after_completed(database, status):
    """`waiting` is where a merged item sits while a post-merge node runs: a
    merge alone releases nothing."""
    await database.write(lambda c: _blocked_on(c, status))

    assert await database.write(store.release_blocked) == []
    assert database.read(_item_status) == "blocked"

    await database.write(
        lambda c: c.execute("UPDATE work_items SET status = 'completed' WHERE id = 'd1'")
    )
    assert await database.write(store.release_blocked) == ["w1"]
    assert database.read(_item_status) == "queued"
    assert database.read(store.queued_ids) == ["w1"]
    types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w1"))]
    assert types[-2:] == ["work_item_blocked", "work_item_unblocked"]


async def test_a_blocked_item_goes_back_when_what_it_comes_after_is_abandoned(database):
    await database.write(lambda c: _blocked_on(c, "abandoned"))

    assert await database.write(store.release_blocked) == []

    assert database.read(_item_status) == "paused"
    last = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
    assert (last["type"], last["payload"]["why"]) == ("work_item_dequeued", "dependency_abandoned")
    assert "d1" in last["payload"]["detail"]
    # Its dependency is still declared: a person drops it or abandons the item.
    assert [d["id"] for d in database.read(lambda c: store.dependencies_of(c, "w1"))] == ["d1"]


async def test_pausing_a_blocked_item_puts_it_back_with_its_dependencies(database):
    await database.write(lambda c: _blocked_on(c, "active"))

    assert await database.write(lambda c: store.dequeue_work_item(c, "w1", why="paused"))

    assert database.read(_item_status) == "paused"
    assert [d["id"] for d in database.read(lambda c: store.unmet_dependencies(c, "w1"))] == ["d1"]


@pytest.mark.parametrize(
    ("only", "dropped", "left"),
    [(None, ["d1", "d3"], ["d2"]), ("d2", ["d2"], ["d1", "d3"]), ("nope", [], ["d1", "d2", "d3"])],
    ids=["every-unmet-one", "the-one-named", "not-a-dependency"],
)
async def test_dropping_dependencies(database, only, dropped, left):
    """With no name, the unmet ones go and a met one stays on the record."""

    def seed(c):
        schema.insert_item(c, wid="d1", status="active")
        schema.insert_item(c, wid="d2", status="completed")
        schema.insert_item(c, wid="d3", status="paused")
        schema.insert_item(c, wid="w1", status="paused")
        store.set_dependencies(c, "w1", ["d1", "d2", "d3"])

    await database.write(seed)

    assert await database.write(lambda c: store.drop_dependencies(c, "w1", only)) == dropped
    assert [d["id"] for d in database.read(lambda c: store.dependencies_of(c, "w1"))] == left


@pytest.mark.parametrize(
    ("ids", "problem"),
    [(["gone"], "no work item 'gone'"), (["dead"], "abandoned"), (["done", "live"], None)],
    ids=["unknown", "abandoned", "fine"],
)
async def test_what_a_new_item_may_come_after(database, ids, problem):
    def seed(c):
        schema.insert_item(c, wid="dead", status="abandoned")
        schema.insert_item(c, wid="done", status="completed")
        schema.insert_item(c, wid="live", status="active")

    await database.write(seed)
    found = database.read(lambda c: store.dependency_problem(c, ids))
    assert (found is None) if problem is None else (problem in found)
    assert database.read(lambda c: store.any_unfinished(c, ["done"])) is False
    assert database.read(lambda c: store.any_unfinished(c, ["done", "live"])) is True
