"""The review flow's API: pins, compare, threads, reviews, replies."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from support.api import _await_gate, _poll_node_started, _review_early

_REVIEW = pytest.mark.api_client(edit_templates=_review_early)


def _post(client, repo):
    return client.post(
        "/api/work-items",
        json={
            "title": "make the failing test pass",
            "repo": str(repo),
            "chain_template": "review-early",
        },
    ).json()["id"]


def _worktree(client, wid) -> Path:
    return Path(client.get(f"/api/work-items/{wid}").json()["worktree_path"])


@pytest.fixture
def gated(client, repo, monkeypatch):
    """An item parked at its `chain_review` gate, attempt 1."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _post(client, repo)
    _await_gate(client, wid, "chain_review")
    return wid


@_REVIEW
def test_reaching_a_gate_pins_the_node_and_the_gate(client, gated):
    from kraft import store

    db = client.app.state.db
    rows = client.portal.call(db.read, lambda c: store.node_run_rows(c, gated))
    by_node = {r["node_id"]: r for r in rows}
    assert by_node["implementation"]["end_sha"] is not None
    gate = by_node["chain_review"]
    assert gate["attempt"] == 1 and gate["start_sha"] == gate["end_sha"]
    ref = subprocess.run(
        ["git", "rev-parse", f"refs/kraft/{gated}/chain_review/1"],
        cwd=_worktree(client, gated),
        capture_output=True,
        text=True,
    )
    assert ref.stdout.strip() == gate["end_sha"]


@_REVIEW
def test_abandon_drops_the_items_refs(client, gated, repo):
    assert client.post(f"/api/work-items/{gated}/abandon").status_code == 200
    refs = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", f"refs/kraft/{gated}/"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout
    assert refs.strip() == ""


@_REVIEW
def test_compare_base_to_latest_matches_the_whole_change(client, gated):
    r = client.get(f"/api/work-items/{gated}/compare", params={"from": "base", "to": "latest"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["to"]["sha"] is None and body["rebased"] is False
    assert all("touched_by" in f for f in body["files"])


@_REVIEW
def test_compare_rejects_bad_targets(client, gated):
    for params, code in [
        ({"from": "latest", "to": "base"}, 400),
        ({"from": "nope", "to": "latest"}, 400),
        ({"from": "attempt:9", "to": "latest"}, 404),
        ({"from": "last_review", "to": "latest"}, 404),
    ]:
        assert client.get(f"/api/work-items/{gated}/compare", params=params).status_code == code


@_REVIEW
def test_compare_with_a_vanished_sha_is_an_error_not_empty(client, gated):
    """Review Focus 5: a gc'd attempt must not read as 'no changes'."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "UPDATE node_runs SET end_sha = ? WHERE work_item_id = ? AND node_id = ?",
            ("0" * 40, gated, "chain_review"),
        )
        conn.commit()
    finally:
        conn.close()
    r = client.get(f"/api/work-items/{gated}/compare", params={"from": "attempt:1", "to": "latest"})
    assert r.status_code == 500


@_REVIEW
def test_item_detail_carries_attempts_and_reject_default(client, gated):
    body = client.get(f"/api/work-items/{gated}").json()
    assert [a["n"] for a in body["attempts"]] == [1]
    assert body["reject_default"] == "implementation"
    assert body["last_review_sha"] is None


def _new_thread(client, wid, **kw):
    body = {
        "body": "use a set",
        "file_path": "a.py",
        "side": "new",
        "start_line": 3,
        "end_line": 4,
        "label": "must_fix",
    }
    body.update(kw)
    return client.post(f"/api/work-items/{wid}/threads", json=body)


@_REVIEW
def test_thread_lifecycle_draft_edit_then_locked_after_submit(client, gated):
    r = _new_thread(client, gated)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert client.patch(f"/api/threads/{tid}", json={"body": "use a frozenset"}).status_code == 200
    client.post(f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "comment"})
    assert client.patch(f"/api/threads/{tid}", json={"body": "x"}).status_code == 409
    assert client.delete(f"/api/threads/{tid}").status_code == 409
    assert client.post(f"/api/threads/{tid}/resolve").status_code == 200
    assert client.post(f"/api/threads/{tid}/reopen").status_code == 200


@_REVIEW
def test_bad_ranges_and_suggestions_are_refused_and_write_nothing(client, gated):
    """Review Focus 2."""
    cases = [
        {"start_line": 5, "end_line": 4},
        {"side": None},  # lines without a side
        {
            "start_line": None,
            "end_line": None,
            "side": None,
            "suggestion": {"start_line": 1, "end_line": 1, "replacement": "x"},
        },
        {"suggestion": {"start_line": 2, "end_line": 3, "replacement": "x"}},  # outside 3-4
        {"label": "blocker"},
    ]
    for kw in cases:
        assert _new_thread(client, gated, **kw).status_code == 422, kw
    assert client.get(f"/api/work-items/{gated}/threads").json() == []


@_REVIEW
def test_worker_agents_cannot_use_human_routes(client, gated):
    """Review Focus 3."""
    agent = {"x-kraft-session-id": "s1"}
    assert (
        client.post(
            f"/api/work-items/{gated}/threads", json={"body": "x"}, headers=agent
        ).status_code
        == 403
    )
    tid = _new_thread(client, gated).json()["id"]
    assert client.post(f"/api/threads/{tid}/resolve", headers=agent).status_code == 403
    assert (
        client.post(
            f"/api/work-items/{gated}/gates/chain_review/review",
            json={"outcome": "comment"},
            headers=agent,
        ).status_code
        == 403
    )


def test_threads_need_a_pending_gate(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = client.post("/api/work-items", json={"title": "t", "repo": str(repo)}).json()["id"]
    assert _new_thread(client, wid).status_code == 409


@_REVIEW
def test_approve_is_refused_while_a_must_fix_is_open_even_a_draft(client, gated):
    """Review Focus 4, at every human door."""
    tid = _new_thread(client, gated).json()["id"]  # a draft must_fix
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "approve"}
    )
    assert r.status_code == 409 and tid in r.text
    assert client.post(f"/api/work-items/{gated}/gates/chain_review/approve").status_code == 409
    client.post(f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "comment"})
    client.post(f"/api/threads/{tid}/resolve")
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "approve"}
    )
    assert r.status_code == 200, r.text


@_REVIEW
def test_request_changes_sends_the_threads_as_the_note(client, gated, tmp_path, monkeypatch):
    prompts = tmp_path / "prompts.log"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompts))
    tid = _new_thread(client, gated, body="evict LRU").json()["id"]
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review",
        json={"outcome": "request_changes", "summary": "Bound the cache."},
    )
    assert r.status_code == 200, r.text
    _poll_node_started(client, gated, "implementation")
    _await_gate(client, gated, "chain_review")
    log = prompts.read_text()
    assert "Bound the cache." in log and f"[{tid}]" in log and "evict LRU" in log
    body = client.get(f"/api/work-items/{gated}").json()
    assert body["last_review_sha"] is not None


@_REVIEW
def test_request_changes_to_a_bad_node_writes_no_review(client, gated):
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review",
        json={"outcome": "request_changes", "node": "nowhere"},
    )
    assert r.status_code == 400
    assert client.get(f"/api/work-items/{gated}").json()["last_review_sha"] is None


@_REVIEW
def test_review_on_a_gate_that_is_not_pending_is_409(client, gated):
    r = client.post(f"/api/work-items/{gated}/gates/other/review", json={"outcome": "comment"})
    assert r.status_code in (404, 409)
