"""The start queue: queued items start, earliest first, as slots free."""

import dataclasses
import json
import os
import sqlite3
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest
from support.api import (
    _blocked_after,
    _force_node,
    _paused,
    _poll_events,
    _post_default,
    _set_status,
    _status_of,
)

from kraft import start_queue


def _db():
    return closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"))


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


def _tick(client):
    return client.portal.call(partial(start_queue.tick, client.app))


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


def test_a_start_the_door_now_refuses_puts_the_item_back_and_says_why(client, stopped, board):
    wid = stopped
    assert client.post(f"/api/work-items/{wid}/retry", json={}).json()["status"] == "queued"
    with _db() as c, c:
        c.execute("UPDATE work_items SET current_node_id = 'gone' WHERE id = ?", (wid,))

    _set_status(board, "paused")
    assert _tick(client) == []

    assert _status_of(client, wid) == "needs_human"
    last = client.get(f"/api/work-items/{wid}/events").json()[-1]
    assert last["type"] == "work_item_dequeued"
    assert last["payload"]["why"] == "refused"
    assert "no current node" in last["payload"]["detail"]


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
