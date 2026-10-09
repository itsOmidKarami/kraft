"""A queued item: what pausing it and a gate decision do to it. The doors that
queue a start are pinned beside the doors themselves."""

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from support.api import _in_state

DOORS = json.loads((Path(__file__).parent / "lifecycle_doors.json").read_text())


def test_pausing_a_queued_retry_puts_the_stop_back(client, repo):
    """A person who pauses a queued retry finds the failure it came from, kind
    and reason, and the same Retry."""
    client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)
    wid = _in_state(client, repo, DOORS["states"]["failed"])
    busy = _in_state(client, repo, DOORS["states"]["running"])
    before = client.get(f"/api/work-items/{wid}").json()

    assert (
        client.post(f"/api/work-items/{wid}/retry", json={"steer": "s"}).json()["status"]
        == "queued"
    )
    queued = client.get(f"/api/work-items/{wid}").json()["queued"]
    assert queued["verb"] == "retry" and queued["since"]
    r = client.post(f"/api/work-items/{wid}/pause", json={})

    assert r.status_code == 200, r.text
    after = client.get(f"/api/work-items/{wid}").json()
    assert after["queued"] is None
    assert (after["status"], after["stop"]["kind"], after["stop_reason"]) == (
        "needs_human",
        "failed",
        before["stop_reason"],
    )
    assert client.get(f"/api/work-items/{busy}").json()["status"] == "active"


@pytest.mark.parametrize(
    ("decision", "body"), [("approve", {}), ("reject", {"note": "n"})], ids=["approve", "reject"]
)
def test_a_queued_item_refuses_a_gate_decision(client, repo, decision, body):
    """Queued from a gate by a retry, the gate is still open in the timeline.
    A decision would start the item past the queue."""
    wid = _in_state(client, repo, DOORS["states"]["queued_at_gate"])

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/{decision}", json=body)

    assert r.status_code == 409, r.text
    assert "queued" in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "queued"


@pytest.mark.parametrize("state", ["queued", "blocked"])
@pytest.mark.parametrize("door", ["budget_raise", "skip", "escalate", "retry", "reopen_mr"])
def test_a_held_item_tells_a_door_it_refuses_how_to_take_it_out(client, repo, state, door):
    """Not only a 409: the item is queued or blocked, and pausing takes it out."""
    spec = DOORS["doors"][door]
    wid = _in_state(client, repo, DOORS["states"][state])

    r = client.post(f"/api/work-items/{wid}/{spec['path']}", json=spec["body"])

    assert r.status_code == 409, r.text
    assert f"is {state}" in r.json()["detail"]
    assert f"kraft item pause {wid}" in r.json()["detail"]


def test_pausing_an_item_the_scheduler_just_took_is_refused(client, repo, monkeypatch):
    """The scheduler's take landed between pause's read and its write: the item
    is starting, so pause does not answer that it left the queue."""
    from kraft import store

    wid = _in_state(client, repo, DOORS["states"]["queued"])
    real = store.dequeue_work_item

    def taken_first(conn, work_item_id, **kw):
        store.take_queued(conn, work_item_id)
        return real(conn, work_item_id, **kw)

    monkeypatch.setattr(store, "dequeue_work_item", taken_first)

    r = client.post(f"/api/work-items/{wid}/pause", json={})

    assert r.status_code == 409, r.text
    assert "try again" in r.json()["detail"]


def test_pausing_an_item_while_the_queue_starts_it_says_so(client, repo, monkeypatch):
    """Between the scheduler's take and the door's claim the row reads paused.
    Pause answers that a start is under way, not that the item is held."""
    from kraft import start_queue
    from kraft.api.routes import lifecycle

    client.portal.call(client.app.state.queue_task.cancel)
    wid = _in_state(client, repo, DOORS["states"]["queued"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def door(*_):
        entered.set()
        await release.wait()

    monkeypatch.setattr(lifecycle, "resume_work_item", door)
    start = client.portal.start_task_soon(start_queue._start_one, client.app, wid)
    client.portal.call(asyncio.wait_for, entered.wait(), 10)

    r = client.post(f"/api/work-items/{wid}/pause", json={})

    client.portal.call(release.set)
    start.result(timeout=10)
    assert r.status_code == 409, r.text
    assert "being started from the queue" in r.json()["detail"]
    assert wid not in start_queue.starting(client.app)
