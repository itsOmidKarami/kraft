"""`launch.placed`: a step on a group takes its slot when it chooses, and gives back what the
launch did not take over."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from support.chain_builders import v1_node
from support.harness import v1_chain
from support.remote import Peer, attach

from kraft import store
from kraft.remote import launch, local_link, protocol, registry
from kraft.remote import runs as runs_mod
from kraft.remote.hub import Hub
from kraft.remote.runs import Reserved, Runs


def _workers(local_group: bool = True, **machines):
    return registry.WorkersFile.model_validate(
        {
            "workers": {
                n: {
                    "token_sha256": registry.hash_token(f"kwt_{n}"),
                    "capacity": 1,
                    "groups": ["gpu"],
                    **extra,
                }
                for n, extra in (machines or {"a": {}, "b": {}}).items()
            },
            "local": {"groups": ["gpu"], "capacity": 1} if local_group else {},
        }
    )


@pytest.fixture
async def world(database, run_dirs, item_on, repo):
    """A hub with machines `a` and `b` (group `gpu`, one slot each) online, the local worker in
    the group, a `Runs` whose reservations are recorded, and an item row."""
    hub = Hub("2.3.0")
    hub.apply(_workers())
    runs = Runs(hub, run_dirs=run_dirs, db=database, call=AsyncMock())
    hub.runs = runs
    runs_mod.install(runs)
    for n in ("a", "b"):
        await attach(hub, Peer(), n, caps=frozenset({protocol.RESERVE}), token=f"kwt_{n}")
    reserved, unreserved, refuse = [], [], set()

    async def reserve(machine, key, *, timeout=30):
        reserved.append((machine, key))
        return Reserved(machine not in refuse, 1 if machine in refuse else None)

    runs.reserve = reserve
    runs.unreserve = AsyncMock(side_effect=lambda m, k: unreserved.append((m, k)))
    runs.unreserve_soon = lambda m, k: unreserved.append((m, k))
    it = await item_on(v1_chain([v1_node("build")], repo=repo), repo=repo)
    row = database.read(lambda c: c.execute("SELECT * FROM work_items").fetchone())

    class World:
        pass

    w = World()
    w.hub, w.runs, w.row, w.db = hub, runs, row, database
    w.reserved, w.unreserved, w.refuse, w.it = reserved, unreserved, refuse, it
    yield w
    runs_mod.install(None)
    await hub.close()


def _placed(w, key="w/1", *, can_park=True, resume=False, needs=()):
    return launch.placed(
        w.db,
        w.row,
        "build",
        "build.main.t",
        "gpu",
        [],
        session_key=key,
        resume=resume,
        can_park=can_park,
        needs=needs,
    )


async def test_the_first_free_remote_member_is_taken_and_reserved_then_given_back_on_exit(world):
    async with _placed(world) as p:
        assert (p.runner, p.held.machine, p.held_local) == ("gpu", "a", None)
        assert world.reserved == [("a", "w/1")] and world.hub.slots_used("a") == 1
    assert world.hub.slots_used("a") == 0 and world.unreserved == [("a", "w/1")]


@pytest.mark.parametrize("how", ["normal", "error", "cancel"])
async def test_what_it_holds_is_released_however_the_launch_ends(world, how):
    async def body():
        async with _placed(world):
            assert world.hub.slots_used("a") == 1
            if how == "error":
                raise RuntimeError("the launch failed")
            if how == "cancel":
                await asyncio.sleep(30)

    if how == "error":
        with pytest.raises(RuntimeError):
            await body()
    elif how == "cancel":
        task = asyncio.create_task(body())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await body()
    assert world.hub.slots_used("a") == 0 and world.unreserved == [("a", "w/1")]


async def test_a_member_that_refuses_the_reservation_is_marked_full_and_the_next_is_taken(world):
    world.refuse.add("a")
    async with _placed(world) as p:
        assert p.held.machine == "b"
        assert world.hub.slots_used("a") == 0 and not world.hub.free("a", [])
    assert ("a", "w/1") in world.unreserved


async def test_the_local_worker_is_taken_when_every_remote_member_is_busy_or_refuses(world):
    world.refuse.update({"a", "b"})
    link = local_link.current()
    async with _placed(world) as p:
        assert (p.runner, p.held, p.held_local) == ("local", None, "w/1")
        assert link.free() is False  # its one slot is taken
    assert link.free()


async def test_two_placements_never_take_one_free_slot(world):
    world.refuse.update({"a", "b"})
    async with _placed(world, "w/1") as first, _placed(world, "w/2") as second:
        assert first.runner == "local" and second.runner == "gpu"
        assert second.wait == "waiting for a machine in gpu" and second.held_local is None


async def test_a_step_that_cannot_park_waits_for_whichever_side_frees_first(world):
    world.refuse.update({"a", "b"})
    link = local_link.current()
    link.take("other/1")  # the local worker's one slot is busy too
    waiter = asyncio.create_task(_enter(world))
    await asyncio.sleep(0.1)
    assert not waiter.done()
    saying = world.db.read(
        lambda c: c.execute(
            "SELECT payload FROM events WHERE type = 'remote_step_waiting'"
        ).fetchall()
    )
    assert len(saying) == 1 and "waiting for a machine in gpu" in saying[0]["payload"]
    link.release("other/1")
    placement = await asyncio.wait_for(waiter, 2)
    assert placement.runner == "local"
    placement.release()


async def _enter(world):
    async with _placed(world, "w/9", can_park=False) as p:
        # hand the held slot out of the context so the test can look at it
        held = launch.Placement.__new__(launch.Placement)
        held.__dict__.update(p.__dict__)
        p.held = p.held_local = None
        return held


async def test_a_codex_step_places_again_on_the_local_worker_when_no_remote_member_has_the_cap(
    world,
):
    needs = {protocol.CODEX_HOOK: "the hook"}
    async with _placed(world) as p:
        assert p.held.machine == "a"
        await p.choose(needs)  # what `PlaceAgain` asks for
        assert p.runner == "local" and p.held is None and p.held_local == "w/1"
    assert world.hub.slots_used("a") == 0


async def test_a_resumed_session_goes_back_to_where_it_ran_and_waits_for_it(world, database):
    await database.write(
        lambda c: store.create_session(
            c,
            id="s0",
            work_item_id=world.row["id"],
            node_id="build",
            hook_point="build.main.t",
            log_path="/l",
            result_path="/r",
            runner="b",
        )
    )
    async with _placed(world, resume=True) as p:
        assert p.held.machine == "b" and p.runner == "b"
    world.hub.mark_full("b")
    async with _placed(world, resume=True) as p:
        assert p.held is None and p.wait == "waiting for b"  # not "a", though it is free
    async with _placed(world, resume=False) as p:
        assert p.held.machine == "a"


async def test_a_group_that_loses_its_last_member_refuses_a_step_waiting_for_it(world):
    world.refuse.update({"a", "b"})
    link = local_link.current()
    link.take("other/1")
    waiter = asyncio.create_task(_enter(world))
    await asyncio.sleep(0.1)
    world.hub.apply(registry.WorkersFile.model_validate({}))
    placement = await asyncio.wait_for(waiter, 2)
    assert "not a machine or a group" in placement.refusal


@pytest.fixture
def agent_calls(monkeypatch):
    """`run_agent_task` with the launch itself replaced: what it was handed, by call."""
    from kraft.adapters import agent

    calls = []

    async def launched(db, run_dirs, **kw):
        calls.append((kw["runner"], kw["placed_claim"].machine if kw["placed_claim"] else None))
        return "done"

    monkeypatch.setattr(agent, "_run_agent_task", launched)
    return calls


async def _agent_step(world, *, follow_node, hook_point="escalation"):
    from kraft.adapters import agent

    return await agent.run_agent_task(
        world.db,
        None,
        follow_node=follow_node,
        runner="gpu",
        work_item_id=world.row["id"],
        node_id="build",
        hook_point=hook_point,
        session_id="s9",
        repo_entry=None,
    )


@pytest.mark.parametrize(
    ("ran", "want"),
    [({"runner": "b"}, ("b", "b")), ({"pid": 7}, (None, None))],
    ids=["on-a-machine", "on-the-local-worker"],
)
async def test_an_escalation_or_review_reply_runs_where_its_nodes_task_ran(
    world, agent_calls, ran, want
):
    await world.db.write(
        lambda c: store.create_session(
            c,
            id="s0",
            work_item_id=world.row["id"],
            node_id="build",
            hook_point="build.main.t",
            log_path="/l",
            result_path="/r",
            runner=ran.get("runner"),
        )
    )
    if "pid" in ran:
        await world.db.write(
            lambda c: store.session_running(c, "s0", 7, None, work_item_id=world.row["id"])
        )
    assert await _agent_step(world, follow_node=True) == "done"
    assert agent_calls == [want]
    assert world.hub.slots_used("b") == 0 and local_link.current().free()


async def test_a_first_time_review_on_a_group_picks_a_free_member_the_local_worker_included(
    world, agent_calls
):
    await _agent_step(world, follow_node=False, hook_point="review.auto")
    world.refuse.update({"a", "b"})
    await _agent_step(world, follow_node=False, hook_point="review.auto")
    assert agent_calls == [("gpu", "a"), (None, None)]


async def test_a_launch_the_group_member_cannot_take_places_the_step_again(world, monkeypatch):
    from kraft.adapters import agent

    seen = []

    async def launched(db, run_dirs, **kw):
        seen.append((kw["runner"], kw["placed_claim"].machine if kw["placed_claim"] else None))
        if len(seen) == 1:  # the codex hook it cannot trust
            raise launch.PlaceAgain({protocol.CODEX_HOOK: "the hook"})
        return "done"

    monkeypatch.setattr(agent, "_run_agent_task", launched)
    assert await _agent_step(world, follow_node=False, hook_point="review.auto") == "done"
    assert seen == [("gpu", "a"), (None, None)]  # a had no cap: the local worker took it
