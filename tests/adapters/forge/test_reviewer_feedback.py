"""What each automated reviewer has said on a merge request, read off `gh` and
`glab` (stubbed at the CLI) for the review wait's collect path: its newest
activity, its comments and reviews since the push, its unresolved threads, and a
check's state. When a reviewer has *finished* is test_settle.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from kraft.adapters import forge
from kraft.automated_review import BotReviewer, CheckReviewer

from .api_outputs import READ_BRANCH, READ_THREAD
from .outputs import glab_mr
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
BOT = BotReviewer(bot="coderabbitai")
CHECK = CheckReviewer(check="review-bot")

GH_ISSUE_COMMENTS = "api repos/{owner}/{repo}/issues/7/comments"
GH_REVIEWS = "api repos/{owner}/{repo}/pulls/7/reviews"
GLAB_NOTES = "api projects/:id/merge_requests/9/notes"
GLAB_DISCUSSIONS = "api projects/:id/merge_requests/9/discussions"


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


def _gh_comment_node(id, login, body, at, **extra):
    return {
        "id": f"C_{id}",
        "fullDatabaseId": "901",
        "author": {"login": login, "__typename": "Bot"},
        "body": body,
        "path": "calc.py",
        "line": None,
        "originalLine": 3,
        "createdAt": at,
        "updatedAt": at,
        "state": "SUBMITTED",
        "replyTo": None,
        "pullRequestReview": {"fullDatabaseId": "51"},
        **extra,
    }


def _gh_thread(id, resolved, login, body, at, *more):
    return {
        "id": id,
        "isResolved": resolved,
        "comments": {"nodes": [_gh_comment_node(id, login, body, at), *more]},
    }


def _gh_threads(*nodes, more_after=None):
    page = {"hasNextPage": more_after is not None, "endCursor": more_after}
    pull = {"reviewThreads": {"pageInfo": page, "nodes": list(nodes)}}
    return json.dumps({"data": {"repository": {"pullRequest": pull}}})


def _glab_note(id, username, body, at, **extra):
    return {
        "id": id,
        "author": {"username": username},
        "body": body,
        "created_at": at,
        "updated_at": at,
        **extra,
    }


def _glab_discussion(id, *notes):
    return {"id": id, "notes": list(notes)}


_GH_BOT = {
    READ_BRANCH: GH_PR,
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
_GH_CHECK = {READ_BRANCH: GH_PR, GH_STATUS: json.dumps({"statuses": []})}
_GLAB_BOT = {
    **glab_mr(GLAB_MR),
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


_AT = "2026-01-01T12:00:50Z"


def _gh_expected(body="unused x", *notes):
    first = forge.ForgeNote("C_T1", "901", "coderabbitai", "Bot", "unused x", _AT, _AT, None, "51")
    return {
        "id": "T1",
        "file": "calc.py",
        "line": 3,
        "body": body,
        "notes": (first, *notes),
    }


def _glab_expected():
    note = forge.ForgeNote("3", "3", "coderabbitai", None, "unused x", _AT, _AT)
    return {"id": "d1", "file": "calc.py", "line": 3, "body": "unused x", "notes": (note,)}


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
            [_gh_expected()],
            None,
        ),
        # A pending comment is a draft nobody else sees: out of `notes`, but the
        # flattened `body` is as it always was (the head wait's fingerprints).
        (
            GH,
            {
                **_GH_BOT,
                "api graphql": _gh_threads(
                    _gh_thread(
                        "T1",
                        False,
                        "coderabbitai",
                        "unused x",
                        _AT,
                        _gh_comment_node(
                            "P", "a-person", "draft", _AT, state="PENDING", fullDatabaseId="902"
                        ),
                    )
                ),
            },
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
            [_gh_expected("unused x\na-person: draft")],
            None,
        ),
        (
            GH,
            {
                **_GH_BOT,
                "api graphql": _gh_threads(
                    _gh_thread(
                        "T1",
                        False,
                        "coderabbitai",
                        "unused x",
                        _AT,
                        _gh_comment_node(
                            "R",
                            "a-person",
                            "no",
                            "2026-01-01T12:01:00Z",
                            fullDatabaseId="903",
                            author={"login": "a-person", "__typename": "User"},
                            replyTo={"id": "C_T1"},
                            pullRequestReview={"fullDatabaseId": "52"},
                        ),
                    )
                ),
            },
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
            [
                _gh_expected(
                    "unused x\na-person: no",
                    forge.ForgeNote(
                        "C_R",
                        "903",
                        "a-person",
                        "User",
                        "no",
                        "2026-01-01T12:01:00Z",
                        "2026-01-01T12:01:00Z",
                        "C_T1",
                        "52",
                    ),
                )
            ],
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
            [_glab_expected()],
            None,
        ),
        # An approval, or its withdrawal, is the bot's activity, dated by its
        # system note; a person's approval is not the bot's.
        (
            GLAB,
            {
                **_GLAB_BOT,
                GLAB_NOTES: json.dumps(
                    [
                        *json.loads(_GLAB_BOT[GLAB_NOTES]),
                        _glab_note(
                            7,
                            "coderabbitai",
                            "approved this merge request",
                            "2026-01-01T12:01:00Z",
                            system=True,
                        ),
                        _glab_note(
                            8,
                            "a-person",
                            "approved this merge request",
                            "2026-01-01T12:02:00Z",
                            system=True,
                        ),
                        _glab_note(
                            9,
                            "coderabbitai",
                            "unapproved this merge request",
                            "2026-01-01T12:03:00Z",
                            system=True,
                        ),
                    ]
                ),
            },
            BOT,
            datetime(2026, 1, 1, 12, 3, 0, tzinfo=UTC),
            [_comment("walkthrough", "2026-01-01T12:00:30Z")],
            [_glab_expected()],
            None,
        ),
        # A system note ("changed this line") is flattened into `body` as ever, and
        # left out of `notes`.
        (
            GLAB,
            {
                **_GLAB_BOT,
                GLAB_DISCUSSIONS: json.dumps(
                    [
                        _glab_discussion(
                            "d1",
                            _glab_note(
                                3,
                                "coderabbitai",
                                "unused x",
                                _AT,
                                resolvable=True,
                                resolved=False,
                                position={"new_path": "calc.py", "new_line": 3},
                            ),
                            _glab_note(10, "coderabbitai", "changed this line", _AT, system=True),
                        )
                    ]
                ),
            },
            BOT,
            datetime(2026, 1, 1, 12, 0, 50, tzinfo=UTC),
            [_comment("walkthrough", "2026-01-01T12:00:30Z")],
            [{**_glab_expected(), "body": "unused x\ncoderabbitai: changed this line"}],
            None,
        ),
        (
            GLAB,
            {
                **glab_mr(GLAB_MR),
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
    ids=[
        "gh-bot",
        "gh-pending-left-out-of-notes",
        "gh-a-reply-names-what-it-answers",
        "gh-check",
        "gh-check-cancelled",
        "glab-bot",
        "glab-bot-approved",
        "glab-system-note-left-out-of-notes",
        "glab-check",
    ],
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


_OLD = "2025-12-31T00:00:00Z"
_NEW = "2026-01-01T12:30:00Z"


def _page(make, n=100):
    """A full page of 100: the read must ask for the next one."""
    return json.dumps([make(i) for i in range(n)])


def _old_gh_comment(i):
    return _gh_comment("a-person", f"old {i}", _OLD)


def _old_gh_review(i):
    return _gh_review("COMMENTED", _OLD, login="a-person")


def _old_glab_note(i):
    return _glab_note(100 + i, "a-person", f"old {i}", _OLD, system=False)


def _old_glab_discussion(i):
    return _glab_discussion(f"old{i}", _glab_note(200 + i, "a-person", "old", _OLD))


def _gh_resolved_threads(n, *, end):
    nodes = [_gh_thread(f"R{i}", True, "coderabbitai", "done", _OLD) for i in range(n)]
    return _gh_threads(*nodes, more_after=end)


def _thread_with_more(id):
    thread = _gh_thread(id, False, "coderabbitai", "unused x", _NEW)
    thread["comments"]["pageInfo"] = {"hasNextPage": True, "endCursor": "c1"}
    return thread


def _gh_rest_of_thread(late):
    node = {
        "id": "T1",
        "comments": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [_gh_comment_node("late", "a-person", late, _NEW)],
        },
    }
    return json.dumps({"data": {"node": node}})


def _routes(specific: dict, base: dict, override: dict) -> dict:
    """The page-2 or cursor route first, then the base routes, with `override`'s
    answers in their base slots (behind `specific`)."""
    return {**specific, **base, **override}


@pytest.mark.parametrize(
    "backend, routes, seen, newest",
    [
        (
            GH,
            _routes(
                {
                    "api*issues/7/comments*-F page=2*": json.dumps(
                        [_gh_comment("coderabbitai[bot]", "newest comment", _NEW)]
                    )
                },
                _GH_BOT,
                {GH_ISSUE_COMMENTS: _page(_old_gh_comment)},
            ),
            "summary",
            "newest comment",
        ),
        (
            GH,
            _routes(
                {
                    "api*pulls/7/reviews*-F page=2*": json.dumps(
                        [_gh_review("COMMENTED", _NEW, body="newest review")]
                    )
                },
                _GH_BOT,
                {GH_REVIEWS: _page(_old_gh_review)},
            ),
            "summary",
            "newest review",
        ),
        (
            GH,
            _routes(
                {
                    "api*graphql*after=t100*": _gh_threads(
                        _gh_thread("T-newest", False, "coderabbitai", "late", _NEW)
                    )
                },
                _GH_BOT,
                {"api graphql": _gh_resolved_threads(100, end="t100")},
            ),
            "thread",
            "T-newest",
        ),
        (
            GH,
            _routes(
                {READ_THREAD: _gh_rest_of_thread("late answer")},
                _GH_BOT,
                {"api graphql": _gh_threads(_thread_with_more("T1"))},
            ),
            "note",
            "late answer",
        ),
        (
            GLAB,
            _routes(
                {
                    "api*merge_requests/9/notes*-F page=2*": json.dumps(
                        [_glab_note(900, "coderabbitai", "newest note", _NEW, system=False)]
                    )
                },
                _GLAB_BOT,
                {GLAB_NOTES: _page(_old_glab_note)},
            ),
            "summary",
            "newest note",
        ),
        (
            GLAB,
            _routes(
                {
                    "api*merge_requests/9/discussions*-F page=2*": json.dumps(
                        [
                            _glab_discussion(
                                "d-newest",
                                _glab_note(
                                    901,
                                    "coderabbitai",
                                    "late",
                                    _NEW,
                                    resolvable=True,
                                    resolved=False,
                                    position={"new_path": "calc.py", "new_line": 1},
                                ),
                            )
                        ]
                    )
                },
                _GLAB_BOT,
                {GLAB_DISCUSSIONS: _page(_old_glab_discussion)},
            ),
            "thread",
            "d-newest",
        ),
    ],
    ids=[
        "gh-comments",
        "gh-reviews",
        "gh-threads",
        "gh-thread-comments",
        "glab-notes",
        "glab-discussions",
    ],
)
async def test_every_page_of_bot_feedback_is_read(cli, tmp_path, backend, routes, seen, newest):
    cli.stub(backend.name, routes=routes, default="[]")

    (got,) = await backend.cls().reviewer_feedback(
        repo=tmp_path, branch="kraft/w1", reviewers=(BOT,), since=SINCE
    )

    found = {
        "summary": [s["body"] for s in got.summaries],
        "thread": [t["id"] for t in got.threads],
        "note": [n.body for t in got.threads for n in t["notes"]],
    }[seen]
    assert newest in found
