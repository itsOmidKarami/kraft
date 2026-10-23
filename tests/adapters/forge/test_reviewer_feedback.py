"""What each automated reviewer has said on a merge request, read off `gh` and
`glab` (stubbed at the CLI) for the review wait's collect path: its newest
activity, its comments and reviews since the push, its unresolved threads, and a
check's state. When a reviewer has *finished* is test_settle.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from kraft.adapters import forge
from kraft.automated_review import AutomatedReview

from .test_automated_review import (
    GH,
    GH_PR,
    GH_RUNS,
    GH_STATUS,
    GLAB,
    GLAB_MR,
    GLAB_STATUSES,
    HEAD,
)

SINCE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
BOT = AutomatedReview(bot="coderabbitai")
CHECK = AutomatedReview(check="review-bot")

GH_ISSUE_COMMENTS = "api repos/{owner}/{repo}/issues/7/comments?per_page=100"
GH_REVIEWS = "api repos/{owner}/{repo}/pulls/7/reviews?per_page=100"
GLAB_NOTES = "api projects/:id/merge_requests/9/notes?per_page=100"
GLAB_DISCUSSIONS = "api projects/:id/merge_requests/9/discussions?per_page=100"


def _gh_comment(login, body, at):
    return {"user": {"login": login}, "body": body, "updated_at": at}


def _gh_review(state, at, *, commit=HEAD, login="coderabbitai[bot]", body="looks mostly fine"):
    return {
        "id": 51,
        "user": {"login": login},
        "state": state,
        "commit_id": commit,
        "body": body,
        "submitted_at": at,
    }


def _gh_thread(id, resolved, login, body, at):
    return {
        "id": id,
        "isResolved": resolved,
        "comments": {
            "nodes": [
                {
                    "author": {"login": login},
                    "body": body,
                    "path": "calc.py",
                    "line": None,
                    "originalLine": 3,
                    "updatedAt": at,
                }
            ]
        },
    }


def _gh_threads(*nodes):
    pull = {"reviewThreads": {"nodes": list(nodes)}}
    return json.dumps({"data": {"repository": {"pullRequest": pull}}})


def _glab_note(id, username, body, at, **extra):
    return {"id": id, "author": {"username": username}, "body": body, "updated_at": at, **extra}


def _glab_discussion(id, *notes):
    return {"id": id, "notes": list(notes)}


_GH_BOT = {
    "pr view": GH_PR,
    GH_ISSUE_COMMENTS: json.dumps(
        [
            _gh_comment("coderabbitai[bot]", "stale summary", "2026-01-01T11:00:00Z"),
            _gh_comment("coderabbitai[bot]", "walkthrough", "2026-01-01T12:00:30Z"),
            _gh_comment("a-person", "thanks", "2026-01-01T12:05:00Z"),
        ]
    ),
    GH_REVIEWS: json.dumps(
        [
            _gh_review("COMMENTED", "2026-01-01T12:00:40Z"),
            _gh_review("DISMISSED", "2026-01-01T12:00:45Z"),
            _gh_review("COMMENTED", "2026-01-01T12:00:46Z", commit="an-older-head"),
        ]
    ),
    "api graphql": _gh_threads(
        _gh_thread("T1", False, "coderabbitai", "unused x", "2026-01-01T12:00:50Z"),
        _gh_thread("T2", True, "coderabbitai", "nit, fixed", "2026-01-01T12:00:20Z"),
        _gh_thread("T3", False, "a-person", "please rename", "2026-01-01T12:09:00Z"),
    ),
}
_GH_CHECK = {"pr view": GH_PR, GH_STATUS: json.dumps({"statuses": []})}
_GLAB_BOT = {
    "mr view": GLAB_MR,
    GLAB_NOTES: json.dumps(
        [
            _glab_note(1, "coderabbitai", "stale summary", "2026-01-01T11:00:00Z", system=False),
            _glab_note(2, "coderabbitai", "walkthrough", "2026-01-01T12:00:30Z", system=False),
            _glab_note(3, "coderabbitai", "inline", "2026-01-01T12:00:31Z", position={"x": 1}),
            _glab_note(4, "coderabbitai", "moved", "2026-01-01T12:00:32Z", system=True),
            _glab_note(5, "a-person", "thanks", "2026-01-01T12:05:00Z", system=False),
        ]
    ),
    GLAB_DISCUSSIONS: json.dumps(
        [
            _glab_discussion(
                "d1",
                _glab_note(
                    3,
                    "coderabbitai",
                    "unused x",
                    "2026-01-01T12:00:50Z",
                    resolvable=True,
                    resolved=False,
                    position={"new_path": "calc.py", "new_line": 3},
                ),
            ),
            _glab_discussion(
                "d2",
                _glab_note(
                    6,
                    "coderabbitai",
                    "fixed",
                    "2026-01-01T12:00:20Z",
                    resolvable=True,
                    resolved=True,
                    position={"new_path": "calc.py", "new_line": 9},
                ),
            ),
        ]
    ),
}


def _comment(body, at):
    return {"kind": "comment", "state": None, "body": body, "updated_at": at}


@pytest.mark.parametrize(
    "backend, routes, reviewer, last_activity, summaries, threads, check",
    [
        (
            GH,
            _GH_BOT,
            BOT,
            datetime(2026, 1, 1, 12, 0, 50, tzinfo=UTC),
            [
                _comment("walkthrough", "2026-01-01T12:00:30Z"),
                {
                    "kind": "review",
                    "state": "COMMENTED",
                    "body": "looks mostly fine",
                    "updated_at": "2026-01-01T12:00:40Z",
                },
            ],
            [{"id": "T1", "file": "calc.py", "line": 3, "body": "unused x"}],
            None,
        ),
        (
            GH,
            {
                **_GH_CHECK,
                GH_RUNS: json.dumps(
                    {
                        "check_runs": [
                            {
                                "status": "completed",
                                "conclusion": "failure",
                                "output": {"title": "3 problems", "summary": "see below"},
                            }
                        ]
                    }
                ),
            },
            CHECK,
            None,
            [],
            [],
            {
                "name": "review-bot",
                "finished": True,
                "conclusion": "failure",
                "output": "3 problems\nsee below",
            },
        ),
        (
            GH,
            {
                **_GH_CHECK,
                GH_RUNS: json.dumps(
                    {"check_runs": [{"status": "completed", "conclusion": "cancelled"}]}
                ),
            },
            CHECK,
            None,
            [],
            [],
            {"name": "review-bot", "finished": False, "conclusion": "cancelled", "output": ""},
        ),
        (
            GLAB,
            _GLAB_BOT,
            BOT,
            datetime(2026, 1, 1, 12, 0, 50, tzinfo=UTC),
            [_comment("walkthrough", "2026-01-01T12:00:30Z")],
            [{"id": "d1", "file": "calc.py", "line": 3, "body": "unused x"}],
            None,
        ),
        (
            GLAB,
            {
                "mr view": GLAB_MR,
                GLAB_STATUSES: json.dumps(
                    [{"id": 4, "name": "review-bot", "status": "failed", "description": "2 issues"}]
                ),
            },
            CHECK,
            None,
            [],
            [],
            {"name": "review-bot", "finished": True, "conclusion": "failed", "output": "2 issues"},
        ),
    ],
    ids=["gh-bot", "gh-check", "gh-check-cancelled", "glab-bot", "glab-check"],
)
async def test_reviewer_feedback_reads(
    cli, tmp_path, backend, routes, reviewer, last_activity, summaries, threads, check
):
    cli.stub(backend.name, routes=routes, default="[]")

    (got,) = await backend.cls().reviewer_feedback(
        repo=tmp_path, branch="kraft/w1", reviewers=(reviewer,), since=SINCE
    )

    assert got.reviewer == reviewer
    assert got.last_activity == last_activity
    assert list(got.summaries) == summaries
    assert list(got.threads) == threads
    assert got.check == check


async def test_the_fake_answers_its_script_then_has_nothing_to_say(tmp_path):
    scripted = forge.ReviewerFeedback(BOT, SINCE, summaries=(_comment("hi", "t"),))
    fake = forge.FakeForge(feedback_script=[[scripted]])
    ask = {"repo": tmp_path, "branch": "kraft/w1", "reviewers": (BOT, CHECK), "since": SINCE}

    first = await fake.reviewer_feedback(**ask)
    # The script's last answer repeats, as every other script of the fake's does.
    again = await fake.reviewer_feedback(**ask)
    silent = await forge.FakeForge().reviewer_feedback(**ask)

    assert first == again == [scripted]
    assert [(f.last_activity, f.check and f.check["finished"]) for f in silent] == [
        (None, None),
        (None, True),
    ]
    assert fake.reviewers_asked == [(BOT, CHECK), (BOT, CHECK)]
