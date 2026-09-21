"""Gate approve/reject and the reject loop."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from support.api import (
    _approve_gate,
    _await_gate,
    _poll_events,
    _poll_node_started,
    _post_default,
    _wait_for_status,
)

_FAKE_REVIEWER = Path(__file__).resolve().parents[1] / "support" / "fake_reviewer.py"


#: The shipped `default` chain reaches its `chain_finalized` gate only after the
#: draft merge request's automated-review wait, which has no handler until Task
#: 9. This is the shortest V1 chain that reaches one for real: the summary
#: author writes the review brief the gate is about.
_REVIEW_EARLY = """\
id: review-early
nodes:
  - id: implementation
    kind: exec
    tasks:
      - id: implement
        extends: implementer
  - id: work_item_summary
    kind: exec
    tasks:
      - id: author
        extends: write_summary
  - id: chain_review
    kind: gate
    chain_finalized: true
    artifact: review_brief
    reject_to: implementation
"""


def _review_early(tdir):
    """`review-early`: a chain that reaches a final-review gate."""
    (tdir / "chains" / "review-early.yaml").write_text(_REVIEW_EARLY)


def _review_then_more(tdir):
    """`review-early`, plus `review-then-more`: the same chain with a node after the gate."""
    _review_early(tdir)
    (tdir / "chains" / "review-then-more.yaml").write_text(
        _REVIEW_EARLY.replace("id: review-early", "id: review-then-more")
        + "  - id: after\n    kind: exec\n    tasks:\n      - id: implement\n"
        "        extends: implementer\n"
    )


def _reject_cap(*, escalate=True):
    """A policy that caps every loop, gate rejections included, at one attempt."""
    tail = "" if escalate else "auto_escalate_stuck: false\n"

    def edit(tdir):
        (tdir / "policy.yaml").write_text(
            "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n" + tail
        )

    return pytest.mark.api_client(edit_templates=edit)


_REVIEW = pytest.mark.api_client(edit_templates=_review_early)


def _reject_past_the_cap(client, wid):
    """Reject `spec_approval` twice under a one-attempt cap: the second breaches
    it. Returns both responses."""
    _poll_events(client, wid, "gate_requested")
    reject = f"/api/work-items/{wid}/gates/spec_approval/reject"
    first = client.post(reject, json={"note": "again"})
    _poll_events(client, wid, "gate_requested", count=2)
    return first, client.post(reject, json={"note": "still no"})


def _post(client, repo, chain_template):
    return client.post(
        "/api/work-items",
        json={
            "title": "make the failing test pass",
            "repo": str(repo),
            "chain_template": chain_template,
        },
    ).json()["id"]


def _review_brief_path(client, wid):
    run_dir = Path(client.app.state.run_dirs.base)
    return run_dir / "worktrees" / wid / ".engineering" / "review_briefs" / f"{wid}.md"


def _create_running_session(wid: str, session_id: str, node_id: str, *, pid: int) -> None:
    """A live `worker_sessions` row on `node_id`, `hook_point='gate_review'`
    -- the shape a gate's in-flight `auto_escalate` review leaves, without
    actually dispatching an agent."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
            "log_path, result_path, status, created_at) VALUES (?, ?, ?, 'gate_review', ?, "
            "'x', 'x', 'running', datetime('now'))",
            (session_id, wid, node_id, pid),
        )
        conn.commit()
    finally:
        conn.close()


def _fixloop(tdir):
    """A `fixloop` chain whose verify node's reviewer fails once, and its own
    policy -- do not gate on the shipped attempts value."""
    (tdir / "chains" / "fixloop.yaml").write_text(
        "id: fixloop\n"
        "nodes:\n"
        "  - id: verify\n"
        "    kind: exec\n"
        "    tasks:\n"
        "      - id: review\n"
        "        kind: subprocess\n"
        f"        command: {sys.executable} {_FAKE_REVIEWER}\n"
        "    fix_loop:\n"
        "      tasks:\n"
        "        - id: repair\n"
        "          extends: repair_mr_feedback\n"
    )
    # attempts=1, not 2: an identical fingerprint recurring at round 1
    # correctly trips the stuck-detector before a 2-attempt cap would ever be
    # reached. attempts=1 breaches the cap on the second bump, strictly
    # before the stuck-check runs, so this test still exercises the
    # cap-breach path specifically.
    (tdir / "policy.yaml").write_text(
        "loops:\n  verify.fix_loop: { attempts: 1, wall_clock_s: 3600 }\n"
        "default: { attempts: 1, wall_clock_s: 3600 }\n"
    )


