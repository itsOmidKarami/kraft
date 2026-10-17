"""A queued item: what pausing it and a gate decision do to it. The doors that
queue a start are pinned beside the doors themselves."""

import asyncio
import dataclasses
import json
import sqlite3
from functools import partial
from pathlib import Path

import pytest
from support.api import (
    _force_node,
    _hold_storage,
    _in_state,
    _orchestrator_db,
    _paused,
    _poll_events,
    _post_default,
    _queue_tick,
    _set_status,
)

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


@pytest.mark.parametrize(
    ("by_the_scheduler", "says"),
    [(False, "try again"), (True, "being started from the queue")],
    ids=["something-else", "the-scheduler"],
)
def test_pausing_an_item_the_scheduler_just_took_is_refused(
    client, repo, monkeypatch, by_the_scheduler, says
):
    """The take landed between pause's read and its write. Taken by the
    scheduler, the item is starting; taken by anything else, it only moved."""
    from kraft import start_queue, store

    wid = _in_state(client, repo, DOORS["states"]["queued"])
    real = store.dequeue_work_item

    def taken_first(conn, work_item_id, **kw):
        if by_the_scheduler:
            start_queue.starting(client.app).add(work_item_id)
        store.take_queued(conn, work_item_id)
        return real(conn, work_item_id, **kw)

    monkeypatch.setattr(store, "dequeue_work_item", taken_first)

    r = client.post(f"/api/work-items/{wid}/pause", json={})

    assert r.status_code == 409, r.text
    assert says in r.json()["detail"]


@pytest.mark.parametrize("state", ["queued", "queued_at_gate"], ids=["from-paused", "from-a-stop"])
def test_pausing_an_item_while_the_queue_starts_it_says_so(client, repo, monkeypatch, state):
    """Between the scheduler's take and the door's claim the row reads paused,
    or needs_human for an item queued from a stop. Pause answers that a start
    is under way, not that the item is held."""
    from kraft import start_queue
    from kraft.api.routes import lifecycle

    client.portal.call(client.app.state.queue_task.cancel)
    wid = _in_state(client, repo, DOORS["states"][state])
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


def test_a_new_start_over_the_storage_limit_is_queued_and_says_why(client, repo):
    wid = _paused(client, repo, chain_template="default")
    _hold_storage(client, used=2, limit=1)

    r = client.post(f"/api/work-items/{wid}/resume", json={})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["slots"]["storage"] == {"used_bytes": 2, "limit_bytes": 1}
    assert client.get(f"/api/work-items/{wid}").json()["queued"]["storage"] == {
        "used_bytes": 2,
        "limit_bytes": 1,
    }
    _poll_events(client, wid, "work_item_storage_held")


