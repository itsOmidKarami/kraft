"""What approving a gate does to the worktree's base (Kraft-f1xy). A sibling
of test_gates.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.api import _poll_events, _poll_node_started, _post_default
from support.harness import commit_all


@pytest.mark.parametrize("base", ["moved", "blocked"])
def test_approving_a_gate_refreshes_the_base_and_never_fails_on_it(client, repo, monkeypatch, base):
    """Kraft-f1xy: an item that sat at `spec_approval` went on from the commit it
    forked from. The approval brings its branch up to date, as a resume does,
    before the chain goes on; one that cannot be brought up to date (an
    untracked file the base would overwrite) still lands, the item goes on from
    its old base, and an event says so."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    item = client.get(f"/api/work-items/{wid}").json()
    old_base = item["base_ref"]
    worktree = Path(item["worktree_path"])
    if base == "blocked":
        (worktree / "moved.txt").write_text("the item's own\n")
    (repo / "moved.txt").write_text("moved on\n")
    new_base = commit_all(repo, "moved on")

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

    assert r.status_code == 200, r.text
    evts = _poll_events(client, wid, "gate_approved")
    _poll_node_started(client, wid, "plan")
    now = client.get(f"/api/work-items/{wid}").json()
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    if base == "moved":
        assert now["base_ref"] == new_base
        assert "worktree_rebase_verified" in types
        assert (worktree / "moved.txt").read_text() == "moved on\n"
    else:
        assert now["base_ref"] == old_base
        [skipped] = [
            e
            for e in client.get(f"/api/work-items/{wid}/events").json()
            if e["type"] == "worktree_refresh_skipped"
        ]
        assert skipped["payload"]["gate"] == "spec_approval"
        assert "moved.txt" in skipped["payload"]["reason"]
    assert any(e["type"] == "gate_approved" for e in evts)


def test_a_gate_after_the_merge_request_opened_is_not_refreshed(client, repo, monkeypatch):
    """What is pushed is a reviewer's to read: only a gate no forge task came
    before is refreshed at approval."""
    from kraft.api.routes import gates as gates_route

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    st = client.app.state
    row = client.portal.call(
        lambda: st.db.read(
            lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
        )
    )
    nodes = gates_route.gate_nodes(st, row)

    assert gates_route._before_any_push(nodes, "spec_approval")
    assert gates_route._before_any_push(nodes, "local_review")
    assert not gates_route._before_any_push(nodes, "final_review")


def test_an_in_place_refresh_that_clashes_stops_the_approved_item_as_checkout(
    client, repo, monkeypatch
):
    """The approval lands, but a rebase that left the person's checkout
    clashing stops the item for them, naming the stash."""
    from kraft import inplace

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "autostart": True, "in_place": True},
    )
    wid = r.json()["id"]
    _poll_events(client, wid, "gate_requested")

    async def clash(*_, **__):
        raise inplace.AutostashClash("your edits to f clash", ["f"], "5ca1ab1e")

    monkeypatch.setattr(inplace, "rebase", clash)

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

    assert r.status_code == 200, r.text
    now = client.get(f"/api/work-items/{wid}").json()
    assert now["status"] == "needs_human"
    evts = client.get(f"/api/work-items/{wid}/events").json()
    [stop] = [e["payload"] for e in evts if e["type"] == "work_item_needs_human"]
    assert (stop["kind"], stop["facts"]) == ("checkout", {"paths": ["f"], "stash": "5ca1ab1e"})
    assert "gate_approved" in [e["type"] for e in evts]
