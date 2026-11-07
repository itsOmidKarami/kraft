"""`when_free.is_free` over a sliced item: the parent waits, the children run."""

from support.harness import v1_named_chain
from test_when_free import _set_status

from kraft import when_free


async def test_a_parent_waiting_on_its_slices_is_not_busy_but_its_children_are(
    item_on, database, run_dirs
):
    parent = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation")
    await database.write(
        lambda c: c.execute(
            "UPDATE work_items SET status = 'waiting', stop_kind = 'slices' WHERE id = ?",
            (parent.id,),
        )
    )
    assert when_free.is_free(database)

    child = await item_on(v1_named_chain(run_dirs.base / "t"), "implementation", wid="c1")
    await _set_status(database, child.id, "active")
    assert not when_free.is_free(database)
