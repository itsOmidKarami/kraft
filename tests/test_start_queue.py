"""The start queue: queued items start, earliest first, as slots free."""

import asyncio
import dataclasses
import json
from functools import partial
from pathlib import Path

import pytest
from support.api import (
    _blocked_after,
    _force_node,
    _hold_storage,
    _in_state,
    _paused,
    _poll_events,
    _post_default,
    _set_status,
    _status_of,
)
from support.api import (
    _orchestrator_db as _db,
)
from support.api import (
    _queue_tick as _tick,
)
from support.harness import IN_PLACE, v1_named_chain

from kraft import start_queue

DOORS = json.loads((Path(__file__).parent / "api" / "lifecycle_doors.json").read_text())


@pytest.fixture
def stopped(client, repo):
    """An item stopped at `implementation`. Made before `board` fills the only
    slot: an autostart create on a full board would be queued itself."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "implementation", "needs_human")
    return wid


@pytest.fixture
def board(client, repo):
    """A one-slot board with that slot taken, and the app's own poller stopped
    so only the ticks a test makes move the queue. Yields the busy item's id."""
    client.portal.call(client.app.state.queue_task.cancel)
    busy = _post_default(client, repo)
    _poll_events(client, busy, "gate_requested")
    _set_status(busy, "active")
    client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)
    return busy


def test_the_queue_starts_the_earliest_item_when_a_slot_frees(client, repo, board):
    first = _paused(client, repo, chain_template="default")
    second = _paused(client, repo, chain_template="default")
    for wid in (first, second):
        assert client.post(f"/api/work-items/{wid}/resume", json={}).json()["status"] == "queued"

    assert _tick(client) == []
    assert (_status_of(client, first), _status_of(client, second)) == ("queued", "queued")

    _set_status(board, "paused")
    assert _tick(client) == [first]
    assert _status_of(client, second) == "queued"
    _poll_events(client, first, "work_item_resumed")


def test_a_start_makes_the_saved_request_again(client, stopped, board):
    """The verb, its body and the caller are what was asked, not defaults."""
    wid = stopped
    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"steer": "keep the header"},
        headers={"x-kraft-client": "mcp"},
    )
    assert r.json()["status"] == "queued"
    with _db() as c:
        saved = json.loads(
            c.execute("SELECT queued_request FROM work_items WHERE id = ?", (wid,)).fetchone()[0]
        )
    assert (saved["verb"], saved["body"], saved["headers"]) == (
        "retry",
        {"steer": "keep the header"},
        {"x-kraft-client": "mcp"},
    )

    _set_status(board, "paused")
    assert _tick(client) == [wid]

    _poll_events(client, wid, "work_item_retried")
    events = client.get(f"/api/work-items/{wid}/events").json()
    assert [e["payload"]["steer"] for e in events if e["type"] == "work_item_retried"] == [
        "keep the header"
    ]


def _node_gone(wid, monkeypatch):
    with _db() as c, c:
        c.execute("UPDATE work_items SET current_node_id = 'gone' WHERE id = ?", (wid,))


