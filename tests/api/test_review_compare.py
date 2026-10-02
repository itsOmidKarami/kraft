"""What the compare and the item detail add for the review screen: ignore_whitespace,
viewed marks and the fix target."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from support.api import _await_gate, _force_node, _poll_node_started, _review_early, _started

_REVIEW = pytest.mark.api_client(edit_templates=_review_early)


def _worktree(client, wid) -> Path:
    return Path(client.get(f"/api/work-items/{wid}").json()["worktree_path"])


@pytest.fixture
def gated(client, repo, monkeypatch):
    """An item parked at its `chain_review` gate, attempt 1."""
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    wid = _started(
        client,
        {
            "title": "make the failing test pass",
            "repo": str(repo),
            "chain_template": "review-early",
        },
    )
    _await_gate(client, wid, "chain_review")
    return wid


def _tracked(wt) -> str:
    out = subprocess.run(["git", "ls-files"], cwd=wt, capture_output=True, text=True, check=True)
    return next(p for p in out.stdout.split() if not p.startswith("."))


def _viewed(client, wid, method, path, to):
    return client.request(method, f"/api/work-items/{wid}/viewed", params={"file": path, "to": to})


def _compare(client, wid, frm, to="latest"):
    body = client.get(f"/api/work-items/{wid}/compare", params={"from": frm, "to": to}).json()
    return {f["path"]: f for f in body["files"]}


@_REVIEW
def test_viewed_mark_follows_the_file_content_not_the_from_side(client, gated):
    wt = _worktree(client, gated)
    kept = _tracked(wt)
    (wt / kept).write_text("one\n")
    assert _compare(client, gated, "base")[kept]["viewed"] is False
    r = _viewed(client, gated, "PUT", kept, "latest")
    assert r.json() == {"file": kept, "to": "latest", "viewed": True}
    # an unchanged file keeps its mark whatever the compare starts from
    assert _compare(client, gated, "base")[kept]["viewed"] is True
    assert _compare(client, gated, "attempt:1")[kept]["viewed"] is True
    (wt / kept).write_text("two\n")
    assert _compare(client, gated, "base")[kept]["viewed"] is False
    assert _viewed(client, gated, "DELETE", kept, "latest").json()["viewed"] is False


@_REVIEW
def test_unmark_clears_every_mark_on_that_blob(client, gated):
    path = next(iter(_compare(client, gated, "base", "attempt:1")))
    assert _viewed(client, gated, "PUT", path, "attempt:1").status_code == 200
    assert _compare(client, gated, "base", "attempt:1")[path]["viewed"] is True
    # the working tree still holds the same blob: one DELETE at `latest` clears both keys
    assert _viewed(client, gated, "PUT", path, "latest").status_code == 200
    _viewed(client, gated, "DELETE", path, "latest")
    assert _compare(client, gated, "base", "attempt:1")[path]["viewed"] is False
    assert _compare(client, gated, "base")[path]["viewed"] is False


@_REVIEW
def test_viewed_mark_on_a_deleted_file_holds(client, gated):
    wt = _worktree(client, gated)
    path = _tracked(wt)
    (wt / path).unlink()
    _viewed(client, gated, "PUT", path, "latest")
    assert _compare(client, gated, "base")[path]["viewed"] is True


@_REVIEW
def test_viewed_rejects_bad_requests(client, gated):
    assert _viewed(client, gated, "PUT", "a.txt", "attempt:9").status_code == 404
    assert _viewed(client, gated, "PUT", "../x", "latest").status_code == 400
    assert _viewed(client, gated, "PUT", "", "latest").status_code == 400
    assert _viewed(client, "nope", "PUT", "a.txt", "latest").status_code == 404


@_REVIEW
def test_compare_without_marks_reads_no_blobs(client, gated, monkeypatch):
    from kraft import config

    real, seen = config.git_read, []

    def spy(cwd, *args, **kw):
        seen.append(args)
        return real(cwd, *args, **kw)

    monkeypatch.setattr(config, "git_read", spy)
    _compare(client, gated, "base")
    blob_reads = [
        a for a in seen if a[0] == "hash-object" or (a[0] == "rev-parse" and ":" in a[-1])
    ]
    assert blob_reads == []


@_REVIEW
def test_compare_ignore_whitespace_agrees_across_files_counts_and_diff(client, gated):
    wt = _worktree(client, gated)
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=wt, capture_output=True, text=True, check=True
    ).stdout.split()
    tracked = next(p for p in tracked if not p.startswith("."))
    text = (wt / tracked).read_text()
    (wt / tracked).write_text("    " + text.replace("\n", "\n    "))
    url = f"/api/work-items/{gated}/compare"
    params = {"from": "attempt:1", "to": "latest"}
    plain = client.get(url, params=params).json()
    quiet = client.get(url, params={**params, "ignore_whitespace": 1}).json()
    assert plain["ignore_whitespace"] is False and quiet["ignore_whitespace"] is True
    # the fake fix already edited the file for real; the indent on top shows only without -w
    count = lambda body: sum(  # noqa: E731
        f["insertions"] + f["deletions"] for f in body["files"] if f["path"] == tracked
    )
    assert count(quiet) < count(plain)
    assert quiet["diff"].count("\n+") < plain["diff"].count("\n+")


@_REVIEW
def test_item_detail_carries_the_fix_target_of_the_pending_gate(client, gated):
    body = client.get(f"/api/work-items/{gated}").json()
    ft = body["fix_target"]
    assert ft["node"] == body["reject_default"] == "implementation"
    assert (ft["gate"], ft["then"]) == ("chain_review", ["work_item_summary"])
    assert ft["round"]["n"] == 1 and ft["round"]["max"] >= 1


@_REVIEW
def test_fix_target_round_counts_rejections_against_the_stored_cap(client, gated):
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "request_changes"}
    )
    assert r.status_code == 200, r.text
    _poll_node_started(client, gated, "implementation")
    _await_gate(client, gated, "chain_review")
    assert client.get(f"/api/work-items/{gated}").json()["fix_target"]["round"]["n"] == 2
    # the cap the counter snapshotted wins over whatever the policy says now
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute("UPDATE retry_counters SET cap_attempts = 7 WHERE work_item_id = ?", (gated,))
        conn.commit()
    finally:
        conn.close()
    assert client.get(f"/api/work-items/{gated}").json()["fix_target"]["round"]["max"] == 7


@_REVIEW
def test_fix_target_route_follows_a_chosen_node_and_refuses_bad_ones(client, gated):
    url = f"/api/work-items/{gated}/fix-target"
    ft = client.get(url, params={"node": "work_item_summary"}).json()
    assert (ft["node"], ft["then"], ft["gate"]) == ("work_item_summary", [], "chain_review")
    assert client.get(url).json()["node"] == "implementation"
    assert client.get(url, params={"node": "chain_review"}).status_code == 400
    assert client.get(url, params={"node": "nowhere"}).status_code == 400
    assert client.get(url, params={"gate": "other"}).status_code == 409


@_REVIEW
def test_fix_target_route_ends_with_the_item(client, gated):
    assert client.post(f"/api/work-items/{gated}/abandon").status_code == 200
    assert client.get(f"/api/work-items/{gated}/fix-target").status_code == 409


def _paused_default(client, repo) -> str:
    """A not-yet-started default-chain item: no gate pending, no worktree, no
    base commit."""
    r = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_fix_target_without_a_gate_needs_a_current_node(client, repo):
    wid = _paused_default(client, repo)
    r = client.get(f"/api/work-items/{wid}/fix-target")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "this work item has no current node"


def test_fix_target_without_a_gate_refuses_a_node_after_the_current_one(client, repo):
    wid = _paused_default(client, repo)
    _force_node(wid, "implementation", "paused")
    url = f"/api/work-items/{wid}/fix-target"

    assert client.get(url, params={"node": "plan"}).json()["node"] == "plan"
    for node in ("work_brief", "nowhere"):
        r = client.get(url, params={"node": node})
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == (
            f"cannot request changes at {node!r}: not at or before the current node"
        )


def test_fix_target_without_a_gate_needs_a_working_agent_at_or_before_it(client, repo):
    """`spec` writes through a skill, so nothing at or before it is a node a
    gateless `request_changes` could re-run."""
    wid = _paused_default(client, repo)
    _force_node(wid, "spec", "paused")
    r = client.get(f"/api/work-items/{wid}/fix-target")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "no node at or before the current one runs a working agent"


def test_compare_needs_a_base_commit(client, repo):
    wid = _paused_default(client, repo)
    r = client.get(f"/api/work-items/{wid}/compare", params={"from": "base", "to": "latest"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "this work item has no base commit to compare against"
