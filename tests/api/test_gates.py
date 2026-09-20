"""Gate approve/reject, chain-review splice, and the reject loop."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from support.api import (
    _FAKE_CLAUDE,
    _approve_gate,
    _await_gate,
    _client,
    _poll_events,
    _poll_node_started,
    _post_default,
    _wait_for_status,
)
from support.harness import fake_templates_dir, make_repo

_FAKE_REVIEWER = Path(__file__).resolve().parents[1] / "support" / "fake_reviewer.py"


def _chain_review_path(client, wid):
    run_dir = Path(client.app.state.run_dirs.base)
    return run_dir / "worktrees" / wid / ".engineering" / "chain_reviews" / f"{wid}.md"


def _write_chain_review(client, wid, envelope):
    path = _chain_review_path(client, wid)
    path.parent.mkdir(parents=True, exist_ok=True)
    front_matter = f"---\nwork_item_ids: [{wid}]\nkind: chain_reviews\ntitle: t\n---\n\n"
    path.write_text(front_matter + json.dumps(envelope) + "\n")


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


def test_default_chain_fix_loop_breach_over_http(tmp_path, monkeypatch):
    """This test's target is specifically `verify`'s cap-breach machinery
    over HTTP, so `on.test.run` is noop'd everywhere (`noop_verify=True`)
    and the fix loop is driven by `on.review.local.run`'s own findings
    instead -- the same scripted-reviewer approach `test_findings_loop.py`
    uses."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "noop")
    repo = make_repo(tmp_path)
    # Own policy fixture — do not gate on the shipped attempts value.
    tdir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE), noop_verify=True)
    registry_path = tdir / "registry.yaml"
    registry = yaml.safe_load(registry_path.read_text())
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
    registry["hooks"]["on.review.local.run"] = {
        "kind": "subprocess",
        "command": [sys.executable, str(_FAKE_REVIEWER)],
    }
    registry_path.write_text(yaml.safe_dump(registry))
    # attempts=1, not 2: an identical fingerprint recurring at round 1
    # correctly trips the stuck-detector before a 2-attempt cap would ever be
    # reached. attempts=1 breaches the cap on the second bump, strictly
    # before the stuck-check runs, so this test still exercises the
    # cap-breach path specifically.
    (tdir / "policy.yaml").write_text(
        "loops:\n  verify_fix_loop: { attempts: 1, wall_clock_s: 3600 }\n"
        "default: { attempts: 1, wall_clock_s: 3600 }\n"
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

        assert len([e for e in events if e["type"] == "fix_cycle_started"]) == 1

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
        evts = _poll_node_started(client, wid, "plan")
        assert any(e["type"] == "node_started" and e["payload"]["node_id"] == "plan" for e in evts)


def test_approve_accepts_a_custom_gate_in_this_items_chain(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    templates_dir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    path = templates_dir / "default.yaml"
    chain = yaml.safe_load(path.read_text())
    chain["nodes"][0]["gate_after"] = "release/ready#1"
    path.write_text(yaml.safe_dump(chain, sort_keys=False))

    with _client(tmp_path, monkeypatch, templates_dir=templates_dir) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "release/ready#1")
        response = client.post(f"/api/work-items/{wid}/gates/release%2Fready%231/approve")
        assert response.status_code == 200, response.text
        assert _poll_node_started(client, wid, "plan")


def test_reject_accepts_an_encoded_slash_containing_custom_gate(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    templates_dir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    path = templates_dir / "default.yaml"
    chain = yaml.safe_load(path.read_text())
    chain["nodes"][0]["gate_after"] = "release/ready#1"
    path.write_text(yaml.safe_dump(chain, sort_keys=False))

    with _client(tmp_path, monkeypatch, templates_dir=templates_dir) as client:
        wid = _post_default(client, repo)
        _await_gate(client, wid, "release/ready#1")
        response = client.post(
            f"/api/work-items/{wid}/gates/release%2Fready%231/reject", json={"note": "redo"}
        )

    assert response.status_code == 200, response.text


def test_approve_rejects_gate_absent_from_this_items_chain(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        response = client.post(f"/api/work-items/{wid}/gates/release_ready/approve")

    assert response.status_code == 404


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
    "delete on_failure", or a node-level repair hook silently stops firing on
    every chain review that says the chain is fine as-is.

    The shipped `default` chain no longer carries a node-level `on_failure`
    itself (Kraft-uhev1 phase 2 moved the CI repair onto its binding), so
    this test gives `mr_checks` one directly -- an install whose chain
    predates that move still has one, and the splice must still preserve it.
    """
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    templates_dir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    default_path = templates_dir / "default.yaml"
    default = yaml.safe_load(default_path.read_text())
    for node in default["nodes"]:
        if node["id"] == "mr_checks":
            node["on_failure"] = ["on.mr_checks.repair"]
    default_path.write_text(yaml.safe_dump(default, sort_keys=False))
    with _client(tmp_path, monkeypatch, templates_dir=templates_dir) as client:
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


def test_a_spliced_schema_only_node_gets_its_steps_derived(tmp_path, monkeypatch):
    """The reviewer's schema teaches `tasks`; `measure_node` reads `steps`.
    `materialize` is not the only producer of chain_definition nodes, so
    without the normalizer on the splice path a spliced node arrives with no
    `steps` key at all."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    templates_dir = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    with _client(tmp_path, monkeypatch, templates_dir=templates_dir) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
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
        assert _approve_gate(client, wid, "chain_finalized").status_code == 200

        after = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        for node in after:
            assert node.get("steps"), f"{node['id']} spliced without steps"
            assert node["tasks"] == [t for g in node["steps"] for t in g], node["id"]


def test_chain_review_cannot_set_auto_escalate_directly(tmp_path, monkeypatch):
    """Kraft-df4tc: auto_escalate/auto_escalate_stuck/auto_escalate_delay_s are
    never the reviewer's to set -- only the human PATCH route's. A node dict
    that sets one directly must have it stripped before the splice, not
    spliced straight into chain_definition (where gates._auto_review_due and
    store.effective_auto_escalate_stuck would read it back)."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [
            {**n, "auto_escalate": True, "auto_escalate_stuck": True, "auto_escalate_delay_s": 999}
            if n["id"] == "human_review"
            else n
            for n in tail
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 200, r.text
        after = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        human_review_after = next(n for n in after if n["id"] == "human_review")
        human_review_before = next(n for n in before if n["id"] == "human_review")
        assert human_review_after["auto_escalate"] == human_review_before["auto_escalate"]
        assert (
            human_review_after["auto_escalate_stuck"] == human_review_before["auto_escalate_stuck"]
        )
        assert (
            human_review_after["auto_escalate_delay_s"]
            == human_review_before["auto_escalate_delay_s"]
        )


def test_chain_review_may_set_reject_to_directly(tmp_path, monkeypatch):
    """Kraft-df4tc point 1: a reviewer's revised node may name reject_to
    itself -- not just carry it forward -- and it survives the splice."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [{**n, "reject_to": "plan"} if n["id"] == "human_review" else n for n in tail]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 200, r.text
        after = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        assert next(n for n in after if n["id"] == "human_review")["reject_to"] == "plan"


def test_chain_review_forward_reject_to_rejects_the_whole_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [{**n, "reject_to": "merge"} if n["id"] == "verify" else n for n in tail]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 422
        after = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        assert after == before  # nothing spliced


def test_chain_review_proposed_node_overrides_applies_atomically_with_the_splice(
    tmp_path, monkeypatch
):
    """Kraft-df4tc point 2: a reviewer's proposed_node_overrides lands in
    node_overrides in the same approval as the splice, and reaches the next
    dispatch for that node."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [
            {**n, "proposed_node_overrides": {"effort": "high"}} if n["id"] == "verify" else n
            for n in tail
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 200, r.text
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["node_overrides"]["verify"] == {"effort": "high"}
        # the node itself does not carry the key -- it's a separate override layer
        after = item["chain_definition"]["nodes"]
        assert "proposed_node_overrides" not in next(n for n in after if n["id"] == "verify")


def test_chain_review_bad_proposed_node_overrides_rejects_the_whole_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [
            {**n, "proposed_node_overrides": {"effort": "turbo"}} if n["id"] == "verify" else n
            for n in tail
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 422
        item = client.get(f"/api/work-items/{wid}").json()
        assert item.get("node_overrides", {}) == {}
        assert item["chain_definition"]["nodes"] == before  # nothing spliced either


def test_chain_review_proposed_node_overrides_rejects_unwhitelisted_fields(tmp_path, monkeypatch):
    """Kraft-df4tc security finding: a reviewer may only propose model/
    escalate_model/effort -- anything else (auto_escalate, attempts, ...) is
    the human-facing PATCH route's territory and must 422 here too, not land
    in node_overrides where effective_chain would fold it onto the node."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        revised = [
            {**n, "proposed_node_overrides": {"auto_escalate": True, "attempts": 9999}}
            if n["id"] == "human_review"
            else n
            for n in tail
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 422
        item = client.get(f"/api/work-items/{wid}").json()
        assert item.get("node_overrides", {}) == {}
        assert item["chain_definition"]["nodes"] == before


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
            # A person only ever rejects; `fixed` is a gate-reviewer verdict and
            # has no door through this endpoint (Kraft-s7c04.16).
            "verdict": "reject",
        }

        # the spec node runs again and asks for its gate a second time
        _poll_events(client, wid, "gate_requested", count=2)
        item = client.get(f"/api/work-items/{wid}").json()
        assert item["pending_gate"] == "spec_approval"
        assert client.post(f"/api/work-items/{wid}/gates/spec_approval/approve").status_code == 200


def test_gate_reject_is_bounded_by_its_reject_loop(tmp_path, monkeypatch):
    """Rejections are capped like a fix loop; the breach stops the item.

    `auto_escalate_stuck` is explicitly disarmed here: this test is about
    the cap, not about the escalation dispatch the breach also now triggers
    (Kraft-h48r) -- see `test_gate_reject_loop_breach_auto_escalates_when_armed`
    for that."""
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\nauto_escalate_stuck: false\n"
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


def test_gate_reject_loop_breach_auto_escalates_when_armed(tmp_path, monkeypatch):
    """Kraft-h48r: `reject_gate`'s cap-breach branch used to return straight
    past `auto_escalate_stuck` -- the agent-side `reject` verdict re-enters
    `walk.run_once`, whose own post-step call reaches it normally, but this
    HTTP route never goes through either `walk.run` or `resuming.resume`."""
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "again"})
        _poll_events(client, wid, "gate_requested", count=2)
        r = client.post(
            f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "still no"}
        )
        assert r.status_code == 200, r.text
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
        assert calls == [(wid, True)]


