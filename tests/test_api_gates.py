"""Gate approve/reject, chain-review splice, and the reject loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from support.api import (
    _FAKE_CLAUDE,
    _approve_gate,
    _await_gate,
    _client,
    _poll_events,
    _post_default,
    _wait_for_status,
)
from support.harness import fake_templates_dir, make_repo


def _chain_review_path(client, wid):
    run_dir = Path(client.app.state.run_dirs.base)
    return run_dir / "worktrees" / wid / ".engineering" / "chain_reviews" / f"{wid}.md"


def _write_chain_review(client, wid, envelope):
    path = _chain_review_path(client, wid)
    path.parent.mkdir(parents=True, exist_ok=True)
    front_matter = f"---\nwork_item_ids: [{wid}]\nkind: chain_reviews\ntitle: t\n---\n\n"
    path.write_text(front_matter + json.dumps(envelope) + "\n")


def test_default_chain_fix_loop_breach_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")
    repo = make_repo(tmp_path)
    # Own policy fixture — do not gate on the shipped attempts value.
    tdir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (tdir / "policy.yaml").write_text(
        "loops:\n  verify_fix_loop: { attempts: 2, wall_clock_s: 3600 }\n"
        "default: { attempts: 2, wall_clock_s: 3600 }\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=tdir) as client:
        wid = client.post(
            "/api/work-items",
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "default",
            },
        ).json()["id"]
        # verify (fix_loop) sits before the human_review gate, so the cap breach
        # drops the item to needs_human before human_review_approval is ever reached.
        for gate in ("spec_approval", "plan_approval", "chain_finalized"):
            assert _approve_gate(client, wid, gate, timeout=60).status_code == 200
        events = _poll_events(client, wid, "work_item_needs_human", timeout=60)

        assert len([e for e in events if e["type"] == "fix_cycle_started"]) == 2

        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["current_node_id"] == "verify"
        assert any(
            s["node_id"] == "verify" and s["status"] == "capped_out"
            for s in item["worker_sessions"]
        )


def test_gate_approve_advances_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"

        r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")
        assert r.status_code == 200, r.text
        _poll_events(client, wid, "gate_approved")
        assert any(
            e["type"] == "node_started" and e["payload"]["node_id"] == "plan"
            for e in client.get(f"/api/work-items/{wid}/events").json()
        )


def test_chain_review_splice_runs_the_revised_tail(tmp_path, monkeypatch):
    """Kraft-hm0: a genuinely revised tail is spliced into `chain_definition`
    on `chain_finalized` approval, and the next node runs the added task, not
    whatever the template originally had there."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        tail = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        original = [n for n in tail if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [
            {"id": "extra_check", "tasks": ["on.review.local.run"], "gate_after": None},
            *original,
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )

        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 200, r.text
        _await_gate(client, wid, "human_review_approval")
        started = [
            e["payload"]["node_id"]
            for e in client.get(f"/api/work-items/{wid}/events").json()
            if e["type"] == "node_started"
        ]
        assert "extra_check" in started
        assert client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"][3][
            "id"
        ] == ("extra_check")


def test_chain_review_unchanged_tail_round_trips(tmp_path, monkeypatch):
    """The skill's own documented common case: emitting the tail unchanged is
    a no-op in effect, the same code path as a real splice (Kraft-hm0)."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")
        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]

        # fake-claude.sh's default answer for on.chain.review_ready is the
        # unchanged tail — no override needed here.
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 200, r.text
        after = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        assert after == before


def test_chain_review_splice_keeps_on_failure_from_schema_only_nodes(tmp_path, monkeypatch):
    """Kraft-eod0: the skill's documented node schema is only 4 of a node's 8
    real keys. A reviewer following it to the letter emits nodes with just
    `{id, tasks, gate_after, fix_loop}` -- the splice must not read that as
    "delete on_failure", or mr_checks's repair hook silently stops firing on
    every chain review that says the chain is fine as-is."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        mr_checks_before = next(n for n in before if n["id"] == "mr_checks")
        assert mr_checks_before["on_failure"] == ["on.mr_checks.repair"]

        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        # Only the 4 keys the skill's schema teaches -- reproduces a reviewer
        # that followed it to the letter, not one that happened to echo extra
        # keys back.
        schema_only = [
            {
                "id": n["id"],
                "tasks": n["tasks"],
                "gate_after": n["gate_after"],
                "fix_loop": n["fix_loop"],
            }
            for n in tail
        ]
        _write_chain_review(
            client,
            wid,
            {
                "status": "ready_for_approval",
                "revised_chain_nodes": schema_only,
                "rationale": "no change",
            },
        )

        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 200, r.text
        after = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        mr_checks_after = next(n for n in after if n["id"] == "mr_checks")
        assert mr_checks_after["on_failure"] == ["on.mr_checks.repair"]


