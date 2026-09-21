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