def _door_raises(wid, monkeypatch):
    from kraft.api.routes import lifecycle

    async def boom(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(lifecycle, "_retry", boom)


@pytest.mark.parametrize(
    ("breaks", "why", "detail"),
    [(_node_gone, "refused", "no current node"), (_door_raises, "failed", "RuntimeError: boom")],
    ids=["refused", "failed"],
)
def test_a_start_the_door_now_refuses_puts_the_item_back_and_says_why(
    client, repo, stopped, board, monkeypatch, breaks, why, detail
):
    """Refused or failed, the item is back where it came from with the reason
    in its timeline, and the steer it was asked with, and the pass goes on to
    the next queued item."""
    wid = stopped
    queued = client.post(f"/api/work-items/{wid}/retry", json={"steer": "keep it"})
    assert queued.json()["status"] == "queued"
    after = _paused(client, repo, chain_template="default")
    assert client.post(f"/api/work-items/{after}/resume", json={}).json()["status"] == "queued"
    breaks(wid, monkeypatch)

    _set_status(board, "paused")
    assert _tick(client) == [after]

    assert _status_of(client, wid) == "needs_human"
    last = client.get(f"/api/work-items/{wid}/events").json()[-1]
    assert last["type"] == "work_item_dequeued"
    assert last["payload"]["why"] == why
    assert detail in last["payload"]["detail"]
    assert last["payload"]["detail"].endswith(
        "; the queued retry was dropped, with its steer: keep it"
    )


def test_a_start_that_fails_after_its_claim_reports_the_stop_it_left(
    client, repo, board, monkeypatch
):
    """The door's bracket stopped the item. The event names that status, which
    an open board shows, not the one the item was queued from."""
    from kraft import store

    wid = _paused(client, repo, chain_template="default")
    assert client.post(f"/api/work-items/{wid}/resume", json={}).json()["status"] == "queued"

    def boom(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "take_steer", boom)  # resume reads it after its claim

    _set_status(board, "paused")
    assert _tick(client) == []

    assert _status_of(client, wid) == "needs_human"
    last = client.get(f"/api/work-items/{wid}/events").json()[-1]
    assert (last["type"], last["payload"]["to"]) == ("work_item_dequeued", "needs_human")


def test_an_autostart_filed_on_a_full_board_starts_when_a_slot_frees(client, repo, board):
    wid = _post_default(client, repo)
    assert _status_of(client, wid) == "queued"

    _set_status(board, "paused")
    assert _tick(client) == [wid]
    _poll_events(client, wid, "work_item_resumed")


def test_an_item_that_loses_the_slot_keeps_its_place(client, repo, board):
    """Taken by the scheduler, then the slot went to someone else: queued
    again, at the time it first joined."""
    first = _paused(client, repo, chain_template="default")
    second = _paused(client, repo, chain_template="default")
    for wid in (first, second):
        client.post(f"/api/work-items/{wid}/resume", json={})

    # The board is still full: the door's claim finds no slot.
    started = client.portal.call(partial(start_queue._start_one, client.app, first))

    assert started is False
    assert _status_of(client, first) == "queued"
    with _db() as c:
        order = [
            r[0]
            for r in c.execute(
                "SELECT id FROM work_items WHERE status = 'queued' "
                "ORDER BY json_extract(queued_request, '$.at'), id"
            )
        ]
    assert order == [first, second]


@pytest.mark.parametrize(
    ("claimed", "left"),
    [
        pytest.param(False, "queued", id="before the door's claim"),
        pytest.param(True, "active", id="after it: the restart resumes the item"),
    ],
)
def test_a_start_cancelled_by_a_shutdown_goes_back_in_the_queue_in_its_place(
    client, repo, board, monkeypatch, claimed, left
):
    """Kraft-1zhnj: the scheduler is cancelled inside the door. The request it
    took is not lost with it, and the cancellation still ends the scheduler."""
    from kraft.api.routes import lifecycle

    first = _paused(client, repo, chain_template="default")
    second = _paused(client, repo, chain_template="default")
    for wid in (first, second):
        client.post(f"/api/work-items/{wid}/resume", json={"steer": "go left"})

    async def shut_down(wid, *_):
        if claimed:
            _set_status(wid, "active")
        raise asyncio.CancelledError

    monkeypatch.setattr(lifecycle, "resume_work_item", shut_down)

    async def start():
        try:
            return await start_queue._start_one(client.app, first)
        except asyncio.CancelledError:
            return "cancelled"

    assert client.portal.call(start) == "cancelled"
    assert _status_of(client, first) == left
    with _db() as c:
        held = c.execute(
            "SELECT id, json_extract(queued_request, '$.body.steer') FROM work_items "
            "WHERE status = 'queued' ORDER BY json_extract(queued_request, '$.at'), id"
        ).fetchall()
    assert held == ([] if claimed else [(first, "go left")]) + [(second, "go left")]


async def test_a_start_put_back_by_a_shutdown_still_says_who_has_the_checkout(
    item_on, run_dirs, database
):
    from kraft import store

    chain = v1_named_chain(run_dirs.base / "t")
    await item_on(chain, wid="a", target=IN_PLACE)
    b = await item_on(chain, wid="b", target=IN_PLACE)
    await database.write(lambda c: store.pause_work_item(c, "b", []))
    saved = {"verb": "resume", "body": {}, "headers": {}, "from": "paused", "at": "2026-01-01"}

    await database.write(lambda c: start_queue._requeue(c, "b", saved))

    assert b.events("work_item_queued")[-1]["payload"]["held_by"] == "a"


def test_the_rebuilt_request_names_the_app_and_the_caller(client):
    request = start_queue._request(client.app, {"x-kraft-session-id": "s1"})
    assert request.app is client.app
    assert request.headers.get("x-kraft-session-id") == "s1"
    assert request.headers.get("x-kraft-client") is None


@pytest.fixture
def quiet(client):
    """The app's own poller stopped, so only a test's ticks move anything."""
    client.portal.call(client.app.state.queue_task.cancel)


def test_a_blocked_item_starts_once_what_it_comes_after_completes(client, repo, quiet):
    dep, wid = _blocked_after(client, repo)

    assert _tick(client) == []
    assert _status_of(client, wid) == "blocked"

    _set_status(dep, "completed")
    assert _tick(client) == [wid]
    _poll_events(client, wid, "work_item_resumed")
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert types.index("work_item_unblocked") < types.index("work_item_resumed")


def test_an_escalation_waiting_behind_a_walk_starts_on_a_full_board(
    client, repo, stopped, board, monkeypatch
):
    """It runs beside the walk and takes no slot; the walk ahead of it still waits."""
    import sqlite3

    from kraft import store

    asked = []

    async def dispatch(*a, **kw):
        asked.append(kw["message"])
        return "done"

    monkeypatch.setattr("kraft.escalate.dispatch", dispatch)
    new = _paused(client, repo, chain_template="default")
    assert client.post(f"/api/work-items/{new}/resume", json={}).json()["status"] == "queued"
    with _db() as c, c:
        c.row_factory = sqlite3.Row
        assert store.queue_work_item(
            c,
            stopped,
            verb="escalate",
            body={"message": "look"},
            headers={},
            from_statuses=["needs_human"],
        )

    assert _tick(client) == [stopped]
    assert _status_of(client, new) == "queued"
    for _ in range(200):
        if asked:
            break
        client.portal.call(asyncio.sleep, 0.05)
    assert asked == ["look"]


def test_a_released_item_waits_in_the_queue_when_the_board_is_full(client, repo, board):
    dep, wid = _blocked_after(client, repo)
    _set_status(dep, "completed")

    assert _tick(client) == []

    assert _status_of(client, wid) == "queued"
    assert _status_of(client, board) == "active"


def test_a_blocked_item_is_put_back_when_what_it_comes_after_is_abandoned(client, repo, quiet):
    dep, wid = _blocked_after(client, repo)
    _set_status(dep, "abandoned")

    assert _tick(client) == []

    assert _status_of(client, wid) == "paused"
    last = client.get(f"/api/work-items/{wid}/events").json()[-1]
    assert (last["type"], last["payload"]["why"]) == ("work_item_dequeued", "dependency_abandoned")


def test_a_start_held_for_storage_keeps_its_place_and_lets_a_worktree_holder_past(
    client, repo, stopped, board
):
    new = _paused(client, repo, chain_template="default")
    for wid, verb in ((new, "resume"), (stopped, "retry")):
        assert client.post(f"/api/work-items/{wid}/{verb}", json={}).json()["status"] == "queued"
    _hold_storage(client)
    _set_status(board, "paused")

    assert _tick(client) == [stopped]
    assert _status_of(client, new) == "queued"


def test_removing_the_limit_releases_a_held_start(client, repo, board):
    new = _paused(client, repo, chain_template="default")
    _hold_storage(client)
    _set_status(board, "paused")
    assert client.post(f"/api/work-items/{new}/resume", json={}).json()["status"] == "queued"
    assert _tick(client) == []

    client.app.state.policy = dataclasses.replace(
        client.app.state.policy, storage_limit_bytes=None, storage_quota_bytes=None
    )

    assert _tick(client) == [new]


@pytest.mark.parametrize("lost", ["checkout", "shutdown"])
def test_a_spawned_replay_is_started_only_once_it_holds_its_session(
    client, repo, quiet, monkeypatch, lost
):
    """An escalation's door returns before its session is written. Its
    checkout taken meanwhile, it is queued again in its place; the server
    shutting down meanwhile, it goes back in its place and no agent starts."""
    import sqlite3

    from kraft import executor, store

    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["failed"], in_place=True)
    later = _in_state(client, repo, DOORS["states"]["paused"], in_place=True)
    with _db() as c, c:
        c.row_factory = sqlite3.Row
        for item, verb, body, came in (
            (wid, "escalate", {"message": "look"}, "needs_human"),
            (later, "resume", {}, "paused"),
        ):
            assert store.queue_work_item(
                c, item, verb=verb, body=body, headers={}, from_statuses=[came], held_by=working
            )
        [at] = c.execute("SELECT queued_request FROM work_items WHERE id = ?", (wid,)).fetchone()
    _set_status(working, "paused")
    reached = asyncio.Event()

    async def before_the_session(row):
        if lost == "checkout":
            _set_status(working, "active")
            return
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(executor, "restore_pins", before_the_session)

    async def tick():
        if lost == "checkout":
            return await start_queue.tick(client.app)
        ticking = asyncio.ensure_future(start_queue.tick(client.app))
        await reached.wait()
        ticking.cancel()
        await asyncio.gather(ticking, return_exceptions=True)
        await asyncio.sleep(0)
        return client.app.state.tasks.get(wid)

    assert client.portal.call(tick) in ([], None)
    with _db() as c:
        held = c.execute(
            "SELECT id, queued_request FROM work_items WHERE status = 'queued' "
            "ORDER BY json_extract(queued_request, '$.at'), id"
        ).fetchall()
        sessions = c.execute(
            "SELECT COUNT(*) FROM worker_sessions WHERE work_item_id = ?", (wid,)
        ).fetchone()[0]
    assert [i for i, _ in held] == [wid, later]
    assert json.loads(held[0][1])["at"] == json.loads(at)["at"]
    assert sessions == 0


