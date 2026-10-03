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
    ]
    for kw in refused:
        assert _new_thread(client, gated, **kw).status_code == 422, kw
    tid = across["id"]
    assert client.patch(f"/api/threads/{tid}", json={"suggestion": fix}).status_code == 422
    reply = {"body": "like this", "suggestion": fix}
    assert client.post(f"/api/threads/{tid}/comments", json=reply).status_code == 422
    assert len(client.get(f"/api/work-items/{gated}/threads").json()) == 3


@_REVIEW
def test_a_long_quote_is_clipped_and_never_blocks_the_comment(client, gated):
    """A quote is context: one 70,000-character line is clipped, not refused."""
    r = _new_thread(client, gated, quote="+" + "a" * 70_000)
    assert r.status_code == 201, r.text[:200]
    quote = r.json()["quote"]
    assert len(quote) == 64_000 and quote.endswith("…")


@_REVIEW
def test_a_thread_sent_with_no_quote_is_quoted_from_the_diff(client, gated):
    """The CLI and MCP draw no diff, so they send no quote: the server quotes
    the range, and the agent reads the lines, as for one made on the page."""
    import subprocess

    from api.test_review import _worktree

    wt = _worktree(client, gated)
    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "commit", "-qam", "fix"], cwd=wt, check=True)
    r = _new_thread(client, gated, file_path="calc.py", start_line=1, end_line=2)
    assert r.status_code == 201, r.text
    assert r.json()["quote"] == " def add(a, b):\n+    return a + b"
    old = _new_thread(client, gated, file_path="calc.py", side="old", start_line=2, end_line=2)
    assert old.json()["quote"] == "-    return a - b  # bug: should be +"
    sent = _new_thread(client, gated, file_path="calc.py", start_line=2, end_line=2, quote="+x")
    assert sent.json()["quote"] == "+x"


@_REVIEW
def test_a_suggestion_on_old_side_lines_is_refused(client, gated):
    """A suggestion replaces new-side lines: on an old-side range it would
    replace other lines than the ones it was written against."""
    fix = {"start_line": 3, "end_line": 3, "replacement": "x"}
    assert _new_thread(client, gated, side="old", suggestion=fix).status_code == 422
    tid = _new_thread(client, gated, side="old").json()["id"]
    assert client.patch(f"/api/threads/{tid}", json={"suggestion": fix}).status_code == 422
    assert _new_thread(client, gated, suggestion=fix).status_code == 201


@_REVIEW
def test_a_thread_on_a_path_outside_the_repo_is_refused(client, gated):
    """Host git reads `file_path` to quote the range: it is a path in the
    repository, as `PUT /viewed` takes one."""
    for path in ("/etc/passwd", "../other/calc.py", "a/../../calc.py"):
        r = _new_thread(client, gated, file_path=path)
        assert r.status_code == 422, (path, r.text)
        assert "file_path must be a path inside the repository" in r.text
    assert client.get(f"/api/work-items/{gated}/threads").json() == []
