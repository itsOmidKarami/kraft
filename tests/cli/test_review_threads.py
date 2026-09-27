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
    assert "t1" in out and "a.py:3-4" in out and "fix this" in out


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
