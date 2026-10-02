"""What a gate's approve hands back: the item, in the detail's shape. A
sibling of test_gates.py."""

from __future__ import annotations

from support.api import _poll_events, _post_default


def test_a_gate_answer_carries_the_overrides_as_objects(client, repo, monkeypatch):
    """`kraft item approve --json` printed `agent_overrides` as JSON text where
    `kraft view show --json` printed an object: the approve answer is the
    item's row, in the detail's shape for the three override columns."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    patch = {
        "agent_overrides": {"model": "opus", "effort": "low"},
        "node_overrides": {"plan": {"effort": "high"}},
    }
    assert client.patch(f"/api/work-items/{wid}", json=patch).status_code == 200

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")
    assert r.status_code == 200, r.text
    assert r.json()["agent_overrides"] == {"model": "opus", "effort": "low"}
    assert r.json()["node_overrides"] == {"plan": {"effort": "high"}}
    assert r.json()["policy_override"] is None
    _poll_events(client, wid, "gate_approved")
