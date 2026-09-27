"""The review flow's API: pins, compare, threads, reviews, replies."""

from __future__ import annotations

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
