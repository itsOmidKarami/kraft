"""`stops.claimed_or_stopped`: the invariant that no path may leave a work item
claimed and unowned."""

from __future__ import annotations

import asyncio

import pytest
from support.harness import v1_chain, v1_item

from kraft import db, store
from kraft.executor import stops
from kraft.paths import RunDirs


def _chain(repo):
    return v1_chain(
        [
            {
                "id": "implementation",
                "kind": "exec",
                "tasks": [{"id": "build", "kind": "subprocess", "command": "true"}],
            }
        ],
        repo=repo,
    )


def _drive(tmp_path, body):
    async def main():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await v1_item(database, _chain(tmp_path), repo=str(tmp_path), wid="w1")
            await database.write(lambda c: store.enter_node(c, "w1", "implementation"))
            await database.write(
                lambda c: store.claim_for_run(c, "w1", from_statuses=["active", "paused"])
            )
            assert _status(database) == "active", "the fixture did not claim the item"
            return await body(database)
        finally:
            await database.close()

    return asyncio.run(main())


def _status(database, wid="w1"):
    return database.read(
        lambda c: c.execute("SELECT status FROM work_items WHERE id=?", (wid,)).fetchone()
    )["status"]


def test_an_exit_that_neither_hands_off_nor_stops_leaves_the_item_stopped(tmp_path):
    """The whole point. A claimed item reads `active`, which means "a walk is
    behind this"; an early return that spawns nothing would leave it looking
    running with nothing coming for it, and no selector filters on `active`."""

    async def body(database):
        async with stops.claimed_or_stopped(database, "w1", "implementation", reason="gave up"):
            pass
        return _status(database)

    assert _drive(tmp_path, body) == "needs_human"


def test_a_raising_exit_stops_the_item_and_still_raises(tmp_path):
    """An exception is an exit too, and the ones that matter most here are the
    ones no static sweep of `return`/`raise` statements can see -- a `LookupError`
    from `walk.chain_of`, a `StopIteration` from a `next(...)`. The bracket must
    not swallow it: the 409 a refused hand-off raises has to reach the caller."""

    async def body(database):
        with pytest.raises(LookupError, match="boom"):
            async with stops.claimed_or_stopped(database, "w1", "implementation", reason="gave up"):
                raise LookupError("boom")
        return _status(database)

    assert _drive(tmp_path, body) == "needs_human"


def test_a_handed_off_item_is_left_alone(tmp_path):
    """`handed_off` is "something else owns this now" -- true both when this call
    spawned the walk and when `spawn` refused because another walk already holds
    it. Stopping either would kill a live run."""

    async def body(database):
        async with stops.claimed_or_stopped(
            database, "w1", "implementation", reason="gave up", handed_off=lambda: True
        ):
            pass
        return _status(database)

    assert _drive(tmp_path, body) == "active"


def test_an_item_the_body_already_stopped_is_not_stopped_twice(tmp_path):
    """Idempotent, so a site that already had its own per-branch stop keeps
    exactly one event rather than gaining a second, contradictory one."""

    async def body(database):
        async with stops.claimed_or_stopped(database, "w1", "implementation", reason="gave up"):
            await database.write(
                lambda c: store.mark_needs_human(c, "w1", "implementation", "the real reason")
            )
        reasons = [
            e["payload"]
            for e in database.read(
                lambda c: c.execute(
                    "SELECT payload FROM events WHERE work_item_id='w1' "
                    "AND type='work_item_needs_human'"
                ).fetchall()
            )
        ]
        return _status(database), len(reasons)

    assert _drive(tmp_path, body) == ("needs_human", 1)


def test_a_walk_that_reached_a_terminal_status_needs_no_callback(tmp_path):
    """A site that awaits its walk inline (`gates.resume_after_escalation`)
    registers no task, so it passes no `handed_off`. It does not need one: a
    finished walk has already written its own status, and the bracket's `active`
    test is false."""

    async def body(database):
        async with stops.claimed_or_stopped(database, "w1", "implementation", reason="gave up"):
            await database.write(lambda c: store.mark_completed(c, "w1"))
        return _status(database)

    assert _drive(tmp_path, body) == "completed"


def test_a_gate_approval_that_cannot_start_a_walk_stops_the_item(tmp_path):
    """The live instance `dev/check_claim_handoff.py` could not see until its
    `CLAIMS` set stopped being hand-written.

    `store.approve_gate` is an unconditional `UPDATE work_items SET status =
    'active'` -- a claim like any other, and `api/deps.py`'s own `task_is_live`
    docstring already named the hazard: "calling `store.approve_gate`/
    `apply_rejection` and only then discovering `spawn` refuses would leave the
    gate cleared and the item `active` with no walk behind it". Both approval
    doors then computed their start index with a **defaultless** `next(...)`
    (`gate_node_index`, which its own docstring says "Raises `StopIteration` for
    a gate this chain does not have"), after the claim.

    Asserted here against the bracket rather than over HTTP, because the failure
    is the *stored status*, not the response: the gate is cleared either way.
    """

    async def body(database):
        await database.write(lambda c: store.approve_gate(c, "w1", "spec_approval"))
        assert _status(database) == "active", "approve_gate is a claim"
        # `next(...)` inside a coroutine raises `StopIteration`, which Python
        # re-wraps as `RuntimeError` on the way out -- so the assertion is on the
        # *stored status*, which is the thing that was wrong.
        with pytest.raises((StopIteration, RuntimeError)):
            async with stops.claimed_or_stopped(
                database, "w1", "implementation", reason="could not start a walk"
            ):
                raise StopIteration("gate_node_index on a gate this chain does not have")
        return _status(database)

    assert _drive(tmp_path, body) == "needs_human"


def test_a_stop_the_bracket_writes_names_the_exception_that_caused_it(tmp_path):
    """Task 6b review round 1: the bracket's own reason ("could not start a
    walk") says what happened, not why -- the exception that escaped reached
    only the server log. It rides on the card now; an exit with nothing in
    flight keeps the bare reason."""
    from kraft import events

    async def body(database):
        with pytest.raises(KeyError):
            async with stops.claimed_or_stopped(
                database, "w1", "implementation", reason="could not start a walk"
            ):
                raise KeyError("no-such-node")
        evts = database.read(lambda c: events.read_after(c, 0, "w1"))
        return [e for e in evts if e["type"] == "work_item_needs_human"][-1]["payload"]["reason"]

    reason = _drive(tmp_path, body)
    assert reason.startswith("could not start a walk")
    assert "no-such-node" in reason