@pytest.mark.api_client(edit_templates=_fixloop)
def test_a_fix_loop_breach_over_http(client, repo, tmp_path, monkeypatch):
    """Was `test_default_chain_fix_loop_breach_over_http`. This test's target is
    a fix loop's cap-breach machinery over HTTP, driven by the scripted
    reviewer's own findings -- the same approach `test_findings_loop.py` uses.
    The shipped `default` chain's fix loop measures the changed-test-scope
    builtin, which the fixture neuters to `true`, so the loop is its own chain
    here."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")
    plan_path = tmp_path / "review-plan.json"
    plan_path.write_text(
        json.dumps(
            [
                {
                    "status": "failed",
                    "findings": [
                        {
                            "severity": "critical",
                            "message": "needs a fix",
                            "file": "a.py",
                            "line": 1,
                            "source_plugin": "fake",
                        }
                    ],
                }
            ]
        )
    )
    monkeypatch.setenv("KRAFT_FAKE_REVIEW_PLAN", str(plan_path))
    wid = _post(client, repo, "fixloop")
    events = _poll_events(client, wid, "work_item_needs_human", timeout=60)

    assert len([e for e in events if e["type"] == "fix_cycle_started"]) == 1

    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    assert item["current_node_id"] == "verify"
    assert any(
        s["node_id"] == "verify" and s["status"] == "capped_out" for s in item["worker_sessions"]
    )


def test_gate_approve_advances_chain(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")
    assert r.status_code == 200, r.text
    _poll_events(client, wid, "gate_approved")
    evts = _poll_node_started(client, wid, "plan")
    assert any(e["type"] == "node_started" and e["payload"]["node_id"] == "plan" for e in evts)


def test_approve_rejects_gate_absent_from_this_items_chain(client, repo):
    wid = _post_default(client, repo)
    response = client.post(f"/api/work-items/{wid}/gates/release_ready/approve")
    assert response.status_code == 404


@_REVIEW
def test_chain_review_missing_artifact_stops_at_needs_human(client, repo, tmp_path, monkeypatch):
    """A final-review gate cannot be approved without the document it is about
    (`apply_approval`, `chain-finalized-remains-a-dedicated-marker`). V1's
    final-review document is the review brief its gate names."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post(client, repo, "review-early")
    _await_gate(client, wid, "chain_review")
    _review_brief_path(client, wid).unlink()

    r = _approve_gate(client, wid, "chain_review")
    assert r.status_code == 422, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"


@_REVIEW
def test_chain_review_repeated_approve_keeps_erroring(client, repo, tmp_path, monkeypatch):
    """Kraft-iv4y: the exact reported shape -- a human retries `approve` on a
    gate that already failed once. `pending_gate` never clears (it's read off
    `gate_requested`/`gate_approved`/`gate_rejected`, none of which fire here),
    so the second call must error the same way as the first, not silently
    return 200 with the row unchanged."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post(client, repo, "review-early")
    _await_gate(client, wid, "chain_review")
    _review_brief_path(client, wid).unlink()

    first = _approve_gate(client, wid, "chain_review")
    second = client.post(f"/api/work-items/{wid}/gates/chain_review/approve")
    assert first.status_code == second.status_code == 422
    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "chain_review"


@pytest.mark.api_client(edit_templates=_review_then_more)
def test_chain_review_approval_advances_without_splicing(client, repo, monkeypatch):
    """V1's final-review approval advances to the node after the gate, and
    nothing rewrites the materialized tail (Task 4b parked
    `_splice_chain_review`). The brief carries a legacy envelope that proposes
    an empty tail; nothing reads it, and `after` still starts. What this pins
    is "approval advances past a present brief" -- a rewired splice would fail
    it by refusing (a 422: the splice reads a different artifact and the
    legacy `chain_definition`), not by dropping `after`."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post(client, repo, "review-then-more")
    _await_gate(client, wid, "chain_review")
    _review_brief_path(client, wid).write_text(
        "---\nkind: review_brief\n---\n"
        + json.dumps({"status": "ready_for_approval", "revised_chain_nodes": []})
    )

    r = _approve_gate(client, wid, "chain_review")
    assert r.status_code == 200, r.text
    _poll_node_started(client, wid, "after")


