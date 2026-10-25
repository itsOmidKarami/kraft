"""`store.pinned_machine`: where an `any` step stays for the rest of a pass."""

from support.store_fixtures import mk_item

from kraft import store


def _session(database, sid, **kw):
    return database.write(
        lambda c: store.create_session(
            c,
            id=sid,
            work_item_id="w1",
            node_id="verify",
            hook_point="on.test.run",
            log_path=f"/l/{sid}",
            result_path=f"/r/{sid}",
            **kw,
        )
    )


async def test_a_nodes_machine_is_that_of_its_latest_remote_session_since_it_was_entered(
    database,
):
    await mk_item(database)

    def pinned():
        return database.read(lambda c: store.pinned_machine(c, "w1", "verify"))

    assert pinned() is None
    for sid, runner in (("a", "gpu"), ("b", None), ("c", "box")):
        await _session(database, sid, runner=runner)
    assert pinned() == "box"
    await database.write(lambda c: store.enter_node(c, "w1", "verify"))  # entered again: a new pass
    assert pinned() is None
    await _session(database, "d", runner="gpu")
    assert pinned() == "gpu"


async def test_a_tasks_runner_is_where_its_latest_started_session_ran_local_when_it_ran_there(
    database,
):
    await mk_item(database)

    def pinned(hook_point="on.test.run"):
        return database.read(lambda c: store.pinned_runner(c, "w1", "verify", hook_point))

    assert pinned() is None
    await _session(database, "a", runner="gpu")
    assert pinned() == "gpu"
    await _session(database, "b")  # never started: no machine, no pid
    assert pinned() == "gpu"
    await database.write(lambda c: store.session_running(c, "b", 4242, None, work_item_id="w1"))
    assert pinned() == "local"  # started on the local worker
    assert pinned("another.task") is None  # another task's machine is not this task's
    assert database.read(lambda c: store.pinned_runner(c, "w1", "verify")) == "local"  # any task
    await database.write(lambda c: store.enter_node(c, "w1", "verify"))
    assert pinned() is None
