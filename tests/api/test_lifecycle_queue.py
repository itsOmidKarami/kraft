"""A queued item: what pausing it and a gate decision do to it. The doors that
queue a start are pinned beside the doors themselves."""

import asyncio
import dataclasses
import json
import os
import sqlite3
import time
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
    _status_of,
)
from support.harness import git

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
    # The board row and the item page say who has the checkout.
    listed = next(i for i in client.get("/api/work-items").json()["items"] if i["id"] == wid)
    assert listed["queued"].items() >= {"verb": door, **held}.items()
    assert client.get(f"/api/work-items/{wid}").json()["queued"].items() >= held.items()
    assert tick() == []
    assert len(queued()) == 1, "kept its place, not asked again"

    _set_status(working, "paused")

    assert tick() == [wid]


@pytest.mark.parametrize(
    ("state", "door", "body", "verb", "launch", "asked"),
    [
        (
            "failed",
            "escalate",
            {"message": "look again"},
            "escalate",
            "kraft.escalate.dispatch",
            ("message", "look again"),
        ),
        (
            "gate",
            "gates/spec_approval/review",
            {"outcome": "comment"},
            "reply",
            "kraft.review_reply.run",
            ("gate", "spec_approval"),
        ),
    ],
    ids=["escalate", "reply"],
)
def test_a_session_outside_the_walk_waits_for_the_checkout(
    client, repo, monkeypatch, state, door, body, verb, launch, asked
):
    """`in-place-sessions-take-the-lease`: no agent starts in a checkout another
    in-place item is using; the request waits, queued, and runs as asked once
    the checkout frees."""
    client.portal.call(client.app.state.queue_task.cancel)
    launched = []

    async def spy(*a, **kw):
        launched.append(kw)
        return "done"

    monkeypatch.setattr(launch, spy)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"][state], in_place=True)

    r = client.post(f"/api/work-items/{wid}/{door}", json=body)

    held = {"why": "checkout", "held_by": working, "held_by_title": "A"}
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json().items() >= held.items()
    assert [e["payload"] for e in _of(client, wid, "work_item_queued")] == [
        {"verb": verb, "from": "needs_human", **held}
    ]
    assert _queue_tick(client) == []
    assert launched == []

    _set_status(working, "paused")

    assert _queue_tick(client) == [wid]
    deadline = time.monotonic() + 10
    while not launched and time.monotonic() < deadline:
        time.sleep(0.05)
    [kw] = launched
    assert kw[asked[0]] == asked[1]


def test_a_second_comment_while_the_reply_waits_queues_nothing_more(client, repo, monkeypatch):
    """The waiting reply answers every thread unanswered when it runs."""
    client.portal.call(client.app.state.queue_task.cancel)
    launched = []

    async def spy(*a, **kw):
        launched.append(kw)
        return "done"

    monkeypatch.setattr("kraft.review_reply.run", spy)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["gate"], in_place=True)
    review = f"/api/work-items/{wid}/gates/spec_approval/review"
    client.post(review, json={"outcome": "comment"})

    r = client.post(review, json={"outcome": "comment"})

    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["reply_agent"]) == ("queued", False)
    assert len(_of(client, wid, "work_item_queued")) == 1
    _set_status(working, "paused")
    assert _queue_tick(client) == [wid]
    deadline = time.monotonic() + 10
    while not launched and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(launched) == 1


@pytest.mark.parametrize("in_place", [False, True], ids=["worktree", "in-place"])
def test_a_comment_on_an_item_queued_for_a_retry_starts_the_reply_only_in_its_worktree(
    client, repo, monkeypatch, in_place
):
    """A worktree item queued for a retry still gets its threads answered now.
    In place, nothing starts in the checkout ahead of the item's queued start:
    its reply waits for it."""
    launched = []

    async def spy(*a, **kw):
        launched.append(kw)
        return "done"

    monkeypatch.setattr("kraft.review_reply.run", spy)
    wid = _in_state(client, repo, DOORS["states"]["queued_at_gate"], in_place=in_place)
    if not in_place:
        worktree = Path(os.environ["KRAFT_RUN_DIR"]) / "worktrees" / wid
        git(repo, "worktree", "add", "-q", "-b", f"kraft/{wid}", str(worktree))

    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/review", json={"outcome": "comment"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["reply_agent"] is not in_place
    assert (r.json().get("status") == "queued") is in_place


