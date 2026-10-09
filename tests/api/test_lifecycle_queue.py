"""A queued item: what pausing it and a gate decision do to it. The doors that
queue a start are pinned beside the doors themselves."""

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
