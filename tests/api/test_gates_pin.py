"""What a person passing the final gate pins: the head the merge may push
(`kraft.reviewed_head`). A sibling of test_gates.py. The default chain's
`final_review` lies past a merge request, so these take `spec_approval` for
it: what makes a gate the final one is `reviewed_head.final_gate`'s to say."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.api import _poll_events, _post_default
from support.harness import commit_all, git

from kraft import inplace, reviewed_head, store
from kraft.api import deps

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
    assert item.get("review_head") == (head if final else None)
    shown = client.get(f"/api/work-items/{wid}/artifact")
    assert shown.status_code == 200, shown.text
    assert shown.json().get("head") == (head if final else None)

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


def _agent_at_work(client, wid: str, *, recorded: bool) -> None:
    """A live agent session on `spec_approval` (a reviewer, a reply), started
    over the item's head as it stands: recorded the way a launch records it
    (`store.create_session(tips=...)`), or, `recorded` False, a row with no
    start tips at all, as one launched before they were recorded."""
    if not recorded:
        _create_running_session(wid, "r1", "spec_approval", pid=4242)
        return
    st = client.app.state

    async def start():
        row = deps._work_item_row(st, wid)
        folder = inplace.item_folder(st.run_dirs, row)
        tips = reviewed_head.pin_for(st.db, row, "spec_approval", folder)
        await st.db.write(
            lambda c: store.create_session(
                c,
                id="r1",
                work_item_id=wid,
                node_id="spec_approval",
                hook_point="spec_approval.reply",
                log_path="x",
                result_path="x",
                tips=tips,
            )
        )
        await st.db.write(lambda c: store.session_running(c, "r1", 4242, None, work_item_id=wid))

    client.portal.call(start)


@pytest.mark.parametrize(
    ("door", "event", "final", "recorded", "commits", "refused"),
    [
        ("gates/spec_approval/approve", "gate_approved", True, True, True, True),
        ("gates/spec_approval/approve", "gate_approved", True, True, False, False),
        ("gates/spec_approval/approve", "gate_approved", True, False, False, True),
        ("gates/spec_approval/approve", "gate_approved", False, True, True, False),
        ("skip", "node_skipped", True, True, True, True),
        ("skip", "node_skipped", True, True, False, False),
    ],
    ids=[
        "approved-over-an-agent-that-committed",
        "approved-over-an-agent-that-committed-nothing",
        "approved-over-an-agent-that-recorded-no-head",
        "approved-another-gate-over-an-agent-that-committed",
        "skipped-over-an-agent-that-committed",
        "skipped-over-an-agent-that-committed-nothing",
    ],
)
def test_passing_the_final_gate_over_a_live_agent_pins_nothing_it_wrote(
    client, repo, monkeypatch, door, event, final, recorded, commits, refused
):
    """An agent still at work at the final gate (a reviewer, a reply) may
    have committed after the person last looked. Approving or skipping the
    gate stops it; when the branch moved since the agent started -- or it
    recorded no start to tell -- the decision is refused and the item left
    paused, so a second look is what pins. An agent that moved nothing costs
    no second look: the head is pinned. Any other gate pins nothing, and
    goes on."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid, **_: None)
    if final:
        monkeypatch.setattr(reviewed_head, "final_gate", lambda row: "spec_approval")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    item = client.get(f"/api/work-items/{wid}").json()
    worktree = Path(item["worktree_path"])
    _agent_at_work(client, wid, recorded=recorded)
    if commits:
        (worktree / "agent.txt").write_text("the agent's own edit\n")
        commit_all(worktree, "the agent's own edit")
    head = git(worktree, "rev-parse", f"refs/heads/{item['branch']}")

    r = client.post(f"/api/work-items/{wid}/{door}", json={})

    if not refused:
        assert r.status_code == 200, r.text
        [passed] = [e["payload"] for e in _poll_events(client, wid, event) if e["type"] == event]
        if final:
            [(checkout, tip)] = passed["tips"].items()
            assert (reviewed_head.same_checkout(checkout, worktree), tip) == (True, head)
        else:
            assert "tips" not in passed
        return
    assert r.status_code == 409, r.text
    assert "look at it again" in r.json()["detail"]
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert event not in types
    item = client.get(f"/api/work-items/{wid}").json()
    assert (item["status"], item["pending_gate"]) == ("paused", "spec_approval")

    again = client.post(f"/api/work-items/{wid}/{door}", json={})

    assert again.status_code == 200, again.text
    [passed] = [e["payload"] for e in _poll_events(client, wid, event) if e["type"] == event]
    [(checkout, tip)] = passed["tips"].items()
    assert (reviewed_head.same_checkout(checkout, worktree), tip) == (True, head)