@pytest.mark.parametrize(
    ("state", "door", "body", "verb"),
    [
        ("failed", "escalate", {"message": "look again"}, "escalate"),
        ("gate", "gates/spec_approval/review", {"outcome": "comment"}, "reply"),
    ],
    ids=["escalate", "reply"],
)
def test_a_checkout_taken_after_the_doors_look_still_gets_no_second_session(
    client, repo, monkeypatch, state, door, body, verb
):
    """The door found the checkout free; another item took it before the agent's
    session was written. The session is refused, and the request waits."""
    from kraft import executor

    client.portal.call(client.app.state.queue_task.cancel)
    working = _in_state(client, repo, DOORS["states"]["paused"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"][state], in_place=True)
    if verb == "reply":
        client.post(
            f"/api/work-items/{wid}/threads",
            json={
                "body": "why?",
                "file_path": "a.py",
                "side": "new",
                "start_line": 1,
                "end_line": 1,
                "label": "question",
            },
        )
    restore = executor.restore_pins

    async def taken_meanwhile(row):
        _set_status(working, "active")
        return await restore(row)

    monkeypatch.setattr(executor, "restore_pins", taken_meanwhile)

    r = client.post(f"/api/work-items/{wid}/{door}", json=body)

    assert r.status_code == 200, r.text
    assert r.json().get("status") != "queued", "the door saw the checkout taken"
    deadline = time.monotonic() + 10
    while _status_of(client, wid) != "queued" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert [e["payload"] for e in _of(client, wid, "work_item_queued")] == [
        {
            "verb": verb,
            "from": "needs_human",
            "why": "checkout",
            "held_by": working,
            "held_by_title": "A",
        }
    ]
    with _orchestrator_db() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM worker_sessions WHERE work_item_id = ?", (wid,)
            ).fetchone()[0]
            == 0
        )
    assert not _of(client, wid, "escalation_message"), "its replay records the message"


@pytest.mark.parametrize(
    ("state", "door", "body"),
    [
        ("failed", "escalate", {"message": "look again"}),
        ("gate", "gates/spec_approval/review", {"outcome": "comment"}),
    ],
    ids=["escalate", "reply"],
)
def test_a_session_outside_the_walk_refused_its_checkout_stops_the_item_as_checkout(
    client, repo, monkeypatch, state, door, body
):
    """The checkout could not be switched to the item's branch: no agent
    starts, and the item stops for the person whose checkout it is."""
    from kraft import inplace

    async def refused(*a, **kw):
        raise inplace.CheckoutRefused("git would not switch the checkout")

    monkeypatch.setattr(inplace, "take", refused)
    wid = _in_state(client, repo, DOORS["states"][state], in_place=True)
    if door.endswith("review"):
        client.post(
            f"/api/work-items/{wid}/threads",
            json={
                "body": "why?",
                "file_path": "a.py",
                "side": "new",
                "start_line": 1,
                "end_line": 1,
                "label": "question",
            },
        )

    stops = len(_of(client, wid, "work_item_needs_human"))

    r = client.post(f"/api/work-items/{wid}/{door}", json=body)

    assert r.status_code == 200, r.text
    _poll_events(client, wid, "work_item_needs_human", count=stops + 1)
    stop = _of(client, wid, "work_item_needs_human")[-1]["payload"]
    assert (stop["kind"], stop["reason"]) == ("checkout", "git would not switch the checkout")
    with _orchestrator_db() as c:
        assert [
            r[0]
            for r in c.execute("SELECT status FROM worker_sessions WHERE work_item_id = ?", (wid,))
        ] == ["config_error"]


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
    client, repo, monkeypatch, decision, body, recorded
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

    from kraft import executor

    steers, real_run = [], executor.run
    monkeypatch.setattr(
        executor, "run", lambda *a, **kw: steers.append(kw["steer"]) or real_run(*a, **kw)
    )
    _set_status(working, "paused")

    assert _queue_tick(client) == [wid]
    assert steers == [body.get("note")]
    _poll_events(client, wid, "node_started")
    assert _of(client, wid, "node_started")[0]["payload"]["node_id"] == goes_on_at
    assert not _of(client, wid, "work_item_resumed"), "replayed as a person's resume"
    if decision == "approve":
        assert len(_of(client, wid, "gate_requested")) == 1, "the gate was asked again"


