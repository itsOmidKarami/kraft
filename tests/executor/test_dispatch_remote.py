"""A node placed on a machine, walked by the executor."""

from support.chain_builders import v1_node, v1_task
from support.harness import entry_of, v1_chain

from kraft import executor, store
from kraft.executor.context import LaunchContext

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))


async def _walk(it, database, run_dirs):
    return await executor.run_once(database, run_dirs, work_item_id=it.id, launch=NO_SETUP)


async def test_a_step_on_an_offline_machine_parks_the_item_and_runs_when_it_returns(
    rig_offline, item_on, database, run_dirs, repo
):
    chain = v1_chain(
        [v1_node("build", runner="gpu", tasks=[v1_task("t", command="true")])], repo=repo
    )
    it = await item_on(chain, repo=repo)
    await _walk(it, database, run_dirs)
    assert it.status() == "waiting" and it.sessions() == []
    (event,) = it.events("remote_step_waiting")
    assert event["payload"]["reason"] == "waiting for gpu (offline: never connected)"
    await database.write(lambda c: store.mark_reentered(c, it.id))
    await _walk(it, database, run_dirs)  # looked at again, nothing changed: still one event
    assert len(it.events("remote_step_waiting")) == 1
    await rig_offline.connect()
    await database.write(lambda c: store.mark_reentered(c, it.id))  # what the wait scheduler does
    await _walk(it, database, run_dirs)
    assert it.status() == "completed"
    assert [(s["runner"], s["status"]) for s in it.sessions()] == [("gpu", "done")]


async def test_a_node_override_beats_the_librarys_placement_and_an_unknown_machine_stops_the_step(
    rig, item_on, database, run_dirs, repo
):
    chain = v1_chain(
        [v1_node("build", runner="gpu", tasks=[v1_task("t", command="true")])], repo=repo
    )
    it = await item_on(chain, repo=repo)
    await database.write(
        lambda c: store.set_node_overrides(c, it.id, {"build": {"runner": "ghost"}})
    )
    await _walk(it, database, run_dirs)
    (session,) = it.sessions()
    assert session["status"] == "config_error" and session["runner"] is None
    assert it.status() == "needs_human"


async def test_the_default_placement_is_local_and_leaves_the_runner_column_empty(
    item_on, database, run_dirs, repo
):
    it = await item_on(v1_chain([v1_node("build")], repo=repo), repo=repo)
    await _walk(it, database, run_dirs)
    assert it.status() == "completed" and all(s["runner"] is None for s in it.sessions())
