"""Review-thread verbs through cli.main(): comment, threads, compare, review.

Split out of test_verbs.py, which covers every other verb through the same
dispatch table and rendering.
"""

from __future__ import annotations

import inspect
import json

import pytest

from kraft import cli, client


@pytest.mark.parametrize(
    "argv, fn, expected",
    [
        (
            [
                "item",
                "comment",
                "7",
                "--body",
                "hi",
                "--file",
                "a.py",
                "--lines",
                "3-4",
                "--label",
                "must-fix",
                "--suggest",
                "x",
            ],  # fmt: skip
            "add_review_comment",
            {
                "body": "hi",
                "work_item_id": "7",
                "thread_id": None,
                "file_path": "a.py",
                "start_line": 3,
                "end_line": 4,
                "side": None,
                "label": "must_fix",
                "suggestion": "x",
                "start_side": None,
                "quote": None,
            },
        ),
        (
            ["item", "comment", "7", "--body", "hi", "--file", "a.py", "--lines", "3-2"]
            + ["--start-side", "old", "--side", "new"],
            "add_review_comment",
            {
                "body": "hi",
                "work_item_id": "7",
                "thread_id": None,
                "file_path": "a.py",
                "start_line": 3,
                "end_line": 2,
                "side": "new",
                "label": None,
                "suggestion": None,
                "start_side": "old",
                "quote": None,
            },
        ),
        (
            ["item", "comment", "--reply", "T1", "--body", "hi"],
            "add_review_comment",
            {
                "body": "hi",
                "work_item_id": None,
                "thread_id": "T1",
                "file_path": None,
                "start_line": None,
                "end_line": None,
                "side": None,
                "label": None,
                "suggestion": None,
                "start_side": None,
                "quote": None,
            },
        ),
        (["item", "resolve", "T1"], "resolve_thread", {"thread_id": "T1"}),
        (["item", "reopen", "T1"], "reopen_thread", {"thread_id": "T1"}),
        (
            ["item", "review", "7", "request-changes", "--summary", "s"],
            "submit_review",
            {"outcome": "request_changes", "work_item_id": "7", "summary": "s", "node": None},
        ),
    ],
    ids=[
        "comment-new-thread",
        "comment-across-sides",
        "comment-reply",
        "resolve-thread",
        "reopen-thread",
        "review-request-changes",
    ],
)
def test_a_review_verb_passes_its_arguments_through(app, monkeypatch, capsys, argv, fn, expected):
    """Same table as test_verbs.py's test_a_verb_passes_its_arguments_through,
    split out here for the review-thread verbs only."""
    real = getattr(client, fn)
    seen = {}

    async def fake(*args, **kwargs):
        bound = inspect.signature(real).bind(*args, **kwargs)
        bound.apply_defaults()
        seen.update(bound.arguments)
        return {
            "id": "w1",
            "node_id": "n",
            "steer": None,
            "status": "active",
            "progress": {"current": 2, "total": 3, "title": "serve"},
        }

    monkeypatch.setattr(client, fn, fake)
    cli.main(argv)
    assert seen == expected
    assert "w1" in capsys.readouterr().out


def test_review_request_changes_json_is_pure_json_even_with_a_target(app, monkeypatch, capsys):
    """A gateless request-changes always has a `target` key; `--json` must still
    print exactly the payload, with no human-readable line ahead of it."""

    async def fake_submit_review(outcome, work_item_id=None, summary=None, node=None):
        return {
            "review_id": "r1",
            "outcome": "request_changes",
            "gate": None,
            "target": "plan",
            "target_reason": "requested",
            "action": "rerun",
        }

    monkeypatch.setattr(client, "submit_review", fake_submit_review)
    cli.main(["item", "review", "w1", "request-changes", "--summary", "s", "--json"])
    assert json.loads(capsys.readouterr().out)["target"] == "plan"


@pytest.mark.parametrize(
    "flags",
    [["--side", "old"], ["--start-side", "old", "--side", "new"]],
    ids=["old-side", "across-sides"],
)
def test_comment_refuses_a_suggestion_on_old_side_lines(app, capsys, flags):
    """A suggestion replaces new-side lines: `--side old --suggest` would tell
    the agent to replace other lines than the ones it is about (R10F-04)."""
    argv = ["item", "comment", "7", "--body", "x", "--file", "a.py", "--lines", "2-3"]
    with pytest.raises(SystemExit) as caught:
        cli.main([*argv, *flags, "--suggest", "y"])
    assert caught.value.code == 2
    assert "--suggest replaces new-side lines" in capsys.readouterr().err


def test_a_new_thread_prints_where_it_is_and_the_lines_it_quotes(app, monkeypatch, capsys):
    async def fake(*_a, **_kw):
        return {
            "id": "t1",
            "file_path": "calc.py",
            "side": "old",
            "start_side": "old",
            "start_line": 2,
            "end_line": 2,
            "quote": "-    return a - b",
            "comments": [{"author": "you", "body": "x", "draft": True}],
        }

    monkeypatch.setattr(client, "add_review_comment", fake)
    cli.main(["item", "comment", "7", "--body", "x", "--file", "calc.py", "--lines", "2"])
    assert capsys.readouterr().out.splitlines() == [
        "drafted thread t1 on calc.py:-2",
        "    | -    return a - b",
        "it goes out with your next kraft item review",
    ]


