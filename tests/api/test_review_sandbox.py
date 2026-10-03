"""A review of an item whose sandbox has no route back to Kraft: what the
review route promises about its reply agent."""

# ruff: noqa: F811 -- `gated` is an imported fixture, taken by name.
from __future__ import annotations

# Shared, not copied: the item parked at its gate, and a thread on it.
from api.test_review import _REVIEW, _new_thread, gated  # noqa: F401


@_REVIEW
def test_a_comment_review_says_no_reply_agent_runs_in_a_sandbox_without_network(
    client, gated, monkeypatch
):
    """It would answer by `kraft item reply`, which such a sandbox cannot reach."""
    monkeypatch.setattr("kraft.executor.item_sandbox", lambda row, launch: {"kind": "docker"})
    _new_thread(client, gated)
    r = client.post(
        f"/api/work-items/{gated}/gates/chain_review/review", json={"outcome": "comment"}
    )
    assert r.json()["reply_agent"] is False
    events = client.get(f"/api/work-items/{gated}/events").json()
    assert [e["payload"]["gate"] for e in events if e["type"] == "reply_agent_skipped"] == [
        "chain_review"
    ]


@_REVIEW
def test_no_quote_is_read_while_a_sandboxed_session_is_live(client, gated, monkeypatch):
    """Host git does not read a worktree a sandboxed worker can still write:
    the thread is filed, with no quote, and the quote is never attempted."""
    monkeypatch.setattr("kraft.store.live_session_ids", lambda conn, wid: ["s1"])
    monkeypatch.setattr(
        "kraft.executor.stops._item_sandbox", lambda row, launch: {"kind": "docker"}
    )

    def never(*_a, **_kw):
        raise AssertionError("quote_range ran while a sandboxed session was live")

    monkeypatch.setattr("kraft.review.quote_range", never)
    r = _new_thread(client, gated, file_path="calc.py", start_line=1, end_line=2)
    assert r.status_code == 201, r.text
    assert r.json()["quote"] is None
