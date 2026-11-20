"""One bot thread's reads and writes on `gh` and `glab`, stubbed at the CLI: the
login Kraft writes as, one thread read, a reply, a resolve, the top-level comments
and their create, edit and delete. The API clients are the same code on an HTTP
transport, so the parity tables (test_github_parity.py, test_forge_parity.py) prove
them alike."""

from __future__ import annotations

import json

import pytest

from kraft.adapters import forge

from .api_outputs import READ_BRANCH, READ_THREAD, gql
from .nodes import NOT_FOUND
from .outputs import glab_mr
from .test_automated_review import GH, GH_PR, GLAB, GLAB_MR

_AT = "2026-01-01T12:00:00Z"
_GH_COMMENT = {
    "id": "PRRC_1",
    "fullDatabaseId": "901",
    "author": {"__typename": "Bot", "login": "coderabbitai"},
    "body": "unused x",
    "path": "calc.py",
    "line": 3,
    "originalLine": 3,
    "createdAt": _AT,
    "updatedAt": _AT,
    "state": "SUBMITTED",
    "replyTo": None,
    "pullRequestReview": {"fullDatabaseId": "51"},
}


def _gh_thread_node(number=7, *comments):
    return gql(
        {
            "node": {
                "id": "PRT_1",
                "isResolved": False,
                "isOutdated": False,
                "viewerCanReply": True,
                "viewerCanResolve": True,
                "pullRequest": {"number": number},
                "comments": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": list(comments) or [_GH_COMMENT],
                },
            }
        }
    )


_GH_THREAD = _gh_thread_node(
    7, _GH_COMMENT, {**_GH_COMMENT, "id": "PRRC_2", "state": "PENDING", "body": "draft"}
)
_GH_REPLY = json.dumps(
    {"id": 1234, "node_id": "PRRC_x", "created_at": _AT, "body": "Answer:\n\nok"}
)
_GH_COMMENT_ANSWER = json.dumps(
    {"id": 555, "node_id": "IC_555", "created_at": _AT, "body": "summary"}
)
_GH_PAGE = json.dumps(
    [
        {
            "id": n,
            "node_id": f"IC_{n}",
            "user": {"login": "a-person", "type": "User"},
            "body": "hi",
            "created_at": _AT,
            "updated_at": _AT,
        }
        for n in range(100)
    ]
)
_GH_LAST = json.dumps(
    [
        {
            "id": 999,
            "node_id": "IC_999",
            "user": {"login": "coderabbitai[bot]", "type": "Bot"},
            "body": "walkthrough",
            "created_at": _AT,
            "updated_at": _AT,
        }
    ]
)

_GLAB_NOTE = {
    "id": 3,
    "author": {"username": "coderabbitai"},
    "body": "unused x",
    "created_at": _AT,
    "updated_at": _AT,
    "resolvable": True,
    "resolved": False,
    "position": {"new_path": "calc.py", "new_line": 3},
}
_GLAB_THREAD = json.dumps(
    {
        "id": "d1",
        "notes": [_GLAB_NOTE, {**_GLAB_NOTE, "id": 4, "system": True, "body": "changed this line"}],
    }
)
_GLAB_DISC = "api projects/:id/merge_requests/9/discussions/d1"
_GLAB_REPLY = json.dumps({"id": 77, "created_at": _AT, "body": "Answer:\n\nok"})
_GLAB_NOTE_ANSWER = json.dumps({"id": 555, "created_at": _AT, "body": "summary"})


def _thread(mr=7, notes=None, **kw):
    note = forge.ForgeNote("PRRC_1", "901", "coderabbitai", "Bot", "unused x", _AT, _AT)
    return forge.ForgeThread(
        "PRT_1", mr, False, False, True, True, "calc.py", 3, notes or (note,), **kw
    )


def _gh_note():
    return forge.ForgeNote("IC_555", "555", "k", "User", "b", _AT, _AT)


def _glab_thread(mr=9):
    note = forge.ForgeNote("3", "3", "coderabbitai", None, "unused x", _AT, _AT)
    return forge.ForgeThread("d1", mr, False, False, True, True, "calc.py", 3, (note,))