@pytest.mark.parametrize(
    ("verb", "fn", "line"),
    [
        ("resolve", "resolve_thread", "resolved thread t1 on calc.py:+9 to +11"),
        ("reopen", "reopen_thread", "reopened thread t1 on calc.py:+9 to +11"),
    ],
    ids=["resolve", "reopen"],
)
def test_resolve_and_reopen_print_one_line_naming_the_thread(
    app, monkeypatch, capsys, verb, fn, line
):
    """They printed the whole thread, its comments as one JSON line (R11a)."""

    async def fake(*_a, **_kw):
        return {
            "id": "t1",
            "file_path": "calc.py",
            "side": "new",
            "start_side": "new",
            "start_line": 9,
            "end_line": 11,
            "state": "resolved" if verb == "resolve" else "open",
            "comments": [{"id": "c1", "author": "you", "body": "x", "draft": False}],
        }

    monkeypatch.setattr(client, fn, fake)
    cli.main(["item", verb, "t1"])
    assert capsys.readouterr().out == f"{line}\n"
    cli.main(["item", verb, "t1", "--json"])
    assert json.loads(capsys.readouterr().out)["comments"][0]["id"] == "c1"


def test_comment_needs_lines_for_a_suggestion(app, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["item", "comment", "--body", "x", "--suggest", "y"])
    assert caught.value.code == 2
    assert "--suggest needs --lines" in capsys.readouterr().err


def test_view_threads_renders_a_block_per_thread(app, monkeypatch, capsys):
    async def fake_threads(work_item_id=None, open_only=False):
        return [
            {
                "id": "t1",
                "file_path": "a.py",
                "start_line": 3,
                "end_line": 4,
                "label": "must_fix",
                "state": "open",
                "draft": False,
                "comments": [{"author": "you", "body": "fix this", "draft": False}],
            }
        ]

    monkeypatch.setattr(client, "threads", fake_threads)
    cli.main(["view", "threads", "w1"])
    out = capsys.readouterr().out
    assert "t1" in out and "a.py:+3 to +4" in out and "fix this" in out


def test_view_threads_names_a_range_across_sides_by_its_marks(app, monkeypatch, capsys):
    """Old line 2 through new line 2 reads `-2 to +2`; a range on one side marks
    its side too (R10a-08), so removed lines 3-4 never read like added ones."""

    def thread(tid, start_side, side, start, end):
        return {
            "id": tid,
            "file_path": "calc.py",
            "side": side,
            "start_side": start_side,
            "start_line": start,
            "end_line": end,
            "label": None,
            "state": "open",
            "draft": False,
            "comments": [],
        }

    async def fake_threads(work_item_id=None, open_only=False):
        return [
            thread("t1", "old", "new", 2, 2),
            thread("t2", "new", "new", 3, 4),
            thread("t3", "old", "old", 3, 4),
            thread("t4", "new", "new", 8, 8),
        ]

    monkeypatch.setattr(client, "threads", fake_threads)
    cli.main(["view", "threads", "w1"])
    out = capsys.readouterr().out
    assert "t1  calc.py:-2 to +2  [open]" in out
    assert "t2  calc.py:+3 to +4  [open]" in out
    assert "t3  calc.py:-3 to -4  [open]" in out
    assert "t4  calc.py:+8  [open]" in out


def test_view_compare_forwards_targets_and_stats_the_files(app, monkeypatch, capsys):
    async def fake_compare(work_item_id=None, from_="base", to="latest", nodes=None):
        assert (work_item_id, from_, to, nodes) == ("w1", "attempt:1", "latest", None)
        return {
            "from": {"target": "attempt:1"},
            "to": {"target": "latest"},
            "rebased": False,
            "files": [
                {"path": "a.py", "insertions": 3, "deletions": 1, "touched_by": ["implementation"]}
            ],
            "diff": "",
            "untracked": [],
            "truncated": False,
        }

    monkeypatch.setattr(client, "compare", fake_compare)
    cli.main(["view", "compare", "w1", "--from", "attempt:1", "--to", "latest", "--stat"])
    out = capsys.readouterr().out
    assert "attempt:1 -> latest" in out and "a.py" in out and "implementation" in out


@pytest.mark.parametrize("verb, fn", [("compare", "compare"), ("diff", "diff")])
def test_view_ignore_whitespace_reaches_the_client_only_when_asked(app, monkeypatch, verb, fn):
    seen = []

    async def fake(*args, **kwargs):
        seen.append(kwargs.get("ignore_whitespace", False))
        return {"files": [], "diff": "", "untracked": [], "truncated": False, "base_ref": "a"}

    monkeypatch.setattr(client, fn, fake)
    for flag in ([], ["--ignore-whitespace"], ["-w"]):
        cli.main(["view", verb, "w1", "--name-only", *flag])
    assert seen == [False, True, True]


def test_client_sends_ignore_whitespace_only_when_set(monkeypatch):
    import asyncio

    sent = []

    async def fake_get(path, **params):
        sent.append((path, params.get("ignore_whitespace")))
        return {}

    monkeypatch.setattr(client.transport, "_get", fake_get)
    asyncio.run(client.compare("w1", ignore_whitespace=True))
    asyncio.run(client.compare("w1"))
    asyncio.run(client.diff("w1", ignore_whitespace=True))
    asyncio.run(client.diff("w1"))
    assert [v for _, v in sent] == [True, None, True, None]
