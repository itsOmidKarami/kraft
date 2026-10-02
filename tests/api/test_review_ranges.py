"""Review threads on a range of lines, across sides too: a sibling of
test_review.py, which is at its line budget."""

# ruff: noqa: F811 -- `gated` is an imported fixture, taken by name.
from __future__ import annotations

from api.test_review import _REVIEW, _new_thread, gated  # noqa: F401


@_REVIEW
def test_a_range_across_sides_round_trips_and_takes_no_suggestion(client, gated):
    """A removed line through its replacement (-2 to +2) is one thread: its start
    keeps its own side and its quote comes back as sent. A range on one side
    reads `start_side` as its `side`, as every older thread does."""
    quote = "-    return a - b\n+    return a + b"
    r = _new_thread(client, gated, start_side="old", start_line=2, end_line=2, quote=quote)
    assert r.status_code == 201, r.text
    across = r.json()
    assert (across["start_side"], across["start_line"]) == ("old", 2)
    assert (across["side"], across["end_line"], across["quote"]) == ("new", 2, quote)
    # Across sides the numbers count different files: old 5 through new 3 is a range.
    assert _new_thread(client, gated, start_side="old", start_line=5, end_line=3).status_code == 201
    one = _new_thread(client, gated, start_side="new").json()
    assert (one["start_side"], one["side"], one["quote"]) == ("new", "new", None)
    listed = {t["id"]: t for t in client.get(f"/api/work-items/{gated}/threads").json()}
    assert listed[across["id"]] == across
    assert listed[one["id"]]["start_side"] == "new"

    fix = {"start_line": 2, "end_line": 2, "replacement": "return a + b"}
    refused = [
        {"start_side": "old", "start_line": 2, "end_line": 2, "suggestion": fix},
        {"start_side": "old", "side": None, "start_line": None, "end_line": None},
        {"side": None, "start_line": None, "end_line": None, "quote": "x"},
        {"start_side": "both"},
        {"quote": "x" * 64_001},
    ]
    for kw in refused:
        assert _new_thread(client, gated, **kw).status_code == 422, kw
    tid = across["id"]
    assert client.patch(f"/api/threads/{tid}", json={"suggestion": fix}).status_code == 422
    reply = {"body": "like this", "suggestion": fix}
    assert client.post(f"/api/threads/{tid}/comments", json=reply).status_code == 422
    assert len(client.get(f"/api/work-items/{gated}/threads").json()) == 3
