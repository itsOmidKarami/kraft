"""PATCH and DELETE /comments/{cid}: a reviewer revising or withdrawing a
reply before the review it belongs to is submitted. Threads, reviews and the
agents' reply door are in test_review.py."""

from __future__ import annotations

import pytest
from support.api import _held_at

_AGENT = {"x-kraft-session-id": "s1"}


@pytest.fixture
def running(client, repo, monkeypatch):
    """An item held at `spec`'s agent task, with no gate pending: its
    worktree exists, so a thread and a review can be filed against it."""
    return _held_at(client, repo, "spec", monkeypatch)


def _thread(client, wid) -> str:
    r = client.post(
        f"/api/work-items/{wid}/threads",
        json={
            "body": "use a set",
            "file_path": "a.py",
            "side": "new",
            "start_line": 3,
            "end_line": 4,
            "label": "question",
            "anchor_sha": "0" * 40,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _reply(client, tid, body="first") -> str:
    r = client.post(f"/api/threads/{tid}/comments", json={"body": body})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _comments(client, wid, tid) -> dict[str, dict]:
    [t] = [t for t in client.get(f"/api/work-items/{wid}/threads").json() if t["id"] == tid]
    return {c["id"]: c for c in t["comments"]}


def test_a_draft_reply_can_be_edited_then_deleted(client, running):
    tid = _thread(client, running)
    cid = _reply(client, tid)
    suggestion = {"start_line": 3, "end_line": 4, "replacement": "frozenset()"}

    r = client.patch(f"/api/comments/{cid}", json={"body": "second", "suggestion": suggestion})

    assert r.status_code == 200, r.text
    assert (r.json()["body"], r.json()["suggestion"]) == ("second", suggestion)
    stored = _comments(client, running, tid)[cid]
    assert (stored["body"], stored["suggestion"], stored["draft"]) == ("second", suggestion, True)

    assert client.delete(f"/api/comments/{cid}").status_code == 204
    assert cid not in _comments(client, running, tid)


def test_a_suggestion_outside_the_threads_lines_is_refused_and_changes_nothing(client, running):
    tid = _thread(client, running)
    cid = _reply(client, tid)
    outside = {"start_line": 1, "end_line": 3, "replacement": "x"}

    r = client.patch(f"/api/comments/{cid}", json={"body": "second", "suggestion": outside})

    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "the suggestion's lines must sit inside 3-4"
    assert _comments(client, running, tid)[cid]["body"] == "first"


def test_worker_agents_cannot_edit_or_delete_a_comment(client, running):
    """Agents speak only through `POST /threads/{tid}/replies`."""
    tid = _thread(client, running)
    cid = _reply(client, tid)

    patched = client.patch(f"/api/comments/{cid}", json={"body": "x"}, headers=_AGENT)
    deleted = client.delete(f"/api/comments/{cid}", headers=_AGENT)

    for r in (patched, deleted):
        assert r.status_code == 403, r.text
        assert "POST /threads/{id}/replies" in r.json()["detail"]
    assert _comments(client, running, tid)[cid]["body"] == "first"


def test_an_unknown_comment_is_404(client):
    for r in (
        client.patch("/api/comments/nope", json={"body": "x"}),
        client.delete("/api/comments/nope"),
    ):
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "unknown comment 'nope'"


def test_a_submitted_reply_or_an_agents_one_cannot_change(client, running):
    """Once its review is submitted a reply is on the record; an agent's reply
    was never the reviewer's to change."""
    tid = _thread(client, running)
    mine = _reply(client, tid)
    r = client.post(f"/api/work-items/{running}/review", json={"outcome": "comment"})
    assert r.status_code == 200, r.text
    spec = next(
        s["id"]
        for s in client.get(f"/api/work-items/{running}").json()["worker_sessions"]
        if s["node_id"] == "spec"
    )
    agents = client.post(
        f"/api/threads/{tid}/replies",
        json={"body": "done"},
        headers={"x-kraft-session-id": spec},
    )
    assert agents.status_code == 201, agents.text
    before = _comments(client, running, tid)

    for cid in (mine, agents.json()["id"]):
        for r in (
            client.patch(f"/api/comments/{cid}", json={"body": "x"}),
            client.delete(f"/api/comments/{cid}"),
        ):
            assert r.status_code == 409, r.text
            assert r.json()["detail"] == "only an unsubmitted comment of yours can change"
    assert _comments(client, running, tid) == before