def test_gate_approve_wrong_gate_409(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    r = client.post(f"/api/work-items/{wid}/gates/plan_approval/approve")
    assert r.status_code == 409


def test_two_concurrent_approves_produce_one_walk_and_one_409(client, repo, monkeypatch):
    """spec §2: a pending gate has no status to claim, so `spawn`'s own
    refusal is the only thing standing between two concurrent approves and
    two walks from the same node index."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    app = client.app

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://kraft") as ac:
            return await asyncio.gather(
                ac.post(f"/api/work-items/{wid}/gates/spec_approval/approve"),
                ac.post(f"/api/work-items/{wid}/gates/spec_approval/approve"),
            )

    a, b = client.portal.call(scenario)
    assert sorted([a.status_code, b.status_code]) == [200, 409]


def test_gate_unknown_name_404(client, repo):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    assert client.post(f"/api/work-items/{wid}/gates/not_a_gate/approve").status_code == 404


def test_gate_reject_requires_note_and_re_runs_the_producer(client, repo):
    """A producer gate rejection is backward motion, not a dead end (02 §7.2).

    Before this, reject only appended an event: the gate stopped being pending
    and the node had no fix loop, so approve/retry/pause/resume all 409'd and
    the work item could never move again.
    """
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")

    assert (
        client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={}).status_code == 422
    )

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "too vague"})
    assert r.status_code == 200, r.text
    evts = client.get(f"/api/work-items/{wid}/events").json()
    rej = [e for e in evts if e["type"] == "gate_rejected"]
    assert rej and rej[0]["payload"] == {
        "gate": "spec_approval",
        "note": "too vague",
        "node": "spec",
        "by": "human",
        # A person only ever rejects; `fixed` is a gate-reviewer verdict and
        # has no door through this endpoint (Kraft-s7c04.16).
        "verdict": "reject",
    }

    # the spec node runs again and asks for its gate a second time
    _poll_events(client, wid, "gate_requested", count=2)
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["pending_gate"] == "spec_approval"
    assert client.post(f"/api/work-items/{wid}/gates/spec_approval/approve").status_code == 200


@_reject_cap(escalate=False)
def test_gate_reject_is_bounded_by_its_reject_loop(client, repo):
    """Rejections are capped like a fix loop; the breach stops the item.

    `auto_escalate_stuck` is explicitly disarmed here: this test is about
    the cap, not about the escalation dispatch the breach also now triggers
    (Kraft-h48r) -- see `test_gate_reject_loop_breach_auto_escalates_when_armed`
    for that."""
    wid = _post_default(client, repo)
    first, second = _reject_past_the_cap(client, wid)
    assert (first.status_code, second.status_code) == (200, 200)
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    assert item["pending_gate"] is None
    stops = [
        e
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "work_item_needs_human"
    ]
    assert stops and "spec_approval_reject_loop" in stops[-1]["payload"]["reason"]


@pytest.mark.parametrize(
    "escalated",
    [
        pytest.param(True, marks=_reject_cap(escalate=True), id="armed"),
        pytest.param(False, marks=_reject_cap(escalate=False), id="disarmed"),
    ],
)
def test_gate_reject_loop_breach_auto_escalates_only_when_armed(
    client, repo, monkeypatch, escalated
):
    """Kraft-h48r: `reject_gate`'s cap-breach branch used to return straight
    past `auto_escalate_stuck` -- the agent-side `reject` verdict re-enters
    `walk.run_once`, whose own post-step call reaches it normally, but this
    HTTP route never goes through either `walk.run` or `resuming.resume`.
    Disarmed, it must still no-op exactly like it does on the walk-driven path."""
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    wid = _post_default(client, repo)
    _first, r = _reject_past_the_cap(client, wid)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
    assert calls == ([(wid, True)] if escalated else [])


@_REVIEW
def test_rejecting_the_final_gate_re_enters_at_implementation(client, repo, tmp_path, monkeypatch):
    """Kraft-ko7j. The final review gate used to be terminal: the note went
    into an event nothing read and the item sat in needs_human with approve
    or abandon as its only exits. It now routes to the node named by the
    gate's `reject_to`, carrying the note as that node's steer."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    prompts = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompts))
    wid = _post(client, repo, "review-early")
    _await_gate(client, wid, "chain_review")

    r = client.post(
        f"/api/work-items/{wid}/gates/chain_review/reject",
        json={"note": "the retry path is untested"},
    )
    assert r.status_code == 200, r.text

    # the chain went back to work rather than stopping
    rejected = [
        e for e in _poll_events(client, wid, "gate_rejected") if e["type"] == "gate_rejected"
    ]
    assert rejected[-1]["payload"]["node"] == "implementation"

    # ...walked implementation -> work_item_summary -> chain_review and
    # asked for the same gate a second time
    _poll_events(client, wid, "gate_requested", count=2)
    starts = [
        e["payload"]["node_id"]
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "node_started"
    ]
    assert starts.count("implementation") == 2
    assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "chain_review"

    # and the note led the re-run's instruction rather than dying in an event
    assert "the retry path is untested" in prompts.read_text()


def test_reject_refuses_a_node_after_its_gate(client, repo, monkeypatch):
    """A rejection is backward motion. Naming a later node would let the human
    skip every node between the gate and the target."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _await_gate(client, wid, "spec_approval")
    assert client.post(f"/api/work-items/{wid}/gates/spec_approval/approve").status_code == 200
    _await_gate(client, wid, "plan_approval")

    r = client.post(
        f"/api/work-items/{wid}/gates/plan_approval/reject",
        json={"note": "wrong", "node": "merge"},
    )
    assert r.status_code == 400, r.text
    assert "merge" in r.json()["detail"]
    # nothing was spawned and nothing was recorded
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    assert item["pending_gate"] == "plan_approval"
    assert not [
        e
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "gate_rejected"
    ]


def test_reject_refuses_a_node_that_is_not_in_this_chain(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _await_gate(client, wid, "spec_approval")
    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/reject",
        json={"note": "wrong", "node": "no_such_node"},
    )
    assert r.status_code == 400, r.text


@_reject_cap()
def test_a_rejection_note_is_the_default_steer_for_the_next_retry(client, repo, monkeypatch):
    """Kraft-ko7j: a human who typed a reason should not have to retype it to
    make it count."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _reject_past_the_cap(client, wid)
    _wait_for_status(client, wid, "needs_human")

    r = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r.status_code == 200, r.text
    assert r.json()["steer"] == "still no"
    retried = [
        e
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "work_item_retried"
    ]
    assert retried[-1]["payload"]["steer"] == "still no"


@_reject_cap()
def test_retry_clears_the_gate_reject_counter(client, repo, monkeypatch):
    """Kraft-ko7j §A4. Retry is the human's override of the reject cap: after
    it, the re-opened gate can be rejected again rather than 'exhausted'
    forever."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post_default(client, repo)
    _reject_past_the_cap(client, wid)
    _wait_for_status(client, wid, "needs_human")

    assert client.post(f"/api/work-items/{wid}/retry", json={}).status_code == 200
    _await_gate(client, wid, "spec_approval")
    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "third time"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active", "the cleared cap did not give the gate a fresh budget"


def test_gate_approve_unknown_work_item_404(client):
    assert client.post("/api/work-items/nope/gates/spec_approval/approve").status_code == 404


def test_reject_with_invalid_policy_503s_without_stopping_the_live_review(client, repo):
    """The 503 bail-out changes nothing, so it must not be reached having
    already paused the item and killed its auto_escalate review agent."""
    from kraft.api import deps

    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")

    async def _never_returning():
        await asyncio.Event().wait()

    async def inject():
        deps.spawn(client.app, wid, _never_returning())

    client.portal.call(inject)
    before = client.get(f"/api/work-items/{wid}").json()
    client.app.state.invalid_policy = ["boom: not a number"]

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "no"})
    assert r.status_code == 503, r.text
    after = client.get(f"/api/work-items/{wid}").json()
    assert after["status"] == before["status"]
    assert after["pending_gate"] == before["pending_gate"]
    assert client.portal.call(lambda: deps.cancel(client.app, wid)) is None


def test_reject_with_bad_node_400s_without_stopping_the_live_review(client, repo):
    """A typo'd `node` raises ValueError in `apply_rejection`; that must be
    caught before the live auto_escalate review is stopped, same as the
    invalid_policy 503 above -- a bad target changes nothing."""
    from kraft.api import deps

    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")

    async def _never_returning():
        await asyncio.Event().wait()

    async def inject():
        deps.spawn(client.app, wid, _never_returning())

    client.portal.call(inject)
    before = client.get(f"/api/work-items/{wid}").json()

    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/reject",
        json={"note": "no", "node": "not_a_real_node"},
    )
    assert r.status_code == 400, r.text
    after = client.get(f"/api/work-items/{wid}").json()
    assert after["status"] == before["status"]
    assert after["pending_gate"] == before["pending_gate"]
    assert client.portal.call(lambda: deps.cancel(client.app, wid)) is None


def test_approve_gate_stops_a_live_auto_review_session_first(client, repo, monkeypatch):
    """A gate's own in-flight auto_escalate review must not survive into the
    next node's worktree once a human approves: its session is paused and
    SIGTERM'd before the approval's own executor.run spawns."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    # V1: the gate is its own node, so its review session sits on it.
    _create_running_session(wid, "r1", "spec_approval", pid=4242)

    r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

    assert r.status_code == 200, r.text
    assert terminated == [4242]
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert types.index("pause_requested") < types.index("gate_approved")


def test_reject_gate_stops_a_live_auto_review_session_first(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    # V1: the gate is its own node, so its review session sits on it.
    _create_running_session(wid, "r1", "spec_approval", pid=4242)

    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/reject",
        json={"note": "not yet"},
    )

    assert r.status_code == 200, r.text
    assert terminated == [4242]
    types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert "pause_requested" in types


def test_reject_with_a_bad_node_leaves_a_live_auto_review_running(client, repo, monkeypatch):
    """The refusing check runs before the kill (code review finding): a human
    who gets a 400 must find the item exactly as it was -- gate still
    pending, review session still live -- the same ordering retry applies to
    its own refusals."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    # V1: the gate is its own node, so its review session sits on it.
    _create_running_session(wid, "r1", "spec_approval", pid=4242)

    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/reject",
        json={"note": "wrong", "node": "no_such_node"},
    )

    assert r.status_code == 400, r.text
    assert terminated == []
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "needs_human"
    assert item["pending_gate"] == "spec_approval"
    assert "pause_requested" not in [
        e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()
    ]


def test_a_worker_agent_s_gate_approval_is_not_recorded_as_a_person_s(client, repo):
    """Kraft-s7c04.43: the MCP approve tool lands on this route, which took the
    `by="human"` default from store/gates.py. analytics.py branches on
    `by == "agent"` to decide whether a run boundary was real human oversight,
    so this path silently inflated that reading in the safe-looking direction."""
    wid = _post_default(client, repo)
    _await_gate(client, wid, "spec_approval")
    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/approve",
        headers={"X-Kraft-Session-Id": "abc123"},
    )
    assert r.status_code == 200, r.text
    evts = client.get(f"/api/work-items/{wid}/events").json()
    appr = [e for e in evts if e["type"] == "gate_approved"]
    assert appr and appr[-1]["payload"]["by"] == "agent"


def test_an_assistant_session_through_mcp_is_neither_a_worker_nor_a_person(client, repo):
    """Every one of the 28 real MCP gate calls on this machine came from a
    non-worker assistant session, which carries no KRAFT_SESSION_ID. Recording
    those as `human` is the defect; recording them as `agent` would claim a
    Kraft worker approved its own artifact, which is a different thing."""
    wid = _post_default(client, repo)
    _await_gate(client, wid, "spec_approval")
    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/approve",
        headers={"X-Kraft-Client": "mcp"},
    )
    assert r.status_code == 200, r.text
    evts = client.get(f"/api/work-items/{wid}/events").json()
    appr = [e for e in evts if e["type"] == "gate_approved"]
    assert appr and appr[-1]["payload"]["by"] == "assistant"


def test_a_gate_rejection_records_its_caller_the_same_way(client, repo):
    """The bead asked for reject to be checked while in there. It has the same
    gap: apply_rejection takes `by` and the route never passed it."""
    wid = _post_default(client, repo)
    _await_gate(client, wid, "spec_approval")
    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/reject",
        json={"note": "not specific enough"},
        headers={"X-Kraft-Client": "mcp"},
    )
    assert r.status_code == 200, r.text
    evts = client.get(f"/api/work-items/{wid}/events").json()
    rej = [e for e in evts if e["type"] == "gate_rejected"]
    assert rej and rej[-1]["payload"]["by"] == "assistant"


def test_a_worker_s_own_session_id_outranks_how_it_connected(client, repo):
    """A Kraft worker reaching the API through MCP carries both headers. What it
    is outranks how it connected."""
    wid = _post_default(client, repo)
    _await_gate(client, wid, "spec_approval")
    r = client.post(
        f"/api/work-items/{wid}/gates/spec_approval/approve",
        headers={"X-Kraft-Session-Id": "abc123", "X-Kraft-Client": "mcp"},
    )
    assert r.status_code == 200, r.text
    evts = client.get(f"/api/work-items/{wid}/events").json()
    assert [e for e in evts if e["type"] == "gate_approved"][-1]["payload"]["by"] == "agent"


def test_the_mcp_transport_announces_itself_and_the_bare_cli_does_not(monkeypatch):
    """The bearer token cannot be the discriminator -- transport attaches it to
    every CLI call too, so a human typing `kraft item approve` would read as an
    agent. This header is what separates them."""
    from kraft.client import transport

    monkeypatch.delenv("KRAFT_CLIENT", raising=False)
    monkeypatch.delenv("KRAFT_SESSION_ID", raising=False)
    assert "X-Kraft-Client" not in transport.http().headers

    monkeypatch.setenv("KRAFT_CLIENT", "mcp")
    assert transport.http().headers["X-Kraft-Client"] == "mcp"