@pytest.mark.parametrize(
    ("envelope", "reason_has"),
    [
        (
            {"status": "error", "revised_chain_nodes": [], "rationale": "cannot decide"},
            "cannot decide",
        ),
        (
            {"status": "ready_for_approval", "revised_chain_nodes": [{"bad": "shape"}]},
            "chain_review",
        ),
    ],
)
def test_chain_review_error_or_invalid_tail_stops_at_needs_human(
    tmp_path, monkeypatch, envelope, reason_has
):
    """Kraft-hm0/unk: `status: "error"` and an invalid `revised_chain_nodes`
    both stop the item at needs_human rather than advancing, and never touch
    `chain_definition`.

    Kraft-iv4y: the approve call itself now errors instead of returning 200
    with nothing changed -- a human retrying the same broken approval needs
    to hear "no" clearly, not read it off the row's unchanged status."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")
        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]

        _write_chain_review(client, wid, envelope)
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 422, r.text
        assert reason_has in r.json()["detail"]
        assert "kraft item retry" in r.json()["detail"]
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["chain_definition"]["nodes"] == before
        stops = [
            e
            for e in client.get(f"/api/work-items/{wid}/events").json()
            if e["type"] == "work_item_needs_human"
        ]
        assert stops and reason_has in stops[-1]["payload"]["reason"]


def test_chain_review_missing_artifact_stops_at_needs_human(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")
        _chain_review_path(client, wid).unlink()

        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 422, r.text
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"


def test_chain_review_repeated_approve_keeps_erroring(tmp_path, monkeypatch):
    """Kraft-iv4y: the exact reported shape -- a human retries `approve` on a
    gate that already failed once. `pending_gate` never clears (it's read off
    `gate_requested`/`gate_approved`/`gate_rejected`, none of which fire here),
    so the second call must error the same way as the first, not silently
    return 200 with the row unchanged."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")
        _chain_review_path(client, wid).unlink()

        first = _approve_gate(client, wid, "chain_finalized")
        second = client.post(f"/api/work-items/{wid}/gates/chain_finalized/approve")
        assert first.status_code == second.status_code == 422
        assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == "chain_finalized"


