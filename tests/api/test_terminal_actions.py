"""Manual completion and cancellation: explicit work-item terminal actions
that require a reason, stop active work, record an audit event and end the
chain (`manual-completion-is-an-explicit-work-item-terminal-action`,
`manual-cancellation-is-an-explicit-work-item-terminal-action`)."""

from __future__ import annotations

import pytest
from support.api import _force_node, _set_status

#: verb -> (status it ends in, the audit event it records)
ENDS = {
    "complete": ("completed", "work_item_manually_completed"),
    "cancel": ("abandoned", "work_item_cancelled"),
}
VERBS = pytest.mark.parametrize("verb", list(ENDS))


def _stopped(client, repo):
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    _force_node(wid, "verification", "needs_human")
    return wid


def _events(client, wid, type):
    return [
        e["payload"]
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == type
    ]


@VERBS
@pytest.mark.parametrize("body", [{}, {"reason": "   "}], ids=["missing", "blank"])
def test_a_terminal_action_requires_a_reason(client, repo, verb, body):
    wid = _stopped(client, repo)

    r = client.post(f"/api/work-items/{wid}/{verb}", json=body)

    assert r.status_code == 422, r.text
    assert "reason" in str(r.json()["detail"])
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"


@VERBS
def test_a_terminal_action_ends_the_item_and_records_why(client, repo, verb):
    wid = _stopped(client, repo)

    r = client.post(f"/api/work-items/{wid}/{verb}", json={"reason": "shipped by hand"})

    assert r.status_code == 200, r.text
    status, audit = ENDS[verb]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == status
    assert _events(client, wid, audit) == [{"reason": "shipped by hand", "node_id": "verification"}]
    # No door leads back onto the chain.
    for door in ("retry", "resume", "skip"):
        assert client.post(f"/api/work-items/{wid}/{door}", json={}).status_code == 409, door


@VERBS
def test_a_terminal_action_stops_active_work(client, repo, monkeypatch, verb):
    """The running session is stopped and marked, not left to finish."""
    from kraft import store
    from kraft.api.routes import lifecycle

    signalled = []
    monkeypatch.setattr(lifecycle, "_terminate", signalled.append)
    wid = _stopped(client, repo)
    db = client.app.state.db

    async def seed():
        await db.write(
            lambda c: store.create_session(
                c,
                id="s-review",
                work_item_id=wid,
                node_id="verification",
                hook_point="verification.review.code_review",
                log_path="/l",
                result_path="/r",
            )
        )
        await db.write(lambda c: store.session_running(c, "s-review", 4242, 1.0))

    client.portal.call(seed)
    _set_status(wid, "active")

    assert client.post(f"/api/work-items/{wid}/{verb}", json={"reason": "no"}).status_code == 200

    assert signalled == [4242]
    sessions = client.get(f"/api/work-items/{wid}").json()["worker_sessions"]
    assert [s["status"] for s in sessions] == ["paused"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == ENDS[verb][0]


@VERBS
@pytest.mark.parametrize("ended", ["completed", "abandoned"])
def test_an_ended_item_cannot_be_ended_again(client, repo, verb, ended):
    wid = _stopped(client, repo)
    _set_status(wid, ended)

    r = client.post(f"/api/work-items/{wid}/{verb}", json={"reason": "again"})

    assert r.status_code == 409, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == ended


@pytest.mark.parametrize("close", [False, True], ids=["by-default", "opted-in"])
def test_manual_completion_closes_beads_only_when_asked(client, repo, monkeypatch, close):
    """Ruling 167 (Kraft-kgbwt): a hand-completed item's work may have landed
    elsewhere, or not at all, so its beads stay open unless the operator says
    `close_beads`."""
    from kraft import executor

    closed = []

    async def fake_close(db, row, bd_cwd, run_dirs, *, by_hand=False):
        closed.append((row["id"], by_hand))

    monkeypatch.setattr(executor, "close_beads", fake_close)
    wid = _stopped(client, repo)
    body = {"reason": "done by hand", **({"close_beads": True} if close else {})}

    assert client.post(f"/api/work-items/{wid}/complete", json=body).status_code == 200
    assert closed == ([(wid, True)] if close else [])


# -- Kraft-dncfg: no action on an ended item runs its chain again -------------

#: Every HTTP door onto an item's chain, with a body it would otherwise accept.
DOORS = {
    "approve": ("gates/spec_approval/approve", {}),
    "reject": ("gates/spec_approval/reject", {"note": "no"}),
    "resume": ("resume", {"steer": "go on"}),
    "retry": ("retry", {}),
    "skip": ("skip", {}),
    "steer": ("steer", {"text": "go on"}),
    "escalate": ("escalate", {"message": "look"}),
    "budget-raise": ("budget/raise", {"budget_usd": 100.0}),
}


def _ended_at_a_gate(client, repo, verb):
    """An item stopped at `spec_approval`, then ended by `verb`."""
    from kraft import store

    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    _force_node(wid, "spec_approval", "needs_human")
    db = client.app.state.db

    async def gate():
        await db.write(lambda c: store.request_gate(c, wid, "spec_approval", "spec_approval"))

    client.portal.call(gate)
    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "spec_approval"
    assert client.post(f"/api/work-items/{wid}/{verb}", json={"reason": "x"}).status_code == 200
    return wid


@VERBS
def test_ending_an_item_closes_its_pending_gate(client, repo, verb):
    wid = _ended_at_a_gate(client, repo, verb)

    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] is None


@VERBS
@pytest.mark.parametrize("door", list(DOORS))
def test_no_door_runs_an_ended_items_chain_again(client, repo, verb, door):
    """Kraft-dncfg: approve on a cancelled item answered 200 and walked the
    chain on. Every door refuses with a 409 naming the status, and leaves the
    item where it was."""
    wid = _ended_at_a_gate(client, repo, verb)
    before = len(client.get(f"/api/work-items/{wid}/events").json())
    path, body = DOORS[door]

    r = client.post(f"/api/work-items/{wid}/{path}", json=body)

    status = ENDS[verb][0]
    assert r.status_code == 409, r.text
    assert f"work item is {status}" in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == status
    assert len(client.get(f"/api/work-items/{wid}/events").json()) == before


@VERBS
@pytest.mark.parametrize(
    "body",
    [{"chain_template": "quick-task"}, {"node_overrides": {"implementation": {"model": "m"}}}],
    ids=["chain_template", "node_overrides"],
)
def test_an_ended_item_cannot_be_reconfigured(client, repo, verb, body):
    """Kraft-6vni1: `PATCH` is a door onto the chain too. The item never
    started, so neither change would be refused for any other reason."""
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    assert client.post(f"/api/work-items/{wid}/{verb}", json={"reason": "x"}).status_code == 200
    before = client.get(f"/api/work-items/{wid}").json()

    r = client.patch(f"/api/work-items/{wid}", json=body)

    assert r.status_code == 409, r.text
    assert f"work item is {ENDS[verb][0]}" in r.json()["detail"]
    after = client.get(f"/api/work-items/{wid}").json()
    assert (after["chain_template"], after["node_overrides"]) == (
        before["chain_template"],
        before["node_overrides"],
    )
