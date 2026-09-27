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
def test_archive_drops_the_items_refs(client, gated, repo):
    """Abandon has its own test; archive reclaims through the same
    `_remove_worktree` and must drop the attempt refs too. Abandon first (only an
    ended item can be archived), plant a ref after it, then archive."""
    assert client.post(f"/api/work-items/{gated}/abandon").status_code == 200
    subprocess.run(
        ["git", "update-ref", f"refs/kraft/{gated}/manual/1", "HEAD"], cwd=repo, check=True
    )
    assert client.post(f"/api/work-items/{gated}/archive").status_code == 200
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
    assert (
        client.post(
            f"/api/work-items/{gated}/review",
            json={"outcome": "comment"},
            headers=agent,
        ).status_code
        == 403
    )


def test_threads_can_be_filed_without_a_pending_gate(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = client.post("/api/work-items", json={"title": "t", "repo": str(repo)}).json()["id"]
    r = _new_thread(client, wid, label="question", anchor_sha="0" * 40)
    assert r.status_code == 201, r.text
    assert r.json()["gate"] is None


def test_threads_are_refused_on_a_finished_item(client, repo, monkeypatch):
    from support.api import _set_status

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = client.post("/api/work-items", json={"title": "t", "repo": str(repo)}).json()["id"]
    _set_status(wid, "completed")
    assert _new_thread(client, wid, anchor_sha="0" * 40).status_code == 409


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
def test_approve_that_the_gate_refuses_writes_no_review(client, gated):
    """The file's own invariant -- 'a refused review records nothing' -- must
    hold even when the refusal comes from `approve_gate` itself (here: the
    final-review gate's missing artifact), not just from `submit_review`'s
    own must-fix/reject-target checks."""
    tid = _new_thread(client, gated, label="question").json()["id"]
    run_dir = Path(client.app.state.run_dirs.base)
    brief = run_dir / "worktrees" / gated / ".engineering" / "review_briefs" / f"{gated}.md"
    brief.unlink()

    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "approve"}
    )
    assert r.status_code == 422, r.text
    body = client.get(f"/api/work-items/{gated}").json()
    assert body["pending_gate"] == "chain_review"
    assert body["last_review_sha"] is None
    # not stamped with a review it never got -- still a draft, still editable
    assert client.patch(f"/api/threads/{tid}", json={"body": "x"}).status_code == 200


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


def _session_of(client, wid, node_id):
    """A worker_sessions id for `node_id` on `wid`, as a real agent would carry."""
    body = client.get(f"/api/work-items/{wid}").json()
    return next(s["id"] for s in body["worker_sessions"] if s["node_id"] == node_id)


@_REVIEW
def test_agent_reply_takes_its_author_from_the_session(client, gated):
    tid = _new_thread(client, gated).json()["id"]
    client.post(f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "comment"})
    sid = _session_of(client, gated, "implementation")
    r = client.post(
        f"/api/threads/{tid}/replies",
        json={"body": "done", "claim": "fixed"},
        headers={"x-kraft-session-id": sid},
    )
    assert r.status_code == 201, r.text
    assert r.json()["author"] == "implementation" and r.json()["attempt"] == 1
    [t] = client.get(f"/api/work-items/{gated}/threads").json()
    assert t["state"] == "claimed"


@_REVIEW
def test_reply_door_refuses_humans_and_other_items(client, gated, repo):
    tid = _new_thread(client, gated).json()["id"]
    assert client.post(f"/api/threads/{tid}/replies", json={"body": "x"}).status_code == 403
    assert (
        client.post(
            f"/api/threads/{tid}/replies",
            json={"body": "x"},
            headers={"x-kraft-session-id": "no-such"},
        ).status_code
        == 403
    )
    # a session of another item
    other = _post(client, repo)
    _await_gate(client, other, "chain_review")
    sid = _session_of(client, other, "implementation")
    assert (
        client.post(
            f"/api/threads/{tid}/replies", json={"body": "x"}, headers={"x-kraft-session-id": sid}
        ).status_code
        == 403
    )


@_REVIEW
def test_an_agent_cannot_reply_to_a_draft(client, gated):
    tid = _new_thread(client, gated).json()["id"]
    sid = _session_of(client, gated, "implementation")
    r = client.post(
        f"/api/threads/{tid}/replies", json={"body": "x"}, headers={"x-kraft-session-id": sid}
    )
    assert r.status_code == 404  # a draft is not visible to agents


