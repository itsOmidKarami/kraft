"""The head wait's reads with Kraft's facts given (`reviewer_feedback(kraft=)`):
Kraft's conversation in a bot's thread is not the bot reviewing the push. The
reads without Kraft's facts are test_reviewer_feedback.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from kraft.adapters.forge.replies import facts

from .api_outputs import READ_BRANCH
from .outputs import glab_mr
from .test_automated_review import GH, GH_PR, GLAB, GLAB_MR, HEAD
from .test_reviewer_feedback import (
    BOT,
    GH_ISSUE_COMMENTS,
    GH_REVIEWS,
    GLAB_DISCUSSIONS,
    GLAB_NOTES,
    _gh_comment_node,
    _gh_threads,
    _glab_discussion,
    _glab_note,
)

SINCE = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
KRAFT = facts.Kraft("kraft-user", "wi1", {})
MARKED = "Declined:\n\nno\n\n<!-- kraft-reply:wi1-0123456789ab -->"
T_0, T_5, T_10 = "2026-01-01T10:00:00Z", "2026-01-01T10:05:00Z", "2026-01-01T10:10:00Z"


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _review(id, at, *, state="COMMENTED", body=""):
    return {
        "id": id,
        "user": {"login": "coderabbitai[bot]"},
        "state": state,
        "commit_id": HEAD,
        "body": body,
        "submitted_at": at,
    }


def _node(id, body, at, review, *, reply_to="C_1", login="coderabbitai", type_="Bot"):
    return _gh_comment_node(
        id,
        login,
        body,
        at,
        fullDatabaseId=str(id),
        author={"login": login, "__typename": type_},
        replyTo=None if reply_to is None else {"id": reply_to},
        pullRequestReview={"fullDatabaseId": review},
    )


def _thread(id, *nodes):
    return {"id": id, "isResolved": False, "comments": {"nodes": list(nodes)}}


#: The bot's review 54 opened T1; Kraft answered; the bot replied in review 55.
_OPENED = _node(1, "rename x", T_0, "54", reply_to=None)
_KRAFT_SAID = _node(2, MARKED, T_5, "60", login="kraft-user", type_="User")
_BOT_REPLIED = _node(3, "fine", T_10, "55")


def _gh(*, r54_body="two notes", r55_state="COMMENTED", said=_KRAFT_SAID, more=()):
    # `said=None`: the thread shows no Kraft note (a person deleted it).
    nodes = [n for n in (_OPENED, said, _BOT_REPLIED) if n is not None]
    return {
        READ_BRANCH: GH_PR,
        GH_ISSUE_COMMENTS: "[]",
        GH_REVIEWS: json.dumps(
            [_review(54, T_0, body=r54_body), _review(55, T_10, state=r55_state)]
        ),
        "api graphql": _gh_threads(_thread("T1", *nodes), *more),
    }


_GLAB_SUMMARY = _glab_note(10, "coderabbitai", "walkthrough", "2026-01-01T09:30:00Z", system=False)
_WHERE = {"position": {"new_path": "calc.py", "new_line": 3}, "type": "DiffNote"}
_D1 = (
    _glab_note(11, "coderabbitai", "rename x", T_0, resolvable=True, resolved=False, **_WHERE),
    _glab_note(12, "kraft-user", MARKED, T_5, resolvable=True, resolved=False, **_WHERE),
    # GitLab lists this reply among the merge request's notes with no position.
    _glab_note(13, "coderabbitai", "fine", T_10, resolvable=True, resolved=False, system=False),
)


def _glab(*notes):
    return {
        **glab_mr(GLAB_MR),
        GLAB_NOTES: json.dumps([_GLAB_SUMMARY, *notes]),
        GLAB_DISCUSSIONS: json.dumps([_glab_discussion("D1", *notes)]),
    }


_GLAB = _glab(*_D1)
#: Resolving the discussion moved every note's `updated_at` (measured on GitLab, 2026-11-22).
_MOVED = tuple({**n, "updated_at": "2026-01-01T10:20:00Z"} for n in _D1)
#: The record holds Kraft's reply in D1; a person deleted the Kraft note.
_DELETED = tuple(n for n in _D1 if n["id"] != 12)
_RECORD = facts.Kraft("kraft-user", "wi1", {}, frozenset({"T1", "D1"}))


@pytest.mark.parametrize(
    "backend, routes, kraft, activity, summaries, threads",
    [
        (GH, _gh(), KRAFT, T_0, [T_0], ["T1"]),
        (GH, _gh(), None, T_10, [T_0, T_10], ["T1"]),
        (GH, _gh(r54_body=""), KRAFT, T_0, [T_0], ["T1"]),
        (
            GH,
            _gh(more=(_thread("T2", _node(4, "also y", T_10, "55", reply_to=None)),)),
            KRAFT,
            T_10,
            [T_0, T_10],
            ["T1", "T2"],
        ),
        (GH, _gh(r55_state="CHANGES_REQUESTED"), KRAFT, T_10, [T_0, T_10], ["T1"]),
        (GH, _gh(said=_node(2, MARKED, T_5, "56")), KRAFT, T_10, [T_0, T_10], ["T1"]),
        (GLAB, _GLAB, KRAFT, "2026-01-01T09:30:00Z", ["2026-01-01T09:30:00Z"], ["D1"]),
        (GLAB, _GLAB, None, T_10, ["2026-01-01T09:30:00Z", T_10], ["D1"]),
        (GH, _gh(said=None), _RECORD, T_0, [T_0], ["T1"]),
        (GH, _gh(said=None), KRAFT, T_10, [T_0, T_10], ["T1"]),
        (GLAB, _glab(*_DELETED), _RECORD, "2026-01-01T09:30:00Z", ["2026-01-01T09:30:00Z"], ["D1"]),
        (GLAB, _glab(*_MOVED), KRAFT, "2026-01-01T09:30:00Z", ["2026-01-01T09:30:00Z"], ["D1"]),
        (
            GLAB,
            _glab(*_MOVED),
            None,
            "2026-01-01T10:20:00Z",
            ["2026-01-01T09:30:00Z", "2026-01-01T10:20:00Z"],
            ["D1"],
        ),
    ],
    ids=[
        "gh-reply-in-a-kraft-thread",
        "gh-the-same-off",
        "gh-the-bot-s-own-review-still-counts",
        "gh-review-with-a-new-thread",
        "gh-a-changes-requested-reply-still-counts",
        "gh-marker-on-a-bot-note",
        "glab-reply-in-a-kraft-discussion",
        "glab-the-same-off",
        "gh-kraft-note-deleted-record-remembers",
        "gh-kraft-note-deleted-record-forgot",
        "glab-kraft-note-deleted-record-remembers",
        "glab-a-resolve-moves-updated-at",
        "glab-a-resolve-moves-updated-at-kraft-unknown",
    ],
)
async def test_the_head_wait_leaves_out_the_conversation(
    cli, tmp_path, backend, routes, kraft, activity, summaries, threads
):
    cli.stub(backend.name, routes=routes, default="[]")

    (got,) = await backend.cls().reviewer_feedback(
        repo=tmp_path, branch="kraft/w1", reviewers=(BOT,), since=SINCE, kraft=kraft
    )

    assert (
        got.last_activity,
        [s["updated_at"] for s in got.summaries],
        [t["id"] for t in got.threads],
    ) == (_at(activity), summaries, threads)
