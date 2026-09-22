"""The seeded `default` chain, walked end to end over HTTP: every gate approved
through the API, every external wait parked and woken by the one scheduler,
on a repo whose forge is the dev-only `FakeForge` (`forge: fake`)."""

from __future__ import annotations

import time
from functools import partial

from support.api import _approve_gate, _post_default
from support.harness import entry_of

from kraft import waits
from kraft.adapters import forge
from kraft.api import deps

#: A `now` every parked wait is due by, so the test never sleeps out a backoff.
LATER = "2999-01-01T00:00:00+00:00"


def _walk_to_the_end(client, wid, timeout=120):
    """Approve each gate as it opens and tick the scheduler whenever the item
    parks, until it completes. Returns (gates approved, nodes parked on)."""
    approved, parked = [], []
    deadline = time.monotonic() + timeout
    while (item := client.get(f"/api/work-items/{wid}").json())["status"] != "completed":
        assert time.monotonic() < deadline, item
        if item["status"] in ("active", "pending") or deps.task_is_live(client.app, wid):
            time.sleep(0.02)
        elif item["pending_gate"]:
            assert _approve_gate(client, wid, item["pending_gate"]).status_code == 200
            approved.append(item["pending_gate"])
        elif item["status"] == "waiting":
            parked.append(item["current_node_id"])
            assert client.portal.call(partial(waits.tick, client.app, now=LATER)) == [wid]
        else:
            raise AssertionError(f"stopped at {item['current_node_id']}: {item['status']}")
    return approved, parked


def _on_a_fake_forge(monkeypatch, **states) -> forge.FakeForge:
    """Every agent on the fake `claude`, every repo on the returned forge."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    fake = forge.FakeForge(**states)
    monkeypatch.setattr(forge.run, "_DEV_FAKE", fake)
    monkeypatch.setattr(
        deps, "_connected", lambda repos, path: entry_of({"setup_command": "", "forge": "fake"})
    )
    return fake


def test_approving_every_gate_through_the_api_walks_the_default_chain_to_post_merge_ci(
    client, repo, monkeypatch
):
    """The Phase 6 exit (it replaces Ruling 48b's stop at automated review).
    Each of the five waits is pending once, then settles: the pipeline, the
    automated review, the external approval, the merge landing and the
    target branch's pipeline after it."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_LABELS", "type::fix")
    fake = _on_a_fake_forge(
        monkeypatch,
        ci_states=["pending", "success"],
        review_results=["pending", "clean"],
        approval_states=["pending", "approved"],
        merge_delay=1,
        branch_ci_states=["pending", "success"],
    )
    wid = _post_default(client, repo)

    approved, parked = _walk_to_the_end(client, wid)

    assert approved == ["spec_approval", "plan_approval", "local_review", "chain_review"]
    assert parked == [
        "merge_request_feedback",
        "merge_request_feedback",
        "external_approval",
        "merge",
        "post_merge_ci",
    ]
    assert fake.merged == [1]
    # Ruling 207: the draft opens with the metadata the describe node wrote.
    assert fake.opened_titles[1] == "fake mr_meta"
    assert fake.opened_meta[1].labels == ("type::fix",)
    assert fake.opened_bodies[1].startswith("fake mr_meta body")
    evts = client.get(f"/api/work-items/{wid}/events").json()
    # `default-post-draft-flow-is-ordered`: every node, in the seeded order --
    # the summary and final gate before the approval wait, merge after it.
    assert [e["payload"]["node_id"] for e in evts if e["type"] == "node_completed"] == [
        "spec",
        "spec_approval",
        "plan",
        "plan_approval",
        "implementation",
        "verification",
        "work_brief",
        "local_review",
        "describe_merge_request",
        "draft_merge_request",
        "merge_request_feedback",
        "work_item_summary",
        "chain_review",
        "mark_ready",
        "external_approval",
        "merge",
        "post_merge_ci",
    ]
    ended = [e["payload"] for e in evts if e["type"] == "external_wait_ended"]
    assert {e["task"] for e in ended} == {
        "merge_request_feedback.ci.await_ci",
        "merge_request_feedback.automated_review.await_review",
        "external_approval.main.await",
        "merge.main.merge",
        "post_merge_ci.main.await",
    }
    assert {e["outcome"] for e in ended} == {"settled"}


def test_with_no_merge_request_metadata_the_draft_opens_with_the_default_body(
    client, repo, monkeypatch
):
    """Ruling 207's fallback: an item that skipped `describe_merge_request`
    has no `mr_meta`, and its draft still opens, titled after the work item,
    unlabelled, with Kraft's own body."""
    fake = _on_a_fake_forge(monkeypatch)
    title = "make the failing test pass"
    body = {"title": title, "repo": str(repo), "chain_template": "default"}
    r = client.post("/api/work-items", json={**body, "skip_nodes": ["describe_merge_request"]})
    assert r.status_code == 201

    _walk_to_the_end(client, r.json()["id"])

    assert fake.opened_titles[1] == title
    assert fake.opened_meta[1] == forge.MRMeta()
    assert fake.opened_bodies[1].startswith(f"Opened by Kraft for work item {r.json()['id']}.")
