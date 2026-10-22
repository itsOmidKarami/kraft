"""What a person passing the final gate pins: the head the merge may push
(`kraft.reviewed_head`). A sibling of test_gates.py. The default chain's
`final_review` lies past a merge request, so these take `spec_approval` for
it: what makes a gate the final one is `reviewed_head.final_gate`'s to say."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.api import _poll_events, _post_default
from support.harness import commit_all, git

from kraft import reviewed_head

from .test_gates import _create_running_session


@pytest.mark.parametrize("final", [True, False], ids=["the-final-gate", "another-gate"])
@pytest.mark.parametrize(
    ("door", "event"),
    [("gates/spec_approval/approve", "gate_approved"), ("skip", "node_skipped")],
    ids=["approved", "skipped"],
)
def test_passing_the_final_gate_pins_the_head_it_passes(
    client, repo, monkeypatch, final, door, event
):
    """An approval of the final gate, or a skip of it -- a person passing it
    explicitly -- records the branch tip it passes, which is all the merge
    may push. Any other gate pins nothing."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    if final:
        monkeypatch.setattr(reviewed_head, "final_gate", lambda row: "spec_approval")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    item = client.get(f"/api/work-items/{wid}").json()
    worktree = Path(item["worktree_path"])
    head = git(worktree, "rev-parse", f"refs/heads/{item['branch']}")

    r = client.post(f"/api/work-items/{wid}/{door}", json={})

    assert r.status_code == 200, r.text
    [passed] = [e["payload"] for e in _poll_events(client, wid, event) if e["type"] == event]
    if not final:
        assert "tips" not in passed
        return
    [(checkout, tip)] = passed["tips"].items()
    assert (reviewed_head.same_checkout(checkout, worktree), tip) == (True, head)


def test_the_refresh_after_approving_the_final_gate_keeps_the_approved_head(
    client, repo, monkeypatch
):
    """A final gate nothing was pushed before is brought up to date with its
    base on approval, as any such gate is. That rebase is Kraft's own, with
    nothing to resolve: the rebased head is the approved one, repinned."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    monkeypatch.setattr(reviewed_head, "final_gate", lambda row: "spec_approval")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    (repo / "moved.txt").write_text("moved on\n")
    commit_all(repo, "moved on")

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

    assert r.status_code == 200, r.text
    evts = _poll_events(client, wid, "worktree_rebase_verified")
    [approved] = [e["payload"] for e in evts if e["type"] == "gate_approved"]
    [verified] = [e["payload"] for e in evts if e["type"] == "worktree_rebase_verified"]
    [repinned] = [e["payload"] for e in evts if e["type"] == "reviewed_head_repinned"]
    [(checkout, was)] = approved["tips"].items()
    assert repinned == {
        "tips": {checkout: verified["worktree_head"]},
        "moved": {checkout: {"from": was, "to": verified["worktree_head"]}},
        "cause": "rebase",
    }


@pytest.mark.parametrize("final", [True, False], ids=["the-final-gate", "another-gate"])
def test_approving_the_final_gate_over_a_live_agent_pins_nothing_it_may_have_written(
    client, repo, monkeypatch, final
):
    """An agent still at work at the final gate (a reviewer, a reply) may
    have committed after the person last looked, and nothing records the head
    before it started: the approval stops it, pauses the item and is refused,
    so a second look is what pins. Any other gate pins nothing, and goes on."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid, **_: None)
    if final:
        monkeypatch.setattr(reviewed_head, "final_gate", lambda row: "spec_approval")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _create_running_session(wid, "r1", "spec_approval", pid=4242)

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

    if not final:
        assert r.status_code == 200, r.text
        return
    assert r.status_code == 409, r.text
    assert "look at it again" in r.json()["detail"]
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert "gate_approved" not in types
    item = client.get(f"/api/work-items/{wid}").json()
    assert (item["status"], item["pending_gate"]) == ("paused", "spec_approval")

    again = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

    assert again.status_code == 200, again.text
    [approved] = [
        e["payload"]
        for e in _poll_events(client, wid, "gate_approved")
        if e["type"] == "gate_approved"
    ]
    assert "tips" in approved
