"""The review flow's API: pins, compare, threads, reviews, replies."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from support.api import _await_gate, _review_early

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