def test_an_item_with_a_worktree_is_not_held_by_the_storage_limit(client, repo):
    """Retrying it adds little, and finishing it is what frees the space."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, "implementation", "needs_human")
    assert (client.app.state.run_dirs.worktrees / wid).is_dir()
    _hold_storage(client)

    r = client.post(f"/api/work-items/{wid}/retry", json={})

    assert r.status_code == 200, r.text
    assert r.json().get("status") != "queued"
    assert client.get(f"/api/work-items/{wid}").json()["queued"] is None


@pytest.mark.parametrize(
    ("door", "state", "came_from"),
    [("resume", "paused", "paused"), ("retry", "failed", "needs_human")],
    ids=["resume", "retry"],
)
def test_resuming_behind_a_working_in_place_item_queues_and_says_who(
    client, repo, door, state, came_from
):
    client.portal.call(client.app.state.queue_task.cancel)
    tick = partial(_queue_tick, client)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"][state], in_place=True)

    r = client.post(f"/api/work-items/{wid}/{door}", json={})

    held = {"why": "checkout", "held_by": working, "held_by_title": "A"}
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json().items() >= held.items()

    def queued():
        return [
            e
            for e in _poll_events(client, wid, "work_item_queued")
            if e["type"] == "work_item_queued"
        ]

    assert queued()[-1]["payload"] == {"verb": door, "from": came_from, **held}
    assert tick() == []
    assert len(queued()) == 1, "kept its place, not asked again"

    _set_status(working, "paused")

    assert tick() == [wid]


@pytest.mark.parametrize(
    "body", [{}, {"path": "implementation.main.implement"}], ids=["node", "task"]
)
def test_skipping_behind_a_working_in_place_item_is_refused(client, repo, body):
    _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["failed"], in_place=True)

    r = client.post(f"/api/work-items/{wid}/skip", json=body)

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "the checkout is in use by A; try again when it stops"


def _of(client, wid, type_):
    return [e for e in client.get(f"/api/work-items/{wid}/events").json() if e["type"] == type_]


@pytest.mark.parametrize(
    ("decision", "body", "recorded"),
    [("approve", {}, "gate_approved"), ("reject", {"note": "redo it"}, "gate_rejected")],
    ids=["approve", "reject"],
)
def test_a_gate_decision_behind_a_working_in_place_item_is_kept_and_goes_on_later(
    client, repo, decision, body, recorded
):
    """Recorded at once, queued for the checkout, and replayed as Kraft's own
    walk from where the decision sends it: past an approved gate, or at a
    rejection's target -- never the gate asked again."""
    client.portal.call(client.app.state.queue_task.cancel)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["gate"], in_place=True)

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/{decision}", json=body)

    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["held_by"]) == ("queued", working)
    [decided] = _of(client, wid, recorded)
    assert decided["payload"]["gate"] == "spec_approval"
    assert [e["payload"] for e in _of(client, wid, "work_item_queued")] == [
        {
            "verb": "continue",
            "from": "paused",
            "why": "checkout",
            "held_by": working,
            "held_by_title": "A",
        }
    ]
    nodes = [
        n["id"]
        for n in json.loads(client.get(f"/api/work-items/{wid}").json()["materialized_chain"])[
            "chain"
        ]["nodes"]
    ]
    goes_on_at = (
        nodes[nodes.index("spec_approval") + 1]
        if decision == "approve"
        else decided["payload"]["node"]
    )

    _set_status(working, "paused")

    assert _queue_tick(client) == [wid]
    _poll_events(client, wid, "node_started")
    assert _of(client, wid, "node_started")[0]["payload"]["node_id"] == goes_on_at
    assert not _of(client, wid, "work_item_resumed"), "replayed as a person's resume"
    if decision == "approve":
        assert len(_of(client, wid, "gate_requested")) == 1, "the gate was asked again"


def test_a_self_retry_queued_for_the_checkout_is_replayed_as_the_agents(client, repo):
    """`only-a-person-resets-a-cap-counter`: the replay is the escalation's own retry."""
    from kraft import events, store

    client.portal.call(client.app.state.queue_task.cancel)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["failed"], in_place=True)
    with _orchestrator_db() as c, c:
        c.row_factory = sqlite3.Row
        cursor = events.read_after(c, 0, wid)[-1]["seq"]
        events.append(
            c,
            wid,
            "work_item_self_retry_requested",
            {"node_id": "implementation", "key": None, "gate_key": None, "steer": "fixed it"},
        )
        assert store.queue_work_item(
            c,
            wid,
            verb="self_retry",
            body={"cursor": cursor},
            headers={},
            from_statuses=["needs_human"],
            held_by=working,
        )
    assert _queue_tick(client) == []

    _set_status(working, "paused")

    assert _queue_tick(client) == [wid]
    [retried] = [
        e
        for e in _poll_events(client, wid, "work_item_retried")
        if e["type"] == "work_item_retried"
    ]
    assert retried["payload"]["escalated"] is True
    assert retried["payload"]["steer"] == "fixed it"
    assert not _of(client, wid, "cap_counters_reset")


def test_an_autostart_behind_a_working_in_place_item_is_filed_queued(client, repo):
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")

    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "in_place": True, "autostart": True},
    )

    assert r.json()["status"] == "queued", r.text
    assert r.json()["held_by"] == working
    [queued] = _of(client, r.json()["id"], "work_item_queued")
    assert queued["payload"]["held_by"] == working