def test_gate_reject_loop_breach_does_not_escalate_when_disarmed(tmp_path, monkeypatch):
    """Regression guard for the branch above: `auto_escalate_stuck: false`
    must still no-op here exactly like it does on the walk-driven path."""
    calls = []

    async def fake_dispatch(database, run_dirs, *, work_item_id, message, launch, auto, evts=None):
        calls.append((work_item_id, auto))
        return "done"

    monkeypatch.setattr("kraft.executor.gates.escalate.dispatch", fake_dispatch)
    repo = make_repo(tmp_path)
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    (templates / "policy.yaml").write_text(
        "loops: {}\ndefault: {attempts: 1, wall_clock_s: 3600}\nauto_escalate_stuck: false\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=templates) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        client.post(f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "again"})
        _poll_events(client, wid, "gate_requested", count=2)
        r = client.post(
            f"/api/work-items/{wid}/gates/spec_approval/reject", json={"note": "still no"}
        )
        assert r.status_code == 200, r.text
        assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
        assert calls == []


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


def test_approve_gate_stops_a_live_auto_review_session_first(tmp_path, monkeypatch):
    """A gate's own in-flight auto_escalate review must not survive into the
    next node's worktree once a human approves: its session is paused and
    SIGTERM'd before the approval's own executor.run spawns."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _create_running_session(wid, "r1", "spec", pid=4242)

        r = client.post(f"/api/work-items/{wid}/gates/spec_approval/approve")

        assert r.status_code == 200, r.text
        assert terminated == [4242]
        types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
        assert types.index("pause_requested") < types.index("gate_approved")


def test_reject_gate_stops_a_live_auto_review_session_first(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _create_running_session(wid, "r1", "spec", pid=4242)

        r = client.post(
            f"/api/work-items/{wid}/gates/spec_approval/reject",
            json={"note": "not yet"},
        )

        assert r.status_code == 200, r.text
        assert terminated == [4242]
        types = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
        assert "pause_requested" in types


def test_reject_with_a_bad_node_leaves_a_live_auto_review_running(tmp_path, monkeypatch):
    """The refusing check runs before the kill (code review finding): a human
    who gets a 400 must find the item exactly as it was -- gate still
    pending, review session still live -- the same ordering retry applies to
    its own refusals."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    terminated = []
    monkeypatch.setattr("kraft.api.routes.lifecycle._terminate", lambda pid: terminated.append(pid))
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        _poll_events(client, wid, "gate_requested")
        _create_running_session(wid, "r1", "spec", pid=4242)

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


def test_chain_review_model_override_reaches_the_next_dispatch_of_that_node(tmp_path, monkeypatch):
    """End to end: a reviewer's proposed_node_overrides on a tail node is
    spliced in atomically with the chain revision, and the very next
    dispatch of that node launches with the overridden model -- not just
    stored, actually read at dispatch time (Task 3's merge). The reported
    "model" on worker_sessions comes from the agent's own usage payload
    (fixtures/fake-claude.sh always reports "fake-agent"), so this checks
    the launch argv via KRAFT_FAKE_CLAUDE_ARGV_LOG instead."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    argv_log = tmp_path / "argv.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_ARGV_LOG", str(argv_log))
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        # `implementation` is the only tail node bound to a real agent hook
        # under this test fixture's registry (`on.implementation.start`) --
        # `verify`'s tasks are a subprocess and a noop, neither of which ever
        # reaches `resolve_invocation`, so the override would never surface
        # in an argv if targeted there.
        revised = [
            {**n, "proposed_node_overrides": {"model": "picked-model"}}
            if n["id"] == "implementation"
            else n
            for n in tail
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        assert _approve_gate(client, wid, "chain_finalized").status_code == 200
        _await_gate(client, wid, "human_review_approval")

        records = [r.splitlines() for r in argv_log.read_text().split("\x00\n") if r.strip()]
        # The override only ever applies to implementation's own dispatch --
        # no other node in this chain carries a node_overrides entry -- so
        # any recorded argv carrying "picked-model" proves the override
        # reached that launch, not just node_overrides storage.
        assert any("picked-model" in r for r in records), records


def test_chain_review_carried_forward_bounce_target_must_still_exist(tmp_path, monkeypatch):
    """Kraft-df4tc: `validate_nodes` runs on the reviewer's own values, before
    `carry_forward_node_fields`. A reviewer that renames `verify` leaves
    `pre_mr_rebase`'s carried-forward `rebase_bounce_to: verify` dangling --
    walk.py's bare `next(...)` would raise StopIteration mid-walk. The merged
    tail has to be re-validated, and the whole approval rejected."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = _post_default(client, repo)
        for gate in ("spec_approval", "plan_approval"):
            _await_gate(client, wid, gate)
            assert _approve_gate(client, wid, gate).status_code == 200
        _await_gate(client, wid, "chain_finalized")

        before = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        tail = [n for n in before if n["id"] not in ("spec", "plan", "chain_review")]
        # rename `verify`; every other node comes back untouched, so
        # `pre_mr_rebase` says nothing about rebase_bounce_to itself
        revised = [
            {k: v for k, v in n.items() if k != "rebase_bounce_to"}
            | ({"id": "verify_all"} if n["id"] == "verify" else {})
            for n in tail
        ]
        _write_chain_review(
            client,
            wid,
            {"status": "ready_for_approval", "revised_chain_nodes": revised, "rationale": "t"},
        )
        r = _approve_gate(client, wid, "chain_finalized")
        assert r.status_code == 422, r.text
        assert "rebase_bounce_to" in r.json()["detail"]
        after = client.get(f"/api/work-items/{wid}").json()["chain_definition"]["nodes"]
        assert after == before  # nothing spliced


def test_a_worker_agent_s_gate_approval_is_not_recorded_as_a_person_s(tmp_path, monkeypatch):
    """Kraft-s7c04.43: the MCP approve tool lands on this route, which took the
    `by="human"` default from store/gates.py. analytics.py branches on
    `by == "agent"` to decide whether a run boundary was real human oversight,
    so this path silently inflated that reading in the safe-looking direction."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_an_assistant_session_through_mcp_is_neither_a_worker_nor_a_person(tmp_path, monkeypatch):
    """Every one of the 28 real MCP gate calls on this machine came from a
    non-worker assistant session, which carries no KRAFT_SESSION_ID. Recording
    those as `human` is the defect; recording them as `agent` would claim a
    Kraft worker approved its own artifact, which is a different thing."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_a_gate_rejection_records_its_caller_the_same_way(tmp_path, monkeypatch):
    """The bead asked for reject to be checked while in there. It has the same
    gap: apply_rejection takes `by` and the route never passed it."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_a_worker_s_own_session_id_outranks_how_it_connected(tmp_path, monkeypatch):
    """A Kraft worker reaching the API through MCP carries both headers. What it
    is outranks how it connected."""
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
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


def test_carry_forward_keeps_steps_for_unchanged_tasks():
    from kraft.templates import carry_forward_node_fields, with_steps

    old = [{"id": "impl", "steps": [["a"], ["b"]], "tasks": ["a", "b"]}]
    same = with_steps(carry_forward_node_fields(old, [{"id": "impl", "tasks": ["a", "b"]}])[0])
    assert same["steps"] == [["a"], ["b"]]
    changed = with_steps(carry_forward_node_fields(old, [{"id": "impl", "tasks": ["b", "a"]}])[0])
    assert changed["steps"] == [["b", "a"]]