@pytest.mark.parametrize(
    "backend, routes",
    [
        (GH, {"api graphql": gql({"viewer": {"login": "kraft-user"}})}),
        (GLAB, {"api user": json.dumps({"username": "kraft-user"})}),
    ],
    ids=["gh", "glab"],
)
async def test_viewer_is_the_login_kraft_writes_as(cli, tmp_path, backend, routes):
    cli.stub(backend.name, routes=routes)

    assert await backend.cls().viewer(repo=tmp_path) == "kraft-user"


@pytest.mark.parametrize(
    "backend, routes, mr, can_reply, notes",
    [
        (GH, {READ_BRANCH: GH_PR, READ_THREAD: _GH_THREAD}, 7, True, 1),
        (GH, {READ_BRANCH: GH_PR, READ_THREAD: _gh_thread_node(8)}, None, None, None),
        (GH, {READ_BRANCH: GH_PR, READ_THREAD: gql({"node": None})}, None, None, None),
        (GLAB, {**glab_mr(GLAB_MR), _GLAB_DISC: _GLAB_THREAD}, 9, True, 1),
        (
            GLAB,
            {
                **glab_mr(json.dumps({"iid": 9, "discussion_locked": True})),
                _GLAB_DISC: _GLAB_THREAD,
            },
            9,
            False,
            1,
        ),
        (GLAB, {**glab_mr(GLAB_MR), _GLAB_DISC: NOT_FOUND}, None, None, None),
    ],
    ids=[
        "gh",
        "gh-another-pull-request",
        "gh-not-a-thread",
        "glab",
        "glab-locked",
        "glab-missing",
    ],
)
async def test_read_thread(cli, tmp_path, backend, routes, mr, can_reply, notes):
    cli.stub(backend.name, routes=routes)
    thread_id = "PRT_1" if backend is GH else "d1"

    got = await backend.cls().read_thread(repo=tmp_path, branch="kraft/w1", thread_id=thread_id)

    if mr is None:
        assert got is None
        return
    assert (got.mr, got.can_reply, len(got.notes)) == (mr, can_reply, notes)
    assert got.file == "calc.py"
    assert got.notes[0].body == "unused x"


@pytest.mark.parametrize(
    "backend, thread, route, answer, path, body, ids",
    [
        (
            GH,
            _thread(),
            "api*pulls/7/comments/901/replies*",
            _GH_REPLY,
            "repos/{owner}/{repo}/pulls/7/comments/901/replies",
            "Answer: ok",
            ("1234", "PRRC_x"),
        ),
        (
            GH,
            _thread(),
            "api*pulls/7/comments/901/replies*",
            _GH_REPLY,
            "repos/{owner}/{repo}/pulls/7/comments/901/replies",
            "@someone starts like a mention",
            ("1234", "PRRC_x"),
        ),
        (
            GLAB,
            _glab_thread(),
            "api*discussions/d1/notes*",
            _GLAB_REPLY,
            "projects/:id/merge_requests/9/discussions/d1/notes",
            "Answer: ok",
            ("77",),
        ),
    ],
    ids=["gh", "gh-body-starting-with-at", "glab"],
)
async def test_reply_to_thread_posts_on_the_first_comment(
    cli, tmp_path, backend, thread, route, answer, path, body, ids
):
    cli.stub(backend.name, routes={route: answer})

    posted = await backend.cls().reply_to_thread(repo=tmp_path, thread=thread, body=body)

    args = cli.argv(backend.name)
    assert path in args and "POST" in args
    assert args[args.index(f"body={body}") - 1] == "-f"  # always a raw string, never `-F`
    assert (posted.ids, posted.created_at) == (ids, _AT)
    assert posted.body == json.loads(answer)["body"]


@pytest.mark.parametrize(
    "backend, thread, routes, expect",
    [
        (
            GH,
            _thread(),
            {"api graphql": gql({})},
            ["resolveReviewThread", "threadId", "thread=PRT_1"],
        ),
        (
            GLAB,
            _glab_thread(),
            {"api*discussions/d1*": json.dumps({"id": "d1"})},
            ["projects/:id/merge_requests/9/discussions/d1", "PUT", "-F\nresolved=true"],
        ),
    ],
    ids=["gh", "glab"],
)
async def test_resolve_thread(cli, tmp_path, backend, thread, routes, expect):
    cli.stub(backend.name, routes=routes)

    await backend.cls().resolve_thread(repo=tmp_path, thread=thread)

    args = cli.argv(backend.name)
    assert all(part in "\n".join(args) for part in expect)