def test_gate_approve_wrong_gate_409(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        r = client.post(f"/api/work-items/{wid}/gates/plan_approval/approve")
        assert r.status_code == 409


def test_two_concurrent_approves_produce_one_walk_and_one_409(tmp_path, monkeypatch):
    """spec §2: a pending gate has no status to claim, so `spawn`'s own
    refusal is the only thing standing between two concurrent approves and
    two walks from the same node index."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_gate_unknown_name_404(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        assert client.post(f"/api/work-items/{wid}/gates/not_a_gate/approve").status_code == 404


def test_gate_reject_requires_note_and_re_runs_the_producer(tmp_path, monkeypatch):
    """A producer gate rejection is backward motion, not a dead end (02 §7.2).

    Before this, reject only appended an event: the gate stopped being pending
    and the node had no fix loop, so approve/retry/pause/resume all 409'd and
    the work item could never move again.
    """
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")

        assert (
            client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={}).status_code
            == 422
        )

        r = client.post(
            f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "too vague"}
        )
        assert r.status_code == 200, r.text
        evts = client.get(f"/api/work-items/{wid}/events").json()
        rej = [e for e in evts if e["type"] == "gate_rejected"]
        assert rej and rej[0]["payload"] == {
            "gate": "spec_approval",
            "note": "too vague",
            "node": "spec",
            "by": "human",
        }

        # the spec node runs again and asks for its gate a second time
        _poll_events(client, wid, "gate_requested", count=2)
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["pending_gate"] == "spec_approval"
        assert client.post(f"/api/work-items/{wid}/gates/spec_approval/approve").status_code == 200


def test_gate_reject_is_bounded_by_its_reject_loop(tmp_path, monkeypatch):
    """Rejections are capped like a fix loop; the breach stops the item."""
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        assert (
            client.post(
                f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "again"}
            ).status_code
            == 200
        )
        _poll_events(client, wid, "gate_requested", count=2)
        assert (
            client.post(
                f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "still no"}
            ).status_code
            == 200
        )
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["status"] == "needs_human"
        assert item["pending_gate"] is None
        stops = [
            e
            for e in client.get(f"/api/work-items/{wid}/events").json()
            if e["type"] == "work_item_needs_human"
        ]
        assert stops and "spec_approval_reject_loop" in stops[-1]["payload"]["reason"]


def test_rejecting_the_final_gate_re_enters_at_implementation(tmp_path, monkeypatch):
    """Kraft-ko7j. `human_review_approval` used to be terminal: the note went
    into an event nothing read and the item sat in needs_human with approve
    or abandon as its only exits. It now routes to the node named by the
    chain's `reject_to`, carrying the note as that node's steer."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    prompts = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompts))
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval", "chain_finalized"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "human_review_approval")

        r = client.post(
            f"/api/work-items/{wid}/gates/human_review_approval/reject",
            json={"note": "the retry path is untested"},
        )
        assert r.status_code == 200, r.text

        # the chain went back to work rather than stopping
        rejected = [
            e for e in _poll_events(client, wid, "gate_rejected") if e["type"] == "gate_rejected"
        ]
        assert rejected[-1]["payload"]["node"] == "implementation"

        # ...walked implementation -> verify -> open_mr -> mr_checks -> human_review
        # and asked for the same gate a second time
        _poll_events(client, wid, "gate_requested", count=5)
        starts = [
            e["payload"]["node_id"]
            for e in client.get(f"/api/work-items/{wid}/events").json()
            if e["type"] == "node_started"
        ]
        assert starts.count("implementation") == 2
        assert client.get(f"/api/work-items/{wid}").json()["pending_gate"] == (
            "human_review_approval"
        )

        # and the note led the re-run's instruction rather than dying in an event
        assert "the retry path is untested" in prompts.read_text()


def test_reject_refuses_a_node_after_its_gate(tmp_path, monkeypatch):
    """A rejection is backward motion. Naming a later node would let the human
    skip every node between the gate and the target."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_reject_refuses_a_node_that_is_not_in_this_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "spec_approval")
        r = client.post(
            f"/api/work-items/{wid}/gates/spec_approval/reject",
            json={"note": "wrong", "node": "no_such_node"},
        )
        assert r.status_code == 400, r.text


def test_a_rejection_note_is_the_default_steer_for_the_next_retry(tmp_path, monkeypatch):
    """Kraft-ko7j: a human who typed a reason should not have to retype it to
    make it count."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "spec_approval")
        client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "again"})
        _await_gate(client, wid, "spec_approval")
        client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "still no"})
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


def test_retry_clears_the_gate_reject_counter(tmp_path, monkeypatch):
    """Kraft-ko7j §A4. Retry is the human's override of the reject cap: after
    it, the re-opened gate can be rejected again rather than 'exhausted'
    forever."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "spec_approval")
        for note in ("again", "still no"):
            client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": note})
            if note == "again":
                _await_gate(client, wid, "spec_approval")
        _wait_for_status(client, wid, "needs_human")

        assert client.post(f"/api/work-items/{wid}/retry", json={}).status_code == 200
        _await_gate(client, wid, "spec_approval")
        r = client.post(
            f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "third time"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active", (
            "the cleared cap did not give the gate a fresh budget"
        )


def test_gate_approve_unknown_work_item_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/api/work-items/nope/gates/spec_approval/approve").status_code == 404


def test_reject_with_invalid_policy_503s_without_stopping_the_live_review(tmp_path, monkeypatch):
    """The 503 bail-out changes nothing, so it must not be reached having
    already paused the item and killed its auto_escalate review agent."""
    from kraft.api import deps

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_reject_with_bad_node_400s_without_stopping_the_live_review(tmp_path, monkeypatch):
    """A typo'd `node` raises ValueError in `apply_rejection`; that must be
    caught before the live auto_escalate review is stopped, same as the
    invalid_policy 503 above -- a bad target changes nothing."""
    from kraft.api import deps

    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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