def test_a_held_approval_refreshes_the_base_before_it_goes_on(client, repo, monkeypatch):
    """As a direct approval does: a rebase that left the person's checkout
    clashing stops the item for them, and the walk does not go on."""
    from kraft import inplace

    client.portal.call(client.app.state.queue_task.cancel)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["gate"], in_place=True)
    client.post(f"/api/work-items/{wid}/gates/spec_approval/approve", json={})

    async def clash(*_, **__):
        raise inplace.AutostashClash("your edits to f clash", ["f"], "5ca1ab1e")

    monkeypatch.setattr(inplace, "rebase", clash)
    _set_status(working, "paused")

    assert _queue_tick(client) == [wid]
    _poll_events(client, wid, "work_item_needs_human")
    stop = _of(client, wid, "work_item_needs_human")[-1]["payload"]
    assert (stop["kind"], stop["facts"]) == ("checkout", {"paths": ["f"], "stash": "5ca1ab1e"})
    assert _of(client, wid, "node_started") == []


@pytest.mark.parametrize(
    ("by", "source"), [("human", "human"), ("agent", "gate_review")], ids=["person", "agent"]
)
def test_a_held_rejection_paused_and_resumed_by_hand_keeps_its_re_run(
    client, repo, monkeypatch, by, source
):
    """The queued request goes with the pause; the re-run does not, nor whose
    note it carries: an agent's stays an agent's."""
    from kraft import executor, store

    client.portal.call(client.app.state.queue_task.cancel)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["gate"], in_place=True)
    if by == "human":
        client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "redo it"})
    else:
        with _orchestrator_db() as c, c:
            c.row_factory = sqlite3.Row
            store.reject_gate(
                c, wid, "spec_approval", "redo it", reopen=True, node="spec", by="agent"
            )
    [rejected] = _of(client, wid, "gate_rejected")
    _set_status(working, "paused")
    assert client.post(f"/api/work-items/{wid}/pause", json={}).status_code == 200
    sources, real_run = [], executor.run
    monkeypatch.setattr(
        executor, "run", lambda *a, **kw: sources.append(kw["steer_source"]) or real_run(*a, **kw)
    )

    r = client.post(f"/api/work-items/{wid}/resume", json={})

    assert r.status_code == 200, r.text
    assert r.json()["steer"] == "redo it"
    assert sources == [source]
    evts = _poll_events(client, wid, "node_started")
    after = [e for e in evts if e["seq"] > rejected["seq"]]
    first = next(e for e in after if e["type"] in ("node_started", "gate_requested"))
    assert first["type"] == "node_started", "the gate was asked again"
    assert first["payload"]["node_id"] == rejected["payload"]["node"]


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


def test_a_held_decision_refused_beside_a_live_walk_keeps_its_steer(client, repo):
    """The replay is refused, and the rejection's note waits on the item for
    whatever starts it next."""
    client.portal.call(client.app.state.queue_task.cancel)
    working = _in_state(client, repo, DOORS["states"]["running"], in_place=True, title="A")
    wid = _in_state(client, repo, DOORS["states"]["gate"], in_place=True)
    client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "redo it"})
    _set_status(working, "paused")

    async def live():
        client.app.state.tasks[wid] = asyncio.get_running_loop().create_future()

    client.portal.call(live)

    assert _queue_tick(client) == []
    [dequeued] = _of(client, wid, "work_item_dequeued")
    assert dequeued["payload"]["why"] == "refused"
    with _orchestrator_db() as c:
        assert c.execute(
            "SELECT status, pending_steer_context FROM work_items WHERE id = ?", (wid,)
        ).fetchone() == ("paused", "redo it")