@pytest.mark.parametrize(
    "backend, routes, authors, types",
    [
        (
            GH,
            {
                READ_BRANCH: GH_PR,
                "api*issues/7/comments*-F page=2*": _GH_LAST,
                "api repos/{owner}/{repo}/issues/7/comments": _GH_PAGE,
            },
            101,
            {"User", "Bot"},
        ),
        (
            GLAB,
            {
                **glab_mr(GLAB_MR),
                "api projects/:id/merge_requests/9/notes": json.dumps(
                    [
                        {"id": 1, "author": {"username": "a"}, "body": "plain", "system": False},
                        {"id": 2, "author": {"username": "a"}, "body": "moved", "system": True},
                        {
                            "id": 3,
                            "author": {"username": "a"},
                            "body": "x",
                            "type": "DiscussionNote",
                        },
                        {"id": 4, "author": {"username": "a"}, "body": "y", "type": "DiffNote"},
                    ]
                ),
            },
            1,
            {None},
        ),
    ],
    ids=["gh", "glab"],
)
async def test_mr_comments(cli, tmp_path, backend, routes, authors, types):
    cli.stub(backend.name, routes=routes, default="[]")

    got = await backend.cls().mr_comments(repo=tmp_path, branch="kraft/w1")

    assert len(got) == authors
    assert {n.author_type for n in got} == types


@pytest.mark.parametrize(
    "backend, routes, note_id, path, method, ids",
    [
        (
            GH,
            {READ_BRANCH: GH_PR, "api*issues/7/comments*": _GH_COMMENT_ANSWER},
            None,
            "repos/{owner}/{repo}/issues/7/comments",
            "POST",
            ("555", "IC_555"),
        ),
        (
            GH,
            {"api*issues/comments/555*": _GH_COMMENT_ANSWER},
            "555",
            "repos/{owner}/{repo}/issues/comments/555",
            "PATCH",
            ("555", "IC_555"),
        ),
        (
            GLAB,
            {**glab_mr(GLAB_MR), "api*merge_requests/9/notes*": _GLAB_NOTE_ANSWER},
            None,
            "projects/:id/merge_requests/9/notes",
            "POST",
            ("555",),
        ),
        (
            GLAB,
            {**glab_mr(GLAB_MR), "api*merge_requests/9/notes/555*": _GLAB_NOTE_ANSWER},
            "555",
            "projects/:id/merge_requests/9/notes/555",
            "PUT",
            ("555",),
        ),
    ],
    ids=["gh-create", "gh-edit", "glab-create", "glab-edit"],
)
async def test_upsert_comment(cli, tmp_path, backend, routes, note_id, path, method, ids):
    cli.stub(backend.name, routes=routes)

    posted = await backend.cls().upsert_comment(
        repo=tmp_path, branch="kraft/w1", note_id=note_id, body="summary"
    )

    args = cli.argv(backend.name)
    assert path in args and method in args and "body=summary" in args
    assert posted.ids == ids


@pytest.mark.parametrize(
    "backend, routes, note, in_thread, path",
    [
        (
            GH,
            {"api*pulls/comments/901*": ""},
            forge.ForgeNote("PRRC_1", "901", "k", "User", "b", _AT, _AT),
            True,
            "repos/{owner}/{repo}/pulls/comments/901",
        ),
        (
            GH,
            {"api*issues/comments/555*": ""},
            forge.ForgeNote("IC_555", "555", "k", "User", "b", _AT, _AT),
            False,
            "repos/{owner}/{repo}/issues/comments/555",
        ),
        (
            GLAB,
            {**glab_mr(GLAB_MR), "api*merge_requests/9/notes/555*": ""},
            forge.ForgeNote("555", "555", "k", None, "b", _AT, _AT),
            False,
            "projects/:id/merge_requests/9/notes/555",
        ),
    ],
    ids=["gh-thread", "gh-comment", "glab"],
)
async def test_delete_note(cli, tmp_path, backend, routes, note, in_thread, path):
    cli.stub(backend.name, routes=routes)

    await backend.cls().delete_note(
        repo=tmp_path, branch="kraft/w1", note=note, in_thread=in_thread
    )

    args = cli.argv(backend.name)
    assert path in args and "DELETE" in args