@pytest.mark.parametrize(
    ("verb", "body", "runs_on"),
    [
        ("escalate", {"message": "look"}, ("kraft.escalate", "_record_message")),
        ("self_retry", None, ("kraft.executor.retry", "retry")),
    ],
    ids=["escalate", "self_retry"],
)
def test_a_spawned_replay_counts_as_started_once_it_holds_its_claim(
    client, repo, quiet, monkeypatch, verb, body, runs_on
):
    """Its session written, or its claim made: started, while its work runs
    on. The queue does not wait out an agent's whole turn."""
    import importlib
    import sqlite3

    from kraft import events, store

    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["failed"], in_place=True)
    with _db() as c, c:
        c.row_factory = sqlite3.Row
        if body is None:
            body = {"cursor": events.read_after(c, 0, wid)[-1]["seq"]}
            events.append(
                c,
                wid,
                "work_item_self_retry_requested",
                {"node_id": "implementation", "key": None, "gate_key": None, "steer": "s"},
            )
        assert store.queue_work_item(
            c, wid, verb=verb, body=body, headers={}, from_statuses=["needs_human"]
        )
    _set_status(working, "paused")

    async def runs_on_and_on(*a, **kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(importlib.import_module(runs_on[0]), runs_on[1], runs_on_and_on)

    async def tick():
        return await asyncio.wait_for(start_queue.tick(client.app), 10)

    assert client.portal.call(tick) == [wid]