@_REVIEW
def test_a_comment_review_gets_answers_and_leaves_the_gate_pending(client, gated):
    _new_thread(client, gated, label="question", body="why a list?")
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "comment"}
    )
    assert r.status_code == 200 and r.json()["reply_agent"] is True
    body = client.get(f"/api/work-items/{gated}").json()
    assert body["pending_gate"] == "chain_review" and body["status"] == "needs_human"
    sessions = [s for s in body["worker_sessions"] if s["hook_point"] == "chain_review.reply"]
    assert sessions and sessions[0]["node_id"] == "chain_review"


@_REVIEW
def test_drafts_are_submitted_before_the_gate_call_runs(client, gated, monkeypatch):
    """Kraft-dl5fl 1: the re-run agent can reply the moment the walk starts, so
    the threads it is told about must already be submitted by then."""
    from kraft import events, store
    from kraft.api.routes import review as review_routes

    tid = _new_thread(client, gated, label="question").json()["id"]
    db = client.app.state.db
    seen = {}

    async def fake_reject(wid, gate, body, request):
        seen["draft"] = db.read(lambda c: store.is_draft_thread(c, tid))
        seen["events"] = [e["type"] for e in db.read(lambda c: events.read_after(c, 0, wid))]
        return {"id": wid}

    monkeypatch.setattr(review_routes.gate_routes, "reject_gate", fake_reject)
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review",
        json={"outcome": "request_changes"},
    )
    assert r.status_code == 200, r.text
    assert seen["draft"] is False
    # published only once the gate call succeeded
    assert "review_submitted" not in seen["events"]
    types = [e["type"] for e in client.get(f"/api/work-items/{gated}/events").json()]
    assert "review_submitted" in types


@_REVIEW
def test_an_unreadable_head_refuses_the_review_and_records_nothing(client, gated, monkeypatch):
    """Kraft-dl5fl 3: `reviews.head_sha` is never '' -- compare would 500 on it."""
    from kraft.api.routes import review as review_routes

    tid = _new_thread(client, gated, label="question").json()["id"]
    monkeypatch.setattr(review_routes.config_mod, "git_read", lambda *a, **k: None)
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "comment"}
    )
    assert r.status_code == 409, r.text
    assert client.get(f"/api/work-items/{gated}").json()["last_review_sha"] is None
    assert client.patch(f"/api/threads/{tid}", json={"body": "x"}).status_code == 200


def test_a_gateless_comment_is_recorded_with_no_gate(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")  # stays running at implementation
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "5")
    wid = client.post("/api/work-items", json={"title": "KRAFT_SLOW t", "repo": str(repo)}).json()[
        "id"
    ]
    _poll_node_started(client, wid, "spec")  # the worktree exists once its first node starts
    _new_thread(client, wid, label="question", anchor_sha="0" * 40)
    r = client.post(f"/api/work-items/{wid}/review", json={"outcome": "comment"})
    assert r.status_code == 200, r.text
    assert r.json()["gate"] is None
    events = [e["type"] for e in client.get(f"/api/work-items/{wid}/events").json()]
    assert "review_submitted" in events


def test_approve_needs_a_pending_gate(client, repo, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "5")
    wid = client.post("/api/work-items", json={"title": "KRAFT_SLOW t", "repo": str(repo)}).json()[
        "id"
    ]
    _poll_node_started(client, wid, "spec")  # the worktree exists once its first node starts
    r = client.post(f"/api/work-items/{wid}/review", json={"outcome": "approve"})
    assert r.status_code == 409
    assert "no gate is pending" in r.text


@_REVIEW
def test_the_item_review_route_acts_on_the_pending_gate(client, gated):
    _new_thread(client, gated, label="question")
    r = client.post(f"/api/work-items/{gated}/review", json={"outcome": "comment"})
    assert r.status_code == 200 and r.json()["reply_agent"] is True


def test_a_gateless_comment_is_refused_when_nothing_ahead_can_read_it(client, repo, monkeypatch):
    """`gateless-comment-nothing-downstream-reads-is-refused`, at the API: an
    item forced onto its chain's forge-only tail (past the last gate and the
    last agent task) has nothing ahead that could ever read a thread.

    `KRAFT_FAKE_CLAUDE=slow` holds the walk at `spec`'s agent task -- long
    enough that the worktree exists (so `git rev-parse HEAD` in the route
    resolves) but no gate is ever requested (so `board._pending_gate` reads
    None on its own, with nothing to race)."""
    from support.api import _force_node, _poll_node_started, _post_default

    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "5")
    wid = _post_default(client, repo)
    _poll_node_started(client, wid, "spec")
    _force_node(wid, "merge", "needs_human")

    r = client.post(f"/api/work-items/{wid}/review", json={"outcome": "comment"})

    assert r.status_code == 409, r.text
    assert "nothing ahead in this chain will read these threads" in r.text
    assert client.get(f"/api/work-items/{wid}").json()["last_review_sha"] is None
