"""The seeded `default` chain, walked end to end over HTTP: every gate approved
through the API, every external wait parked and woken by the one scheduler,
on a repo whose forge is the dev-only `FakeForge` (`forge: fake`)."""

from __future__ import annotations

import time
from functools import partial

from support.api import _approve_gate, _post_default

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


def test_approving_every_gate_through_the_api_walks_the_default_chain_to_post_merge_ci(
    client, repo, monkeypatch
):
    """The Phase 6 exit (it replaces Ruling 48b's stop at automated review).
    Each of the five waits is pending once, then settles: the pipeline, the
    automated review, the external approval, the merge landing and the
    target branch's pipeline after it."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    fake = forge.FakeForge(
        ci_states=["pending", "success"],
        review_results=["pending", "clean"],
        approval_states=["pending", "approved"],
        merge_delay=1,
        branch_ci_states=["pending", "success"],
    )
    monkeypatch.setattr(forge.run, "_DEV_FAKE", fake)
    monkeypatch.setattr(
        deps, "_connected", lambda repos, path: {"setup_command": "", "forge": "fake"}
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
    evts = client.get(f"/api/work-items/{wid}/events").json()
    completed = [e["payload"]["node_id"] for e in evts if e["type"] == "node_completed"]
    assert completed[-1] == "post_merge_ci"
    ended = [e["payload"] for e in evts if e["type"] == "external_wait_ended"]
    assert {e["task"] for e in ended} == {
        "merge_request_feedback.ci.await_ci",
        "merge_request_feedback.automated_review.await_review",
        "external_approval.main.await",
        "merge.main.merge",
        "post_merge_ci.main.await",
    }
    assert {e["outcome"] for e in ended} == {"settled"}
