"""A step whose runner is a group, walked by the executor: machine `gpu` (one slot) and the
local worker (one slot) are both in group `grp`."""

import pytest
from support.chain_builders import v1_node, v1_task
from support.harness import entry_of, v1_chain
from support.remote import TOKEN, make_rig

from kraft import executor, store
from kraft.executor.context import LaunchContext
from kraft.remote import local_link, registry
from kraft.remote import runs as runs_mod

NO_SETUP = LaunchContext(repo_entry=entry_of({"setup_command": ""}))


def _workers(*, local: bool = True):
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                "gpu": {
                    "token_sha256": registry.hash_token(TOKEN),
                    "capacity": 1,
                    "groups": ["grp"],
                }
            },
            "local": {"groups": ["grp"], "capacity": 1} if local else {},
        }
    )


@pytest.fixture
async def grp(tmp_path, database, run_dirs, item_on):
    r = await make_rig(tmp_path, database, run_dirs, item_on, workers=_workers())
    yield r
    await r.listener.close()
    runs_mod.install(None)
    await r.hub.close()


async def _walk(it, database, run_dirs, launch=NO_SETUP):
    return await executor.run_once(database, run_dirs, work_item_id=it.id, launch=launch)


def _chain(repo, task=None, **node):
    task = task or v1_task("t", command="true")
    return v1_chain([v1_node("build", runner="grp", tasks=[task], **node)], repo=repo)


async def _again(it, database, run_dirs):
    await database.write(lambda c: store.mark_reentered(c, it.id))  # what the wait scheduler does
    await _walk(it, database, run_dirs)


async def test_a_free_remote_member_takes_the_step(grp, item_on, database, run_dirs, repo):
    it = await item_on(_chain(repo), repo=repo)
    await _walk(it, database, run_dirs)
    assert it.status() == "completed"
    assert [(s["runner"], s["status"]) for s in it.sessions()] == [("gpu", "done")]
    assert grp.hub.slots_used("gpu") == 0 and local_link.current().free()


async def test_a_busy_remote_member_sends_the_step_to_the_local_worker_at_once(
    grp, item_on, database, run_dirs, repo
):
    await grp.hub.claim("gpu", [], "other/1")  # gpu's one slot is taken
    it = await item_on(_chain(repo), repo=repo)
    await _walk(it, database, run_dirs)
    assert it.status() == "completed" and it.events("remote_step_waiting") == []
    assert [(s["runner"], s["status"]) for s in it.sessions()] == [(None, "done")]


async def test_a_step_with_no_free_member_parks_and_runs_on_whichever_frees_first(
    grp, item_on, database, run_dirs, repo
):
    held = await grp.hub.claim("gpu", [], "other/1")
    link = local_link.current()
    link.take("other/2")
    it = await item_on(_chain(repo), repo=repo)
    await _walk(it, database, run_dirs)
    assert it.status() == "waiting" and it.sessions() == []
    (event,) = it.events("remote_step_waiting")
    assert event["payload"]["reason"] == "waiting for a machine in grp"
    await _again(it, database, run_dirs)  # looked at again: nothing changed, still one event
    assert it.sessions() == [] and len(it.events("remote_step_waiting")) == 1
    link.release("other/2")  # the local worker frees first
    await _again(it, database, run_dirs)
    assert it.status() == "completed" and [s["runner"] for s in it.sessions()] == [None]
    assert held.machine == "gpu" and grp.hub.slots_used("gpu") == 1  # still the other step's


async def test_a_group_with_only_the_local_worker_free_waits_for_its_slot_not_a_refusal(
    item_on, tmp_path, database, run_dirs, repo
):
    workers = registry.WorkersFile.model_validate(
        {"workers": {}, "local": {"groups": ["solo"], "capacity": 1}}
    )
    r = await make_rig(tmp_path, database, run_dirs, item_on, workers=workers, online=False)
    try:
        link = local_link.current()
        link.take("other/1")
        chain = v1_chain(
            [v1_node("build", runner="solo", tasks=[v1_task("t", command="true")])], repo=repo
        )
        it = await item_on(chain, repo=repo)
        await _walk(it, database, run_dirs)
        assert it.status() == "waiting" and it.sessions() == []
        link.release("other/1")
        await _again(it, database, run_dirs)
        assert it.status() == "completed" and [s["runner"] for s in it.sessions()] == [None]
    finally:
        await r.listener.close()
        runs_mod.install(None)
        await r.hub.close()


async def test_two_scopes_with_one_free_remote_slot_place_themselves_one_remote_one_local(
    grp, item_on, database, run_dirs, repo
):
    scopes = [
        {"paths": ["**"], "command": "true"},
        {"paths": ["**"], "command": "echo second"},
    ]
    task = {
        "id": "t",
        "kind": "builtin",
        "ref": "kraft.verify_changed_test_scopes",
        "execution": "parallel",
    }
    it = await item_on(_chain(repo, task), repo=repo)
    launch = LaunchContext(repo_entry=entry_of({"setup_command": "", "test_scopes": scopes}))
    await _walk(it, database, run_dirs, launch)
    assert it.status() == "completed"
    assert sorted(s["runner"] or "local" for s in it.sessions()) == ["gpu", "local"]


async def test_an_unknown_name_is_still_a_config_error(grp, item_on, database, run_dirs, repo):
    chain = v1_chain(
        [v1_node("build", runner="nope", tasks=[v1_task("t", command="true")])], repo=repo
    )
    it = await item_on(chain, repo=repo)
    await _walk(it, database, run_dirs)
    (session,) = it.sessions()
    assert session["status"] == "config_error" and session["runner"] is None


async def test_a_machine_that_refuses_the_reservation_sends_the_step_on_to_the_local_worker(
    grp, item_on, database, run_dirs, repo, monkeypatch
):
    from kraft.remote.runs import Reserved

    async def full(machine, key, *, timeout=30):
        return Reserved(False, 1)

    monkeypatch.setattr(grp.runs, "reserve", full)
    it = await item_on(_chain(repo), repo=repo)
    await _walk(it, database, run_dirs)
    assert it.status() == "completed" and [s["runner"] for s in it.sessions()] == [None]
    assert not grp.hub.free("gpu", [])  # left alone until it reports a free slot


async def test_an_agent_step_on_a_group_runs_on_a_member(
    grp, item_on, database, run_dirs, repo, fake_agent
):
    chain = v1_chain(
        [v1_node("build", runner="grp", tasks=[v1_task("implement", kind="agent")])], repo=repo
    )
    it = await item_on(chain, repo=repo)
    await _walk(it, database, run_dirs)
    assert it.status() == "completed" and [s["runner"] for s in it.sessions()] == ["gpu"]
