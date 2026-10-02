"""What the reject and review doors refuse that the approve door already
did: a gate that is not the pending one, a gate the chain does not have,
and two rejects racing for one walk. The reject loop and the rest of
approve/reject are in test_gates.py, the review flow in test_review.py."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from support.api import _await_gate, _paused, _poll_events, _post_default


@pytest.mark.parametrize(
    ("gate", "code", "detail"),
    [
        ("spec_approval", 409, "gate 'spec_approval' is not pending"),
        ("other", 404, "unknown gate 'other'"),
    ],
    ids=["a-gate-that-is-not-pending", "a-gate-this-chain-does-not-have"],
)
def test_a_review_needs_the_pending_gate(client, repo, gate, code, detail):
    wid = _paused(client, repo, chain_template="default")

    r = client.post(f"/api/work-items/{wid}/gates/{gate}/review", json={"outcome": "comment"})

    assert (r.status_code, r.json()["detail"]) == (code, detail)
    assert client.get(f"/api/work-items/{wid}").json()["last_review_sha"] is None


def test_reject_of_a_gate_that_is_not_pending_is_409_and_changes_nothing(client, repo):
    wid = _post_default(client, repo)
    _await_gate(client, wid, "spec_approval")

    r = client.post(f"/api/work-items/{wid}/gates/plan_approval/reject", json={"note": "no"})

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "gate 'plan_approval' is not pending"
    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "spec_approval"
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert "gate_rejected" not in types


def _two_concurrent_rejects(client, repo):
    """Two rejects of `spec_approval` sent at once; the item id, both
    responses, and the events once the surviving walk has re-run `spec` and
    come back to the gate."""
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    app = client.app

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
            url = f"/api/work-items/{wid}/gates/spec_approval/reject"
            return await asyncio.gather(
                ac.post(url, json={"note": "too vague"}),
                ac.post(url, json={"note": "too vague"}),
            )

    a, b = client.portal.call(scenario)
    return wid, (a, b), _poll_events(client, wid, "gate_requested", count=2)


def test_two_concurrent_rejects_produce_one_walk_and_one_409(client, repo):
    """A pending gate has no status to claim, so `spawn`'s own refusal is
    what stands between two concurrent rejects and two walks re-running the
    same node, as it does for approve."""
    _wid, (a, b), events = _two_concurrent_rejects(client, repo)

    refused = b if a.status_code == 200 else a
    assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)
    assert refused.json()["detail"] == "a walk is already running for this work item"
    spec_runs = [
        e for e in events if e["type"] == "node_started" and e["payload"]["node_id"] == "spec"
    ]
    assert len(spec_runs) == 2  # the first run, then one re-run


@pytest.mark.xfail(
    strict=True,
    reason="the refused reject has already called `store.reject_gate` before `spawn` "
    "refuses it: a second `gate_rejected` and a second reject-loop attempt are recorded",
)
def test_the_refused_concurrent_reject_records_nothing(client, repo):
    wid, _responses, events = _two_concurrent_rejects(client, repo)

    assert sum(e["type"] == "gate_rejected" for e in events) == 1
    db = client.app.state.db
    count = client.portal.call(
        db.read,
        lambda c: c.execute(
            "SELECT count FROM retry_counters WHERE work_item_id = ? AND key = ?",
            (wid, "spec_approval_reject_loop"),
        ).fetchone()[0],
    )
    assert count == 1
