"""Viewed marks on the compare: PUT/DELETE /viewed and `viewed` per file."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support.api import _await_gate, _review_early, _started

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
